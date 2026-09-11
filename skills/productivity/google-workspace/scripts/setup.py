#!/usr/bin/env python3
"""Profile compatibility CLI for native Google Workspace OAuth.

End users connect through Settings -> Keys or the owner-gated
``google_workspace_auth`` tool. Agents must never collect an app credential or
localhost callback in chat. Installation OAuth credentials are provisioned by
the root-owned deployment perimeter and cannot be written through this script.

Commands:
  setup.py --check                          # Is auth valid? Exit 0 = yes, 1 = no
  setup.py --auth-url --services LIST       # Print a least-privilege OAuth URL
  setup.py --auth-code CALLBACK_URL         # Complete with exact localhost URL
  setup.py --revoke                         # Revoke and delete stored token
  setup.py --install-deps                   # Install Python dependencies only

The app file is stored once in installation DATA. Tokens and pending PKCE state
remain profile-scoped and use the native atomic/locking implementation.
"""

from __future__ import annotations  # allow PEP 604 `X | None` on Python 3.9+

import argparse
import json
import shutil
import subprocess
import sys
from importlib.metadata import version as _distribution_version
from pathlib import Path

# Ensure sibling modules (_hermes_home) are importable when run standalone.
_SCRIPTS_DIR = str(Path(__file__).resolve().parent)
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from _hermes_home import display_hermes_home, get_hermes_home
from korra_cli import google_workspace as _native_google
from utils import atomic_json_write
from google_oauth_scopes import (
    MINIMUM_SCOPES,
    SERVICE_SCOPES,
    TOKEN_REQUESTED_SCOPES_KEY,
    TOKEN_SERVICES_KEY,
    granted_scopes_from_payload as _granted_scopes_from_payload,
    parse_services as _parse_services,
    scope_difference as _scope_difference,
    scopes_for_services as _scopes_for_services,
    validate_scope_contract as _validate_scope_contract,
)

HERMES_HOME = get_hermes_home()
TOKEN_PATH = _native_google.token_path(HERMES_HOME)
PENDING_AUTH_PATH = _native_google.pending_path(HERMES_HOME)
_native_google._private_dir(_native_google.profile_google_dir(HERMES_HOME))

# Backwards-compatible name consumed by wrapper tests and older imports.
# ``all`` now expands to these six exact per-service minimum scopes.
SCOPES = list(MINIMUM_SCOPES)

# Exact pins: keep in sync with pyproject.toml [project.optional-dependencies].google
# and tools/lazy_deps.py LAZY_DEPS['skill.google_workspace'].
# Pinning all protects against version drift and ensures the security floors
# (httplib2 GHSA-j5g9-f88f-gfj3, stale pyasn1/google-auth) are honoured
# regardless of install path.
REQUIRED_PACKAGES = [
    "google-api-python-client==2.194.0",
    "google-auth==2.55.1",
    "google-auth-oauthlib==1.3.1",
    "google-auth-httplib2==0.3.1",
    # GHSA-j5g9-f88f-gfj3 — Decompression Bomb DoS via unbounded gzip/deflate
    "httplib2==0.32.0",
    "pyasn1==0.6.4",
]

# OAuth redirect for "out of band" manual code copy flow.
# Google deprecated OOB, so we use a localhost redirect and tell the user to
# copy the code from the browser's URL bar (or the page body).
REDIRECT_URI = _native_google.REDIRECT_URI


def _normalize_authorized_user_payload(payload: dict) -> dict:
    normalized = dict(payload)
    if not normalized.get("type"):
        normalized["type"] = "authorized_user"
    return normalized


def _load_token_payload(path: Path = TOKEN_PATH) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _missing_required_packages() -> list[str]:
    """Return exact requirements absent or stale in this interpreter.

    All REQUIRED_PACKAGES entries are exact ``name==version`` pins, so a
    direct version comparison is sufficient — no ``packaging`` dependency
    needed in this standalone script.
    """
    missing = []
    for spec in REQUIRED_PACKAGES:
        name, _, wanted = spec.partition("==")
        try:
            if _distribution_version(name) != wanted:
                missing.append(spec)
        except Exception:
            missing.append(spec)
    return missing


def install_deps():
    """Install missing or stale Google API packages. Returns True on success."""
    missing = _missing_required_packages()
    if not missing:
        print("Dependencies already installed.")
        return True

    print("Installing Google API dependencies...")

    # First choice: pip in the current interpreter. Works for most installs.
    try:
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "--quiet"] + missing,
            stdout=subprocess.DEVNULL,
        )
        remaining = _missing_required_packages()
        if remaining:
            print(f"ERROR: Dependencies remain stale after pip install: {' '.join(remaining)}")
            return False
        print("Dependencies installed.")
        return True
    except subprocess.CalledProcessError as e:
        pip_error = e

    # Fallback: the interpreter has no pip (the Korra Docker image's venv is
    # built with `uv sync`, which does not bootstrap pip). `uv pip install
    # --python <interpreter>` installs into that exact interpreter without
    # needing pip present. Targeting sys.executable keeps us on the venv the
    # script is actually running under, rather than guessing.
    uv = shutil.which("uv")
    if uv:
        try:
            subprocess.check_call(
                [uv, "pip", "install", "--python", sys.executable, "--quiet"]
                + missing,
                stdout=subprocess.DEVNULL,
            )
            remaining = _missing_required_packages()
            if remaining:
                print(f"ERROR: Dependencies remain stale after uv install: {' '.join(remaining)}")
                return False
            print("Dependencies installed.")
            return True
        except subprocess.CalledProcessError as e:
            print(f"ERROR: Failed to install dependencies via uv: {e}")
            print(f"Manually: {uv} pip install --python {sys.executable} {' '.join(REQUIRED_PACKAGES)}")
            return False

    print(f"ERROR: Failed to install dependencies: {pip_error}")
    print(
        "On environments without pip (e.g. Nix, or the Korra Docker image's "
        "uv-managed venv), install the optional extra instead:"
    )
    print("  korra setup")
    print(f"Or manually: {sys.executable} -m pip install {' '.join(REQUIRED_PACKAGES)}")
    return False


def _ensure_deps():
    """Check exact dependency versions, install if stale, exit on failure."""
    if _missing_required_packages() and not install_deps():
        sys.exit(1)


def check_auth_live():
    """Check auth with a real API call to detect disabled_client/account issues."""
    # quiet=True suppresses the "AUTHENTICATED" print from check_auth so the
    # final status line reflects the live-call outcome (OK or FAILED).
    if not check_auth(quiet=True):
        return False
    try:
        services, _ = _validate_scope_contract(_load_token_payload(TOKEN_PATH))
        if not services:
            print("LIVE_CHECK_FAILED: No supported service in stored scope contract.")
            return False
        checked = services[0]
        _native_google.check_service(checked, profile_home=HERMES_HOME)
        print(f"LIVE_CHECK_OK: Real {checked} API call succeeded.")
        return True
    except Exception as e:
        err_str = str(e).lower()
        if "disabled_client" in err_str or "invalid_client" in err_str:
            print(f"LIVE_CHECK_FAILED: OAuth client or account disabled: {e}")
            print("  1. Check Google Cloud Console for disabled OAuth client")
            print("  2. Check myaccount.google.com for account status")
            print("  3. Do NOT retry with a disabled account")
        else:
            print(f"LIVE_CHECK_FAILED: {e}")
        return False


def check_auth(quiet: bool = False):
    """Check if stored credentials are valid. Prints status, exits 0 or 1."""
    if not TOKEN_PATH.exists():
        print(f"NOT_AUTHENTICATED: No token at {TOKEN_PATH}")
        return False

    payload = _load_token_payload(TOKEN_PATH)
    try:
        services, expected_scopes = _validate_scope_contract(payload)
    except ValueError as e:
        print(f"TOKEN_SCOPE_CONTRACT_INVALID: {e}")
        return False

    _ensure_deps()
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request

    try:
        native_creds = _native_google._credentials(HERMES_HOME, None)
    except _native_google.GoogleWorkspaceError:
        native_creds = None
    if native_creds is not None:
        if not native_creds.valid:
            print("TOKEN_INVALID: Re-run setup.")
            return False
        if not quiet:
            print(
                f"AUTHENTICATED: Token valid at {TOKEN_PATH} "
                f"(services: {','.join(services)})"
            )
        return True

    try:
        # Don't pass scopes — user may have authorized only a subset.
        # Passing scopes forces google-auth to validate them on refresh,
        # which fails with invalid_scope if the token has fewer scopes
        # than requested.
        creds = Credentials.from_authorized_user_file(str(TOKEN_PATH))
    except Exception as e:
        print(f"TOKEN_CORRUPT: {e}")
        return False

    if creds.valid:
        if services is None:
            print("AUTHENTICATED_LEGACY: Token is valid; choose --services on next authorization.")
        if not quiet:
            label = ",".join(services) if services is not None else "legacy-untracked"
            print(f"AUTHENTICATED: Token valid at {TOKEN_PATH} (services: {label})")
        return True

    if creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            refreshed = _normalize_authorized_user_payload(json.loads(creds.to_json()))
            refreshed_scopes = _granted_scopes_from_payload(refreshed)
            if refreshed_scopes:
                missing, extra = _scope_difference(refreshed_scopes, expected_scopes)
                if missing or extra:
                    print("REFRESH_SCOPE_CONTRACT_INVALID: Google returned a different scope set.")
                    return False
            refreshed.pop("scope", None)
            refreshed["scopes"] = expected_scopes
            if services is not None:
                refreshed[TOKEN_SERVICES_KEY] = list(services)
                refreshed[TOKEN_REQUESTED_SCOPES_KEY] = expected_scopes
            atomic_json_write(TOKEN_PATH, refreshed, mode=0o600)
            if not quiet:
                label = ",".join(services) if services is not None else "legacy-untracked"
                print(f"AUTHENTICATED: Token refreshed at {TOKEN_PATH} (services: {label})")
            return True
        except Exception as e:
            err_str = str(e).lower()
            if "disabled_client" in err_str or "invalid_client" in err_str:
                print(f"OAUTH_CLIENT_DISABLED: {e}")
                print("  The OAuth client or Google account has been disabled.")
                print("  Steps to resolve:")
                print("    1. Check your Google Cloud Console — verify the OAuth client is not disabled")
                print("    2. Check if your Google account itself has been disabled at myaccount.google.com")
                print("    3. If the account is disabled, you can appeal at accounts.google.com/signin/recovery")
                print("    4. Do NOT retry API calls with a disabled account — this may worsen the situation")
                print("    5. If the OAuth client is disabled, create a new one in Google Cloud Console")
            elif "token_revoked" in err_str or "invalid_grant" in err_str:
                print(f"TOKEN_REVOKED: {e}")
                print("  Re-run setup to re-authenticate.")
            else:
                print(f"REFRESH_FAILED: {e}")
            return False

    print("TOKEN_INVALID: Re-run setup.")
    return False


def get_auth_url(services: tuple[str, ...]):
    """Print the OAuth authorization URL. User visits this in a browser."""
    try:
        result = _native_google.start(services, profile_home=HERMES_HOME)
    except (_native_google.GoogleWorkspaceError, ValueError) as e:
        print(f"ERROR: {e}")
        sys.exit(1)
    print(result["authorization_url"])


def exchange_auth_code(code: str):
    """Exchange the authorization code for a token and save it."""
    if not isinstance(code, str) or not code.startswith("http"):
        print("ERROR: Paste the complete localhost callback URL, including state and code.")
        sys.exit(1)
    try:
        _native_google.complete(code, profile_home=HERMES_HOME)
    except _native_google.GoogleWorkspaceError as e:
        print(f"ERROR: {e}")
        sys.exit(1)
    print(f"OK: Authenticated. Token saved to {TOKEN_PATH}")
    print(f"Profile-scoped token location: {display_hermes_home()}/google-workspace/token.json")


def revoke():
    """Revoke stored token and delete it."""
    result = _native_google.revoke(profile_home=HERMES_HOME)
    print(f"Deleted {TOKEN_PATH}")
    if not result["remote_revoked"]:
        print("WARNING: Remote revocation could not be confirmed; local grant was removed.")


def main():
    parser = argparse.ArgumentParser(description="Google Workspace OAuth setup for Korra")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--check", action="store_true", help="Check if auth is valid (exit 0=yes, 1=no)")
    group.add_argument("--check-live", action="store_true", help="Check auth with a real API call (detects disabled_client)")
    group.add_argument("--auth-url", action="store_true", help="Print OAuth URL for user to visit")
    group.add_argument("--auth-code", metavar="CODE", help="Exchange auth code for token")
    group.add_argument("--revoke", action="store_true", help="Revoke and delete stored token")
    group.add_argument("--install-deps", action="store_true", help="Install Python dependencies")
    parser.add_argument(
        "--services",
        metavar="LIST",
        help=(
            "Comma-separated OAuth allowlist for --auth-url: "
            "email,calendar,drive,contacts,sheets,docs, or explicit all"
        ),
    )
    args = parser.parse_args()

    if args.auth_url:
        try:
            services = _parse_services(args.services)
        except ValueError as e:
            parser.error(str(e))
    elif args.services is not None:
        parser.error("--services is only valid with --auth-url")

    if args.check:
        sys.exit(0 if check_auth() else 1)
    if getattr(args, "check_live", False):
        sys.exit(0 if check_auth_live() else 1)
    elif args.auth_url:
        get_auth_url(services)
    elif args.auth_code:
        exchange_auth_code(args.auth_code)
    elif args.revoke:
        revoke()
    elif args.install_deps:
        sys.exit(0 if install_deps() else 1)


if __name__ == "__main__":
    main()

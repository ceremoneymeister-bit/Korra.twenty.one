#!/usr/bin/env python3
"""Google Workspace OAuth2 setup for Korra.

Fully non-interactive — designed to be driven by the agent via terminal commands.
The agent mediates between this script and the user (works on CLI, Telegram, Discord, etc.)

Commands:
  setup.py --check                          # Is auth valid? Exit 0 = yes, 1 = no
  setup.py --client-secret /path/to.json    # Store OAuth client credentials
  setup.py --auth-url --services LIST       # Print a least-privilege OAuth URL
  setup.py --auth-code CODE                 # Exchange auth code for token
  setup.py --revoke                         # Revoke and delete stored token
  setup.py --install-deps                   # Install Python dependencies only

Agent workflow:
  1. Run --check. If exit 0, auth is good — skip setup.
  2. Ask user for client_secret.json path. Run --client-secret PATH.
  3. Run --auth-url --services LIST. Send the printed URL to the user.
  4. User opens URL, authorizes, gets redirected to a page with a code.
  5. User pastes the code. Agent runs --auth-code CODE.
  6. Run --check to verify. Done.
"""

from __future__ import annotations  # allow PEP 604 `X | None` on Python 3.9+

import argparse
import json
import os
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
from google_oauth_scopes import (
    LEGACY_ALL_SCOPES,
    SERVICE_SCOPES,
    TOKEN_REQUESTED_SCOPES_KEY,
    TOKEN_SERVICES_KEY,
    granted_scopes_from_payload as _granted_scopes_from_payload,
    parse_services as _parse_services,
    scope_difference as _scope_difference,
    scopes_for_services as _scopes_for_services,
    tracked_scope_contract as _tracked_scope_contract,
    validate_scope_contract as _validate_scope_contract,
)

HERMES_HOME = get_hermes_home()
TOKEN_PATH = HERMES_HOME / "google_token.json"
CLIENT_SECRET_PATH = HERMES_HOME / "google_client_secret.json"
PENDING_AUTH_PATH = HERMES_HOME / "google_oauth_pending.json"

# Backwards-compatible name for the former fixed full-Workspace scope list.
# Full access is still available, but only through an explicit ``--services all``.
SCOPES = list(LEGACY_ALL_SCOPES)

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
REDIRECT_URI = "http://localhost:1"


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
        from googleapiclient.discovery import build
        from googleapiclient.errors import HttpError
        from google.oauth2.credentials import Credentials

        _, selected_scopes = _validate_scope_contract(_load_token_payload(TOKEN_PATH))
        creds = Credentials.from_authorized_user_file(str(TOKEN_PATH))
        selected = set(selected_scopes)
        expected_not_found = False
        if selected & {
            "https://www.googleapis.com/auth/calendar",
            "https://www.googleapis.com/auth/calendar.events",
        }:
            service = build("calendar", "v3", credentials=creds)
            request = service.events().list(calendarId="primary", maxResults=1)
        elif "https://www.googleapis.com/auth/drive" in selected:
            service = build("drive", "v3", credentials=creds)
            request = service.files().list(pageSize=1, fields="files(id)")
        elif selected & {
            "https://www.googleapis.com/auth/gmail.modify",
            "https://www.googleapis.com/auth/gmail.readonly",
        }:
            service = build("gmail", "v1", credentials=creds)
            request = service.users().labels().list(userId="me")
        elif "https://www.googleapis.com/auth/contacts.readonly" in selected:
            service = build("people", "v1", credentials=creds)
            request = service.people().connections().list(
                resourceName="people/me",
                pageSize=1,
                personFields="names",
            )
        elif "https://www.googleapis.com/auth/spreadsheets" in selected:
            service = build("sheets", "v4", credentials=creds)
            request = service.spreadsheets().get(
                spreadsheetId="1KorraScopeProbe0000000000000000000000000000",
                fields="spreadsheetId",
            )
            expected_not_found = True
        elif "https://www.googleapis.com/auth/documents" in selected:
            service = build("docs", "v1", credentials=creds)
            request = service.documents().get(
                documentId="1KorraScopeProbe0000000000000000000000000000",
            )
            expected_not_found = True
        else:
            print("LIVE_CHECK_FAILED: No supported service in stored scope contract.")
            return False

        try:
            request.execute()
        except HttpError as e:
            if not expected_not_found or getattr(e.resp, "status", None) != 404:
                raise
        print("LIVE_CHECK_OK: Real API call succeeded.")
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
            TOKEN_PATH.write_text(json.dumps(refreshed, indent=2), encoding="utf-8")
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


def store_client_secret(path: str):
    """Copy and validate client_secret.json to the Korra home directory."""
    src = Path(path).expanduser().resolve()
    if not src.exists():
        print(f"ERROR: File not found: {src}")
        sys.exit(1)

    try:
        data = json.loads(src.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        print("ERROR: File is not valid JSON.")
        sys.exit(1)

    if "installed" not in data and "web" not in data:
        print("ERROR: Not a Google OAuth client secret file (missing 'installed' key).")
        print("Download the correct file from: https://console.cloud.google.com/apis/credentials")
        sys.exit(1)

    CLIENT_SECRET_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")
    print(f"OK: Client secret saved to {CLIENT_SECRET_PATH}")


def _save_pending_auth(*, state: str, code_verifier: str, services: tuple[str, ...]):
    """Persist the OAuth session bits needed for a later token exchange."""
    scopes = _scopes_for_services(services)
    PENDING_AUTH_PATH.write_text(
        json.dumps(
            {
                "state": state,
                "code_verifier": code_verifier,
                "redirect_uri": REDIRECT_URI,
                "services": list(services),
                "scopes": scopes,
            },
            indent=2,
        ), encoding="utf-8"
    )


def _load_pending_auth() -> dict:
    """Load the pending OAuth session created by get_auth_url()."""
    if not PENDING_AUTH_PATH.exists():
        print("ERROR: No pending OAuth session found. Run --auth-url --services LIST first.")
        sys.exit(1)

    try:
        data = json.loads(PENDING_AUTH_PATH.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"ERROR: Could not read pending OAuth session: {e}")
        print("Run --auth-url --services LIST again to start a fresh OAuth session.")
        sys.exit(1)

    if not data.get("state") or not data.get("code_verifier"):
        print("ERROR: Pending OAuth session is missing PKCE data.")
        print("Run --auth-url --services LIST again to start a fresh OAuth session.")
        sys.exit(1)

    try:
        raw_services = data.get("services")
        if not isinstance(raw_services, list) or not raw_services:
            raise ValueError("services must be a non-empty list")
        services = _parse_services(",".join(raw_services))
        expected_scopes = _scopes_for_services(services)
        if data.get("scopes") != expected_scopes:
            raise ValueError("stored scopes do not match selected services")
        if data.get("redirect_uri") != REDIRECT_URI:
            raise ValueError("stored redirect_uri does not match the OAuth client contract")
    except (TypeError, ValueError) as e:
        print(f"ERROR: Pending OAuth scope contract is invalid: {e}")
        print("Run --auth-url --services LIST again to start a fresh OAuth session.")
        sys.exit(1)

    data["services"] = services
    data["scopes"] = expected_scopes

    return data


def _extract_code_and_state(code_or_url: str) -> tuple[str, str | None]:
    """Accept either a raw auth code or the full redirect URL pasted by the user."""
    if not code_or_url.startswith("http"):
        return code_or_url, None

    from urllib.parse import parse_qs, urlparse

    parsed = urlparse(code_or_url)
    params = parse_qs(parsed.query)
    if "code" not in params:
        print("ERROR: No 'code' parameter found in URL.")
        sys.exit(1)

    state = params.get("state", [None])[0]
    return params["code"][0], state


def get_auth_url(services: tuple[str, ...]):
    """Print the OAuth authorization URL. User visits this in a browser."""
    if not CLIENT_SECRET_PATH.exists():
        print("ERROR: No client secret stored. Run --client-secret first.")
        sys.exit(1)

    try:
        requested_scopes = _scopes_for_services(services)
    except ValueError as e:
        print(f"ERROR: Invalid service selection: {e}")
        sys.exit(1)

    if TOKEN_PATH.exists():
        try:
            token_payload = _load_token_payload(TOKEN_PATH)
            tracked = _tracked_scope_contract(token_payload)
            if tracked is None:
                existing_scopes = set(_granted_scopes_from_payload(token_payload))
                if existing_scopes != set(requested_scopes):
                    raise ValueError("existing legacy token has a different scope set")
            elif tracked[0] != services:
                raise ValueError(
                    "requested services differ from the stored token contract; "
                    "run --revoke before changing services"
                )
        except ValueError as e:
            print(f"ERROR: Refusing to change an existing token's scope contract: {e}")
            sys.exit(1)

    _ensure_deps()
    from google_auth_oauthlib.flow import Flow

    flow = Flow.from_client_secrets_file(
        str(CLIENT_SECRET_PATH),
        scopes=requested_scopes,
        redirect_uri=REDIRECT_URI,
        autogenerate_code_verifier=True,
    )
    auth_url, state = flow.authorization_url(
        access_type="offline",
        prompt="consent",
    )
    _save_pending_auth(
        state=state,
        code_verifier=flow.code_verifier,
        services=services,
    )
    # Print just the URL so the agent can extract it cleanly
    print(auth_url)


def exchange_auth_code(code: str):
    """Exchange the authorization code for a token and save it."""
    if not CLIENT_SECRET_PATH.exists():
        print("ERROR: No client secret stored. Run --client-secret first.")
        sys.exit(1)

    pending_auth = _load_pending_auth()
    raw_callback = code
    code, returned_state = _extract_code_and_state(code)
    if raw_callback.startswith("http") and returned_state != pending_auth["state"]:
        print("ERROR: OAuth state mismatch. Run --auth-url --services LIST again.")
        sys.exit(1)

    _ensure_deps()
    from google_auth_oauthlib.flow import Flow
    from urllib.parse import parse_qs, urlparse

    requested_services = pending_auth["services"]
    requested_scopes = pending_auth["scopes"]

    # A full callback URL carries Google's authoritative granted-scope list.
    callback_scopes = None
    if isinstance(raw_callback, str) and raw_callback.startswith("http"):
        params = parse_qs(urlparse(raw_callback).query)
        scope_val = (params.get("scope") or [""])[0].strip()
        if scope_val:
            callback_scopes = scope_val.split()

    flow = Flow.from_client_secrets_file(
        str(CLIENT_SECRET_PATH),
        scopes=requested_scopes,
        redirect_uri=pending_auth.get("redirect_uri", REDIRECT_URI),
        state=pending_auth["state"],
        code_verifier=pending_auth["code_verifier"],
    )

    try:
        # Let the exchange complete so we can inspect and reject a partial grant ourselves.
        os.environ["OAUTHLIB_RELAX_TOKEN_SCOPE"] = "1"
        flow.fetch_token(code=code)
    except Exception as e:
        print(f"ERROR: Token exchange failed: {e}")
        print("The code may have expired. Run --auth-url --services LIST to get a fresh URL.")
        sys.exit(1)

    creds = flow.credentials
    token_payload = _normalize_authorized_user_payload(json.loads(creds.to_json()))

    reported_scope_sets = []
    if hasattr(creds, "granted_scopes") and creds.granted_scopes:
        reported_scope_sets.append(list(creds.granted_scopes))
    if callback_scopes:
        reported_scope_sets.append(callback_scopes)
    if not reported_scope_sets:
        print("ERROR: Google did not report the granted scopes; refusing to store an unverified token.")
        print("Run --auth-url --services LIST again and paste the entire redirected URL.")
        sys.exit(1)

    for actually_granted in reported_scope_sets:
        missing, extra = _scope_difference(actually_granted, requested_scopes)
        if missing or extra:
            details = []
            if missing:
                details.append(f"missing: {', '.join(missing)}")
            if extra:
                details.append(f"unexpected: {', '.join(extra)}")
            print("ERROR: Google granted a different scope set (" + "; ".join(details) + ").")
            print("No token was stored. Run --auth-url again and approve the exact selected services.")
            sys.exit(1)

    token_payload.pop("scope", None)
    token_payload["scopes"] = list(requested_scopes)
    token_payload[TOKEN_SERVICES_KEY] = list(requested_services)
    token_payload[TOKEN_REQUESTED_SCOPES_KEY] = list(requested_scopes)

    TOKEN_PATH.write_text(json.dumps(token_payload, indent=2), encoding="utf-8")
    PENDING_AUTH_PATH.unlink(missing_ok=True)
    print(f"OK: Authenticated. Token saved to {TOKEN_PATH}")
    print(f"Profile-scoped token location: {display_hermes_home()}/google_token.json")


def revoke():
    """Revoke stored token and delete it."""
    if not TOKEN_PATH.exists():
        PENDING_AUTH_PATH.unlink(missing_ok=True)
        print("No token to revoke.")
        return

    services = None
    try:
        payload = _load_token_payload(TOKEN_PATH)
        try:
            services, _ = _validate_scope_contract(payload)
        except ValueError as e:
            print(f"WARNING: Stored scope contract is invalid; continuing revocation: {e}")

        revocation_token = payload.get("refresh_token") or payload.get("token")
        if not isinstance(revocation_token, str) or not revocation_token:
            raise ValueError("stored token has no refresh_token or access token")

        import urllib.parse
        import urllib.request
        body = urllib.parse.urlencode({"token": revocation_token}).encode("ascii")
        urllib.request.urlopen(
            urllib.request.Request(
                "https://oauth2.googleapis.com/revoke",
                data=body,
                method="POST",
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            ),
            timeout=15,
        )
        print("Token revoked with Google.")
        if services is not None:
            print(f"Revoked service set: {','.join(services)}")
    except Exception as e:
        print(f"Remote revocation failed (token may already be invalid): {e}")
    finally:
        TOKEN_PATH.unlink(missing_ok=True)
        PENDING_AUTH_PATH.unlink(missing_ok=True)
        print(f"Deleted {TOKEN_PATH}")


def main():
    parser = argparse.ArgumentParser(description="Google Workspace OAuth setup for Korra")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--check", action="store_true", help="Check if auth is valid (exit 0=yes, 1=no)")
    group.add_argument("--check-live", action="store_true", help="Check auth with a real API call (detects disabled_client)")
    group.add_argument("--client-secret", metavar="PATH", help="Store OAuth client_secret.json")
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
    elif args.client_secret:
        store_client_secret(args.client_secret)
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

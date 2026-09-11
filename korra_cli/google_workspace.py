"""Native, profile-isolated Google Workspace OAuth service.

The OAuth client belongs to one Korra installation.  User grants belong to a
single profile.  Public methods return status and URLs only; app secrets,
authorization codes, access tokens, and refresh tokens never leave this layer.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import stat
import time
import urllib.error
import urllib.parse
import urllib.request
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator

from korra_constants import get_default_hermes_root, get_hermes_home, korra_env
from korra_cli.google_workspace_scopes import (
    GOOGLE_IDENTITY_SCOPES,
    SERVICE_SCOPES,
    TOKEN_REQUESTED_SCOPES_KEY,
    TOKEN_SERVICES_KEY,
    granted_scopes_from_payload,
    legacy_scope_inventory,
    parse_services,
    scope_difference,
    scopes_for_services,
    validate_scope_contract,
)
from utils import atomic_json_write


AUTHORIZATION_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
REVOCATION_ENDPOINT = "https://oauth2.googleapis.com/revoke"
REDIRECT_URI = "http://localhost"
PENDING_TTL_SECONDS = 10 * 60
LOCK_TIMEOUT_SECONDS = 3.0
DEFAULT_APP_MOUNT_PATH = Path("/run/korra-secrets/google-oauth-client.json")
MOUNTINFO_PATH = Path("/proc/self/mountinfo")


class GoogleWorkspaceError(RuntimeError):
    def __init__(self, code: str, message: str, *, status_code: int = 400) -> None:
        self.code = code
        self.status_code = status_code
        super().__init__(message)


def installation_google_dir(root: Path | None = None) -> Path:
    return _confined_child(root or get_default_hermes_root(), "google")


def app_credentials_path() -> Path:
    configured = korra_env("KORRA_GOOGLE_OAUTH_CLIENT_PATH", "").strip()
    return Path(configured) if configured else DEFAULT_APP_MOUNT_PATH


def profile_google_dir(profile_home: Path | None = None) -> Path:
    # Keep mutable profile grants outside installation DATA/google. The
    # default profile home is the installation root itself, so sharing the
    # same directory would let the runtime replace the operator-owned app or
    # make its own token directory unwritable once DATA/google is root-owned.
    return _confined_child(profile_home or get_hermes_home(), "google-workspace")


def token_path(profile_home: Path | None = None) -> Path:
    return profile_google_dir(profile_home) / "token.json"


def pending_path(profile_home: Path | None = None) -> Path:
    return profile_google_dir(profile_home) / "pending.json"


def legacy_token_path(profile_home: Path | None = None) -> Path:
    return (profile_home or get_hermes_home()) / "google_token.json"


def legacy_app_path(profile_home: Path | None = None) -> Path:
    return (profile_home or get_hermes_home()) / "google_client_secret.json"


def _active_token_path(profile_home: Path | None = None) -> Path:
    current = token_path(profile_home)
    _reject_symlink(current)
    return current if current.exists() else legacy_token_path(profile_home)


def _confined_child(base: Path, name: str) -> Path:
    base_path = Path(base).expanduser()
    candidate = base_path / name
    try:
        resolved_base = base_path.resolve(strict=False)
        if candidate.is_symlink():
            raise GoogleWorkspaceError(
                "state_path_unsafe",
                f"Google state path must not be a symbolic link: {candidate}",
                status_code=409,
            )
        resolved_candidate = candidate.resolve(strict=False)
    except OSError as exc:
        raise GoogleWorkspaceError(
            "state_path_unsafe",
            f"Cannot validate Google state path: {candidate}",
            status_code=409,
        ) from exc
    if resolved_candidate.parent != resolved_base:
        raise GoogleWorkspaceError(
            "state_path_unsafe",
            f"Google state path escapes its owner directory: {candidate}",
            status_code=409,
        )
    return candidate


def _private_dir(path: Path) -> None:
    if path.is_symlink():
        raise GoogleWorkspaceError(
            "state_path_unsafe",
            f"Google state directory must not be a symbolic link: {path}",
            status_code=409,
        )
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.is_symlink() or not path.is_dir():
        raise GoogleWorkspaceError(
            "state_path_unsafe",
            f"Google state directory is unsafe: {path}",
            status_code=409,
        )
    try:
        path.chmod(0o700)
    except OSError as exc:
        raise GoogleWorkspaceError("state_permissions", f"Cannot secure {path}: {exc}", status_code=500) from exc


def _reject_symlink(path: Path) -> None:
    try:
        if path.is_symlink():
            raise GoogleWorkspaceError(
                "state_path_unsafe",
                f"Google state file must not be a symbolic link: {path}",
                status_code=409,
            )
    except OSError as exc:
        raise GoogleWorkspaceError(
            "state_path_unsafe",
            f"Cannot validate Google state file: {path}",
            status_code=409,
        ) from exc


def _reject_symlink_components(path: Path) -> None:
    current = path
    while True:
        _reject_symlink(current)
        if current.parent == current:
            return
        current = current.parent


def _atomic_private_json(path: Path, payload: dict[str, Any]) -> None:
    _private_dir(path.parent)
    _reject_symlink(path)
    atomic_json_write(path, payload, mode=0o600)
    _reject_symlink(path)


def _safe_unlink(path: Path) -> None:
    _reject_symlink(path)
    path.unlink(missing_ok=True)


def _read_json(path: Path, *, label: str) -> dict[str, Any]:
    _reject_symlink(path)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise GoogleWorkspaceError(f"{label}_missing", f"{label} is not configured") from None
    except (OSError, ValueError) as exc:
        raise GoogleWorkspaceError(f"{label}_invalid", f"{label} is unreadable or invalid") from exc
    if not isinstance(value, dict):
        raise GoogleWorkspaceError(f"{label}_invalid", f"{label} must be a JSON object")
    return value


def _app_block(payload: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    present = [name for name in ("installed", "web") if isinstance(payload.get(name), dict)]
    if len(present) != 1:
        raise GoogleWorkspaceError(
            "app_invalid", "OAuth app must contain exactly one installed or web credential"
        )
    kind = present[0]
    block = payload[kind]
    client_id = str(block.get("client_id") or "").strip()
    client_secret = str(block.get("client_secret") or "").strip()
    auth_uri = str(block.get("auth_uri") or AUTHORIZATION_ENDPOINT).strip()
    token_uri = str(block.get("token_uri") or TOKEN_ENDPOINT).strip()
    if not client_id or not client_secret:
        raise GoogleWorkspaceError("app_invalid", "OAuth app is missing client_id or client_secret")
    if auth_uri != AUTHORIZATION_ENDPOINT or token_uri != TOKEN_ENDPOINT:
        raise GoogleWorkspaceError("app_invalid", "OAuth app has unexpected Google endpoints")
    redirects = block.get("redirect_uris")
    if not isinstance(redirects, list) or REDIRECT_URI not in redirects:
        raise GoogleWorkspaceError(
            "redirect_not_registered",
            f"OAuth client must register the exact redirect URI {REDIRECT_URI}",
        )
    return kind, block


def _mount_field(value: str) -> str:
    return (
        value.replace("\\040", " ")
        .replace("\\011", "\t")
        .replace("\\012", "\n")
        .replace("\\134", "\\")
    )


def _is_exact_read_only_mount(path: Path) -> bool:
    try:
        target = path.resolve(strict=True)
        for line in MOUNTINFO_PATH.read_text(encoding="utf-8").splitlines():
            fields = line.split()
            if len(fields) >= 6 and Path(_mount_field(fields[4])) == target:
                return "ro" in fields[5].split(",")
    except (OSError, ValueError):
        return False
    return False


def _operator_app_file_is_safe(app_stat: os.stat_result) -> bool:
    return (
        stat.S_ISREG(app_stat.st_mode)
        and app_stat.st_uid == 0
        and app_stat.st_gid == os.getegid()
        and stat.S_IMODE(app_stat.st_mode) == 0o640
    )


def _validate_operator_app_permissions() -> None:
    """Require root ownership and an exact read-only runtime mount."""
    path = app_credentials_path()
    _reject_symlink_components(path)
    try:
        app_stat = path.stat()
    except FileNotFoundError:
        raise GoogleWorkspaceError(
            "app_missing",
            "operator-managed OAuth app is not configured",
        ) from None
    except OSError as exc:
        raise GoogleWorkspaceError(
            "app_permissions",
            "operator-managed OAuth app permissions cannot be verified",
            status_code=409,
        ) from exc

    app_ok = (
        _operator_app_file_is_safe(app_stat)
        and path.is_absolute()
        and _is_exact_read_only_mount(path)
    )
    if not app_ok:
        raise GoogleWorkspaceError(
            "app_permissions",
            "OAuth app must be a root-owned, runtime-group-readable 0640 file "
            "mounted read-only at the configured runtime path",
            status_code=409,
        )


def _load_app() -> tuple[str, dict[str, Any]]:
    _validate_operator_app_permissions()
    payload = _read_json(app_credentials_path(), label="app")
    return _app_block(payload)


@contextmanager
def _state_lock(profile_home: Path | None = None) -> Iterator[None]:
    directory = profile_google_dir(profile_home)
    _private_dir(directory)
    path = directory / ".oauth.lock"
    deadline = time.monotonic() + LOCK_TIMEOUT_SECONDS
    fd: int | None = None
    while fd is None:
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            try:
                age = time.time() - path.stat().st_mtime
                if age > PENDING_TTL_SECONDS:
                    _safe_unlink(path)
                    continue
            except OSError:
                pass
            if time.monotonic() >= deadline:
                raise GoogleWorkspaceError(
                    "flow_busy", "Another Google authorization operation is in progress", status_code=409
                ) from None
            time.sleep(0.02)
    try:
        os.write(fd, f"{os.getpid()}\n".encode("ascii"))
        os.fsync(fd)
        yield
    finally:
        os.close(fd)
        _safe_unlink(path)


def _pending_record(profile_home: Path | None = None) -> dict[str, Any] | None:
    path = pending_path(profile_home)
    _reject_symlink(path)
    if not path.exists():
        return None
    try:
        payload = _read_json(path, label="pending flow")
        created_at = int(payload["created_at"])
        expires_at = int(payload["expires_at"])
        if expires_at != created_at + PENDING_TTL_SECONDS:
            raise ValueError("TTL mismatch")
        if expires_at <= int(time.time()):
            _safe_unlink(path)
            return None
        services = parse_services(",".join(payload["services"]))
        if payload.get("scopes") != scopes_for_services(services):
            raise ValueError("scope mismatch")
        if payload.get("redirect_uri") != REDIRECT_URI:
            raise ValueError("redirect mismatch")
        if not isinstance(payload.get("state"), str) or not payload["state"]:
            raise ValueError("state missing")
        if not isinstance(payload.get("code_verifier"), str) or not payload["code_verifier"]:
            raise ValueError("verifier missing")
        payload["services"] = services
        return payload
    except GoogleWorkspaceError:
        return None
    except (KeyError, TypeError, ValueError, OSError):
        return None


def _token_status(profile_home: Path | None = None) -> dict[str, Any]:
    path = token_path(profile_home)
    _reject_symlink(path)
    if path.exists():
        try:
            payload = _read_json(path, label="token")
            tracked = payload.get(TOKEN_SERVICES_KEY) is not None
            services, _ = validate_scope_contract(payload)
            if not tracked:
                inventory = legacy_scope_inventory(payload)
                return {
                    "state": "reauthorization_required",
                    "reason": inventory["kind"],
                    "legacy_scope_count": inventory["scope_count"],
                    "unknown_scope_count": inventory["unknown_scope_count"],
                    "usable_services": inventory["usable_services"],
                    "legacy_compatible": inventory["compatible"],
                    "action": "revoke_reconnect",
                }
            return {
                "state": "connected",
                "services": list(services),
                "expires_at": payload.get("expires_at"),
                "action": None,
            }
        except GoogleWorkspaceError as exc:
            return {"state": "reauthorization_required", "reason": exc.code, "action": "revoke_reconnect"}
        except ValueError:
            return {"state": "reauthorization_required", "reason": "scope_contract_invalid", "action": "revoke_reconnect"}

    old = legacy_token_path(profile_home)
    _reject_symlink(old)
    if old.exists():
        try:
            inventory = legacy_scope_inventory(_read_json(old, label="legacy token"))
        except GoogleWorkspaceError:
            inventory = {"kind": "invalid", "scope_count": 0, "unknown_scope_count": 0}
        return {
            "state": "reauthorization_required",
            "reason": inventory["kind"],
            "legacy_scope_count": inventory["scope_count"],
            "unknown_scope_count": inventory["unknown_scope_count"],
            "usable_services": inventory.get("usable_services", []),
            "legacy_compatible": inventory.get("compatible", False),
            "action": "revoke_reconnect",
        }
    return {"state": "not_connected", "services": [], "action": "connect"}


def status(*, profile_home: Path | None = None) -> dict[str, Any]:
    try:
        kind, _ = _load_app()
        app = {"configured": True, "credential_type": kind, "redirect_uri": REDIRECT_URI}
    except GoogleWorkspaceError as exc:
        operator_path = app_credentials_path()
        app = {
            "configured": False,
            "reason": exc.code,
            "operator_action": f"Mount the Ceremoneymeister OAuth app read-only at {operator_path}",
        }
        if legacy_app_path(profile_home).exists():
            app["legacy_profile_credential"] = True
            app["operator_action"] = "Provision one verified OAuth app through the operator-only read-only mount"
    token = _token_status(profile_home)
    with _state_lock(profile_home):
        pending = _pending_record(profile_home)
    return {
        "app": app,
        "connection": token,
        "pending": (
            {
                "active": True,
                "services": list(pending["services"]),
                "expires_at": pending["expires_at"],
            }
            if pending
            else {"active": False}
        ),
        "available_services": list(SERVICE_SCOPES),
        "completion_mode": "manual_localhost_url",
    }


def start(
    services: str | tuple[str, ...],
    *,
    profile_home: Path | None = None,
) -> dict[str, Any]:
    selected = parse_services(services) if isinstance(services, str) else services
    scopes = scopes_for_services(selected)
    _, app = _load_app()
    state = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode("ascii")
    now = int(time.time())
    with _state_lock(profile_home):
        _reject_symlink(token_path(profile_home))
        _reject_symlink(legacy_token_path(profile_home))
        _reject_symlink(pending_path(profile_home))
        if token_path(profile_home).exists() or legacy_token_path(profile_home).exists():
            raise GoogleWorkspaceError(
                "revoke_required",
                "Disconnect the existing Google grant before selecting services again",
                status_code=409,
            )
        directory = profile_google_dir(profile_home)
        _atomic_private_json(
            pending_path(profile_home),
            {
                "version": 1,
                "state": state,
                "code_verifier": verifier,
                "redirect_uri": REDIRECT_URI,
                "services": list(selected),
                "scopes": scopes,
                "created_at": now,
                "expires_at": now + PENDING_TTL_SECONDS,
            },
        )
        directory.chmod(0o700)
    query = urllib.parse.urlencode(
        {
            "client_id": app["client_id"],
            "redirect_uri": REDIRECT_URI,
            "response_type": "code",
            "scope": " ".join(scopes),
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "access_type": "offline",
            "prompt": "consent",
            "include_granted_scopes": "false",
        }
    )
    return {
        "status": "pending",
        "authorization_url": f"{AUTHORIZATION_ENDPOINT}?{query}",
        "services": list(selected),
        "expires_at": now + PENDING_TTL_SECONDS,
        "instructions": "Open the URL, approve access, then paste the full localhost URL into Korra Settings → Keys.",
    }


def _parse_callback(callback_url: str) -> tuple[str, str, list[str] | None]:
    try:
        parsed = urllib.parse.urlparse(callback_url.strip())
    except ValueError as exc:
        raise GoogleWorkspaceError("callback_invalid", "Paste the complete localhost callback URL") from exc
    if (
        parsed.scheme != "http"
        or parsed.hostname != "localhost"
        or parsed.port not in (None, 80)
        or parsed.path not in ("", "/")
        or parsed.username is not None
        or parsed.password is not None
        or bool(parsed.fragment)
    ):
        raise GoogleWorkspaceError("callback_invalid", f"Callback must start with {REDIRECT_URI}")
    params = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
    if params.get("error"):
        raise GoogleWorkspaceError("consent_denied", "Google authorization was cancelled or denied")
    code_values = params.get("code", [])
    state_values = params.get("state", [])
    if len(code_values) != 1 or len(state_values) != 1 or not code_values[0] or not state_values[0]:
        raise GoogleWorkspaceError("callback_invalid", "Callback must contain one code and one state")
    scope_value = (params.get("scope") or [""])[0].strip()
    return code_values[0], state_values[0], scope_value.split() if scope_value else None


def _exchange_token(code: str, pending: dict[str, Any], app: dict[str, Any]) -> dict[str, Any]:
    body = urllib.parse.urlencode(
        {
            "code": code,
            "client_id": app["client_id"],
            "client_secret": app["client_secret"],
            "redirect_uri": pending["redirect_uri"],
            "grant_type": "authorization_code",
            "code_verifier": pending["code_verifier"],
        }
    ).encode("ascii")
    request = urllib.request.Request(
        TOKEN_ENDPOINT,
        data=body,
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            result = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, ValueError) as exc:
        raise GoogleWorkspaceError("token_exchange_failed", "Google token exchange failed; start a new flow", status_code=502) from exc
    if not isinstance(result, dict):
        raise GoogleWorkspaceError("token_exchange_failed", "Google returned an invalid token response", status_code=502)
    return result


def complete(
    callback_url: str,
    *,
    profile_home: Path | None = None,
    exchange: Callable[[str, dict[str, Any], dict[str, Any]], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    code, returned_state, callback_scopes = _parse_callback(callback_url)
    _, app = _load_app()
    consumed: dict[str, Any]
    with _state_lock(profile_home):
        pending = _pending_record(profile_home)
        if pending is None:
            raise GoogleWorkspaceError("flow_missing", "Authorization flow is missing, expired, or already consumed", status_code=409)
        if not secrets.compare_digest(returned_state, pending["state"]):
            raise GoogleWorkspaceError("state_mismatch", "OAuth state does not match this profile", status_code=409)
        if int(pending["expires_at"]) <= int(time.time()):
            _safe_unlink(pending_path(profile_home))
            raise GoogleWorkspaceError("flow_expired", "Authorization flow expired; start again", status_code=409)
        consumed_path = profile_google_dir(profile_home) / f".consumed-{secrets.token_hex(8)}.json"
        os.replace(pending_path(profile_home), consumed_path)
        try:
            # Keep the profile lifecycle lock through the remote exchange and
            # local token commit.  Otherwise revoke/start can observe the
            # consumed marker as an idle profile and race a token back into
            # existence after reporting success.
            result = (exchange or _exchange_token)(code, pending, app)
            reported = granted_scopes_from_payload(result) or callback_scopes or []
            # Google identity scopes may be returned implicitly.  They are harmless
            # metadata, but Workspace scopes must match the request exactly.
            workspace_reported = [scope for scope in reported if scope not in GOOGLE_IDENTITY_SCOPES]
            missing, extra = scope_difference(workspace_reported, pending["scopes"])
            if missing or extra:
                raise GoogleWorkspaceError(
                    "scope_mismatch",
                    f"Google returned a different Workspace scope set (missing {len(missing)}, unexpected {len(extra)})",
                )
            access_token = str(result.get("access_token") or "").strip()
            refresh_token = str(result.get("refresh_token") or "").strip()
            if not access_token or not refresh_token:
                raise GoogleWorkspaceError("token_exchange_failed", "Google did not return a complete offline grant")
            expires_in = int(result.get("expires_in") or 0)
            now = int(time.time())
            payload = {
                "version": 1,
                "type": "authorized_user",
                "token": access_token,
                "refresh_token": refresh_token,
                "token_uri": TOKEN_ENDPOINT,
                "scopes": list(pending["scopes"]),
                TOKEN_SERVICES_KEY: list(pending["services"]),
                TOKEN_REQUESTED_SCOPES_KEY: list(pending["scopes"]),
                "expires_at": now + max(0, expires_in),
            }
            _atomic_private_json(token_path(profile_home), payload)
            _safe_unlink(legacy_token_path(profile_home))
            profile_google_dir(profile_home).chmod(0o700)
            return {"status": "connected", "services": list(pending["services"])}
        except (TypeError, ValueError) as exc:
            if isinstance(exc, GoogleWorkspaceError):
                raise
            raise GoogleWorkspaceError("token_exchange_failed", "Google returned an invalid token response") from exc
        finally:
            _safe_unlink(consumed_path)


def cancel(*, profile_home: Path | None = None) -> dict[str, Any]:
    with _state_lock(profile_home):
        existed = pending_path(profile_home).exists()
        _safe_unlink(pending_path(profile_home))
    return {"status": "cancelled", "had_pending_flow": existed}


def _revoke_remote(value: str) -> None:
    body = urllib.parse.urlencode({"token": value}).encode("ascii")
    request = urllib.request.Request(
        REVOCATION_ENDPOINT,
        data=body,
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with urllib.request.urlopen(request, timeout=15):
            return
    except urllib.error.HTTPError as exc:
        if exc.code == 400:  # already revoked/expired
            return
        raise


def revoke(
    *,
    profile_home: Path | None = None,
    remote_revoke: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    with _state_lock(profile_home):
        path = token_path(profile_home)
        old_path = legacy_token_path(profile_home)
        payload: dict[str, Any] = {}
        target = path if path.exists() else old_path
        target_exists = target.exists()
        remote_ok = not target_exists
        if target_exists:
            try:
                payload = _read_json(target, label="token")
            except GoogleWorkspaceError:
                payload = {}
        value = str(payload.get("refresh_token") or payload.get("token") or "").strip()
        if value:
            try:
                (remote_revoke or _revoke_remote)(value)
                remote_ok = True
            except Exception:
                remote_ok = False
        _safe_unlink(path)
        _safe_unlink(old_path)
        _safe_unlink(pending_path(profile_home))
        return {"status": "revoked", "remote_revoked": remote_ok}


def _credentials(
    profile_home: Path | None,
    *,
    required_service: str | None = None,
):
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request

    _, app = _load_app()
    with _state_lock(profile_home):
        path = _active_token_path(profile_home)
        payload = _read_json(path, label="token")
        try:
            services, scopes = validate_scope_contract(payload)
        except ValueError as exc:
            raise GoogleWorkspaceError(
                "scope_contract_invalid",
                str(exc),
                status_code=409,
            ) from exc
        if (
            required_service is not None
            and services != ("all",)
            and required_service not in services
        ):
            raise GoogleWorkspaceError(
                "service_not_selected",
                f"Google service '{required_service}' was not authorized",
                status_code=403,
            )
        expires_at = payload.get("expires_at")
        expiry = None
        if isinstance(expires_at, (int, float)) and expires_at > 0:
            expiry = datetime.fromtimestamp(expires_at, tz=timezone.utc)
        creds = Credentials(
            token=payload.get("token"),
            refresh_token=payload.get("refresh_token"),
            token_uri=TOKEN_ENDPOINT,
            client_id=app["client_id"],
            client_secret=app["client_secret"],
            scopes=scopes,
            expiry=expiry,
        )
        if creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception as exc:
                raise GoogleWorkspaceError(
                    "token_refresh_failed",
                    "Google authorization expired or was revoked; reconnect is required",
                    status_code=401,
                ) from exc
            refreshed_scopes = list(creds.granted_scopes or creds.scopes or scopes)
            # Google may add or omit identity metadata independently of the
            # Workspace grant. Compare only Workspace scopes, while keeping the
            # stored scope inventory unchanged (including recognized legacy
            # identity metadata).
            refreshed_workspace_scopes = [
                scope for scope in refreshed_scopes if scope not in GOOGLE_IDENTITY_SCOPES
            ]
            expected_workspace_scopes = [
                scope for scope in scopes if scope not in GOOGLE_IDENTITY_SCOPES
            ]
            missing, extra = scope_difference(
                refreshed_workspace_scopes,
                expected_workspace_scopes,
            )
            if missing or extra:
                raise GoogleWorkspaceError(
                    "scope_mismatch",
                    "Google returned a different scope set during refresh; reconnect is required",
                    status_code=409,
                )
            refreshed = dict(payload)
            refreshed["token"] = creds.token
            refreshed["scopes"] = scopes
            refreshed.pop("client_id", None)
            refreshed.pop("client_secret", None)
            if creds.expiry is not None:
                refreshed["expires_at"] = int(creds.expiry.timestamp())
            _atomic_private_json(path, refreshed)
        return creds


def check_service(
    service: str,
    *,
    profile_home: Path | None = None,
    probe: Callable[[str, Any], None] | None = None,
) -> dict[str, Any]:
    if service not in SERVICE_SCOPES:
        raise GoogleWorkspaceError("service_invalid", f"Unknown Google service: {service}")
    payload = _read_json(_active_token_path(profile_home), label="token")
    try:
        services, _ = validate_scope_contract(payload)
    except ValueError as exc:
        raise GoogleWorkspaceError(
            "scope_contract_invalid",
            str(exc),
            status_code=409,
        ) from exc
    if services != ("all",) and service not in services:
        raise GoogleWorkspaceError(
            "service_not_selected",
            f"Google service '{service}' was not authorized",
            status_code=403,
        )
    # Revalidate the same service inside _credentials' lifecycle lock. The
    # early check preserves cheap fail-fast behavior; the locked check is the
    # authority if revoke/reconnect races this call.
    creds = _credentials(profile_home, required_service=service)
    if probe is not None:
        probe(service, creds)
    else:
        _live_probe(service, creds)
    return {
        "service": service,
        "status": "ok",
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }


def _live_probe(service: str, creds: Any) -> None:
    from googleapiclient.discovery import build
    from googleapiclient.errors import HttpError

    expected_not_found = False
    if service == "email":
        request = build("gmail", "v1", credentials=creds).users().labels().list(userId="me")
    elif service == "calendar":
        request = build("calendar", "v3", credentials=creds).events().list(calendarId="primary", maxResults=1)
    elif service == "drive":
        request = build("drive", "v3", credentials=creds).files().list(pageSize=1, fields="files(id)")
    elif service == "contacts":
        request = build("people", "v1", credentials=creds).people().connections().list(resourceName="people/me", pageSize=1, personFields="names")
    elif service == "sheets":
        expected_not_found = True
        request = build("sheets", "v4", credentials=creds).spreadsheets().get(spreadsheetId="1KorraScopeProbe0000000000000000000000000000", fields="spreadsheetId")
    else:
        expected_not_found = True
        request = build("docs", "v1", credentials=creds).documents().get(documentId="1KorraScopeProbe0000000000000000000000000000")
    try:
        request.execute()
    except HttpError as exc:
        if not expected_not_found or getattr(exc.resp, "status", None) != 404:
            raise GoogleWorkspaceError("live_check_failed", f"Google {service} check failed", status_code=502) from exc

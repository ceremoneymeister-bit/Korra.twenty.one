"""Native Google Workspace OAuth service with explicit profile sharing.

The OAuth client belongs to one Korra installation.  User grants belong to a
single profile by default.  The installation owner may explicitly let named
profiles use one source profile's grant without copying its token.  Public
methods return status and URLs only; app secrets, authorization codes, access
tokens, and refresh tokens never leave this layer.
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
# Legacy spelling Google Console still writes into downloaded client JSON.
ACCEPTED_AUTHORIZATION_ENDPOINTS = frozenset({AUTHORIZATION_ENDPOINT, "https://accounts.google.com/o/oauth2/auth"})
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


def sharing_policy_path(root: Path | None = None) -> Path:
    return profile_google_dir(root or get_default_hermes_root()) / "shared-access.json"


def shared_all_path(root: Path | None = None) -> Path:
    """«Доступно всем агентам»: one source grant for every profile.

    A separate file on purpose. The explicit policy keeps its strict v1
    schema (older engines read it unchanged after a rollback), and an engine
    that does not know this file simply ignores it — consumers then lose the
    borrowed access instead of the whole Google connection failing closed.
    """
    return profile_google_dir(root or get_default_hermes_root()) / "shared-all.json"


def _local_active_token_path(profile_home: Path | None = None) -> Path:
    current = token_path(profile_home)
    _reject_symlink(current)
    if current.exists():
        return current
    old = legacy_token_path(profile_home)
    _reject_symlink(old)
    return old


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
    # Google Console выдаёт JSON клиента с `https://accounts.google.com/o/oauth2/auth`
    # (без `/v2/`); это тот же сервер авторизации, а сам поток всегда идёт на
    # AUTHORIZATION_ENDPOINT. Отклонять такой файл значит отклонять каждый
    # скачанный из консоли клиент (Виктория, 15.09.2026: `app_invalid`).
    if auth_uri not in ACCEPTED_AUTHORIZATION_ENDPOINTS or token_uri != TOKEN_ENDPOINT:
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
    # POSIX ownership is the whole contract here: root owns the app file and
    # only the runtime group may read it. Platforms without effective-gid
    # (Windows) cannot express it, so the check fails closed rather than
    # accepting a file whose permissions it never verified.
    getegid = getattr(os, "getegid", None)
    if getegid is None:
        return False
    return (
        stat.S_ISREG(app_stat.st_mode)
        and app_stat.st_uid == 0
        and app_stat.st_gid == getegid()
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


@contextmanager
def _sharing_lock(root: Path | None = None) -> Iterator[None]:
    directory = profile_google_dir(root or get_default_hermes_root())
    _private_dir(directory)
    path = directory / ".sharing.lock"
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
                    "sharing_busy",
                    "Another Google sharing operation is in progress",
                    status_code=409,
                ) from None
            time.sleep(0.02)
    try:
        os.write(fd, f"{os.getpid()}\n".encode("ascii"))
        os.fsync(fd)
        yield
    finally:
        os.close(fd)
        _safe_unlink(path)


@contextmanager
def _sharing_guard(profile_home: Path | None = None) -> Iterator[None]:
    if _profile_name_for_home(profile_home) is None:
        yield
        return
    with _sharing_lock():
        yield


def _profile_name_for_home(profile_home: Path | None = None) -> str | None:
    """Return a canonical installation profile id, or None for custom homes."""
    from korra_cli import profiles

    try:
        home = Path(profile_home or get_hermes_home()).expanduser().resolve(strict=False)
        root = Path(get_default_hermes_root()).expanduser().resolve(strict=False)
        if home == root:
            return "default"
        relative = home.relative_to(root / "profiles")
        if len(relative.parts) != 1:
            return None
        name = profiles.normalize_profile_name(relative.parts[0])
        profiles.validate_profile_name(name)
        return name
    except (OSError, ValueError):
        return None


def _profile_home_for_name(name: str) -> Path:
    from korra_cli import profiles

    canonical = profiles.normalize_profile_name(name)
    profiles.validate_profile_name(canonical)
    return profiles.get_profile_dir(canonical)


def _empty_sharing_policy() -> dict[str, Any]:
    return {"version": 1, "profile_sources": {}}


def _read_sharing_policy(root: Path | None = None) -> dict[str, Any]:
    path = sharing_policy_path(root)
    _reject_symlink(path)
    if not path.exists():
        return _empty_sharing_policy()
    try:
        payload = _read_json(path, label="sharing policy")
        if payload.get("version") != 1 or set(payload) != {"version", "profile_sources"}:
            raise ValueError("unsupported schema")
        raw_sources = payload.get("profile_sources")
        if not isinstance(raw_sources, dict):
            raise ValueError("profile_sources must be an object")
        sources: dict[str, str] = {}
        for consumer, source in raw_sources.items():
            if not isinstance(consumer, str) or not isinstance(source, str):
                raise ValueError("profile ids must be strings")
            consumer_home = _profile_home_for_name(consumer)
            source_home = _profile_home_for_name(source)
            canonical_consumer = _profile_name_for_home(consumer_home)
            canonical_source = _profile_name_for_home(source_home)
            if (
                canonical_consumer is None
                or canonical_source is None
                or canonical_consumer != consumer
                or canonical_source != source
                or consumer == source
            ):
                raise ValueError("profile id is not canonical")
            sources[consumer] = source
        if set(sources).intersection(sources.values()):
            raise ValueError("shared grants cannot be chained")
        return {"version": 1, "profile_sources": sources}
    except (GoogleWorkspaceError, ValueError) as exc:
        if isinstance(exc, GoogleWorkspaceError) and exc.code == "state_path_unsafe":
            raise
        raise GoogleWorkspaceError(
            "sharing_policy_invalid",
            "Google shared-access policy is unreadable or invalid",
            status_code=409,
        ) from exc


def _write_sharing_policy(payload: dict[str, Any], root: Path | None = None) -> None:
    path = sharing_policy_path(root)
    sources = payload.get("profile_sources")
    if not sources:
        _safe_unlink(path)
        return
    _atomic_private_json(path, payload)


def _read_shared_all(root: Path | None = None) -> str | None:
    """The profile whose grant every profile may use, or None."""
    path = shared_all_path(root)
    _reject_symlink(path)
    if not path.exists():
        return None
    try:
        payload = _read_json(path, label="shared-all policy")
        if payload.get("version") != 1 or set(payload) != {"version", "source_profile"}:
            raise ValueError("unsupported schema")
        source = payload.get("source_profile")
        if not isinstance(source, str):
            raise ValueError("source must be a profile id")
        if _profile_name_for_home(_profile_home_for_name(source)) != source:
            raise ValueError("profile id is not canonical")
        return source
    except (GoogleWorkspaceError, ValueError) as exc:
        if isinstance(exc, GoogleWorkspaceError) and exc.code == "state_path_unsafe":
            raise
        raise GoogleWorkspaceError(
            "sharing_policy_invalid",
            "Google shared-with-all policy is unreadable or invalid",
            status_code=409,
        ) from exc


def _write_shared_all(source: str | None, root: Path | None = None) -> None:
    path = shared_all_path(root)
    if source is None:
        _safe_unlink(path)
        return
    _atomic_private_json(path, {"version": 1, "source_profile": source})


def _shared_source_name(
    profile_home: Path | None = None,
    *,
    include_all: bool = True,
) -> str | None:
    # A local grant always wins.  This also keeps old installations isolated
    # when an operator manually leaves stale policy behind.
    if _local_active_token_path(profile_home).exists():
        return None
    name = _profile_name_for_home(profile_home)
    if name is None:
        return None
    explicit = _read_sharing_policy()["profile_sources"].get(name)
    if explicit or not include_all:
        return explicit
    # «Всем агентам» covers every profile without its own grant or an
    # explicit mapping — including profiles created later. Who may use the
    # grant in a given turn is decided separately (gateway.principal).
    everyone = _read_shared_all()
    return everyone if everyone and everyone != name else None


def _shared_via_all(profile_home: Path | None = None) -> bool:
    """The profile borrows its access only through «всем агентам»."""
    source = _shared_source_name(profile_home)
    return bool(source) and _shared_source_name(profile_home, include_all=False) is None


def _grant_profile_home(profile_home: Path | None = None) -> Path:
    home = Path(profile_home or get_hermes_home())
    source = _shared_source_name(home)
    return _profile_home_for_name(source) if source else home


def _active_token_path(profile_home: Path | None = None) -> Path:
    return _local_active_token_path(_grant_profile_home(profile_home))


def configure_sharing(
    *,
    source_profile: str,
    profiles: list[str] | None = None,
    all_profiles: bool | None = None,
) -> dict[str, Any]:
    """Replace the explicit consumers of one grant and/or share it with all.

    ``profiles=None`` leaves the explicit list untouched; ``all_profiles``
    ``True`` opens this grant to every profile without its own grant or an
    explicit mapping (also profiles created later), ``False`` closes that if
    it is this grant's, ``None`` leaves it as it is.
    """
    from korra_cli import profiles as profile_store

    source = profile_store.normalize_profile_name(source_profile)
    profile_store.validate_profile_name(source)
    if not profile_store.profile_exists(source):
        raise GoogleWorkspaceError("profile_missing", f"Profile '{source}' does not exist", status_code=404)
    if profiles is None and all_profiles is None:
        raise GoogleWorkspaceError("sharing_request_empty", "Nothing to change in Google sharing")

    if profiles is None:
        with _sharing_lock():
            explicit = sorted(
                consumer
                for consumer, mapped in _read_sharing_policy()["profile_sources"].items()
                if mapped == source
            )
            _apply_shared_all(source, all_profiles)
            return {
                "source_profile": source,
                "profiles": explicit,
                "all_profiles": _read_shared_all() == source,
            }

    consumers: list[str] = []
    for value in profiles:
        canonical = profile_store.normalize_profile_name(value)
        profile_store.validate_profile_name(canonical)
        if canonical == source:
            raise GoogleWorkspaceError("sharing_profile_invalid", "A source profile cannot share with itself")
        if canonical in consumers:
            raise GoogleWorkspaceError("sharing_profile_duplicate", f"Profile '{canonical}' is duplicated")
        if not profile_store.profile_exists(canonical):
            raise GoogleWorkspaceError("profile_missing", f"Profile '{canonical}' does not exist", status_code=404)
        consumers.append(canonical)

    source_home = profile_store.get_profile_dir(source)
    with _sharing_lock():
        policy = _read_sharing_policy()
        mappings = dict(policy["profile_sources"])
        for consumer, mapped_source in list(mappings.items()):
            if mapped_source == source:
                mappings.pop(consumer)

        for consumer in consumers:
            other_source = mappings.get(consumer)
            if other_source is not None and other_source != source:
                raise GoogleWorkspaceError(
                    "sharing_profile_conflict",
                    f"Profile '{consumer}' already uses the grant from '{other_source}'",
                    status_code=409,
                )

        if source in mappings:
            raise GoogleWorkspaceError(
                "sharing_source_conflict",
                f"Profile '{source}' already uses another profile's grant",
                status_code=409,
            )
        existing_sources = set(mappings.values())
        source_targets = sorted(set(consumers).intersection(existing_sources))
        if source_targets:
            raise GoogleWorkspaceError(
                "sharing_profile_conflict",
                "A grant source cannot also consume another shared grant: " + ", ".join(source_targets),
                status_code=409,
            )

        if consumers:
            with _state_lock(source_home):
                source_status = _token_status(source_home)
                if source_status["state"] == "not_connected" or (
                    source_status["state"] == "reauthorization_required"
                    and not source_status.get("legacy_compatible", False)
                ):
                    raise GoogleWorkspaceError(
                        "sharing_source_unusable",
                        f"Profile '{source}' has no usable Google Workspace grant",
                        status_code=409,
                    )

        for consumer in consumers:
            consumer_home = profile_store.get_profile_dir(consumer)
            with _state_lock(consumer_home):
                if _local_active_token_path(consumer_home).exists():
                    raise GoogleWorkspaceError(
                        "sharing_target_connected",
                        f"Profile '{consumer}' already has its own Google Workspace grant",
                        status_code=409,
                    )
                if _pending_record(consumer_home) is not None:
                    raise GoogleWorkspaceError(
                        "sharing_target_pending",
                        f"Profile '{consumer}' has an active Google authorization flow",
                        status_code=409,
                    )
            mappings[consumer] = source

        updated = {"version": 1, "profile_sources": dict(sorted(mappings.items()))}
        _write_sharing_policy(updated)
        _apply_shared_all(source, all_profiles)
        everyone = _read_shared_all() == source
    return {"source_profile": source, "profiles": sorted(consumers), "all_profiles": everyone}


def _apply_shared_all(source: str, all_profiles: bool | None) -> None:
    """Turn «всем агентам» on or off for ``source``. Caller holds the lock."""
    if all_profiles is None:
        return
    current = _read_shared_all()
    if not all_profiles:
        if current == source:
            _write_shared_all(None)
        return
    if current and current != source:
        raise GoogleWorkspaceError(
            "sharing_all_conflict",
            f"Google of profile '{current}' is already shared with all agents; turn that off first",
            status_code=409,
        )
    if source in _read_sharing_policy()["profile_sources"]:
        raise GoogleWorkspaceError(
            "sharing_source_conflict",
            f"Profile '{source}' already uses another profile's grant",
            status_code=409,
        )
    source_home = _profile_home_for_name(source)
    with _state_lock(source_home):
        if not _local_active_token_path(source_home).exists():
            raise GoogleWorkspaceError(
                "sharing_source_unusable",
                f"Profile '{source}' has no Google Workspace grant of its own",
                status_code=409,
            )
        state = _token_status(source_home)
        if state["state"] == "reauthorization_required" and not state.get("legacy_compatible", False):
            raise GoogleWorkspaceError(
                "sharing_source_unusable",
                f"Profile '{source}' has no usable Google Workspace grant",
                status_code=409,
            )
    _write_shared_all(source)


def remove_profile_sharing(profile: str) -> None:
    """Remove a deleted profile from both sides of the sharing policy."""
    from korra_cli import profiles as profile_store

    canonical = profile_store.normalize_profile_name(profile)
    profile_store.validate_profile_name(canonical)
    with _sharing_lock():
        policy = _read_sharing_policy()
        mappings = {
            consumer: source
            for consumer, source in policy["profile_sources"].items()
            if consumer != canonical and source != canonical
        }
        _write_sharing_policy({"version": 1, "profile_sources": mappings})
        if _read_shared_all() == canonical:
            _write_shared_all(None)


def rename_profile_sharing(old_profile: str, new_profile: str) -> None:
    """Keep explicit grants bound to the same profile across an id rename."""
    from korra_cli import profiles as profile_store

    old = profile_store.normalize_profile_name(old_profile)
    new = profile_store.normalize_profile_name(new_profile)
    profile_store.validate_profile_name(old)
    profile_store.validate_profile_name(new)
    with _sharing_lock():
        policy = _read_sharing_policy()
        if new in policy["profile_sources"] or new in policy["profile_sources"].values():
            raise GoogleWorkspaceError(
                "sharing_profile_conflict",
                f"Google shared-access policy already refers to '{new}'",
                status_code=409,
            )
        mappings = {
            (new if consumer == old else consumer): (new if source == old else source)
            for consumer, source in policy["profile_sources"].items()
        }
        _write_sharing_policy({"version": 1, "profile_sources": dict(sorted(mappings.items()))})
        if _read_shared_all() == old:
            _write_shared_all(new)


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
    source = _shared_source_name(profile_home)
    grant_home = _profile_home_for_name(source) if source else Path(profile_home or get_hermes_home())
    token = _token_status(grant_home)
    if source:
        token["shared_from"] = source
        if _shared_via_all(profile_home):
            token["shared_to_all"] = True
    profile_name = _profile_name_for_home(profile_home)
    if profile_name is not None and not source:
        shared_with = sorted(
            consumer
            for consumer, mapped_source in _read_sharing_policy()["profile_sources"].items()
            if mapped_source == profile_name
        )
        if shared_with:
            token["shared_with"] = shared_with
        if _read_shared_all() == profile_name:
            token["shared_with_all"] = True
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


def app_ready() -> bool:
    """Is the operator-managed OAuth app usable on this installation?

    Cheap and secret-free: agent tools use it as their availability gate, so
    an installation without the Ceremoneymeister app mount advertises no
    Google tool at all.
    """
    try:
        _load_app()
    except GoogleWorkspaceError:
        return False
    return True


def _pending_active(profile_home: Path) -> bool:
    """Lock-free, read-only view of an unexpired authorization flow.

    ``_pending_record`` may unlink an expired record and therefore belongs
    under the profile lock.  The installation overview only needs to know
    whether a flow is open, so it must not create lock files or directories in
    every profile it looks at.
    """
    path = pending_path(profile_home)
    _reject_symlink(path)
    if not path.exists():
        return False
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return int(payload["expires_at"]) > int(time.time())
    except (OSError, ValueError, KeyError, TypeError):
        return False


def overview() -> dict[str, Any]:
    """Installation-wide, secret-free map of Google access per profile.

    One consistent snapshot for the owner's «Подключённые сервисы» section: which profile
    holds its own grant, which one borrows it through the explicit sharing
    policy, and which has none.  Nothing here creates state: no lock files,
    no ``google-workspace`` directories in profiles that never touched Google.
    """
    from korra_cli import profiles as profile_store

    try:
        _load_app()
        app: dict[str, Any] = {"configured": True}
    except GoogleWorkspaceError as exc:
        app = {"configured": False, "reason": exc.code}

    sources = _read_sharing_policy()["profile_sources"]
    everyone = _read_shared_all()
    rows: list[dict[str, Any]] = []
    for name in profile_store.list_profile_names():
        if not profile_store.profile_exists(name):
            continue
        home = _profile_home_for_name(name)
        own = _local_active_token_path(home).exists()
        source = None if own else sources.get(name)
        via_all = False
        if not own and not source and everyone and everyone != name:
            source, via_all = everyone, True
        grant_home = _profile_home_for_name(source) if source else home
        token = _token_status(grant_home)
        state = token["state"]
        if state == "connected":
            services = [service for service in token.get("services", []) if service != "all"]
            if token.get("services") == ["all"]:
                services = list(SERVICE_SCOPES)
        elif token.get("legacy_compatible"):
            services = list(token.get("usable_services", []))
        else:
            services = []
        row: dict[str, Any] = {
            "profile": name,
            "access": "own" if own else ("shared" if source else "none"),
            "state": state,
            "services": services,
            "pending": _pending_active(home),
        }
        if source:
            row["shared_from"] = source
        if via_all:
            row["via_all"] = True
        if token.get("reason"):
            row["reason"] = token["reason"]
        if token.get("legacy_compatible"):
            row["legacy_compatible"] = True
        rows.append(row)
    # A stale policy entry for a profile that later got its own grant is not
    # effective (a local grant always wins), so it is not reported as sharing.
    borrowers: dict[str, list[str]] = {}
    for row in rows:
        if row["access"] == "shared":
            borrowers.setdefault(row["shared_from"], []).append(row["profile"])
    for row in rows:
        if row["access"] == "own":
            row["shared_with"] = sorted(borrowers.get(row["profile"], []))
    return {
        "app": app,
        "profiles": rows,
        "available_services": list(SERVICE_SCOPES),
        # The grant every profile may use («Доступно всем агентам»), or None.
        "shared_all_source": everyone,
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
    with _sharing_guard(profile_home):
        # An explicit mapping must be detached first. «Всем агентам» does not
        # block: a profile may still connect an account of its own, and its
        # own grant then wins.
        if _shared_source_name(profile_home, include_all=False):
            raise GoogleWorkspaceError(
                "shared_access_active",
                "Disconnect shared Google access before starting a separate authorization",
                status_code=409,
            )
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
    with _sharing_guard(profile_home), _state_lock(profile_home):
        if _shared_source_name(profile_home, include_all=False):
            raise GoogleWorkspaceError(
                "shared_access_active",
                "Disconnect shared Google access before completing a separate authorization",
                status_code=409,
            )
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
    with _sharing_guard(profile_home):
        source = _shared_source_name(profile_home, include_all=False)
        profile_name = _profile_name_for_home(profile_home)
        policy = _read_sharing_policy()
        if not source and _shared_via_all(profile_home):
            raise GoogleWorkspaceError(
                "shared_with_all",
                "Google is shared with all agents; turn that off in «Подключённые сервисы» "
                "to take it away from a single agent",
                status_code=409,
            )
        if source and profile_name:
            mappings = dict(policy["profile_sources"])
            mappings.pop(profile_name, None)
            _write_sharing_policy({"version": 1, "profile_sources": mappings})
            with _state_lock(profile_home):
                _safe_unlink(pending_path(profile_home))
            return {"status": "detached", "remote_revoked": False}

        shared_with = sorted(
            consumer
            for consumer, mapped_source in policy["profile_sources"].items()
            if mapped_source == profile_name
        )
        if shared_with:
            raise GoogleWorkspaceError(
                "shared_grant_in_use",
                "Disconnect shared access from these profiles first: " + ", ".join(shared_with),
                status_code=409,
            )
        if profile_name is not None and _read_shared_all() == profile_name:
            raise GoogleWorkspaceError(
                "shared_grant_in_use",
                "Turn off shared access for all agents first",
                status_code=409,
            )

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
    grant_home = _grant_profile_home(profile_home)
    with _state_lock(grant_home):
        path = _local_active_token_path(grant_home)
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
            # google-auth 2.x compares expiry with its naive-UTC utcnow().
            # Preserve the absolute UTC instant while matching that contract;
            # an aware datetime raises TypeError before any API call.
            expiry = datetime.fromtimestamp(expires_at, tz=timezone.utc).replace(tzinfo=None)
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

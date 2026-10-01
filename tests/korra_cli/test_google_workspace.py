from __future__ import annotations

import json
import os
import stat
import builtins
import asyncio
import inspect
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import types
import urllib.parse
from pathlib import Path

import pytest

from korra_cli import google_workspace as google
from korra_cli.google_workspace_scopes import (
    LEGACY_ALL_SCOPES,
    SERVICE_SCOPES,
    TOKEN_REQUESTED_SCOPES_KEY,
    TOKEN_SERVICES_KEY,
    legacy_scope_inventory,
    scopes_for_services,
)


@pytest.fixture(autouse=True)
def _non_root_functional_app_fixture(monkeypatch):
    """Use a temporary path seam; production public APIs have no root override."""
    monkeypatch.setenv("KORRA_GOOGLE_OAUTH_CLIENT_PATH", "")
    monkeypatch.setattr(google, "_is_exact_read_only_mount", lambda _path: True)
    if os.geteuid() != 0:
        monkeypatch.setattr(
            google,
            "_operator_app_file_is_safe",
            lambda _stat: True,
        )


def _app() -> dict:
    return {
        "installed": {
            "client_id": "shared-client.apps.googleusercontent.com",
            "client_secret": "operator-only-secret",
            "auth_uri": google.AUTHORIZATION_ENDPOINT,
            "token_uri": google.TOKEN_ENDPOINT,
            "redirect_uris": ["http://localhost"],
        }
    }


def _write_app(root: Path, *, gid: int | None = None) -> None:
    """Provision the operator-owned fixture without using runtime code."""
    runtime_gid = os.getegid() if gid is None else gid
    directory = google.installation_google_dir(root)
    directory.mkdir(parents=True, mode=0o750)
    directory.chmod(0o750)
    if os.geteuid() == 0:
        os.chown(directory, 0, runtime_gid)
    path = directory / "oauth_client.json"
    path.write_text(json.dumps(_app()), encoding="utf-8")
    path.chmod(0o640)
    if os.geteuid() == 0:
        os.chown(path, 0, runtime_gid)
    os.environ["KORRA_GOOGLE_OAUTH_CLIENT_PATH"] = str(path)


def _app_path(root: Path) -> Path:
    return google.installation_google_dir(root) / "oauth_client.json"


def _callback(auth_url: str, *, code: str = "one-time-code", scopes: list[str] | None = None) -> str:
    query = urllib.parse.parse_qs(urllib.parse.urlparse(auth_url).query)
    params: dict[str, str] = {"state": query["state"][0], "code": code}
    if scopes is not None:
        params["scope"] = " ".join(scopes)
    return f"http://localhost/?{urllib.parse.urlencode(params)}"


def _exchange_for(scopes: list[str]):
    def exchange(_code, _pending, _app_block):
        return {
            "access_token": "access-value",
            "refresh_token": "refresh-value",
            "expires_in": 3600,
            "scope": " ".join(scopes),
        }

    return exchange


def _write_token(profile: Path, services: tuple[str, ...]) -> None:
    scopes = scopes_for_services(services)
    directory = google.profile_google_dir(profile)
    directory.mkdir(parents=True, mode=0o700, exist_ok=True)
    from utils import atomic_json_write

    atomic_json_write(
        google.token_path(profile),
        {
            "token": "access-value",
            "refresh_token": "refresh-value",
            "scopes": scopes,
            TOKEN_SERVICES_KEY: list(services),
            TOKEN_REQUESTED_SCOPES_KEY: scopes,
        },
        mode=0o600,
    )


def _installation_profiles(monkeypatch, root: Path, *names: str) -> None:
    import korra_constants

    root.mkdir(parents=True, exist_ok=True)
    for name in names:
        (root / "profiles" / name).mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(google, "get_default_hermes_root", lambda: root)
    monkeypatch.setattr(korra_constants, "get_default_hermes_root", lambda: root)


def test_one_installation_app_and_profile_tokens_are_isolated(tmp_path):
    root = tmp_path / "install"
    first = root / "profiles" / "first"
    second = root / "profiles" / "second"
    _write_app(root)

    first_flow = google.start("drive", profile_home=first)
    second_flow = google.start("calendar", profile_home=second)
    assert first_flow["authorization_url"] != second_flow["authorization_url"]
    assert _app_path(root).exists()
    assert not (first / "google-workspace" / "oauth_client.json").exists()
    assert not (second / "google-workspace" / "oauth_client.json").exists()

    drive_scopes = scopes_for_services(("drive",))
    google.complete(
        _callback(first_flow["authorization_url"], scopes=drive_scopes),
        profile_home=first,
        exchange=_exchange_for(drive_scopes),
    )
    assert google.token_path(first).exists()
    assert stat.S_IMODE(os.stat(google.token_path(first)).st_mode) == 0o600
    assert not google.token_path(second).exists()
    assert "client_secret" not in google.token_path(first).read_text(encoding="utf-8")


def test_explicit_profiles_use_one_source_grant_without_token_copies(tmp_path, monkeypatch):
    root = tmp_path / "install"
    _installation_profiles(monkeypatch, root, "assistant", "rop", "smm")
    _write_app(root)
    source = root / "profiles" / "assistant"
    rop = root / "profiles" / "rop"
    smm = root / "profiles" / "smm"
    _write_token(source, ("drive", "sheets"))

    result = google.configure_sharing(
        source_profile="assistant",
        profiles=["smm", "rop"],
    )

    assert result == {"source_profile": "assistant", "profiles": ["rop", "smm"], "all_profiles": False}
    assert google._active_token_path(rop) == google.token_path(source)
    assert google._active_token_path(smm) == google.token_path(source)
    assert not google.token_path(rop).exists()
    assert not google.token_path(smm).exists()
    assert stat.S_IMODE(google.sharing_policy_path().stat().st_mode) == 0o600
    assert google.status(profile_home=rop)["connection"] == {
        "state": "connected",
        "services": ["drive", "sheets"],
        "expires_at": None,
        "rollback_requires_reconnect": False,
        "action": None,
        "shared_from": "assistant",
    }
    assert google.status(profile_home=source)["connection"]["shared_with"] == ["rop", "smm"]
    assert google.check_service("drive", profile_home=rop, probe=lambda *_args: None)["status"] == "ok"


def test_sharing_with_all_covers_later_profiles_and_leaves_explicit_lists_alone(tmp_path, monkeypatch):
    """Решение Дмитрия 23.09: подключение — платформы, «всем агентам» — и будущим."""
    root = tmp_path / "install"
    _installation_profiles(monkeypatch, root, "assistant", "rop", "smm", "own")
    _write_app(root)
    _write_token(root, ("calendar",))  # the main agent holds the grant
    _write_token(root / "profiles" / "assistant", ("drive",))
    _write_token(root / "profiles" / "own", ("email",))
    google.configure_sharing(source_profile="assistant", profiles=["rop"])
    explicit_before = google.sharing_policy_path().read_bytes()

    result = google.configure_sharing(source_profile="default", all_profiles=True)

    assert result == {"source_profile": "default", "profiles": [], "all_profiles": True}
    # The explicit list is neither rewritten nor overridden.
    assert google.sharing_policy_path().read_bytes() == explicit_before
    assert google._active_token_path(root / "profiles" / "rop") == google.token_path(root / "profiles" / "assistant")
    # A profile with its own grant keeps it.
    assert google._active_token_path(root / "profiles" / "own") == google.token_path(root / "profiles" / "own")
    # Everyone else — including a profile created after the switch — uses it.
    assert google._active_token_path(root / "profiles" / "smm") == google.token_path(root)
    (root / "profiles" / "newbie").mkdir()
    newbie = root / "profiles" / "newbie"
    assert google._active_token_path(newbie) == google.token_path(root)
    connection = google.status(profile_home=newbie)["connection"]
    assert connection["shared_from"] == "default" and connection["shared_to_all"] is True
    assert google.status(profile_home=root)["connection"]["shared_with_all"] is True
    rows = {row["profile"]: row for row in google.overview()["profiles"]}
    assert rows["newbie"]["via_all"] is True and rows["rop"]["shared_from"] == "assistant"
    assert google.overview()["shared_all_source"] == "default"
    assert not google.token_path(newbie).exists()  # nothing is copied

    # Turning it off takes the borrowed access away, explicit mappings stay.
    assert google.configure_sharing(source_profile="default", all_profiles=False)["all_profiles"] is False
    assert google.status(profile_home=newbie)["connection"]["state"] == "not_connected"
    assert google._active_token_path(root / "profiles" / "rop") == google.token_path(root / "profiles" / "assistant")


def test_sharing_with_all_rules_revoke_rename_delete_and_own_accounts(tmp_path, monkeypatch):
    root = tmp_path / "install"
    _installation_profiles(monkeypatch, root, "assistant", "rop", "smm")
    _write_app(root)
    source = root / "profiles" / "assistant"
    _write_token(source, ("calendar",))
    _write_token(root / "profiles" / "smm", ("drive",))

    with pytest.raises(google.GoogleWorkspaceError) as unusable:
        google.configure_sharing(source_profile="rop", all_profiles=True)
    assert unusable.value.code == "sharing_source_unusable"
    google.configure_sharing(source_profile="assistant", all_profiles=True)
    with pytest.raises(google.GoogleWorkspaceError) as conflict:
        google.configure_sharing(source_profile="smm", all_profiles=True)
    assert conflict.value.code == "sharing_all_conflict"

    # One agent cannot be detached from «всем»; the source cannot be revoked under it.
    with pytest.raises(google.GoogleWorkspaceError) as single:
        google.revoke(profile_home=root / "profiles" / "rop", remote_revoke=lambda _v: None)
    assert single.value.code == "shared_with_all"
    with pytest.raises(google.GoogleWorkspaceError) as in_use:
        google.revoke(profile_home=source, remote_revoke=lambda _v: None)
    assert in_use.value.code == "shared_grant_in_use"

    # A covered profile may still connect an account of its own, which then wins.
    flow = google.start("drive", profile_home=root / "profiles" / "rop")
    assert flow["status"] == "pending"

    google.rename_profile_sharing("assistant", "helper")
    assert json.loads(google.shared_all_path().read_text())["source_profile"] == "helper"
    google.remove_profile_sharing("helper")
    assert not google.shared_all_path().exists()


def test_invalid_shared_all_policy_fails_closed(tmp_path, monkeypatch):
    root = tmp_path / "install"
    _installation_profiles(monkeypatch, root, "assistant", "rop")
    _write_app(root)
    _write_token(root / "profiles" / "assistant", ("calendar",))
    google.profile_google_dir(root).mkdir(parents=True, exist_ok=True)
    google.shared_all_path().write_text('{"version": 1, "source_profile": "../x"}', encoding="utf-8")
    with pytest.raises(google.GoogleWorkspaceError) as invalid:
        google._active_token_path(root / "profiles" / "rop")
    assert invalid.value.code == "sharing_policy_invalid"


def test_shared_consumer_detaches_without_revoking_source(tmp_path, monkeypatch):
    root = tmp_path / "install"
    _installation_profiles(monkeypatch, root, "assistant", "rop", "smm")
    _write_app(root)
    source = root / "profiles" / "assistant"
    consumer = root / "profiles" / "rop"
    _write_token(source, ("drive",))
    google.configure_sharing(source_profile="assistant", profiles=["rop", "smm"])
    original_token = google.token_path(source).read_bytes()
    remote_values: list[str] = []

    with pytest.raises(google.GoogleWorkspaceError) as in_use:
        google.revoke(
            profile_home=source,
            remote_revoke=lambda value: remote_values.append(value),
        )
    assert in_use.value.code == "shared_grant_in_use"

    assert google.revoke(
        profile_home=consumer,
        remote_revoke=lambda value: remote_values.append(value),
    ) == {"status": "detached", "remote_revoked": False}
    assert google.token_path(source).read_bytes() == original_token
    assert google.status(profile_home=root / "profiles/smm")["connection"]["state"] == "connected"
    assert google.status(profile_home=source)["connection"]["shared_with"] == ["smm"]
    assert remote_values == []
    assert google.status(profile_home=consumer)["connection"]["state"] == "not_connected"


def test_shared_consumer_cannot_start_its_own_flow_until_detached(tmp_path, monkeypatch):
    root = tmp_path / "install"
    _installation_profiles(monkeypatch, root, "assistant", "rop")
    _write_app(root)
    _write_token(root / "profiles" / "assistant", ("drive",))
    google.configure_sharing(source_profile="assistant", profiles=["rop"])

    with pytest.raises(google.GoogleWorkspaceError) as denied:
        google.start("drive", profile_home=root / "profiles" / "rop")
    assert denied.value.code == "shared_access_active"
    assert not google.pending_path(root / "profiles" / "rop").exists()


def test_sharing_rejects_local_target_and_malformed_policy(tmp_path, monkeypatch):
    root = tmp_path / "install"
    _installation_profiles(monkeypatch, root, "assistant", "rop")
    _write_app(root)
    _write_token(root / "profiles" / "assistant", ("drive",))
    _write_token(root / "profiles" / "rop", ("sheets",))

    with pytest.raises(google.GoogleWorkspaceError) as connected:
        google.configure_sharing(source_profile="assistant", profiles=["rop"])
    assert connected.value.code == "sharing_target_connected"

    google.token_path(root / "profiles" / "rop").unlink()
    policy_path = google.sharing_policy_path()
    policy_path.write_text('{"version":1,"profile_sources":{"rop":"rop"}}', encoding="utf-8")
    with pytest.raises(google.GoogleWorkspaceError) as invalid:
        google.status(profile_home=root / "profiles" / "rop")
    assert invalid.value.code == "sharing_policy_invalid"


def test_profile_rename_and_delete_keep_sharing_policy_safe(tmp_path, monkeypatch):
    root = tmp_path / "install"
    _installation_profiles(monkeypatch, root, "assistant", "rop", "sales")
    _write_token(root / "profiles" / "assistant", ("drive",))
    google.configure_sharing(source_profile="assistant", profiles=["rop"])

    google.rename_profile_sharing("rop", "sales")
    assert google._read_sharing_policy()["profile_sources"] == {"sales": "assistant"}

    google.remove_profile_sharing("assistant")
    assert google._read_sharing_policy()["profile_sources"] == {}
    assert not google.sharing_policy_path().exists()


def test_sharing_policy_symlink_is_not_followed(tmp_path, monkeypatch):
    root = tmp_path / "install"
    _installation_profiles(monkeypatch, root, "rop")
    outside = tmp_path / "outside.json"
    outside.write_text('{"version":1,"profile_sources":{}}', encoding="utf-8")
    google.profile_google_dir(root).mkdir(mode=0o700)
    google.sharing_policy_path().symlink_to(outside)

    with pytest.raises(google.GoogleWorkspaceError) as denied:
        google.status(profile_home=root / "profiles" / "rop")
    assert denied.value.code == "state_path_unsafe"
    assert outside.read_text(encoding="utf-8") == '{"version":1,"profile_sources":{}}'


def test_default_profile_state_is_separate_from_operator_app_directory(tmp_path):
    root = tmp_path / "install"
    _write_app(root)
    app_before = _app_path(root).read_bytes()

    result = google.start("drive", profile_home=root)

    assert result["status"] == "pending"
    assert google.pending_path(root) == root / "google-workspace" / "pending-v2.json"
    assert google.pending_path(root).is_file()
    assert _app_path(root).read_bytes() == app_before
    assert stat.S_IMODE(google.installation_google_dir(root).stat().st_mode) == 0o750


def test_all_alias_resolves_to_each_service_exact_minimum_scope():
    from korra_cli.google_workspace_scopes import parse_services

    selected = parse_services("all")
    assert selected == tuple(SERVICE_SCOPES)
    assert scopes_for_services(selected) == [
        scope
        for service in SERVICE_SCOPES
        for scope in SERVICE_SCOPES[service]
    ]


def test_state_mismatch_does_not_consume_but_success_is_single_consume(tmp_path):
    root = tmp_path / "install"
    profile = root / "profiles" / "finance"
    _write_app(root)
    flow = google.start("drive,sheets", profile_home=profile)
    scopes = scopes_for_services(("drive", "sheets"))
    good = _callback(flow["authorization_url"], scopes=scopes)
    bad = good.replace("state=", "state=wrong")

    with pytest.raises(google.GoogleWorkspaceError, match="state") as mismatch:
        google.complete(bad, profile_home=profile, exchange=_exchange_for(scopes))
    assert mismatch.value.code == "state_mismatch"
    assert google.pending_path(profile).exists()

    google.complete(good, profile_home=profile, exchange=_exchange_for(scopes))
    assert not google.pending_path(profile).exists()
    with pytest.raises(google.GoogleWorkspaceError) as replay:
        google.complete(good, profile_home=profile, exchange=_exchange_for(scopes))
    assert replay.value.code == "flow_missing"


def test_revoke_waits_for_inflight_completion_and_removes_committed_token(tmp_path):
    root = tmp_path / "install"
    profile = root / "profiles" / "finance"
    _write_app(root)
    flow = google.start("drive", profile_home=profile)
    scopes = scopes_for_services(("drive",))
    exchange_entered = threading.Event()
    release_exchange = threading.Event()
    complete_result: dict = {}
    revoke_result: dict = {}
    errors: list[BaseException] = []
    remote_values: list[str] = []

    def blocking_exchange(*_args):
        exchange_entered.set()
        assert release_exchange.wait(2)
        return _exchange_for(scopes)(*_args)

    def run_complete():
        try:
            complete_result.update(
                google.complete(
                    _callback(flow["authorization_url"], scopes=scopes),
                    profile_home=profile,
                    exchange=blocking_exchange,
                )
            )
        except BaseException as exc:
            errors.append(exc)

    def run_revoke():
        try:
            revoke_result.update(
                google.revoke(
                    profile_home=profile,
                    remote_revoke=lambda value: remote_values.append(value),
                )
            )
        except BaseException as exc:
            errors.append(exc)

    complete_thread = threading.Thread(target=run_complete)
    revoke_thread = threading.Thread(target=run_revoke)
    complete_thread.start()
    assert exchange_entered.wait(2)
    revoke_thread.start()
    time.sleep(0.05)
    assert revoke_thread.is_alive()
    release_exchange.set()
    complete_thread.join(2)
    revoke_thread.join(2)

    assert not complete_thread.is_alive()
    assert not revoke_thread.is_alive()
    assert errors == []
    assert complete_result == {"status": "connected", "services": ["drive"]}
    assert revoke_result == {"status": "revoked", "remote_revoked": True}
    assert remote_values == ["refresh-value"]
    assert not google.token_path(profile).exists()


def test_start_waits_for_inflight_completion_and_cannot_replace_flow(tmp_path):
    root = tmp_path / "install"
    profile = root / "profiles" / "finance"
    _write_app(root)
    flow = google.start("drive", profile_home=profile)
    scopes = scopes_for_services(("drive",))
    exchange_entered = threading.Event()
    release_exchange = threading.Event()
    complete_errors: list[BaseException] = []
    start_errors: list[BaseException] = []

    def blocking_exchange(*_args):
        exchange_entered.set()
        assert release_exchange.wait(2)
        return _exchange_for(scopes)(*_args)

    def run_complete():
        try:
            google.complete(
                _callback(flow["authorization_url"], scopes=scopes),
                profile_home=profile,
                exchange=blocking_exchange,
            )
        except BaseException as exc:
            complete_errors.append(exc)

    def run_start():
        try:
            google.start("calendar", profile_home=profile)
        except BaseException as exc:
            start_errors.append(exc)

    complete_thread = threading.Thread(target=run_complete)
    start_thread = threading.Thread(target=run_start)
    complete_thread.start()
    assert exchange_entered.wait(2)
    start_thread.start()
    time.sleep(0.05)
    assert start_thread.is_alive()
    release_exchange.set()
    complete_thread.join(2)
    start_thread.join(2)

    assert not complete_thread.is_alive()
    assert not start_thread.is_alive()
    assert complete_errors == []
    assert start_errors == []
    assert google.token_path(profile).exists()
    pending = google._pending_record(profile)
    assert pending["operation"] == "extend"
    assert set(pending["services"]) == {"calendar", "drive"}


def test_expired_pending_flow_fails_closed(tmp_path, monkeypatch):
    root = tmp_path / "install"
    profile = root / "profiles" / "finance"
    _write_app(root)
    now = int(time.time())
    monkeypatch.setattr(google.time, "time", lambda: now)
    flow = google.start("calendar", profile_home=profile)
    monkeypatch.setattr(google.time, "time", lambda: now + google.PENDING_TTL_SECONDS + 1)
    scopes = scopes_for_services(("calendar",))
    with pytest.raises(google.GoogleWorkspaceError) as expired:
        google.complete(
            _callback(flow["authorization_url"], scopes=scopes),
            profile_home=profile,
            exchange=_exchange_for(scopes),
        )
    assert expired.value.code == "flow_missing"
    assert not google.pending_path(profile).exists()


def test_deployed_nine_scope_legacy_token_stays_bounded_and_requires_reconnect(tmp_path):
    profile = tmp_path / "profile"
    profile.mkdir()
    legacy = {"scopes": [*LEGACY_ALL_SCOPES, "openid"], "refresh_token": "never-returned"}
    google.legacy_token_path(profile).write_text(json.dumps(legacy), encoding="utf-8")

    inventory = legacy_scope_inventory(legacy)
    current = google.status(profile_home=profile)
    assert inventory["kind"] == "legacy_broad"
    assert inventory["scope_count"] == 9
    assert current["connection"] == {
        "state": "reauthorization_required",
        "reason": "legacy_broad",
        "legacy_scope_count": 9,
        "unknown_scope_count": 0,
        "usable_services": ["email", "calendar", "drive", "contacts", "sheets", "docs"],
        "legacy_compatible": True,
        "action": "extend",
    }
    assert "never-returned" not in json.dumps(current)


def test_nagrada_send_and_slides_legacy_grant_reports_only_usable_services(tmp_path):
    profile = tmp_path / "profile"
    profile.mkdir()
    legacy = {
        "scopes": [
            "https://www.googleapis.com/auth/calendar",
            "https://www.googleapis.com/auth/contacts.readonly",
            "https://www.googleapis.com/auth/documents",
            "https://www.googleapis.com/auth/drive",
            "https://www.googleapis.com/auth/gmail.send",
            "https://www.googleapis.com/auth/presentations",
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/userinfo.email",
            "openid",
        ],
        "refresh_token": "never-returned",
    }
    google.legacy_token_path(profile).write_text(json.dumps(legacy), encoding="utf-8")

    inventory = legacy_scope_inventory(legacy)
    current = google.status(profile_home=profile)
    assert inventory == {
        "kind": "legacy_untracked",
        "scope_count": 9,
        "unknown_scope_count": 0,
        "usable_services": ["calendar", "drive", "contacts", "sheets", "docs"],
        "compatible": True,
        "requires_reauthorization": True,
    }
    assert current["connection"] == {
        "state": "reauthorization_required",
        "reason": "legacy_untracked",
        "legacy_scope_count": 9,
        "unknown_scope_count": 0,
        "usable_services": ["calendar", "drive", "contacts", "sheets", "docs"],
        "legacy_compatible": True,
        "action": "extend",
    }


def test_recognized_mail_and_contacts_legacy_grant_keeps_only_actual_services(tmp_path, monkeypatch):
    root = tmp_path / "install"
    profile = tmp_path / "profile"
    profile.mkdir()
    _write_app(root)
    legacy = {
        "token": "access-value",
        "refresh_token": "refresh-value",
        "scopes": [
            "https://mail.google.com/",
            "https://www.googleapis.com/auth/contacts",
        ],
    }
    google.legacy_token_path(profile).write_text(json.dumps(legacy), encoding="utf-8")
    marker = object()
    monkeypatch.setattr(google, "_credentials", lambda *_args, **_kwargs: marker)
    observed = []

    assert google.check_service(
        "email",
        profile_home=profile,
        probe=lambda name, credentials: observed.append((name, credentials)),
    )["status"] == "ok"
    assert google.check_service(
        "contacts",
        profile_home=profile,
        probe=lambda name, credentials: observed.append((name, credentials)),
    )["status"] == "ok"
    with pytest.raises(google.GoogleWorkspaceError) as denied:
        google.check_service("drive", profile_home=profile, probe=lambda *_: None)
    assert denied.value.code == "service_not_selected"
    assert observed == [("email", marker), ("contacts", marker)]

    flow = google.start("drive", profile_home=profile)
    assert set(flow["services"]) == {"email", "contacts", "drive"}
    pending = google._pending_record(profile)
    assert pending["legacy_contract"] is True
    assert set(legacy["scopes"]).issubset(pending["scopes"])


def test_unknown_legacy_scope_is_not_usable(tmp_path, monkeypatch):
    profile = tmp_path / "profile"
    profile.mkdir()
    google.legacy_token_path(profile).write_text(
        json.dumps({"scopes": ["https://example.invalid/unknown"]}),
        encoding="utf-8",
    )
    monkeypatch.setattr(google, "_credentials", lambda *_args, **_kwargs: object())
    with pytest.raises(google.GoogleWorkspaceError, match="cannot be bounded"):
        google.check_service("drive", profile_home=profile, probe=lambda *_: None)


@pytest.mark.parametrize(
    "contents",
    ["not-json", json.dumps({"scopes": list(SERVICE_SCOPES["drive"])})],
)
def test_existing_unrevocable_token_reports_remote_not_confirmed(tmp_path, contents):
    profile = tmp_path / "profile"
    directory = google.profile_google_dir(profile)
    directory.mkdir(parents=True)
    google.token_path(profile).write_text(contents, encoding="utf-8")
    remote_calls = []

    result = google.revoke(
        profile_home=profile,
        remote_revoke=lambda value: remote_calls.append(value),
    )

    assert result == {"status": "revoked", "remote_revoked": False}
    assert remote_calls == []
    assert not google.token_path(profile).exists()


def test_revoke_without_local_token_is_idempotently_confirmed(tmp_path):
    assert google.revoke(profile_home=tmp_path / "profile") == {
        "status": "revoked",
        "remote_revoked": True,
    }


def test_refresh_ignores_identity_scope_drift_and_preserves_legacy_inventory(
    tmp_path, monkeypatch,
):
    root = tmp_path / "install"
    profile = tmp_path / "profile"
    profile.mkdir()
    _write_app(root)
    workspace_scope = SERVICE_SCOPES["email"][0]
    stored_scopes = [workspace_scope, "openid"]
    google.legacy_token_path(profile).write_text(
        json.dumps(
            {
                "token": "expired",
                "refresh_token": "refresh-value",
                "scopes": stored_scopes,
            }
        ),
        encoding="utf-8",
    )

    class FakeCredentials:
        expired = True
        refresh_token = "refresh-value"
        token = "refreshed"
        expiry = None
        granted_scopes = [workspace_scope, "email"]
        scopes = granted_scopes

        def refresh(self, _request):
            return None

    credentials_module = types.ModuleType("google.oauth2.credentials")
    credentials_module.Credentials = lambda **_kwargs: FakeCredentials()
    request_module = types.ModuleType("google.auth.transport.requests")
    request_module.Request = lambda: object()
    monkeypatch.setitem(sys.modules, "google.oauth2.credentials", credentials_module)
    monkeypatch.setitem(sys.modules, "google.auth.transport.requests", request_module)

    credentials = google._credentials(profile)

    assert credentials.token == "refreshed"
    saved = json.loads(google.legacy_token_path(profile).read_text(encoding="utf-8"))
    assert saved["scopes"] == stored_scopes


def test_credentials_pass_naive_utc_expiry_to_google_auth(tmp_path, monkeypatch):
    root = tmp_path / "install"
    profile = tmp_path / "profile"
    profile.mkdir()
    _write_app(root)
    _write_token(profile, ("drive",))
    token_payload = json.loads(google.token_path(profile).read_text(encoding="utf-8"))
    token_payload["expires_at"] = 1893456000  # 2030-01-01T00:00:00Z
    google.token_path(profile).write_text(json.dumps(token_payload), encoding="utf-8")
    google.token_path(profile).chmod(0o600)

    class GoogleAuthCompatibleCredentials:
        refresh_token = "refresh-value"

        def __init__(self, **kwargs):
            self.expiry = kwargs["expiry"]

        @property
        def expired(self):
            if self.expiry.tzinfo is not None:
                raise TypeError("google-auth compares expiry with naive UTC")
            return False

    credentials_module = types.ModuleType("google.oauth2.credentials")
    credentials_module.Credentials = GoogleAuthCompatibleCredentials
    request_module = types.ModuleType("google.auth.transport.requests")
    request_module.Request = lambda: object()
    monkeypatch.setitem(sys.modules, "google.oauth2.credentials", credentials_module)
    monkeypatch.setitem(sys.modules, "google.auth.transport.requests", request_module)

    credentials = google._credentials(profile)

    assert credentials.expiry.isoformat() == "2030-01-01T00:00:00"
    assert credentials.expired is False


def test_revoke_waits_for_inflight_refresh_and_token_stays_deleted(tmp_path, monkeypatch):
    root = tmp_path / "install"
    profile = tmp_path / "profile"
    profile.mkdir()
    _write_app(root)
    _write_token(profile, ("drive",))
    token_payload = json.loads(google.token_path(profile).read_text(encoding="utf-8"))
    token_payload["expires_at"] = 1
    google.token_path(profile).write_text(json.dumps(token_payload), encoding="utf-8")
    google.token_path(profile).chmod(0o600)
    refresh_entered = threading.Event()
    release_refresh = threading.Event()
    errors: list[BaseException] = []
    revoke_result: dict = {}
    remote_values: list[str] = []

    class FakeCredentials:
        expired = True
        refresh_token = "refresh-value"
        token = "expired"
        expiry = None
        granted_scopes = scopes_for_services(("drive",))
        scopes = granted_scopes

        def __init__(self, **_kwargs):
            pass

        def refresh(self, _request):
            refresh_entered.set()
            assert release_refresh.wait(2)
            self.token = "new-access"

    credentials_module = types.ModuleType("google.oauth2.credentials")
    credentials_module.Credentials = FakeCredentials
    request_module = types.ModuleType("google.auth.transport.requests")
    request_module.Request = lambda: object()
    monkeypatch.setitem(sys.modules, "google.oauth2.credentials", credentials_module)
    monkeypatch.setitem(sys.modules, "google.auth.transport.requests", request_module)

    def run_refresh():
        try:
            google._credentials(profile)
        except BaseException as exc:
            errors.append(exc)

    def run_revoke():
        try:
            revoke_result.update(
                google.revoke(
                    profile_home=profile,
                    remote_revoke=lambda value: remote_values.append(value),
                )
            )
        except BaseException as exc:
            errors.append(exc)

    refresh_thread = threading.Thread(target=run_refresh)
    revoke_thread = threading.Thread(target=run_revoke)
    refresh_thread.start()
    assert refresh_entered.wait(2)
    revoke_thread.start()
    time.sleep(0.05)
    assert revoke_thread.is_alive()
    release_refresh.set()
    refresh_thread.join(2)
    revoke_thread.join(2)

    assert errors == []
    assert revoke_result == {"status": "revoked", "remote_revoked": True}
    assert remote_values == ["refresh-value"]
    assert not google.token_path(profile).exists()


def test_private_state_is_atomic_and_has_strict_modes(tmp_path):
    root = tmp_path / "install"
    profile = root / "profiles" / "finance"
    _write_app(root)
    google.start("drive", profile_home=profile)
    assert stat.S_IMODE(os.stat(google.installation_google_dir(root)).st_mode) == 0o750
    assert stat.S_IMODE(os.stat(_app_path(root)).st_mode) == 0o640
    assert stat.S_IMODE(os.stat(google.profile_google_dir(profile)).st_mode) == 0o700
    assert stat.S_IMODE(os.stat(google.pending_path(profile)).st_mode) == 0o600
    assert not list(google.profile_google_dir(profile).glob(".*.tmp"))


def test_profile_google_directory_symlink_fails_closed(tmp_path):
    root = tmp_path / "install"
    profile = tmp_path / "profile"
    outside = tmp_path / "outside"
    profile.mkdir()
    outside.mkdir()
    (profile / "google-workspace").symlink_to(outside, target_is_directory=True)
    _write_app(root)

    with pytest.raises(google.GoogleWorkspaceError) as denied:
        google.start("drive", profile_home=profile)
    assert denied.value.code == "state_path_unsafe"
    assert not (outside / "pending-v2.json").exists()


def test_installation_google_directory_symlink_is_not_followed(tmp_path):
    root = tmp_path / "install"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (root / "google").symlink_to(outside, target_is_directory=True)
    os.environ["KORRA_GOOGLE_OAUTH_CLIENT_PATH"] = str(root / "google" / "oauth_client.json")

    with pytest.raises(google.GoogleWorkspaceError) as denied:
        google._load_app()
    assert denied.value.code == "state_path_unsafe"
    assert not (outside / "oauth_client.json").exists()


def test_installation_app_file_symlink_is_not_followed(tmp_path):
    root = tmp_path / "install"
    state_dir = root / "google"
    state_dir.mkdir(parents=True)
    outside = tmp_path / "outside.json"
    outside.write_text('{"sentinel": true}', encoding="utf-8")
    (state_dir / "oauth_client.json").symlink_to(outside)
    os.environ["KORRA_GOOGLE_OAUTH_CLIENT_PATH"] = str(state_dir / "oauth_client.json")

    with pytest.raises(google.GoogleWorkspaceError) as denied:
        google._load_app()
    assert denied.value.code == "state_path_unsafe"
    assert json.loads(outside.read_text(encoding="utf-8")) == {"sentinel": True}


def test_runtime_has_no_app_install_api_and_can_only_read_operator_credential():
    assert not hasattr(google, "install_app_credentials")
    if os.geteuid() != 0:
        pytest.skip("exact root/runtime-group filesystem check requires root")

    base = Path(tempfile.mkdtemp(prefix="korra-google-app-", dir="/tmp"))
    runtime_gid = 65534
    try:
        base.chmod(0o755)
        _write_app(base, gid=runtime_gid)
        script = """
import json
from pathlib import Path
from korra_cli import google_workspace as google
path = Path(__import__('sys').argv[1]) / 'google' / 'oauth_client.json'
kind, _ = google._app_block(json.loads(path.read_text(encoding='utf-8')))
print(kind)
try:
    path.write_text('{}', encoding='utf-8')
except PermissionError:
    print('write-denied')
try:
    (path.parent / 'replacement.json').write_text('{}', encoding='utf-8')
except PermissionError:
    print('replace-denied')
"""
        result = subprocess.run(
            [sys.executable, "-c", script, str(base)],
            cwd=Path(__file__).resolve().parents[2],
            text=True,
            capture_output=True,
            check=True,
            user=65534,
            group=runtime_gid,
            extra_groups=[],
        )
        assert result.stdout.splitlines() == [
            "installed",
            "write-denied",
            "replace-denied",
        ]
    finally:
        shutil.rmtree(base)


def test_client_launcher_uses_exact_read_only_google_app_mount():
    launcher = (
        Path(__file__).resolve().parents[2] / "docs" / "client-deploy" / "up.sh"
    ).read_text(encoding="utf-8")

    assert "GOOGLE_OWNER_MODE" in launcher
    assert '"0:$ENGINE_GID:640"' in launcher
    assert "dst=/run/korra-secrets/google-oauth-client.json,readonly" in launcher
    assert "KORRA_GOOGLE_OAUTH_CLIENT_PATH=/run/korra-secrets/google-oauth-client.json" in launcher
    assert 'if [ "$AGENT_SUDO" != 0 ]' in launcher
    assert 'if [ "$GOOGLE_OAUTH_KIND" != installed ]' in launcher
    assert "S256" in launcher


def test_public_runtime_api_has_no_oauth_app_root_override():
    for operation in (
        google.status,
        google.start,
        google.complete,
        google.check_service,
    ):
        assert "root" not in inspect.signature(operation).parameters


def test_runtime_default_path_requires_exact_read_only_mount(tmp_path, monkeypatch):
    if os.geteuid() != 0:
        pytest.skip("exact root-owned mount contract requires root")
    root = tmp_path / "operator"
    _write_app(root)
    path = _app_path(root)
    monkeypatch.setenv("KORRA_GOOGLE_OAUTH_CLIENT_PATH", str(path))
    monkeypatch.setattr(google, "_is_exact_read_only_mount", lambda _path: False)

    with pytest.raises(google.GoogleWorkspaceError) as denied:
        google._load_app()
    assert denied.value.code == "app_permissions"

    monkeypatch.setattr(google, "_is_exact_read_only_mount", lambda _path: True)
    assert google._load_app()[0] == "installed"


@pytest.mark.parametrize("name", ["token.json", "pending-v2.json"])
def test_profile_state_file_symlink_fails_closed_without_touching_target(tmp_path, name):
    profile = tmp_path / "profile"
    state_dir = profile / "google-workspace"
    state_dir.mkdir(parents=True)
    outside = tmp_path / "outside.json"
    outside.write_text('{"sentinel": true}', encoding="utf-8")
    (state_dir / name).symlink_to(outside)

    with pytest.raises(google.GoogleWorkspaceError) as denied:
        google.status(profile_home=profile)
    assert denied.value.code == "state_path_unsafe"
    assert json.loads(outside.read_text(encoding="utf-8")) == {"sentinel": True}


@pytest.mark.parametrize("service", tuple(SERVICE_SCOPES))
def test_each_service_has_an_exact_live_check(service, tmp_path, monkeypatch):
    root = tmp_path / "install"
    profile = root / "profile"
    _write_app(root)
    _write_token(profile, (service,))
    marker = object()
    monkeypatch.setattr(google, "_credentials", lambda *_args, **_kwargs: marker)
    observed = []

    result = google.check_service(
        service,
        profile_home=profile,
        probe=lambda name, credentials: observed.append((name, credentials)),
    )
    assert result["status"] == "ok"
    assert observed == [(service, marker)]


def test_live_check_denies_unselected_service_before_probe(tmp_path, monkeypatch):
    profile = tmp_path / "profile"
    _write_token(profile, ("drive",))
    monkeypatch.setattr(google, "_credentials", lambda *_args, **_kwargs: object())
    called = False

    def probe(_service, _credentials):
        nonlocal called
        called = True

    with pytest.raises(google.GoogleWorkspaceError) as denied:
        google.check_service("sheets", profile_home=profile, probe=probe)
    assert denied.value.code == "service_not_selected"
    assert called is False


def test_agent_tool_schema_has_no_secret_or_cross_profile_inputs(monkeypatch):
    from tools import google_workspace_auth_tool as tool
    from tools.registry import registry

    schema = registry.get_schema("google_workspace_auth")
    assert schema is not None
    properties = schema["parameters"]["properties"]
    assert set(properties) == {"action", "services"}
    assert set(properties["action"]["enum"]) == {"start", "status", "cancel"}
    assert not ({"profile", "callback_url", "code", "token", "client_secret"} & set(properties))

    monkeypatch.setattr(tool, "_owner_context_problem", lambda: "owner required")
    denied = json.loads(tool._handle({"action": "status"}))
    assert denied == {"ok": False, "error": "owner_required", "message": "owner required"}

    monkeypatch.setattr(tool, "_owner_context_problem", lambda: None)
    monkeypatch.setattr(
        google,
        "revoke",
        lambda **_kwargs: pytest.fail("the model tool must never revoke a grant"),
    )
    rejected_revoke = json.loads(tool._handle({"action": "revoke"}))
    assert rejected_revoke["ok"] is False
    assert rejected_revoke["error"] == "action_invalid"


def test_agent_tool_context_is_fail_closed_when_context_import_fails(monkeypatch):
    from tools import google_workspace_auth_tool as tool

    real_import = builtins.__import__

    def rejecting_import(name, *args, **kwargs):
        if name == "gateway.session_context":
            raise ImportError("unavailable")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", rejecting_import)
    assert "verified owner context" in tool._owner_context_problem()


@pytest.mark.parametrize(
    ("platform", "user_id", "authorized", "cron"),
    [
        ("", "", False, ""),
        ("cli", "operator", False, ""),
        ("tui", "operator", False, ""),
        ("desktop", "operator", False, ""),
        ("telegram", "", True, ""),
        ("webhook", "operator", True, ""),
        ("telegram", "foreign-sender", False, ""),
        ("telegram", "operator", True, "1"),
    ],
)
def test_agent_tool_denies_missing_local_unidentified_webhook_foreign_and_cron(
    platform, user_id, authorized, cron
):
    from gateway.session_context import clear_session_vars, set_session_vars
    from tools import google_workspace_auth_tool as tool

    tokens = set_session_vars(
        platform=platform,
        user_id=user_id,
        credential_management_authorized=authorized,
        cron_session=cron,
    )
    try:
        assert tool._owner_context_problem() is not None
    finally:
        clear_session_vars(tokens)


def test_agent_tool_accepts_server_authorized_identified_owner_context():
    from gateway.session_context import clear_session_vars, set_session_vars
    from tools import google_workspace_auth_tool as tool

    tokens = set_session_vars(
        platform="telegram",
        user_id="operator",
        credential_management_authorized=True,
        cron_session="",
    )
    try:
        assert tool._owner_context_problem() is None
    finally:
        clear_session_vars(tokens)


def test_dashboard_rejects_an_invalid_profile_selector():
    from fastapi import HTTPException
    from korra_cli.web_routers.google_workspace import _profile_home

    with pytest.raises(HTTPException) as denied:
        _profile_home("../foreign")
    assert denied.value.status_code == 400


def test_dashboard_status_translates_google_workspace_errors(monkeypatch):
    from fastapi import HTTPException
    from korra_cli.web_routers import google_workspace as routes

    def fail_status(**_kwargs):
        raise google.GoogleWorkspaceError(
            "state_path_unsafe",
            "unsafe state",
            status_code=409,
        )

    monkeypatch.setattr(routes.google, "status", fail_status)
    with pytest.raises(HTTPException) as denied:
        asyncio.run(routes.google_status())
    assert denied.value.status_code == 409
    assert denied.value.detail == {
        "code": "state_path_unsafe",
        "message": "unsafe state",
    }


def test_dashboard_bodies_cannot_smuggle_a_profile_selector():
    from pydantic import ValidationError
    from korra_cli.web_routers.google_workspace import GoogleSharingBody, GoogleStartBody

    with pytest.raises(ValidationError):
        GoogleStartBody.model_validate({"services": ["drive"], "profile": "foreign"})
    with pytest.raises(ValidationError):
        GoogleSharingBody.model_validate({"profiles": ["rop"], "source": "foreign"})


def test_dashboard_sharing_uses_selected_profile_as_source(monkeypatch, tmp_path):
    from korra_cli.web_routers import google_workspace as routes

    selected_home = tmp_path / "profiles" / "assistant"
    calls = []
    monkeypatch.setattr(routes, "_profile_home", lambda profile: selected_home)
    monkeypatch.setattr(routes.google, "_profile_name_for_home", lambda home: "assistant")
    monkeypatch.setattr(
        routes.google,
        "configure_sharing",
        lambda **kwargs: calls.append(kwargs) or {"source_profile": "assistant", "profiles": ["rop"]},
    )

    result = asyncio.run(
        routes.google_configure_sharing(routes.GoogleSharingBody(profiles=["rop"]), profile="assistant")
    )

    assert result == {"source_profile": "assistant", "profiles": ["rop"]}
    assert calls == [{"source_profile": "assistant", "profiles": ["rop"], "all_profiles": None}]


def test_dashboard_skill_enable_uses_selected_profile_without_changing_sharing(monkeypatch, tmp_path):
    from fastapi import HTTPException
    from korra_cli.web_routers import google_workspace as routes

    home = tmp_path / "profiles" / "assistant"
    calls = []
    monkeypatch.setattr(routes, "_profile_home", lambda _: home)
    monkeypatch.setattr(routes.google, "set_workspace_skill_enabled",
                        lambda enabled, **kwargs: calls.append((enabled, kwargs)) or {"ok": True})
    assert asyncio.run(routes.google_configure_sharing(
        routes.GoogleSharingBody(skill_enabled=True), profile="assistant")) == {"ok": True}
    assert calls == [(True, {"profile_home": home})]
    with pytest.raises(HTTPException) as denied:
        asyncio.run(routes.google_configure_sharing(
            routes.GoogleSharingBody(skill_enabled=True, all_profiles=True), profile="assistant"))
    assert denied.value.status_code == 400
    assert len(calls) == 1


def test_google_console_legacy_auth_uri_is_accepted(tmp_path):
    """Скачанный из Google Console клиент пишет `auth_uri` без `/v2/`.

    Живой случай 15.09.2026 (Виктория): корректно смонтированное приложение
    отвечало `app_invalid` только из-за этого написания. Поток авторизации всё
    равно идёт на AUTHORIZATION_ENDPOINT движка.
    """
    root = tmp_path / "root"
    _write_app(root)
    path = _app_path(root)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["installed"]["auth_uri"] = "https://accounts.google.com/o/oauth2/auth"
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert google.status(profile_home=root)["app"]["configured"] is True
    # Файл клиента не задаёт адрес согласия: ссылка строится движком и остаётся
    # на v2-endpoint, каким бы ни было написание в JSON.
    flow = google.start("drive", profile_home=root)
    assert flow["authorization_url"].startswith(google.AUTHORIZATION_ENDPOINT + "?")
    payload["installed"]["auth_uri"] = "https://evil.example/o/oauth2/auth"
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert google.status(profile_home=root)["app"]["configured"] is False
    assert google.status(profile_home=root)["app"]["reason"] == "app_invalid"


@pytest.mark.parametrize('failure', ['cancel', 'expired', 'partial', 'no_refresh', 'exchange', 'write'])
def test_extension_failure_preserves_existing_grant(tmp_path, monkeypatch, failure):
    root = tmp_path / 'install'
    home = root / 'profiles/assistant'
    _write_app(root)
    _write_token(home, ('email', 'calendar', 'drive', 'sheets'))
    previous = google.token_path(home).read_bytes()
    flow = google.start('tasks,docs', profile_home=home)
    assert google.token_path(home).read_bytes() == previous
    pending = google._pending_record(home)
    assert set(pending['services']) == {'email', 'calendar', 'drive', 'sheets', 'tasks', 'docs'}
    callback = _callback(flow['authorization_url'])
    if failure == 'cancel':
        google.cancel(profile_home=home)
        with pytest.raises(google.GoogleWorkspaceError):
            google.complete(callback, profile_home=home, exchange=_exchange_for(pending['scopes']))
    elif failure == 'expired':
        monkeypatch.setattr(google.time, 'time', lambda: pending['expires_at'] + 1)
        assert google.status(profile_home=home)['pending']['active'] is False
    else:
        def exchange(*args):
            result = _exchange_for(pending['scopes'])(*args)
            if failure == 'partial':
                result['scope'] = ' '.join(scopes_for_services(('drive',)))
            elif failure == 'no_refresh':
                result.pop('refresh_token')
            elif failure == 'exchange':
                raise google.GoogleWorkspaceError('token_exchange_failed', 'fake network failure')
            return result
        if failure == 'write':
            writer = google._atomic_private_json
            def fail_write(path, payload):
                if path == google.extended_token_path(home):
                    raise OSError('fake disk failure')
                writer(path, payload)
            monkeypatch.setattr(google, '_atomic_private_json', fail_write)
        with pytest.raises((google.GoogleWorkspaceError, OSError)):
            google.complete(callback, profile_home=home, exchange=exchange)
    assert google.token_path(home).read_bytes() == previous
    assert not google.extended_token_path(home).exists()
    assert google._token_status(home)['services'] == ['email', 'calendar', 'drive', 'sheets']


def test_tasks_extension_shared_consumers_and_rollback_file(tmp_path, monkeypatch):
    root = tmp_path / 'install'
    _installation_profiles(monkeypatch, root, 'assistant', 'consumer')
    source = root / 'profiles/assistant'
    consumer = root / 'profiles/consumer'
    _write_app(root)
    _write_token(source, ('drive', 'sheets'))
    before = google.token_path(source).read_bytes()
    google.configure_sharing(source_profile='assistant', profiles=['consumer'])
    flow = google.start('tasks,docs', profile_home=source)
    pending = google._pending_record(source)
    google.complete(_callback(flow['authorization_url']), profile_home=source,
                    exchange=_exchange_for(pending['scopes']))
    assert google.token_path(source).read_bytes() == before
    assert google._active_token_path(consumer) == google.extended_token_path(source)
    assert not google.extended_token_path(consumer).exists()
    assert set(google.status(profile_home=consumer)['connection']['services']) == {'drive', 'sheets', 'docs', 'tasks'}
    with pytest.raises(google.GoogleWorkspaceError) as replay:
        google.complete(_callback(flow['authorization_url']), profile_home=source,
                        exchange=_exchange_for(pending['scopes']))
    assert replay.value.code == 'flow_missing'


def test_live_probe_tasks_and_unknown_service(tmp_path, monkeypatch):
    from unittest.mock import MagicMock
    import googleapiclient.discovery
    fake = MagicMock()
    monkeypatch.setattr(googleapiclient.discovery, 'build', fake)
    google._live_probe('tasks', object())
    assert fake.call_args.args == ('tasks', 'v1')
    fake.return_value.tasklists.return_value.list.assert_called_once_with(maxResults=1)
    fake.reset_mock()
    with pytest.raises(google.GoogleWorkspaceError) as denied:
        google._live_probe('mystery', object())
    assert denied.value.code == 'service_invalid'
    fake.assert_not_called()


def test_missing_grant_and_missing_service_have_distinct_codes(tmp_path):
    home = tmp_path / 'profile'
    with pytest.raises(google.GoogleWorkspaceError) as missing:
        google.check_service('tasks', profile_home=home)
    assert missing.value.code == 'not_authenticated'
    _write_token(home, ('drive',))
    with pytest.raises(google.GoogleWorkspaceError) as scope:
        google.check_service('tasks', profile_home=home)
    assert scope.value.code == 'service_not_selected'


def test_google_grant_provisions_only_google_for_blank_profile(tmp_path, monkeypatch):
    root = tmp_path / 'install'
    _installation_profiles(monkeypatch, root, 'assistant', 'blank', 'disabled')
    _write_token(root / 'profiles/assistant', ('drive', 'sheets'))
    blank = root / 'profiles/blank'
    disabled = root / 'profiles/disabled'
    for home in (blank, disabled):
        (home / '.no-bundled-skills').write_text('keep marker')
    (disabled / 'config.yaml').write_text('skills:\n  disabled: [google-workspace]\n')
    google.configure_sharing(source_profile='assistant', all_profiles=True)
    assert (blank / 'skills/productivity/google-workspace/scripts/google_api.py').is_file()
    assert (blank / '.no-bundled-skills').read_text() == 'keep marker'
    assert not (blank / 'skills/korra-agent').exists()
    assert not (disabled / 'skills/productivity/google-workspace').exists()
    ready = google.workspace_skill_status(blank)
    assert ready['ready'] is True and ready['execution_path'].endswith('/scripts/google_api.py')
    assert google.workspace_skill_status(disabled)['reason'] == 'skill_disabled'
    assert not google.token_path(blank).exists()
    before = list(disabled.rglob('*'))
    google.workspace_skill_status(disabled)
    assert list(disabled.rglob('*')) == before


def test_workspace_status_honors_platform_disable_and_suppression(tmp_path, monkeypatch):
    home = tmp_path / 'profile'
    _write_token(home, ('drive',))
    (home / 'config.yaml').write_text('skills:\n  platform_disabled:\n    telegram: [google-workspace]\n')
    google.ensure_workspace_skill(home)
    info = google.workspace_skill_status(home)
    assert info['enabled_for_channel']['telegram'] is False
    assert info['enabled_for_channel']['api_server'] is True
    (home / 'skills/.curator_suppressed').write_text('google-workspace\n')
    assert google.workspace_skill_status(home)['reason'] == 'skill_disabled'


def test_owner_can_enable_only_google_without_changing_sharing(tmp_path, monkeypatch):
    root = tmp_path / 'install'
    _installation_profiles(monkeypatch, root, 'assistant', 'blank')
    home = root / 'profiles/blank'
    _write_token(root / 'profiles/assistant', ('drive', 'sheets'))
    (home / '.no-bundled-skills').write_text('keep')
    (home / 'config.yaml').write_text('skills:\n  disabled: [google-workspace, other]\n  platform_disabled:\n    telegram: [google-workspace, telegram-skill]\n')
    google.configure_sharing(source_profile='assistant', profiles=['blank'])
    sharing_before = google.sharing_policy_path().read_bytes()
    (home / 'skills').mkdir(exist_ok=True)
    (home / 'skills/.curator_suppressed').write_text('google-workspace\nother\n')
    result = google.set_workspace_skill_enabled(True, profile_home=home)
    assert result == {'ok': True, 'enabled': True}
    import yaml
    config = yaml.safe_load((home / 'config.yaml').read_text())
    assert config['skills']['disabled'] == ['other']
    assert config['skills']['platform_disabled']['telegram'] == ['telegram-skill']
    assert google.workspace_skill_status(home)['ready'] is True
    assert (home / '.no-bundled-skills').read_text() == 'keep'
    assert (home / 'skills/.curator_suppressed').read_text().strip() == 'other'
    assert google.sharing_policy_path().read_bytes() == sharing_before
    assert not google.token_path(home).exists()


def test_profile_created_under_shared_all_gets_google_without_other_skills(tmp_path, monkeypatch):
    from korra_cli import profiles
    root = tmp_path / 'install'
    _installation_profiles(monkeypatch, root)
    monkeypatch.setenv('HERMES_HOME', str(root))
    monkeypatch.setattr(profiles, '_maybe_register_gateway_service', lambda *_: None)
    _write_token(root, ('drive', 'sheets'))
    google.configure_sharing(source_profile='default', all_profiles=True)
    created = profiles.create_profile('new-agent', no_skills=True, no_alias=True)
    assert (created / '.no-bundled-skills').exists()
    assert (created / 'skills/productivity/google-workspace/scripts/google_api.py').is_file()
    assert google.workspace_skill_status(created)['ready'] is True
    assert not google.token_path(created).exists()
    assert len(list((created / 'skills').rglob('SKILL.md'))) == 1


def test_historical_all_does_not_acquire_tasks_scope(tmp_path):
    from korra_cli.google_workspace_scopes import validate_scope_contract
    old_services = tuple(service for service in SERVICE_SCOPES if service != 'tasks')
    old_scopes = scopes_for_services(old_services)
    payload = {'scopes': old_scopes, TOKEN_SERVICES_KEY: ['all'], TOKEN_REQUESTED_SCOPES_KEY: old_scopes}
    services, scopes = validate_scope_contract(payload)
    assert services == old_services and scopes == old_scopes
    assert 'tasks' not in services


def test_downgrade_revoke_or_reconnect_is_respected_after_upgrade(tmp_path):
    root = tmp_path / 'install'
    home = root / 'profiles/assistant'
    _write_app(root)
    _write_token(home, ('drive',))
    flow = google.start('tasks', profile_home=home)
    pending = google._pending_record(home)
    google.complete(_callback(flow['authorization_url']), profile_home=home,
                    exchange=_exchange_for(pending['scopes']))
    assert google._local_active_token_path(home) == google.extended_token_path(home)
    # Simulate .15's local revoke: it ignores the extended file.
    google.token_path(home).unlink()
    assert google._token_status(home)['state'] == 'not_connected'
    assert google.extended_token_path(home).exists()  # preserved, not silently erased
    # A .15 reconsent may connect another account. .16 must use that account.
    _write_token(home, ('sheets',))
    assert google._local_active_token_path(home) == google.token_path(home)
    assert google._token_status(home)['services'] == ['sheets']


def test_tasks_installed_cli_uses_effective_grant_with_fake_http(tmp_path, monkeypatch, capsys):
    import importlib.util
    import httplib2
    import googleapiclient.discovery
    from google_auth_httplib2 import AuthorizedHttp
    from korra_constants import set_hermes_home_override, reset_hermes_home_override
    root = tmp_path / 'install'
    home = root / 'profiles/assistant'
    _write_app(root)
    _write_token(home, ('drive',))
    flow = google.start('tasks', profile_home=home)
    pending = google._pending_record(home)
    google.complete(_callback(flow['authorization_url']), profile_home=home,
                    exchange=_exchange_for(pending['scopes']))
    monkeypatch.setenv('HERMES_HOME', str(home))
    calls = []
    class FakeHTTP:
        def request(self, uri, method='GET', body=None, headers=None, **kwargs):
            calls.append((uri, method, json.loads(body), headers.get('authorization')))
            return httplib2.Response({'status': '200', 'content-type': 'application/json'}), b'{"id":"task-1","status":"completed"}'
    real_build = googleapiclient.discovery.build
    def fake_transport(api, version, *, credentials):
        return real_build(api, version, http=AuthorizedHttp(credentials, http=FakeHTTP()), static_discovery=True)
    monkeypatch.setattr(googleapiclient.discovery, 'build', fake_transport)
    scope = set_hermes_home_override(str(home))
    try:
        script = home / 'skills/productivity/google-workspace/scripts/google_api.py'
        spec = importlib.util.spec_from_file_location('tasks_installed_cli', script)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        monkeypatch.setattr(module, '_gws_binary', lambda: None)
        monkeypatch.setattr(module.sys, 'argv', [str(script), 'tasks', 'complete', 'task-1', '--tasklist', 'list-1'])
        module.main()
    finally:
        reset_hermes_home_override(scope)
    assert len(calls) == 1
    uri, method, body, auth = calls[0]
    assert uri.endswith('/lists/list-1/tasks/task-1?alt=json')
    assert method == 'PATCH' and body == {'status': 'completed'}
    assert auth == 'Bearer access-value'
    assert json.loads(capsys.readouterr().out)['status'] == 'completed'

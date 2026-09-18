"""Codex refresh rotation stays single-owner across named profiles."""

from __future__ import annotations

import base64
import json
import os
import threading
import time
from pathlib import Path

import httpx
import pytest

from korra_cli import auth
from korra_constants import reset_hermes_home_override, set_hermes_home_override


def _pair(prefix: str) -> dict[str, str]:
    return {
        "access_token": f"{prefix}-access",
        "refresh_token": f"{prefix}-refresh",
    }


def _jwt(
    subject: str,
    exp: int,
    *,
    account_id: str | None = None,
    email: str | None = None,
) -> str:
    def encode(payload: dict) -> str:
        raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")

    payload = {"sub": subject, "exp": exp}
    if account_id:
        payload["https://api.openai.com/auth"] = {
            "chatgpt_account_id": account_id,
        }
    if email:
        payload["email"] = email
    return f"{encode({'alg': 'none'})}.{encode(payload)}.sig"


def _write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture
def profile_tree(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    root = tmp_path / ".hermes"
    profiles = [root / "profiles" / name for name in ("alpha", "beta")]
    for profile in profiles:
        profile.mkdir(parents=True)
        _write(profile / "auth.json", {"version": 1, "providers": {}})
    monkeypatch.setenv("KORRA_HOME", str(profiles[0]))
    monkeypatch.setenv("HERMES_HOME", str(profiles[0]))
    auth._global_auth_store_cache = None
    return root, profiles


class _RotatingEndpoint:
    """Rotate one refresh token once and reject a replay."""

    def __init__(self, *, fail_first: bool = False):
        self.fail_first = fail_first
        self.seen: list[str] = []
        self._guard = threading.Lock()

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def post(self, _url, *, headers=None, data=None):
        del headers
        refresh_token = data["refresh_token"]
        with self._guard:
            if self.fail_first:
                self.fail_first = False
                raise httpx.ConnectTimeout("synthetic pre-send refresh timeout")
            self.seen.append(refresh_token)
        time.sleep(0.1)
        if self.seen.count(refresh_token) > 1:
            return httpx.Response(400, json={"error": "refresh_token_reused"})
        return httpx.Response(
            200,
            json={
                "access_token": "new-access",
                "refresh_token": "new-refresh",
            },
        )


def _refresh_from(profile: Path, tokens: dict[str, str]) -> dict[str, str]:
    token = set_hermes_home_override(profile)
    try:
        return auth._refresh_codex_auth_tokens(tokens, timeout_seconds=5.0)
    finally:
        reset_hermes_home_override(token)


def test_shared_root_grant_is_submitted_once_across_profiles(
    profile_tree,
    monkeypatch,
):
    root, profiles = profile_tree
    root_auth = root / "auth.json"
    _write(
        root_auth,
        {
            "version": 1,
            "providers": {
                "openai-codex": {
                    "auth_mode": "chatgpt",
                    "tokens": _pair("old"),
                }
            },
        },
    )
    endpoint = _RotatingEndpoint()
    monkeypatch.setattr(auth.httpx, "Client", lambda **_kwargs: endpoint)
    results: dict[str, dict[str, str]] = {}
    errors: dict[str, Exception] = {}

    def worker(name: str, profile: Path) -> None:
        try:
            results[name] = _refresh_from(profile, _pair("old"))
        except Exception as exc:  # pragma: no cover - asserted below
            errors[name] = exc

    workers = [
        threading.Thread(target=worker, args=(name, profile))
        for name, profile in zip(("alpha", "beta"), profiles)
    ]
    for worker_thread in workers:
        worker_thread.start()
    for worker_thread in workers:
        worker_thread.join(timeout=10)

    assert errors == {}
    assert endpoint.seen == ["old-refresh"]
    assert results == {"alpha": _pair("new"), "beta": _pair("new")}
    assert _read(root_auth)["providers"]["openai-codex"]["tokens"] == _pair("new")
    for profile in profiles:
        assert "openai-codex" not in _read(profile / "auth.json")["providers"]


def test_profile_login_does_not_overwrite_borrowed_root_account(profile_tree):
    root, profiles = profile_tree
    root_auth = root / "auth.json"
    _write(
        root_auth,
        {
            "version": 1,
            "providers": {
                "openai-codex": {
                    "auth_mode": "chatgpt",
                    "tokens": _pair("root"),
                }
            },
        },
    )
    token = set_hermes_home_override(profiles[0])
    try:
        auth._save_codex_tokens(_pair("profile-login"))
    finally:
        reset_hermes_home_override(token)

    assert _read(root_auth)["providers"]["openai-codex"]["tokens"] == _pair("root")
    profile_state = _read(profiles[0] / "auth.json")["providers"]["openai-codex"]
    assert profile_state["tokens"] == _pair("profile-login")


def test_profile_owned_account_refresh_stays_local(profile_tree, monkeypatch):
    root, profiles = profile_tree
    root_auth = root / "auth.json"
    _write(
        root_auth,
        {
            "version": 1,
            "providers": {
                "openai-codex": {
                    "auth_mode": "chatgpt",
                    "tokens": _pair("root"),
                }
            },
        },
    )
    _write(
        profiles[0] / "auth.json",
        {
            "version": 1,
            "providers": {
                "openai-codex": {
                    "auth_mode": "chatgpt",
                    "tokens": _pair("profile-old"),
                }
            },
        },
    )
    endpoint = _RotatingEndpoint()
    monkeypatch.setattr(auth.httpx, "Client", lambda **_kwargs: endpoint)

    assert _refresh_from(profiles[0], _pair("profile-old")) == _pair("new")
    assert _read(root_auth)["providers"]["openai-codex"]["tokens"] == _pair("root")
    profile_state = _read(profiles[0] / "auth.json")["providers"]["openai-codex"]
    assert profile_state["tokens"] == _pair("new")


@pytest.mark.parametrize(
    (
        "root_subject",
        "profile_subject",
        "root_account",
        "profile_account",
        "root_email",
        "profile_email",
    ),
    [
        ("same-subject", "same-subject", None, None, None, None),
        ("", "", None, None, "same@example.test", "same@example.test"),
        (
            "root-subject",
            "profile-subject",
            "shared-account",
            "shared-account",
            None,
            None,
        ),
        (
            "root-subject",
            "profile-subject",
            "root-account",
            "profile-account",
            None,
            None,
        ),
    ],
    ids=[
        "same-subject",
        "same-email",
        "shared-account-different-subjects",
        "different-accounts",
    ],
)
def test_independent_logins_are_never_consolidated_from_identity_claims(
    profile_tree,
    monkeypatch,
    root_subject,
    profile_subject,
    root_account,
    profile_account,
    root_email,
    profile_email,
):
    root, profiles = profile_tree
    root_auth = root / "auth.json"
    root_tokens = {
        "access_token": _jwt(
            root_subject,
            100,
            account_id=root_account,
            email=root_email,
        ),
        "refresh_token": "root-independent-refresh",
    }
    profile_tokens = {
        "access_token": _jwt(
            profile_subject,
            200,
            account_id=profile_account,
            email=profile_email,
        ),
        "refresh_token": "profile-independent-refresh",
    }
    _write(
        root_auth,
        {
            "version": 1,
            "providers": {
                "openai-codex": {
                    "auth_mode": "chatgpt",
                    "tokens": root_tokens,
                }
            },
        },
    )
    _write(
        profiles[0] / "auth.json",
        {
            "version": 1,
            "providers": {
                "openai-codex": {
                    "auth_mode": "chatgpt",
                    "tokens": profile_tokens,
                }
            },
            "credential_pool": {
                "openai-codex": [
                    {
                        "id": "forked-row",
                        "auth_type": "oauth",
                        **profile_tokens,
                    }
                ]
            },
        },
    )
    endpoint = _RotatingEndpoint()
    monkeypatch.setattr(auth.httpx, "Client", lambda **_kwargs: endpoint)

    assert _refresh_from(profiles[0], profile_tokens) == _pair("new")
    assert endpoint.seen == ["profile-independent-refresh"]
    assert _read(root_auth)["providers"]["openai-codex"]["tokens"] == root_tokens
    profile_store = _read(profiles[0] / "auth.json")
    assert profile_store["providers"]["openai-codex"]["tokens"] == _pair("new")
    assert profile_store["credential_pool"]["openai-codex"][0]["id"] == "forked-row"


@pytest.mark.parametrize("endpoint_fails", [False, True], ids=["success", "endpoint-failure"])
def test_exact_copy_migration_removes_only_its_pool_alias(
    profile_tree,
    monkeypatch,
    endpoint_fails,
):
    root, profiles = profile_tree
    root_auth = root / "auth.json"
    copied_tokens = _pair("copied")
    independent_oauth = {
        "id": "independent-account-b",
        "source": "manual:device_code",
        "auth_type": "oauth",
        "access_token": "account-b-access",
        "refresh_token": "account-b-refresh",
        "label": "Account B",
        "last_status": "exhausted",
        "last_error_reason": "rate_limit",
    }
    static_credential = {
        "id": "static-api-key",
        "source": "manual:api_key",
        "auth_type": "api_key",
        "access_token": "static-secret",
        "label": "Static key",
    }
    _write(
        root_auth,
        {
            "version": 1,
            "providers": {
                "openai-codex": {
                    "auth_mode": "chatgpt",
                    "tokens": copied_tokens,
                }
            },
        },
    )
    _write(
        profiles[0] / "auth.json",
        {
            "version": 1,
            "providers": {
                "openai-codex": {
                    "auth_mode": "chatgpt",
                    "tokens": copied_tokens,
                }
            },
            "credential_pool": {
                "openai-codex": [
                    {
                        "id": "copied-alias-a",
                        "source": "device_code",
                        "auth_type": "oauth",
                        **copied_tokens,
                    },
                    independent_oauth,
                    static_credential,
                ]
            },
        },
    )
    endpoint = _RotatingEndpoint(fail_first=endpoint_fails)
    monkeypatch.setattr(auth.httpx, "Client", lambda **_kwargs: endpoint)

    if endpoint_fails:
        with pytest.raises(httpx.ConnectTimeout, match="synthetic pre-send"):
            _refresh_from(profiles[0], copied_tokens)
    else:
        assert _refresh_from(profiles[0], copied_tokens) == _pair("new")

    root_tokens = _read(root_auth)["providers"]["openai-codex"]["tokens"]
    assert root_tokens == (copied_tokens if endpoint_fails else _pair("new"))
    profile_store = _read(profiles[0] / "auth.json")
    assert "openai-codex" not in profile_store["providers"]
    assert profile_store["credential_pool"]["openai-codex"] == [
        independent_oauth,
        static_credential,
    ]


def test_timeout_releases_root_lock_and_preserves_grant(profile_tree, monkeypatch):
    root, profiles = profile_tree
    root_auth = root / "auth.json"
    _write(
        root_auth,
        {
            "version": 1,
            "providers": {
                "openai-codex": {
                    "auth_mode": "chatgpt",
                    "tokens": _pair("old"),
                }
            },
        },
    )
    endpoint = _RotatingEndpoint(fail_first=True)
    monkeypatch.setattr(auth.httpx, "Client", lambda **_kwargs: endpoint)

    with pytest.raises(httpx.ConnectTimeout, match="synthetic pre-send refresh timeout"):
        _refresh_from(profiles[0], _pair("old"))
    assert _read(root_auth)["providers"]["openai-codex"]["tokens"] == _pair("old")

    assert _refresh_from(profiles[1], _pair("old")) == _pair("new")
    assert _read(root_auth)["providers"]["openai-codex"]["tokens"] == _pair("new")


def _exhausted_pool_entry(token: str) -> dict:
    return {
        "id": token,
        "access_token": token,
        "last_status": "exhausted",
        "last_status_at": 1.0,
        "last_error_code": 429,
        "last_error_reason": "rate_limit",
        "last_error_message": "quota exhausted",
        "last_error_reset_at": 9_999_999_999.0,
    }


def test_borrowed_pool_cooldown_is_cleared_in_root(profile_tree):
    root, profiles = profile_tree
    root_auth = root / "auth.json"
    _write(
        root_auth,
        {
            "version": 1,
            "providers": {},
            "credential_pool": {
                "openai-codex": [_exhausted_pool_entry("root-token")],
            },
        },
    )

    token = set_hermes_home_override(profiles[0])
    try:
        assert auth.clear_codex_pool_quota_cooldowns("root-token") == 1
    finally:
        reset_hermes_home_override(token)

    entry = _read(root_auth)["credential_pool"]["openai-codex"][0]
    assert entry["last_status"] is None
    assert "credential_pool" not in _read(profiles[0] / "auth.json")


def test_profile_owned_pool_cooldown_does_not_touch_root(profile_tree):
    root, profiles = profile_tree
    root_auth = root / "auth.json"
    root_entry = _exhausted_pool_entry("root-token")
    profile_entry = _exhausted_pool_entry("profile-token")
    _write(
        root_auth,
        {
            "version": 1,
            "providers": {},
            "credential_pool": {"openai-codex": [root_entry]},
        },
    )
    _write(
        profiles[0] / "auth.json",
        {
            "version": 1,
            "providers": {},
            "credential_pool": {"openai-codex": [profile_entry]},
        },
    )

    token = set_hermes_home_override(profiles[0])
    try:
        assert auth.clear_codex_pool_quota_cooldowns("profile-token") == 1
    finally:
        reset_hermes_home_override(token)

    local = _read(profiles[0] / "auth.json")["credential_pool"]["openai-codex"][0]
    root_row = _read(root_auth)["credential_pool"]["openai-codex"][0]
    assert local["last_status"] is None
    assert root_row["last_status"] == "exhausted"


def test_root_write_through_invalidates_same_tick_fallback_memo(profile_tree):
    root, profiles = profile_tree
    root_auth = root / "auth.json"
    old_store = {
        "version": 1,
        "providers": {
            "openai-codex": {"tokens": _pair("old")},
        },
    }
    _write(root_auth, old_store)
    token = set_hermes_home_override(profiles[0])
    try:
        assert auth.get_provider_auth_state("openai-codex")["tokens"] == _pair("old")
        old_stat = root_auth.stat()
        new_store = {
            "version": 1,
            "providers": {
                "openai-codex": {"tokens": _pair("new")},
            },
        }
        auth._save_auth_store(new_store, target_path=root_auth)
        os.utime(root_auth, ns=(old_stat.st_atime_ns, old_stat.st_mtime_ns))
        assert auth.get_provider_auth_state("openai-codex")["tokens"] == _pair("new")
    finally:
        reset_hermes_home_override(token)

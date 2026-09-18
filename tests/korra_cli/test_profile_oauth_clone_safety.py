"""Profile cloning never forks single-use subscription grants."""

from __future__ import annotations

import json
from pathlib import Path

from korra_cli.auth import strip_cloned_single_use_oauth_grants


def _write_auth(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "providers": {
                    "openai-codex": {
                        "auth_mode": "chatgpt",
                        "tokens": {
                            "access_token": "codex-access",
                            "refresh_token": "codex-refresh",
                        },
                    },
                    "xai-oauth": {
                        "tokens": {
                            "access_token": "xai-access",
                            "refresh_token": "xai-refresh",
                        },
                    },
                    "openrouter": {"api_key": "static-key"},
                },
                "credential_pool": {
                    "openai-codex": [
                        {
                            "id": "codex-oauth",
                            "auth_type": "oauth",
                            "access_token": "codex-access",
                            "refresh_token": "codex-refresh",
                        },
                        {
                            "id": "codex-static",
                            "auth_type": "api_key",
                            "access_token": "static-codex-key",
                        },
                    ],
                    "anthropic": [
                        {
                            "id": "anthropic-oauth",
                            "auth_type": "oauth",
                            "access_token": "sk-ant-oat-token",
                            "refresh_token": "anthropic-refresh",
                        }
                    ],
                    "openrouter": [
                        {
                            "id": "openrouter-static",
                            "auth_type": "api_key",
                            "access_token": "openrouter-key",
                        }
                    ],
                },
            }
        ),
        encoding="utf-8",
    )


def test_strip_cloned_grants_keeps_static_keys(tmp_path):
    profile = tmp_path / "clone"
    profile.mkdir()
    _write_auth(profile / "auth.json")
    (profile / ".anthropic_oauth.json").write_text("{}", encoding="utf-8")

    result = strip_cloned_single_use_oauth_grants(profile)
    store = json.loads((profile / "auth.json").read_text(encoding="utf-8"))

    assert set(result["providers"]) == {"openai-codex", "xai-oauth"}
    assert set(result["pool"]) == {"openai-codex", "anthropic"}
    assert result["files"] == [".anthropic_oauth.json"]
    assert "openai-codex" not in store["providers"]
    assert "xai-oauth" not in store["providers"]
    assert store["providers"]["openrouter"] == {"api_key": "static-key"}
    assert store["credential_pool"]["openai-codex"] == [
        {
            "id": "codex-static",
            "auth_type": "api_key",
            "access_token": "static-codex-key",
        }
    ]
    assert store["credential_pool"]["openrouter"][0]["id"] == "openrouter-static"
    assert not (profile / ".anthropic_oauth.json").exists()


def test_clone_all_applies_oauth_hygiene(tmp_path, monkeypatch):
    from korra_cli.profiles import create_profile

    source = tmp_path / "source"
    source.mkdir()
    _write_auth(source / "auth.json")
    (source / ".anthropic_oauth.json").write_text("{}", encoding="utf-8")
    (source / "config.yaml").write_text("model: {}\n", encoding="utf-8")
    target = tmp_path / "target"
    monkeypatch.setenv("KORRA_HOME", str(source))
    monkeypatch.setenv("HERMES_HOME", str(source))

    created = create_profile(
        "clone-safe",
        clone_all=True,
        no_alias=True,
        _staging_dir=target,
    )

    assert created == target
    store = json.loads((target / "auth.json").read_text(encoding="utf-8"))
    assert "openai-codex" not in store["providers"]
    assert "xai-oauth" not in store["providers"]
    assert store["credential_pool"]["openai-codex"][0]["id"] == "codex-static"
    assert not (target / ".anthropic_oauth.json").exists()

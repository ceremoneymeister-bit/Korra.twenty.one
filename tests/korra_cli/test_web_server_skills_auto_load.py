"""K21-286: переключатель «В каждом чате» пишет skills.auto_load своего профиля."""

import pytest
import yaml

from tests.korra_cli.test_web_server_skills_profiles import (  # noqa: F401
    _load_cfg,
    _write_skill,
    client,
    isolated_profiles,
)


def _put(client, name, enabled, profile="worker_alpha"):
    return client.put(
        "/api/skills/auto-load", json={"name": name, "enabled": enabled, "profile": profile}
    )


def test_toggle_pins_and_unpins_in_the_target_profile_only(client, isolated_profiles):
    resp = _put(client, "worker-skill", True)
    assert resp.status_code == 200 and resp.json()["auto_load"] is True
    assert _load_cfg(isolated_profiles["worker_alpha"])["skills"]["auto_load"] == ["worker-skill"]
    assert "auto_load" not in _load_cfg(isolated_profiles["default"]).get("skills", {})

    listed = client.get("/api/skills", params={"profile": "worker_alpha"}).json()
    assert {s["name"]: s["auto_load"] for s in listed}["worker-skill"] is True

    assert _put(client, "worker-skill", False).json()["auto_load"] is False
    assert _load_cfg(isolated_profiles["worker_alpha"])["skills"]["auto_load"] == []


def test_pinning_twice_does_not_duplicate_and_unknown_skill_is_refused(client, isolated_profiles):
    _put(client, "worker-skill", True)
    _put(client, "worker-skill", True)
    assert _load_cfg(isolated_profiles["worker_alpha"])["skills"]["auto_load"] == ["worker-skill"]

    refused = _put(client, "no-such-skill", True)
    assert refused.status_code == 404
    assert _load_cfg(isolated_profiles["worker_alpha"])["skills"]["auto_load"] == ["worker-skill"]


def test_oversized_pin_is_refused_with_an_explanation(client, isolated_profiles, monkeypatch):
    from agent import skill_commands

    monkeypatch.setattr(skill_commands, "AUTO_LOAD_MAX_CHARS", 1000)
    _write_skill(isolated_profiles["worker_alpha"] / "skills", "big-skill", description="x" * 900)
    refused = _put(client, "big-skill", True)
    assert refused.status_code == 409 and "ограничен" in refused.json()["detail"]
    assert "auto_load" not in _load_cfg(isolated_profiles["worker_alpha"]).get("skills", {})


def test_unpinning_a_stale_name_cleans_the_list(client, isolated_profiles):
    home = isolated_profiles["worker_alpha"]
    (home / "config.yaml").write_text(
        yaml.safe_dump({"skills": {"auto_load": ["gone-skill", "worker-skill"]}}), encoding="utf-8"
    )
    assert _put(client, "gone-skill", False).status_code == 200
    assert _load_cfg(home)["skills"]["auto_load"] == ["worker-skill"]

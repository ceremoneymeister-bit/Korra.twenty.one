"""K21-286: skills.auto_load кладёт закреплённые навыки в системный промпт новой сессии своего профиля."""

from __future__ import annotations

from types import SimpleNamespace

import pytest


def _skill(home, name, body):
    d = home / "skills" / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(f"---\nname: {name}\ndescription: Test.\n---\n\n{body}\n")
    return d / "SKILL.md"


def _config(home, auto_load):
    home.mkdir(parents=True, exist_ok=True)
    lines = "".join(f"    - {n}\n" for n in auto_load)
    (home / "config.yaml").write_text("skills:\n  auto_load:\n" + lines if auto_load else "{}\n")


def _agent(home=None, *, session_id="auto-load", skip_context_files=False, tools=None):
    from run_agent import AIAgent

    agent = AIAgent.__new__(AIAgent)
    agent.valid_tool_names = tools or {"skills_list", "skill_view", "skill_manage"}
    agent.model = "test-model"
    agent.provider = "test"
    agent.pass_session_id = False
    agent.skip_context_files = skip_context_files
    agent._context_cwd_is_launch_artifact = True
    agent.load_soul_identity = False
    agent._memory_enabled = False
    agent._user_profile_enabled = False
    agent._memory_manager = None
    agent._memory_store = None
    agent.session_id = session_id
    agent.platform = "telegram"
    agent._tool_use_enforcement = False
    agent.ephemeral_system_prompt = None
    agent._cached_system_prompt = None
    if home is not None:
        agent._session_db = SimpleNamespace(db_path=home / "state.db")
    return agent


@pytest.fixture
def homes(tmp_path, monkeypatch):
    monkeypatch.delenv("HERMES_IGNORE_RULES", raising=False)
    ambient = tmp_path / "ambient"
    other = tmp_path / "other"
    ambient.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(ambient))
    return ambient, other


def test_pinned_skill_is_in_the_prompt_of_its_own_profile_only(homes):
    ambient, other = homes
    _skill(ambient, "weekly-report", "AMBIENT PINNED BODY")
    _config(ambient, ["weekly-report"])
    _skill(other, "other-method", "OTHER PINNED BODY")
    _config(other, ["other-method"])

    mine = _agent(ambient)._build_system_prompt()
    assert "AMBIENT PINNED BODY" in mine and "OTHER PINNED BODY" not in mine

    # Поток без HERMES_HOME-контекста: промпт берёт дом из базы сессии, а не из окружения.
    theirs = _agent(other)._build_system_prompt()
    assert "OTHER PINNED BODY" in theirs and "AMBIENT PINNED BODY" not in theirs

    _config(ambient, [])
    assert "AMBIENT PINNED BODY" not in _agent(ambient)._build_system_prompt()


def test_internal_forks_and_sessions_without_skills_get_no_pinned_skill(homes):
    ambient, _ = homes
    _skill(ambient, "weekly-report", "PINNED BODY")
    _config(ambient, ["weekly-report"])

    assert "PINNED BODY" not in _agent(ambient, skip_context_files=True)._build_system_prompt()
    assert "PINNED BODY" not in _agent(ambient, tools={"memory"})._build_system_prompt()
    assert "PINNED BODY" in _agent(ambient)._build_system_prompt()


def test_unknown_name_does_not_break_session_start(homes):
    ambient, _ = homes
    _skill(ambient, "weekly-report", "PINNED BODY")
    _config(ambient, ["no-such-skill", "weekly-report"])

    prompt = _agent(ambient)._build_system_prompt()
    assert "PINNED BODY" in prompt


def test_malformed_auto_load_is_ignored(homes):
    ambient, _ = homes
    (ambient / "config.yaml").write_text("skills:\n  auto_load: 'weekly-report'\n")
    assert _agent(ambient)._build_system_prompt()


def test_pinned_prompt_is_resolved_once_per_agent(homes):
    ambient, _ = homes
    path = _skill(ambient, "weekly-report", "ORIGINAL BODY")
    _config(ambient, ["weekly-report"])
    agent = _agent(ambient)
    assert "ORIGINAL BODY" in agent._build_system_prompt()

    path.write_text("---\nname: weekly-report\ndescription: Test.\n---\n\nMUTATED BODY\n")
    _config(ambient, [])
    agent._cached_system_prompt = None
    rebuilt = agent._build_system_prompt()
    assert "ORIGINAL BODY" in rebuilt and "MUTATED BODY" not in rebuilt


def test_total_size_is_capped_and_overflow_is_reported(homes, monkeypatch):
    from agent import skill_commands

    ambient, _ = homes
    _skill(ambient, "first", "A" * 2000)
    _skill(ambient, "second", "B" * 2000)
    _config(ambient, ["first", "second"])
    monkeypatch.setattr(skill_commands, "AUTO_LOAD_MAX_CHARS", 3000)

    prompt, loaded, skipped = skill_commands.build_auto_load_prompt(home_override=ambient)
    assert loaded == ["first"] and skipped == ["second"]
    assert "A" * 2000 in prompt and "B" * 2000 not in prompt


def test_ignore_rules_env_keeps_pinned_skills_out(homes, monkeypatch):
    ambient, _ = homes
    _skill(ambient, "weekly-report", "PINNED BODY")
    _config(ambient, ["weekly-report"])
    monkeypatch.setenv("HERMES_IGNORE_RULES", "1")
    assert "PINNED BODY" not in _agent(ambient)._build_system_prompt()


def test_default_config_has_an_empty_auto_load_list():
    from korra_cli.config import DEFAULT_CONFIG

    assert DEFAULT_CONFIG["skills"]["auto_load"] == []

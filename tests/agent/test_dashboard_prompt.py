"""Источник панели выбирает оформление, сохраняя транспорт и кэш разговора."""

import pytest

from agent.conversation_loop import _restore_or_build_system_prompt
from agent.prompt_builder import PLATFORM_HINTS
from gateway.platforms.api_server import APIServerAdapter
from gateway.session_context import clear_session_vars, set_session_vars
from korra_state import SessionDB
from run_agent import AIAgent


@pytest.fixture
def make_agent(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    (tmp_path / "config.yaml").write_text("memory:\n  enabled: false\n", encoding="utf-8")
    db = SessionDB(tmp_path / "state.db")

    def create(source="dashboard", platform="api_server", session_id="chat-test"):
        if platform == "api_server":
            tokens = APIServerAdapter._bind_api_server_session(session_source=source)
        else:
            tokens = set_session_vars(platform=platform, source=source)
        try:
            return AIAgent(
                platform=platform, session_id=session_id, session_db=db,
                provider="custom", model="test-model", api_key="test-only",
                base_url="http://127.0.0.1:9/v1", enabled_toolsets=[],
                skip_context_files=True, skip_memory=True, load_soul_identity=False,
                quiet_mode=True,
            )
        finally:
            clear_session_vars(tokens)

    yield create, db
    db.close()


def test_dashboard_source_reaches_real_system_prompt(make_agent):
    create, _ = make_agent
    agent = create()
    prompt = agent._build_system_prompt()
    assert PLATFORM_HINTS["dashboard"] in prompt
    assert PLATFORM_HINTS["api_server"] not in prompt
    assert agent.platform == "api_server"


@pytest.mark.parametrize("source,platform", [("", "api_server"), ("api_server", "api_server"), ("dashboard", "telegram")])
def test_other_surfaces_keep_their_hint(make_agent, source, platform):
    create, _ = make_agent
    prompt = create(source, platform)._build_system_prompt()
    assert PLATFORM_HINTS[platform] in prompt
    assert PLATFORM_HINTS["dashboard"] not in prompt


def test_next_request_cannot_change_existing_agent_hint(make_agent):
    create, _ = make_agent
    dashboard = create()
    api = create("", session_id="api-test")
    tokens = APIServerAdapter._bind_api_server_session(session_source="telegram")
    try:
        assert PLATFORM_HINTS["dashboard"] in dashboard._build_system_prompt()
        assert PLATFORM_HINTS["api_server"] in api._build_system_prompt()
    finally:
        clear_session_vars(tokens)


def test_dashboard_hint_supports_existing_config_override(make_agent):
    create, _ = make_agent
    agent = create()
    agent._platform_hint_overrides = {"dashboard": {"replace": "Особое оформление панели"}}
    prompt = agent._build_system_prompt()
    assert "Особое оформление панели" in prompt
    assert PLATFORM_HINTS["dashboard"] not in prompt


def test_saved_prompt_survives_new_formatting_hint(make_agent):
    create, db = make_agent
    agent = create()
    stored = "Сохранённый промпт. " + PLATFORM_HINTS["api_server"]
    db.create_session(agent.session_id, "dashboard")
    db.update_system_prompt(agent.session_id, stored)
    _restore_or_build_system_prompt(agent, None, [{"role": "user", "content": "Продолжим"}])
    assert agent._cached_system_prompt == stored
    assert db.get_session(agent.session_id)["system_prompt"] == stored

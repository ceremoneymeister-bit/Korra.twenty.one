"""K21-269: /learn и /refine в обычном веб-чате вызывают штатные обработчики.

Веб-чат панели ходит в ``/v1/chat/completions``: раньше текст ``/learn …`` и
``/refine …`` уходил модели как обычное сообщение. Теперь ``/learn`` строит тот
же промпт, что и мессенджеры, а ``/refine`` запускает ручной разбор без вызова
модели в основном ходе.
"""

import json
from unittest.mock import MagicMock, patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from agent.learn_prompt import build_learn_prompt
from gateway.config import PlatformConfig
from gateway.platforms.api_server import (
    APIServerAdapter,
    _parse_learning_slash,
    cors_middleware,
    security_headers_middleware,
)

_KEY = "sk-secret-key-for-tests-32-chars"
_AUTH = {"Authorization": f"Bearer {_KEY}"}


def _agent(response="ответ модели"):
    agent = MagicMock()
    agent.run_conversation.return_value = {"final_response": response}
    agent.session_prompt_tokens = 0
    agent.session_completion_tokens = 0
    agent.session_total_tokens = 0
    agent.valid_tool_names = {"skill_manage"}
    agent._spawn_background_review.return_value = True
    return agent


def _adapter():
    return APIServerAdapter(PlatformConfig(enabled=True, extra={"key": _KEY}))


@pytest.mark.parametrize(
    "text,expected",
    [
        ("/learn", ("learn", "")),
        ("/learn как мы сдаём отчёт", ("learn", "как мы сдаём отчёт")),
        ("  /refine  тема\nвторая строка ", ("refine", "тема\nвторая строка")),
        ("/refine", ("refine", "")),
    ],
)
def test_parse_recognises_learning_commands(text, expected):
    assert _parse_learning_slash(text) == expected


@pytest.mark.parametrize(
    "text",
    ["привет", "/learner", "/learning", "расскажи про /learn", "/goal x", "", None,
     [{"type": "text", "text": "/learn"}]],
)
def test_parse_ignores_everything_else(text):
    assert _parse_learning_slash(text) is None


@pytest.mark.asyncio
async def test_learn_sends_the_standard_prompt_and_keeps_the_typed_text():
    adapter = _adapter()
    agent = _agent()
    history = [{"role": "user", "content": "раньше"}]
    with patch.object(adapter, "_create_agent", return_value=agent):
        result, _ = await adapter._run_agent(
            user_message="/learn наш процесс сдачи отчёта",
            conversation_history=history,
            session_id="s1",
        )
    agent.run_conversation.assert_called_once()
    kwargs = agent.run_conversation.call_args.kwargs
    assert kwargs["user_message"] == build_learn_prompt("наш процесс сдачи отчёта")
    assert kwargs["persist_user_message"] == "/learn наш процесс сдачи отчёта"
    assert kwargs["conversation_history"] == history
    assert result["final_response"] == "ответ модели"


@pytest.mark.asyncio
async def test_ordinary_text_is_untouched():
    adapter = _adapter()
    agent = _agent()
    with patch.object(adapter, "_create_agent", return_value=agent):
        await adapter._run_agent(
            user_message="расскажи про /learn", conversation_history=[], session_id="s1"
        )
    kwargs = agent.run_conversation.call_args.kwargs
    assert kwargs["user_message"] == "расскажи про /learn"
    assert "persist_user_message" not in kwargs
    agent._spawn_background_review.assert_not_called()


@pytest.mark.asyncio
async def test_refine_starts_manual_review_once_without_a_model_turn():
    adapter = _adapter()
    agent = _agent()
    history = [
        {"role": "user", "content": "вопрос"},
        {"role": "assistant", "content": "ответ"},
    ]
    with patch.object(adapter, "_create_agent", return_value=agent):
        result, _ = await adapter._run_agent(
            user_message="/refine сохрани правило про отчёты",
            conversation_history=history,
            session_id="s1",
        )
    agent.run_conversation.assert_not_called()
    agent._spawn_background_review.assert_called_once_with(
        messages_snapshot=history,
        review_memory=True,
        review_skills=True,
        focus="сохрани правило про отчёты",
        manual=True,
    )
    assert "Изучаю диалог" in result["final_response"]
    assert "сохрани правило про отчёты" in result["final_response"]
    assert not result.get("failed")


@pytest.mark.asyncio
async def test_refine_without_topic_and_without_skill_tool():
    adapter = _adapter()
    agent = _agent()
    agent.valid_tool_names = {"memory"}
    with patch.object(adapter, "_create_agent", return_value=agent):
        await adapter._run_agent(
            user_message="/refine",
            conversation_history=[{"role": "user", "content": "x"}],
            session_id="s1",
        )
    kwargs = agent._spawn_background_review.call_args.kwargs
    assert kwargs["focus"] is None
    assert kwargs["review_skills"] is False
    assert kwargs["manual"] is True


@pytest.mark.asyncio
async def test_refine_on_empty_dialog_says_there_is_nothing_to_review():
    adapter = _adapter()
    agent = _agent()
    with patch.object(adapter, "_create_agent", return_value=agent):
        result, _ = await adapter._run_agent(
            user_message="/refine", conversation_history=[], session_id="s1"
        )
    agent._spawn_background_review.assert_not_called()
    agent.run_conversation.assert_not_called()
    assert "нечего разбирать" in result["final_response"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "outcome,expected",
    [(False, "уже выполняется"), (RuntimeError("boom"), "Не удалось запустить /refine")],
)
async def test_refine_reports_busy_and_failed_start(outcome, expected):
    adapter = _adapter()
    agent = _agent()
    if isinstance(outcome, Exception):
        agent._spawn_background_review.side_effect = outcome
    else:
        agent._spawn_background_review.return_value = outcome
    with patch.object(adapter, "_create_agent", return_value=agent):
        result, _ = await adapter._run_agent(
            user_message="/refine",
            conversation_history=[{"role": "user", "content": "x"}],
            session_id="s1",
        )
    assert expected in result["final_response"]


def _app(adapter):
    mws = [mw for mw in (cors_middleware, security_headers_middleware) if mw is not None]
    app = web.Application(middlewares=mws)
    app["api_server_adapter"] = adapter
    app.router.add_post("/v1/chat/completions", adapter._handle_chat_completions)
    return app


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
async def test_refine_through_chat_completions_reaches_the_browser(stream):
    adapter = _adapter()
    agent = _agent()
    with patch.object(adapter, "_create_agent", return_value=agent):
        async with TestClient(TestServer(_app(adapter))) as cli:
            resp = await cli.post(
                "/v1/chat/completions",
                headers=_AUTH,
                json={
                    "model": "korra-agent",
                    "stream": stream,
                    "messages": [
                        {"role": "user", "content": "привет"},
                        {"role": "assistant", "content": "здравствуйте"},
                        {"role": "user", "content": "/refine"},
                    ],
                },
            )
            assert resp.status == 200
            raw = await resp.text()
    if stream:
        text = "".join(
            json.loads(line[6:])["choices"][0]["delta"].get("content", "")
            for line in raw.splitlines()
            if line.startswith("data: ") and line != "data: [DONE]"
            if json.loads(line[6:]).get("choices")
        )
    else:
        text = json.loads(raw)["choices"][0]["message"]["content"]
    assert "Изучаю диалог" in text
    agent.run_conversation.assert_not_called()
    agent._spawn_background_review.assert_called_once()
    snapshot = agent._spawn_background_review.call_args.kwargs["messages_snapshot"]
    assert [m["content"] for m in snapshot] == ["привет", "здравствуйте"]


async def _send(text, agent=None, history=None):
    adapter = _adapter()
    agent = agent or _agent()
    with patch.object(adapter, "_create_agent", return_value=agent):
        result, _ = await adapter._run_agent(
            user_message=text,
            conversation_history=history or [{"role": "user", "content": "x"}],
            session_id="s1",
        )
    return agent, result


@pytest.mark.parametrize(
    "text,expected",
    [
        ("/memory", ("memory", "")),
        ("/memory approve a1", ("memory", "approve a1")),
        ("/skills pending", ("skills", "pending")),
        ("/curator status", ("curator", "status")),
        ("/context all", ("context", "all")),
        ("/ctx", ("context", "")),
    ],
)
def test_parse_recognises_registry_learning_commands(text, expected):
    assert _parse_learning_slash(text) == expected


@pytest.mark.parametrize("text", ["/memoryx", "/model", "/status", "/skillset"])
def test_parse_leaves_other_slash_commands_alone(text):
    assert _parse_learning_slash(text) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("command", ["memory", "skills"])
async def test_memory_and_skills_run_the_shared_handler_once(command):
    with patch(
        "korra_cli.write_approval_commands.handle_pending_subcommand",
        return_value="итог обработчика",
    ) as handler, patch("tools.write_approval.write_approval_enabled", return_value=True):
        agent, result = await _send(f"/{command} pending")
    handler.assert_called_once()
    assert handler.call_args.args[1] == ["pending"]
    assert result["final_response"] == "итог обработчика"
    agent.run_conversation.assert_not_called()
    agent._spawn_background_review.assert_not_called()


@pytest.mark.asyncio
async def test_memory_without_arguments_lists_pending_writes():
    agent, result = await _send("/memory")
    assert result["final_response"].strip()
    assert "Неизвестная подкоманда" not in result["final_response"]
    agent.run_conversation.assert_not_called()


@pytest.mark.asyncio
async def test_skills_search_is_explained_not_sent_to_the_model():
    with patch("tools.write_approval.write_approval_enabled", return_value=True):
        agent, result = await _send("/skills search pdf")
    assert "странице «Навыки»" in result["final_response"]
    agent.run_conversation.assert_not_called()


@pytest.mark.asyncio
async def test_curator_runs_in_a_subprocess_of_the_profile_home(tmp_path, monkeypatch):
    import subprocess

    calls = []

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        return subprocess.CompletedProcess(cmd, 0, stdout="curator: всё спокойно\n", stderr="")

    home = tmp_path / "profile-home"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    with patch("subprocess.run", side_effect=fake_run):
        agent, result = await _send("/curator status")
    assert len(calls) == 1
    cmd, kwargs = calls[0]
    assert cmd[1:] == ["-m", "korra_cli.curator", "status"]
    assert kwargs["env"]["HERMES_HOME"] == str(home)
    assert kwargs["timeout"] > 0
    assert "всё спокойно" in result["final_response"]
    agent.run_conversation.assert_not_called()


@pytest.mark.asyncio
async def test_curator_status_does_not_capture_output_of_other_threads(tmp_path, monkeypatch):
    import threading

    home = tmp_path / "profile-home"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    stop = threading.Event()

    def chatter():
        while not stop.is_set():
            print("ЧУЖАЯ-СЕССИЯ-СЕКРЕТ")
            stop.wait(0.005)

    thread = threading.Thread(target=chatter, daemon=True)
    thread.start()
    try:
        _agent_obj, result = await _send("/curator status")
    finally:
        stop.set()
        thread.join()
    assert "ЧУЖАЯ-СЕССИЯ-СЕКРЕТ" not in result["final_response"]
    assert result["final_response"].strip()


@pytest.mark.asyncio
async def test_curator_timeout_gives_a_clear_answer():
    import subprocess

    def hang(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, kwargs["timeout"])

    with patch("subprocess.run", side_effect=hang):
        _agent_obj, result = await _send("/curator status")
    assert "не успела выполниться" in result["final_response"]


@pytest.mark.asyncio
async def test_curator_run_is_explained_because_it_needs_a_model():
    with patch("subprocess.run") as run:
        agent, result = await _send("/curator run")
    run.assert_not_called()
    assert "korra curator run" in result["final_response"]
    agent.run_conversation.assert_not_called()


@pytest.mark.asyncio
async def test_context_is_explained_in_the_web_chat():
    agent, result = await _send("/context")
    assert "мессенджерах" in result["final_response"]
    agent.run_conversation.assert_not_called()


def _unresolvable_codex_provider():
    from korra_cli.auth import AuthError

    def _raise():
        try:
            raise AuthError(
                "No Codex credentials stored. Run `hermes auth` to authenticate.",
                provider="openai-codex", code="codex_auth_missing", relogin_required=True,
            )
        except AuthError as auth:
            raise RuntimeError(str(auth)) from auth

    return patch("gateway.run._resolve_runtime_agent_kwargs", side_effect=_raise)


@pytest.mark.asyncio
@pytest.mark.parametrize("text", ["/memory", "/skills", "/context", "/curator status"])
async def test_no_model_commands_work_without_a_subscription_login(text, tmp_path, monkeypatch):
    import subprocess

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    adapter = _adapter()
    fake_agent_cls = MagicMock()
    completed = subprocess.CompletedProcess([], 0, stdout="curator: всё спокойно\n", stderr="")
    with _unresolvable_codex_provider() as resolver, \
            patch("run_agent.AIAgent", fake_agent_cls), \
            patch.object(adapter, "_create_agent", wraps=adapter._create_agent) as create, \
            patch("subprocess.run", return_value=completed):
        result, usage = await adapter._run_agent(
            user_message=text,
            conversation_history=[{"role": "user", "content": "x"}],
            session_id="s1",
        )
    assert not result.get("failed")
    assert result["completed"] is True
    assert result["final_response"].strip()
    assert "Подписка ChatGPT" not in result["final_response"]
    assert "Не удалось обратиться к провайдеру" not in result["final_response"]
    create.assert_not_called()
    resolver.assert_not_called()
    fake_agent_cls.assert_not_called()
    assert usage["total_tokens"] == 0


@pytest.mark.asyncio
async def test_plain_message_still_reports_the_missing_subscription(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    adapter = _adapter()
    with _unresolvable_codex_provider():
        result, _ = await adapter._run_agent(
            user_message="привет",
            conversation_history=[],
            session_id="s1",
        )
    assert result["failed"] is True
    assert "Подписка ChatGPT / Codex не подключена" in result["final_response"]


@pytest.mark.asyncio
@pytest.mark.parametrize("text", ["/learn тема", "/refine"])
async def test_learn_and_refine_still_need_the_model(text, tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    adapter = _adapter()
    with _unresolvable_codex_provider():
        result, _ = await adapter._run_agent(
            user_message=text,
            conversation_history=[],
            session_id="s1",
        )
    assert result["failed"] is True
    assert "Подписка ChatGPT / Codex не подключена" in result["final_response"]


@pytest.mark.asyncio
async def test_memory_in_web_chat_answers_in_russian(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    _agent_obj, result = await _send("/memory")
    assert result["final_response"] == (
        "Подтверждение записей в память выключено.\n\nОжидающих записей в память нет."
    )
    _agent_obj, result = await _send("/memory approval on")
    assert result["final_response"] == "Подтверждение записей в память включено."
    _agent_obj, result = await _send("/memory pending")
    assert result["final_response"] == "Ожидающих записей в память нет."


@pytest.mark.asyncio
async def test_curator_real_subprocess_changes_only_the_selected_profile(tmp_path, monkeypatch):
    """K21-230 review: KORRA_HOME имеет приоритет, но выбран профиль веб-чата."""
    import json as _json

    from korra_constants import reset_hermes_home_override, set_hermes_home_override
    from gateway.platforms.api_server import _run_web_learning_command
    from tools import skill_usage

    root = tmp_path / "root"
    profile = root / "profiles" / "alice"
    for home in (root, profile):
        (home / "skills" / "owner-method").mkdir(parents=True)
        (home / "skills" / "owner-method" / "SKILL.md").write_text(
            "---\nname: owner-method\ndescription: method\n---\nМетод владельца.\n",
            encoding="utf-8",
        )
        (home / "skills" / ".curator_state").write_text(_json.dumps({"paused": False}))
    monkeypatch.setenv("KORRA_HOME", str(root))
    monkeypatch.setenv("HERMES_HOME", str(root))

    def pinned(home):
        token = set_hermes_home_override(home)
        try:
            return skill_usage.get_record("owner-method").get("pinned")
        finally:
            reset_hermes_home_override(token)

    for home in (root, profile):
        token = set_hermes_home_override(home)
        try:
            skill_usage.mark_agent_created("owner-method")
            assert skill_usage.set_pinned("owner-method", True)
        finally:
            reset_hermes_home_override(token)

    token = set_hermes_home_override(profile)
    try:
        unpin = _run_web_learning_command("curator", "unpin owner-method")
        pause = _run_web_learning_command("curator", "pause")
    finally:
        reset_hermes_home_override(token)

    assert "откреплён" in unpin, unpin
    assert "приостановлено" in pause, pause
    assert pinned(profile) is False
    assert pinned(root) is True
    paused = lambda home: _json.loads((home / "skills" / ".curator_state").read_text())["paused"]
    assert paused(profile) is True
    assert paused(root) is False

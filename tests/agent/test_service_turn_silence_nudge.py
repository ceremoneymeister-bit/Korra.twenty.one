"""Служебный ход (уведомление о фоновой работе) мессенджера и веб-панели может закончиться молчанием.

Случай «Оператора терминала» 19.10: итог уже сообщён, модель ответила одними рассуждениями,
обычное напоминание «дай видимый ответ» выдало владельцу второе сообщение с тем же смыслом.
"""

from types import SimpleNamespace

from agent.conversation_loop import (
    _CODEX_INCOMPLETE_NUDGE,
    _CODEX_INCOMPLETE_NUDGE_SERVICE,
    _incomplete_nudge_for,
)
from gateway.response_filters import (
    SERVICE_TURN_SILENCE_HINT,
    is_intentional_silence_response,
    should_swallow_silence,
)


def _msgs(display_kind, hint=True):
    text = "[IMPORTANT: Background process p1 completed normally]"
    user = {"role": "user", "content": f"{text}\n{SERVICE_TURN_SILENCE_HINT}" if hint else text}
    if display_kind:
        user["display_kind"] = display_kind
    return [user, {"role": "assistant", "content": "", "reasoning": "уже сообщено"}]


def test_service_turn_in_messenger_allows_silence():
    agent = SimpleNamespace(platform="telegram")
    assert _incomplete_nudge_for(agent, _msgs("internal_notification")) == _CODEX_INCOMPLETE_NUDGE_SERVICE
    assert "NO_REPLY" in _CODEX_INCOMPLETE_NUDGE_SERVICE


def test_failed_background_work_keeps_plain_nudge():
    # Уведомление о сбое идёт без подсказки — молчать нельзя, повтор обычный (В4).
    agent = SimpleNamespace(platform="telegram")
    assert _incomplete_nudge_for(agent, _msgs("internal_notification", hint=False)) == _CODEX_INCOMPLETE_NUDGE


def test_human_turn_keeps_plain_nudge():
    agent = SimpleNamespace(platform="telegram")
    assert _incomplete_nudge_for(agent, _msgs(None)) == _CODEX_INCOMPLETE_NUDGE


def test_cli_never_gets_silence_nudge():
    # CLI и TUI маркер не глушат — человек увидел бы «NO_REPLY».
    for platform in ("cli", "tui", "", None):
        agent = SimpleNamespace(platform=platform)
        assert _incomplete_nudge_for(agent, _msgs("internal_notification")) == _CODEX_INCOMPLETE_NUDGE


def test_web_panel_service_turn_allows_silence_human_turn_does_not():
    # Веб-панель глушит NO_REPLY служебного self-post (_hide_silent_service_turn).
    agent = SimpleNamespace(platform="api_server")
    assert _incomplete_nudge_for(agent, _msgs("internal_notification")) == _CODEX_INCOMPLETE_NUDGE_SERVICE
    assert _incomplete_nudge_for(agent, _msgs(None)) == _CODEX_INCOMPLETE_NUDGE


def test_web_panel_hides_only_service_turn_silence():
    from gateway.platforms.api_server import _hide_silent_service_turn

    def _result(text):
        return {"final_response": text, "messages": [{"role": "assistant", "content": text}]}

    db = SimpleNamespace(calls=[])
    db.hide_latest_service_turn_rows = lambda sid, **kw: db.calls.append(sid) or 1
    agent = SimpleNamespace(session_id="s1", _session_db=db)

    silent = _result("NO_REPLY")
    assert _hide_silent_service_turn(agent, silent, "internal_notification") is True
    assert silent["final_response"] == "" and silent["messages"][-1]["display_kind"] == "hidden"
    assert db.calls == ["s1"]

    # Ход человека и служебный ход с новостью не глушатся.
    human = _result("NO_REPLY")
    assert _hide_silent_service_turn(agent, human, None) is False
    assert human["final_response"] == "NO_REPLY"
    news = _result("Помощник нашёл ошибку")
    assert _hide_silent_service_turn(agent, news, "internal_notification") is False
    assert db.calls == ["s1"]


def test_repeat_nudge_looks_through_previous_nudge():
    agent = SimpleNamespace(platform="telegram")
    msgs = _msgs("internal_notification") + [{"role": "user", "content": _CODEX_INCOMPLETE_NUDGE_SERVICE}]
    assert _incomplete_nudge_for(agent, msgs) == _CODEX_INCOMPLETE_NUDGE_SERVICE


def test_no_reply_on_service_turn_is_swallowed_but_human_turn_gets_answer():
    ok = {"failed": False}
    assert "NO_REPLY" in SERVICE_TURN_SILENCE_HINT
    assert is_intentional_silence_response("NO_REPLY")
    assert should_swallow_silence(ok, "NO_REPLY", display_kind="internal_notification")
    # Новое сообщение человека молчанием не закрывается.
    assert not should_swallow_silence(ok, "NO_REPLY", display_kind=None)

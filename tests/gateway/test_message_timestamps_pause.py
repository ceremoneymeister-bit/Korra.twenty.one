"""K21-303: после долгой паузы модель видит актуальную дату у нового сообщения.

Метка ставится только у сообщения, пришедшего после паузы (по умолчанию 6 ч),
в Telegram-пути и в веб-чате. В базе текст остаётся чистым, метка пересобирается
из сохранённого времени, поэтому повтор запроса даёт тот же префикс.
"""
import time
from unittest.mock import MagicMock, patch

import pytest

from gateway.config import PlatformConfig
from gateway.message_timestamps import (
    format_message_timestamp,
    last_message_timestamp,
    render_user_content_with_timestamp,
    should_stamp,
    timestamp_policy,
)
from gateway.platforms.api_server import APIServerAdapter, _stamp_after_pause
from gateway.run import _build_gateway_agent_history, _timestamp_policy
from korra_time import get_timezone

DAY = 86400.0
_KEY = "sk-secret-key-for-tests-32-chars"


def _history(now, gap):
    old = now - gap
    return [
        {"role": "user", "content": "раньше", "timestamp": old - 60},
        {"role": "assistant", "content": "ответ", "timestamp": old - 50},
        {"role": "user", "content": "ещё раз", "timestamp": old},
        {"role": "assistant", "content": "ок", "timestamp": old + 5},
    ]


def test_default_policy_stamps_only_after_a_pause_and_owner_setting_wins():
    assert timestamp_policy({}) == (False, 6 * 3600)
    assert timestamp_policy({"gateway": {"message_timestamps": {"enabled": True}}})[0] is True
    assert timestamp_policy({"gateway": {"message_timestamps": {"after_pause_hours": 0}}}) == (False, 0.0)
    assert timestamp_policy({"gateway": {"message_timestamps": True}}) == (True, 0.0)
    assert _timestamp_policy({"gateway": {"message_timestamps": {"after_pause_hours": 1}}}) == (False, 3600.0)


def test_telegram_history_stamps_the_first_message_after_three_days_only():
    t0 = 1_800_000_000.0
    history = [
        {"role": "user", "content": "утро", "timestamp": t0},
        {"role": "user", "content": "через час", "timestamp": t0 + 3600},
        {"role": "user", "content": "после паузы", "timestamp": t0 + 3600 + 3 * DAY},
    ]
    rendered, _ = _build_gateway_agent_history(history, timestamp_pause_seconds=6 * 3600.0)
    assert [m["content"] for m in rendered[:2]] == ["утро", "через час"]
    assert rendered[2]["content"] == render_user_content_with_timestamp(
        "после паузы", t0 + 3600 + 3 * DAY)
    again, _ = _build_gateway_agent_history(history, timestamp_pause_seconds=6 * 3600.0)
    assert again == rendered
    assert [m["content"] for m in history] == ["утро", "через час", "после паузы"]


def test_telegram_new_message_after_three_days_gets_the_current_date():
    now = time.time()
    history = _history(now, 3 * DAY)
    policy = timestamp_policy({})
    assert should_stamp(policy, now, last_message_timestamp(history))
    assert not should_stamp(policy, now, now - 600)
    assert not should_stamp(policy, now, None)


def test_web_message_after_three_days_gets_the_date_and_replay_is_identical():
    now = time.time()
    tz = get_timezone()
    history = _history(now, 3 * DAY)
    with patch("korra_cli.config.load_config", return_value={}):
        text, run_history, at = _stamp_after_pause("что сегодня?", history)
    assert at is not None
    assert text == f"{format_message_timestamp(at, tz=tz)} что сегодня?"
    # нет паузы внутри истории: старые сообщения не меняются
    assert [m["content"] for m in run_history] == [m["content"] for m in history]

    # Сообщение сохранено чистым с временем at; на следующем запросе префикс тот же.
    stored = history + [
        {"role": "user", "content": "что сегодня?", "timestamp": at},
        {"role": "assistant", "content": "дата", "timestamp": at + 3},
    ]
    later = at + 120
    with patch("korra_cli.config.load_config", return_value={}), \
            patch("gateway.platforms.api_server.time.time", return_value=later):
        text2, replay1, _ = _stamp_after_pause("спасибо", stored)
        _, replay2, _ = _stamp_after_pause("спасибо", stored)
    assert text2 == "спасибо"  # без паузы — без метки
    assert replay1 == replay2
    assert replay1[4]["content"] == f"{format_message_timestamp(at, tz=tz)} что сегодня?"
    assert stored[4]["content"] == "что сегодня?"


def test_web_pause_inside_history_stamps_that_row_only():
    now = time.time()
    history = [
        {"role": "user", "content": "один", "timestamp": now - 5 * DAY},
        {"role": "user", "content": "два", "timestamp": now - 5 * DAY + 60},
        {"role": "user", "content": "три", "timestamp": now - 600},
    ]
    with patch("korra_cli.config.load_config", return_value={}):
        text, run_history, _ = _stamp_after_pause("четыре", history)
    assert run_history[0]["content"] == "один" and run_history[1]["content"] == "два"
    assert run_history[2]["content"].endswith(" три") and run_history[2]["content"] != "три"
    assert text == "четыре"


def test_owner_off_switch_and_enabled_true():
    now = time.time()
    history = _history(now, 3 * DAY)
    with patch("korra_cli.config.load_config",
               return_value={"gateway": {"message_timestamps": {"after_pause_hours": 0}}}):
        text, run_history, _ = _stamp_after_pause("привет", history)
    assert text == "привет" and run_history == history
    with patch("korra_cli.config.load_config",
               return_value={"gateway": {"message_timestamps": {"enabled": True}}}):
        text, run_history, _ = _stamp_after_pause("привет", history)
    assert text.startswith("[") and run_history[0]["content"].startswith("[")


@pytest.mark.asyncio
async def test_web_run_passes_stamped_text_but_persists_the_clean_one():
    now = time.time()
    history = _history(now, 3 * DAY)
    agent = MagicMock()
    agent.run_conversation.return_value = {"final_response": "ок"}
    agent.session_prompt_tokens = agent.session_completion_tokens = agent.session_total_tokens = 0
    adapter = APIServerAdapter(PlatformConfig(enabled=True, extra={"key": _KEY}))
    with patch.object(adapter, "_create_agent", return_value=agent), \
            patch("korra_cli.config.load_config", return_value={}):
        await adapter._run_agent(user_message="какая дата?", conversation_history=history, session_id="s1")
    kwargs = agent.run_conversation.call_args.kwargs
    assert kwargs["user_message"].startswith("[") and kwargs["user_message"].endswith("какая дата?")
    assert kwargs["persist_user_message"] == "какая дата?"
    assert isinstance(kwargs["persist_user_timestamp"], float)

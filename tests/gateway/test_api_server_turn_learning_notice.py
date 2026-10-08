"""Ревью № 3 (K21-294): «Учёл» и после записи, которую агент сделал сам в ходе ответа."""

import json
import threading
import types
from unittest.mock import patch

import pytest

from agent.learning_receipt import (
    DISPLAY_KIND,
    RECEIPT_KEY,
    build_review_receipt,
    build_turn_receipt,
    snapshot_memory,
    undo_receipt,
)
from gateway.config import PlatformConfig
from gateway.platforms.api_server import APIServerAdapter
from korra_state import SessionDB

SKILL = "---\nname: weekly-report\ndescription: weekly report\n---\n\n# Weekly\n\nBody.\n"
RULE = "Планы встреч начинать со строки «Цель: …»"


@pytest.fixture
def env(tmp_path, monkeypatch):
    from agent import skill_utils
    from tools import memory_tool, skill_ledger, skill_manager_tool, skill_usage

    home = tmp_path / "home"
    skills_dir = home / "skills"
    skills_dir.mkdir(parents=True)
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(skill_ledger, "get_hermes_home", lambda: home)
    monkeypatch.setattr(skill_usage, "get_hermes_home", lambda: home)
    monkeypatch.setattr(memory_tool, "get_hermes_home", lambda: home)
    monkeypatch.setattr(skill_manager_tool, "SKILLS_DIR", skills_dir)
    monkeypatch.setattr(skill_utils, "get_all_skills_dirs", lambda: [skills_dir])
    db = SessionDB(db_path=home / "state.db")
    db.create_session("web-1", "api_server")
    yield {"home": home, "db": db}
    db.close()


def _call(call_id, name, args, raw):
    return [
        {"role": "assistant", "tool_calls": [{"id": call_id, "function": {
            "name": name, "arguments": json.dumps(args)}}]},
        {"role": "tool", "tool_call_id": call_id, "content": raw},
    ]


def _memory_add(call_id="m1", text=RULE, target="user"):
    from tools.memory_tool import load_on_disk_store

    raw = json.dumps(load_on_disk_store().add(target, text))
    return _call(call_id, "memory", {"action": "add", "target": target, "content": text}, raw)


def _memory_read(call_id="m0"):
    return _call(call_id, "memory", {"action": "read"}, json.dumps({"success": True, "entries": []}))


def _memory_failed(call_id="m2"):
    raw = json.dumps({"success": False, "error": "Memory at limit"})
    return _call(call_id, "memory", {"action": "add", "target": "user", "content": "x"}, raw)


def _skill_create(call_id="s1"):
    from tools.skill_manager_tool import skill_manage

    raw = skill_manage(action="create", name="weekly-report", content=SKILL)
    return _call(call_id, "skill_manage", {"action": "create", "name": "weekly-report"}, raw)


async def _turn(env, work, notifications="on", after_turn=None):
    adapter = APIServerAdapter(PlatformConfig(enabled=True, extra={"key": "k" * 32}))
    holder = {}

    def run_conversation(**kwargs):
        new = work()
        return {
            "final_response": "Готово",
            "messages": [{"role": "user", "content": "Не так. Всегда начинай с «Цель»"}] + new,
        }

    agent = types.SimpleNamespace(
        session_id="web-1",
        _session_db=env["db"],
        run_conversation=run_conversation,
        session_prompt_tokens=0,
        session_completion_tokens=0,
        session_total_tokens=0,
    )
    adapter._wire_learning_notice(agent, {"display": {"memory_notifications": notifications}})
    holder["agent"] = agent
    with patch.object(adapter, "_create_agent", return_value=agent):
        result, _ = await adapter._run_agent(
            user_message="Не так. Всегда начинай с «Цель»",
            conversation_history=[{"role": "user", "content": "составь план"}],
            session_id="web-1",
        )
    if after_turn:
        after_turn(agent)
    return agent, result


def _notices(db):
    return [m for m in db.get_messages("web-1") if m.get("display_kind") == DISPLAY_KIND]


@pytest.mark.asyncio
async def test_memory_written_by_the_agent_gives_one_notice_with_working_undo(env):
    from tools.memory_tool import MemoryStore

    _agent, result = await _turn(env, _memory_add)

    assert result["final_response"] == "Готово"
    notices = _notices(env["db"])
    assert len(notices) == 1
    assert notices[0]["role"] == "assistant"
    assert "Учёл" in notices[0]["content"] and "заметках о вас" in notices[0]["content"]
    receipt = notices[0]["display_metadata"][RECEIPT_KEY]
    assert receipt["memory"][0]["added"] == [RULE]

    path = MemoryStore._path_for("user")
    assert RULE in path.read_text(encoding="utf-8")
    assert undo_receipt(receipt)["status"] == "undone"
    assert RULE not in path.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_skill_written_by_the_agent_gives_a_notice_with_undo(env):
    await _turn(env, _skill_create)
    notices = _notices(env["db"])
    assert len(notices) == 1 and "«weekly-report»" in notices[0]["content"]
    receipt = notices[0]["display_metadata"][RECEIPT_KEY]
    assert receipt["skills"][0]["entry_ids"]
    assert undo_receipt(receipt)["status"] == "undone"
    assert not list((env["home"] / "skills").rglob("SKILL.md"))


@pytest.mark.asyncio
async def test_several_writes_in_one_turn_give_one_message(env):
    await _turn(env, lambda: _memory_add("m1", "Первая запись") + _memory_add("m2", "Вторая запись")
                + _skill_create())
    notices = _notices(env["db"])
    assert len(notices) == 1
    receipt = notices[0]["display_metadata"][RECEIPT_KEY]
    assert receipt["memory"][0]["added"] == ["Первая запись", "Вторая запись"]
    assert receipt["skills"][0]["name"] == "weekly-report"


@pytest.mark.asyncio
async def test_reading_memory_and_failed_writes_give_nothing(env):
    await _turn(env, lambda: _memory_read() + _memory_failed())
    assert _notices(env["db"]) == []


@pytest.mark.asyncio
async def test_notifications_off_keeps_the_chat_quiet(env):
    await _turn(env, _memory_add, notifications="off")
    assert _notices(env["db"]) == []


@pytest.mark.asyncio
async def test_memory_written_by_someone_else_is_not_credited_to_a_read_only_turn(env):
    from tools.memory_tool import load_on_disk_store

    def work():
        load_on_disk_store().add("user", "Запись чужого фонового разбора")
        return _memory_read()

    await _turn(env, work)
    assert _notices(env["db"]) == []


@pytest.mark.asyncio
async def test_agent_write_plus_background_review_stay_one_message(env):
    def review_later(agent):
        review = _memory_add("r1", "Отчёты сдаём по пятницам", "memory")
        agent.background_review_receipt = build_review_receipt(review, [])
        agent.background_review_callback("💾 Self-improvement review")
        agent.background_review_receipt = None

    agent, _ = await _turn(env, _memory_add, after_turn=review_later)

    notices = _notices(env["db"])
    assert len(notices) == 1
    assert "памяти агента" in notices[0]["content"] and "заметках о вас" in notices[0]["content"]
    receipt = notices[0]["display_metadata"][RECEIPT_KEY]
    assert {m["target"] for m in receipt["memory"]} == {"user", "memory"}
    assert undo_receipt(receipt)["status"] == "undone"
    from tools.memory_tool import MemoryStore

    assert "Отчёты сдаём" not in MemoryStore._path_for("memory").read_text(encoding="utf-8")
    assert RULE not in MemoryStore._path_for("user").read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_background_review_alone_still_adds_its_own_single_message(env):
    def review_later(agent):
        review = _memory_add("r1", "Отчёты сдаём по пятницам", "memory")
        agent.background_review_receipt = build_review_receipt(review, [])
        agent.background_review_callback("x")

    await _turn(env, lambda: [], after_turn=review_later)
    assert len(_notices(env["db"])) == 1


def test_late_background_notice_must_not_reintroduce_cancelled_receipt(env):
    from tools.memory_tool import load_on_disk_store

    db = env["db"]
    adapter = APIServerAdapter(PlatformConfig(enabled=True, extra={"key": "k" * 32}))
    agent = types.SimpleNamespace(session_id="web-1", _session_db=db)
    adapter._wire_learning_notice(agent, {})
    first = build_turn_receipt([{"role": "user", "content": "Не так"}] + _memory_add())
    agent._post_turn_learning_receipt(first)
    row = _notices(db)[0]
    assert undo_receipt(first)["status"] == "undone"
    db.merge_message_display_metadata(row["id"], {RECEIPT_KEY: {**first, "undone": True}})

    review = _memory_add("r1", "Отчёты по пятницам", "memory")
    agent.background_review_receipt = build_review_receipt(review, [])
    agent.background_review_callback("review finished")

    notices = _notices(db)
    assert len(notices) == 2
    assert notices[0]["display_metadata"][RECEIPT_KEY]["undone"] is True
    live = [
        m["display_metadata"][RECEIPT_KEY]
        for m in notices
        if not m["display_metadata"][RECEIPT_KEY].get("undone")
    ]
    assert live
    assert "Планы встреч" not in notices[1]["content"]
    assert "Отчёты по пятницам" in notices[1]["content"]
    outcome = undo_receipt(live[-1])
    assert outcome["status"] == "undone"
    assert snapshot_memory()["memory"] == []

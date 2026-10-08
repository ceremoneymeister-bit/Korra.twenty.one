"""K21-294: квитанция разбора и отмена изменения (навыки и память)."""

import json

import pytest

from agent.learning_receipt import (
    build_review_receipt,
    build_turn_receipt,
    format_notice,
    merge_receipts,
    snapshot_memory,
    undo_receipt,
)

SKILL_V1 = """---
name: weekly-report
description: weekly report
---

# Weekly report

Version one.
"""
SKILL_V2 = SKILL_V1.replace("Version one.", "Version two.")


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
    return {"home": home, "skills": skills_dir}


def _skill(action, content):
    from tools.skill_manager_tool import skill_manage

    raw = skill_manage(action=action, name="weekly-report", content=content)
    return json.loads(raw), raw


def _review_messages(action, raw, call_id="c1", name="weekly-report"):
    return [
        {
            "role": "assistant",
            "tool_calls": [
                {
                    "id": call_id,
                    "function": {
                        "name": "skill_manage",
                        "arguments": json.dumps({"action": action, "name": name}),
                    },
                }
            ],
        },
        {"role": "tool", "tool_call_id": call_id, "content": raw},
    ]


def _skill_md(env):
    return (env["skills"] / "weekly-report" / "SKILL.md")


def test_receipt_names_the_created_skill_and_undo_removes_it(env):
    result, raw = _skill("create", SKILL_V1)
    assert result["success"]
    receipt = build_review_receipt(_review_messages("create", raw), [], snapshot_memory())

    assert [s["name"] for s in receipt["skills"]] == ["weekly-report"]
    assert "«weekly-report»" in format_notice(receipt)
    assert _skill_md(env).exists()

    out = undo_receipt(receipt)
    assert out["status"] == "undone"
    assert not _skill_md(env).exists()


def test_undo_restores_previous_skill_content(env):
    _skill("create", SKILL_V1)
    result, raw = _skill("edit", SKILL_V2)
    receipt = build_review_receipt(_review_messages("edit", raw), [], snapshot_memory())
    assert "Version two." in _skill_md(env).read_text()

    assert undo_receipt(receipt)["status"] == "undone"
    assert "Version one." in _skill_md(env).read_text()


def test_undo_of_overwritten_change_explains_and_changes_nothing(env):
    _skill("create", SKILL_V1)
    _, raw = _skill("edit", SKILL_V2)
    receipt = build_review_receipt(_review_messages("edit", raw), [], snapshot_memory())
    newer = SKILL_V2.replace("Version two.", "Version three, written later.")
    _skill("edit", newer)

    out = undo_receipt(receipt)
    assert out["status"] == "conflict"
    assert "Ничего не изменено" in out["message"]
    assert "Version three" in _skill_md(env).read_text()


def test_second_undo_is_refused_cleanly(env):
    _skill("create", SKILL_V1)
    _, raw = _skill("edit", SKILL_V2)
    receipt = build_review_receipt(_review_messages("edit", raw), [], snapshot_memory())
    assert undo_receipt(receipt)["status"] == "undone"

    again = undo_receipt(receipt)
    assert again["ok"] is False and again["status"] == "conflict"
    assert "Version one." in _skill_md(env).read_text()

    receipt["undone"] = True
    assert undo_receipt(receipt)["status"] == "already_undone"


def test_skill_without_ledger_entry_is_not_silently_rolled_back(env):
    _, raw = _skill("create", SKILL_V1)
    payload = json.loads(raw)
    payload.pop("ledger", None)
    receipt = build_review_receipt(
        _review_messages("create", json.dumps(payload)), [], snapshot_memory()
    )
    out = undo_receipt(receipt)
    assert out["status"] == "conflict"
    assert _skill_md(env).exists()


def test_staged_or_failed_skill_writes_make_no_receipt(env):
    staged = json.dumps({"success": True, "staged": True, "pending_id": "p1"})
    failed = json.dumps({"success": False, "error": "x"})
    assert build_review_receipt(_review_messages("create", staged), [], snapshot_memory()) is None
    assert build_review_receipt(_review_messages("create", failed), [], snapshot_memory()) is None


def test_prior_snapshot_tool_results_are_not_reported(env):
    _, raw = _skill("create", SKILL_V1)
    messages = _review_messages("create", raw, call_id="old")
    assert build_review_receipt(messages, [messages[1]], snapshot_memory()) is None


def _store():
    from tools.memory_tool import load_on_disk_store

    return load_on_disk_store()


def test_memory_add_is_reported_and_undone(env):
    store = _store()
    store.add("memory", "Клиент любит короткие отчёты")
    before = snapshot_memory()
    store.add("memory", "Отчёты сдаём по пятницам")
    receipt = build_review_receipt([], [], before)

    assert receipt["memory"] == [
        {"target": "memory", "added": ["Отчёты сдаём по пятницам"], "removed": []}
    ]
    assert "«Отчёты сдаём по пятницам». Сохранено в памяти агента." in format_notice(receipt)
    assert "Отчёты сдаём по пятницам" in format_notice(receipt, "verbose")

    assert undo_receipt(receipt)["status"] == "undone"
    assert snapshot_memory()["memory"] == ["Клиент любит короткие отчёты"]


def test_memory_replace_is_undone_to_the_old_entry(env):
    store = _store()
    store.add("user", "Пишет по-русски")
    before = snapshot_memory()
    store.replace("user", "Пишет по-русски", "Пишет по-русски, без эмодзи")
    receipt = build_review_receipt([], [], before)

    assert undo_receipt(receipt)["status"] == "undone"
    assert snapshot_memory()["user"] == ["Пишет по-русски"]


def test_memory_undo_refuses_when_entry_was_edited_since(env):
    store = _store()
    before = snapshot_memory()
    store.add("memory", "Первая версия правила")
    receipt = build_review_receipt([], [], before)
    store.replace("memory", "Первая версия правила", "Вторая версия правила")

    out = undo_receipt(receipt)
    assert out["status"] == "conflict"
    assert snapshot_memory()["memory"] == ["Вторая версия правила"]


def test_no_changes_no_receipt(env):
    _store().add("memory", "Что-то давно известное")
    assert build_review_receipt([], [], snapshot_memory()) is None


def _memory_add_messages(text, call_id="m1"):
    from tools.memory_tool import load_on_disk_store

    result = load_on_disk_store().add("user", text)
    assert result["success"]
    return [
        {"role": "user", "content": "Сохрани моё правило"},
        {
            "role": "assistant",
            "tool_calls": [
                {
                    "id": call_id,
                    "function": {
                        "name": "memory",
                        "arguments": json.dumps(
                            {"action": "add", "target": "user", "content": text}
                        ),
                    },
                }
            ],
        },
        {"role": "tool", "tool_call_id": call_id, "content": json.dumps(result)},
    ]


def test_undo_must_preserve_another_chat_write_during_the_turn(env):
    own_messages = _memory_add_messages("Планы начинаем с цели")
    _memory_add_messages("Письма подписываем именем", "other-chat")
    receipt = build_turn_receipt(own_messages)
    outcome = undo_receipt(receipt)
    assert outcome["status"] == "undone"
    assert snapshot_memory()["user"] == ["Письма подписываем именем"]


def test_merged_add_then_refinement_must_undo_to_original_memory(env):
    from tools.memory_tool import load_on_disk_store

    first = build_turn_receipt(_memory_add_messages("Планы начинаем с цели"))
    before_review = snapshot_memory()
    result = load_on_disk_store().replace(
        "user", "Планы начинаем с цели", "Планы встреч начинаем с цели и указываем минуты"
    )
    assert result["success"]
    second = build_review_receipt([], [], before_review)
    merged = merge_receipts(first, second)
    outcome = undo_receipt(merged)
    assert outcome["status"] == "undone"
    assert snapshot_memory()["user"] == []


def test_merged_refinement_restores_content_that_was_there_before_both_writes(env):
    from tools.memory_tool import load_on_disk_store

    store = load_on_disk_store()
    assert store.add("user", "Общаться на «вы»")["success"]
    first = build_turn_receipt(
        _memory_add_messages("Планы начинаем с цели")
    )
    before_review = snapshot_memory()
    assert store.replace("user", "Планы начинаем с цели", "Планы встреч с минутами")["success"]
    merged = merge_receipts(first, build_review_receipt([], [], before_review))
    assert merged["memory"] == [
        {"target": "user", "added": ["Планы встреч с минутами"], "removed": []}
    ]
    assert undo_receipt(merged)["status"] == "undone"
    assert snapshot_memory()["user"] == ["Общаться на «вы»"]


def test_turn_receipt_describes_replace_remove_and_batch_exactly(env):
    from tools.memory_tool import load_on_disk_store, memory_tool

    store = load_on_disk_store()
    for text in ("Старая запись", "Лишняя запись", "Нетронутая запись"):
        assert store.add("user", text)["success"]

    def _messages(**args):
        raw = memory_tool(store=store, target="user", **args)
        return [
            {"role": "user", "content": "x"},
            {
                "role": "assistant",
                "tool_calls": [
                    {"id": "c", "function": {"name": "memory", "arguments": json.dumps(args)}}
                ],
            },
            {"role": "tool", "tool_call_id": "c", "content": raw},
        ]

    replaced = build_turn_receipt(
        _messages(action="replace", old_text="Старая запись", content="Новая запись")
    )
    assert replaced["memory"] == [
        {"target": "user", "added": ["Новая запись"], "removed": ["Старая запись"]}
    ]
    removed = build_turn_receipt(_messages(action="remove", old_text="Лишняя запись"))
    assert removed["memory"] == [
        {"target": "user", "added": [], "removed": ["Лишняя запись"]}
    ]
    batch = build_turn_receipt(
        _messages(
            operations=[
                {"action": "add", "content": "Из пакета"},
                {"action": "replace", "old_text": "Из пакета", "content": "Из пакета, версия 2"},
                {"action": "remove", "old_text": "Нетронутая запись"},
            ]
        )
    )
    assert batch["memory"] == [
        {"target": "user", "added": ["Из пакета, версия 2"], "removed": ["Нетронутая запись"]}
    ]
    assert undo_receipt(batch)["status"] == "undone"
    assert "Нетронутая запись" in snapshot_memory()["user"]
    assert "Из пакета, версия 2" not in snapshot_memory()["user"]


def test_duplicate_add_changes_nothing_and_gives_no_receipt(env):
    from tools.memory_tool import load_on_disk_store

    assert load_on_disk_store().add("user", "Планы начинаем с цели")["success"]
    assert build_turn_receipt(_memory_add_messages("Планы начинаем с цели")) is None

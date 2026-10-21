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
    receipt = build_review_receipt(_review_messages("create", raw), [])

    assert [s["name"] for s in receipt["skills"]] == ["weekly-report"]
    assert "«weekly-report»" in format_notice(receipt)
    assert _skill_md(env).exists()

    out = undo_receipt(receipt)
    assert out["status"] == "undone"
    assert not _skill_md(env).exists()


def test_undo_restores_previous_skill_content(env):
    _skill("create", SKILL_V1)
    result, raw = _skill("edit", SKILL_V2)
    receipt = build_review_receipt(_review_messages("edit", raw), [])
    assert "Version two." in _skill_md(env).read_text()

    assert undo_receipt(receipt)["status"] == "undone"
    assert "Version one." in _skill_md(env).read_text()


def test_undo_of_overwritten_change_explains_and_changes_nothing(env):
    _skill("create", SKILL_V1)
    _, raw = _skill("edit", SKILL_V2)
    receipt = build_review_receipt(_review_messages("edit", raw), [])
    newer = SKILL_V2.replace("Version two.", "Version three, written later.")
    _skill("edit", newer)

    out = undo_receipt(receipt)
    assert out["status"] == "conflict"
    assert "Ничего не изменено" in out["message"]
    assert "Version three" in _skill_md(env).read_text()


def test_second_undo_is_refused_cleanly(env):
    _skill("create", SKILL_V1)
    _, raw = _skill("edit", SKILL_V2)
    receipt = build_review_receipt(_review_messages("edit", raw), [])
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
        _review_messages("create", json.dumps(payload)), []
    )
    out = undo_receipt(receipt)
    assert out["status"] == "conflict"
    assert _skill_md(env).exists()


def test_staged_or_failed_skill_writes_make_no_receipt(env):
    staged = json.dumps({"success": True, "staged": True, "pending_id": "p1"})
    failed = json.dumps({"success": False, "error": "x"})
    assert build_review_receipt(_review_messages("create", staged), []) is None
    assert build_review_receipt(_review_messages("create", failed), []) is None


def test_prior_snapshot_tool_results_are_not_reported(env):
    _, raw = _skill("create", SKILL_V1)
    messages = _review_messages("create", raw, call_id="old")
    assert build_review_receipt(messages, [messages[1]]) is None


def _store():
    from tools.memory_tool import load_on_disk_store

    return load_on_disk_store()


def _memory_delta(receipt):
    """The undo contract is independent of optional display provenance."""
    return [{key: item[key] for key in ("target", "added", "removed")}
            for item in receipt["memory"]]


def _memory_call(call_id="r1", **args):
    """Вызов инструмента ``memory`` и его настоящий ответ — как в сообщениях форка."""
    from tools.memory_tool import memory_tool

    raw = memory_tool(store=_store(), **args)
    assert json.loads(raw)["success"]
    return [
        {
            "role": "assistant",
            "tool_calls": [
                {"id": call_id, "function": {"name": "memory", "arguments": json.dumps(args)}}
            ],
        },
        {"role": "tool", "tool_call_id": call_id, "content": raw},
    ]


def test_memory_add_is_reported_and_undone(env):
    store = _store()
    store.add("memory", "Клиент любит короткие отчёты")
    receipt = build_review_receipt(
        _memory_call(action="add", target="memory", content="Отчёты сдаём по пятницам"), []
    )

    assert _memory_delta(receipt) == [
        {"target": "memory", "added": ["Отчёты сдаём по пятницам"], "removed": []}
    ]
    assert "«Отчёты сдаём по пятницам». Сохранено в памяти агента." in format_notice(receipt)
    assert "Отчёты сдаём по пятницам" in format_notice(receipt, "verbose")

    assert undo_receipt(receipt)["status"] == "undone"
    assert snapshot_memory()["memory"] == ["Клиент любит короткие отчёты"]


def test_memory_replace_is_undone_to_the_old_entry(env):
    store = _store()
    store.add("user", "Пишет по-русски")
    receipt = build_review_receipt(
        _memory_call(
            action="replace",
            target="user",
            old_text="Пишет по-русски",
            content="Пишет по-русски, без эмодзи",
        ),
        [],
    )

    assert undo_receipt(receipt)["status"] == "undone"
    assert snapshot_memory()["user"] == ["Пишет по-русски"]


def test_memory_undo_refuses_when_entry_was_edited_since(env):
    store = _store()
    receipt = build_review_receipt(
        _memory_call(action="add", target="memory", content="Первая версия правила"), []
    )
    store.replace("memory", "Первая версия правила", "Вторая версия правила")

    out = undo_receipt(receipt)
    assert out["status"] == "conflict"
    assert snapshot_memory()["memory"] == ["Вторая версия правила"]


def test_no_changes_no_receipt(env):
    _store().add("memory", "Что-то давно известное")
    assert build_review_receipt([], []) is None


def _memory_add_messages(text, call_id="m1"):
    return [{"role": "user", "content": "Сохрани моё правило"}] + _memory_call(
        call_id, action="add", target="user", content=text
    )


def test_undo_must_preserve_another_chat_write_during_the_turn(env):
    own_messages = _memory_add_messages("Планы начинаем с цели")
    _memory_add_messages("Письма подписываем именем", "other-chat")
    receipt = build_turn_receipt(own_messages)
    outcome = undo_receipt(receipt)
    assert outcome["status"] == "undone"
    assert snapshot_memory()["user"] == ["Письма подписываем именем"]


def test_merged_add_then_refinement_must_undo_to_original_memory(env):
    first = build_turn_receipt(_memory_add_messages("Планы начинаем с цели"))
    second = build_review_receipt(
        _memory_call(
            action="replace",
            target="user",
            old_text="Планы начинаем с цели",
            content="Планы встреч начинаем с цели и указываем минуты",
        ),
        [],
    )
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
    second = build_review_receipt(
        _memory_call(
            action="replace",
            target="user",
            old_text="Планы начинаем с цели",
            content="Планы встреч с минутами",
        ),
        [],
    )
    merged = merge_receipts(first, second)
    assert _memory_delta(merged) == [
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
    assert _memory_delta(replaced) == [
        {"target": "user", "added": ["Новая запись"], "removed": ["Старая запись"]}
    ]
    removed = build_turn_receipt(_messages(action="remove", old_text="Лишняя запись"))
    assert _memory_delta(removed) == [
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
    assert _memory_delta(batch) == [
        {"target": "user", "added": ["Из пакета, версия 2"], "removed": ["Нетронутая запись"]}
    ]
    assert undo_receipt(batch)["status"] == "undone"
    assert "Нетронутая запись" in snapshot_memory()["user"]
    assert "Из пакета, версия 2" not in snapshot_memory()["user"]


def test_duplicate_add_changes_nothing_and_gives_no_receipt(env):
    from tools.memory_tool import load_on_disk_store

    assert load_on_disk_store().add("user", "Планы начинаем с цели")["success"]
    assert build_turn_receipt(_memory_add_messages("Планы начинаем с цели")) is None


def test_background_undo_preserves_another_chat_write(env):
    own_rule = "Планы встреч начинаем с цели"
    other_rule = "Письма подписываем именем"
    own_messages = _memory_call("review-m1", action="add", target="user", content=own_rule)
    assert _store().add("user", other_rule)["success"]
    receipt = build_review_receipt(own_messages, [])
    assert _memory_delta(receipt) == [{"target": "user", "added": [own_rule], "removed": []}]
    outcome = undo_receipt(receipt)
    assert outcome["status"] == "undone"
    assert snapshot_memory()["user"] == [other_rule]


def test_review_without_fork_messages_gives_no_memory_receipt(env):
    assert _store().add("user", "Запись без сообщений форка")["success"]
    assert build_review_receipt([], []) is None


def test_review_receipt_ignores_memory_calls_from_the_parent_history(env):
    messages = _memory_call("old", action="add", target="user", content="Старое правило")
    assert build_review_receipt(messages, [messages[1]]) is None


@pytest.mark.parametrize("mode", ["on", "verbose"])
def test_compaction_reports_new_fact_and_undo_restores_every_old_note(env, mode):
    from tools.memory_tool import memory_tool

    old = [
        "Контекст: /opt/data/home/k21b-context/INDEX.md; сверять список активных работ.",
        "Проект Альфа: консультация, заказ не подтверждён; работы приостановлены.",
        "Проект Бета: подробный архив обсуждений; вернуться после согласования бюджета.",
    ]
    short = [
        "Контекст: /opt/data/home/k21b-context/INDEX.md.",
        "Проект Альфа: консультация, пауза.",
        "Проект Бета: ждём бюджет.",
    ]
    fact = "Оплату принимаем по пятницам"
    store = _store()
    for entry in old:
        assert store.add("memory", entry)["success"]
    before = snapshot_memory()

    operations = [
        {"action": "replace", "old_text": entry, "content": compact}
        for entry, compact in zip(old, short)
    ] + [{"action": "add", "content": fact}]
    # The failed attempt has no effect and must not enter either notice or undo.
    failed_args = {"target": "memory", "operations": operations + [
        {"action": "add", "content": "Не поместится " * 500},
    ]}
    failed = memory_tool(store=store, **failed_args)
    assert not json.loads(failed)["success"]
    messages = [{"role": "user", "content": f"Запомни: {fact}"},
                {"role": "assistant", "tool_calls": [{"id": "failed", "function": {
                    "name": "memory", "arguments": json.dumps(failed_args)}}]},
                {"role": "tool", "tool_call_id": "failed", "content": failed}]
    messages += _memory_call(target="memory", operations=operations)
    receipt = build_turn_receipt(messages)

    text = format_notice(receipt, mode)
    assert fact in text
    assert "сжал 3 старые заметки" in text
    for entry in old + short:
        assert entry not in text
    assert "Альфа" not in text and "Бета" not in text and "/opt/" not in text
    assert receipt["memory"][0]["added"] == short + [fact]
    assert receipt["memory"][0]["removed"] == old
    from agent.learning_receipt import receipt_has_undo

    assert receipt_has_undo(receipt)
    assert undo_receipt(receipt)["status"] == "undone"
    assert snapshot_memory() == before


def test_explicit_edit_and_deletion_keep_the_named_fact_and_path(env):
    old = "Отчёты храним в /srv/customer/reports, каждый пятничный отчёт в своей папке"
    new = "Отчёты храним в /srv/customer/reports"
    assert _store().add("memory", old)["success"]
    edited = build_turn_receipt([
        {"role": "user", "content": f"Замени «{old}» на «{new}»"},
        *_memory_call(action="replace", target="memory", old_text=old, content=new),
    ])
    text = format_notice(edited)
    assert new in text and old in text and "вместо" in text
    assert "сжал" not in text
    removed = build_turn_receipt([
        {"role": "user", "content": f"Забудь «{new}»"},
        *_memory_call(action="remove", target="memory", old_text=new),
    ])
    assert new in format_notice(removed)
    assert "Удалено" in format_notice(removed)
    assert undo_receipt(removed)["status"] == "undone"
    assert undo_receipt(edited)["status"] == "undone"
    assert snapshot_memory()["memory"] == [old]


@pytest.mark.parametrize("explicit", [False, True])
def test_three_removals_with_one_addition_respect_the_named_request(env, explicit):
    old = ["Архив Альфа", "Архив Бета", "Архив Гамма"]
    fact = "Оплату принимаем по пятницам"
    for entry in old:
        assert _store().add("memory", entry)["success"]
    request = f"Запомни: {fact}"
    if explicit:
        request += ". Забудь: " + "; ".join(old)
    receipt = build_turn_receipt([
        {"role": "user", "content": request},
        *_memory_call(target="memory", operations=[
            *[{"action": "remove", "old_text": entry} for entry in old],
            {"action": "add", "content": fact},
        ]),
    ])
    text = format_notice(receipt)
    assert fact in text
    if explicit:
        assert all(entry in text for entry in old)
        assert "сжал" not in text
    else:
        assert all(entry not in text for entry in old)
        assert "сжал 3 старые заметки" in text
    assert undo_receipt(receipt)["status"] == "undone"
    assert snapshot_memory()["memory"] == old


def test_explicit_shortening_next_to_addition_is_not_hidden_in_review(env):
    old = "Отчёты: /opt/data/home/reports; подробное описание расписания"
    new = "Отчёты: /opt/data/home/reports"
    assert _store().add("memory", old)["success"]
    receipt = build_review_receipt(_memory_call(target="memory", operations=[
        {"action": "replace", "old_text": "Отчёты:", "content": new},
        {"action": "add", "content": "Новая встреча по вторникам"},
    ]), [{"role": "user", "content": f"Замени заметку Отчёты: на «{new}» и запомни встречу"}])
    text = format_notice(receipt)
    assert old in text and new in text and "Новая встреча" in text
    assert "сжал" not in text


def test_merged_compaction_chain_counts_original_notes_once(env):
    old = "Архив Альфа: /opt/data/home/alpha/INDEX.md; подробный список ожидающих задач"
    middle = "Архив Альфа: /opt/data/home/alpha/INDEX.md; ожидает"
    short = "Архив Альфа: /opt/data/home/alpha/INDEX.md"
    assert _store().add("memory", old)["success"]
    receipts = []
    for index, (before, after) in enumerate([(old, middle), (middle, short)]):
        receipts.append(build_review_receipt(_memory_call(
            f"m{index}", target="memory", operations=[
                {"action": "replace", "old_text": before, "content": after},
                {"action": "add", "content": f"Новый факт {index}"},
            ],
        ), []))
    merged = merge_receipts(*receipts)
    text = format_notice(merged)
    assert "сжал 1 старую заметку" in text
    assert "Архив" not in text and "/opt/" not in text
    assert "Новый факт 0" in text and "Новый факт 1" in text
    assert undo_receipt(merged)["status"] == "undone"
    assert snapshot_memory()["memory"] == [old]


def test_merged_shortening_of_new_fact_keeps_that_fact_visible(env):
    old = "Отчёты по пятницам: всегда отправлять после обеда"
    short = "Отчёты по пятницам после обеда"
    first = build_review_receipt(_memory_call(action="add", target="memory", content=old), [])
    second = build_review_receipt(_memory_call(target="memory", operations=[
        {"action": "replace", "old_text": old, "content": short},
        {"action": "add", "content": "Оплату принимаем по вторникам"},
    ]), [])
    merged = merge_receipts(first, second)
    text = format_notice(merged)
    assert short in text and "Оплату принимаем" in text
    assert "сжал" not in text
    assert undo_receipt(merged)["status"] == "undone"
    assert snapshot_memory()["memory"] == []


def test_separate_compaction_calls_and_add_alias_keep_only_new_fact(env):
    old = "Архив: /opt/data/home/archive/INDEX.md; подробное описание задач"
    middle = "Архив: /opt/data/home/archive/INDEX.md; ожидает"
    short = "Архив: /opt/data/home/archive/INDEX.md"
    assert _store().add("memory", old)["success"]
    first = build_review_receipt(_memory_call(target="memory", operations=[
        {"action": "replace", "old_text": old, "content": middle},
        {"action": "add", "new_text": "Оплату принимаем по пятницам"},
    ]), [])
    second = build_review_receipt(_memory_call(
        action="replace", target="memory", old_text=middle, content=short,
    ), [])
    merged = merge_receipts(first, second)
    text = format_notice(merged)
    assert "Оплату принимаем по пятницам" in text
    assert "сжал 1 старую заметку" in text
    assert "Архив" not in text and "/opt/" not in text
    assert undo_receipt(merged)["status"] == "undone"
    assert snapshot_memory()["memory"] == [old]


def test_merged_explicit_removals_never_use_legacy_compaction_fallback(env):
    for entry in ("Альфа", "Бета"):
        assert _store().add("memory", entry)["success"]
    receipts = []
    for index, entry in enumerate(("Альфа", "Бета")):
        operations = [{"action": "remove", "old_text": entry}]
        if index == 0:
            operations.append({"action": "add", "content": "Оплату принимаем по пятницам"})
        receipts.append(build_review_receipt(
            _memory_call(f"m{index}", target="memory", operations=operations),
            [{"role": "user", "content": f"Забудь {entry}"}],
        ))
    text = format_notice(merge_receipts(*receipts))
    assert "Альфа" in text and "Бета" in text
    assert "сжал" not in text


def test_remove_exact_match_does_not_hide_an_explicit_sibling_edit(env):
    for entry in ("temp", "temp important X"):
        assert _store().add("memory", entry)["success"]
    receipt = build_turn_receipt([
        {"role": "user", "content": "Замени X на Y и запомни новый факт"},
        *_memory_call(target="memory", operations=[
            {"action": "remove", "old_text": "temp"},
            {"action": "replace", "old_text": "X", "content": "Y"},
            {"action": "add", "content": "Новый факт"},
        ]),
    ])
    text = format_notice(receipt)
    assert "temp important X" in text and "«Y»" in text
    assert "сжал 1 старую заметку" in text


@pytest.mark.parametrize("explicit", [False, True])
def test_replace_then_remove_tracks_the_actual_intermediate_entry(env, explicit):
    old = "Архив X: подробное описание старого проекта"
    assert _store().add("memory", old)["success"]
    receipt = build_turn_receipt([
        {"role": "user", "content": "Замени X и запомни новый факт" if explicit else "Запомни новый факт"},
        *_memory_call(target="memory", operations=[
            {"action": "replace", "old_text": "X", "content": "temp"},
            {"action": "remove", "old_text": "temp"},
            {"action": "add", "content": "Новый факт"},
        ]),
    ])
    text = format_notice(receipt)
    assert "Новый факт" in text
    if explicit:
        assert old in text and "сжал" not in text
    else:
        assert old not in text and "сжал 1 старую заметку" in text
    assert undo_receipt(receipt)["status"] == "undone"
    assert snapshot_memory()["memory"] == [old]


def test_batch_of_only_edits_does_not_mistake_a_longer_replacement_for_add(env):
    old = ["Проект Альфа: длинное описание", "Бета"]
    new = ["Альфа", "Проект Бета: уточнённое длинное описание"]
    for entry in old:
        assert _store().add("memory", entry)["success"]
    receipt = build_review_receipt(_memory_call(target="memory", operations=[
        {"action": "replace", "old_text": before, "content": after}
        for before, after in zip(old, new)
    ]), [])
    text = format_notice(receipt)
    assert all(entry in text for entry in old + new)
    assert "сжал" not in text


def test_duplicate_add_of_a_shortened_note_is_not_an_independent_new_fact(env):
    old = "Отчёты по пятницам: отправлять после обеда"
    short = "Отчёты по пятницам"
    assert _store().add("memory", old)["success"]
    receipt = build_review_receipt(_memory_call(target="memory", operations=[
        {"action": "replace", "old_text": old, "content": short},
        {"action": "add", "content": short},
    ]), [])
    text = format_notice(receipt)
    assert old in text and short in text
    assert "сжал" not in text

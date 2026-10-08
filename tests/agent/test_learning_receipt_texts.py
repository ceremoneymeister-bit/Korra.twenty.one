"""Ревью № 4 (K21-294): тексты «Учёл» и итога отмены."""

import pytest

from agent.learning_receipt import _undo_message, format_notice

LONG = (
    "Пользователь предпочитает планы встреч с первой строкой «Цель: …», нумерованными "
    "пунктами и временем в минутах у каждого пункта, а ещё просит не добавлять лишних "
    "вводных слов и заканчивать план строкой с договорённостями"
)


def _receipt(skills=(), memory=()):
    return {"version": 1, "id": "r1", "skills": list(skills), "memory": list(memory)}


def _mem(target, added=(), removed=()):
    return {"target": target, "added": list(added), "removed": list(removed)}


@pytest.mark.parametrize(
    "item,expected",
    [
        (_mem("user", ["Планы начинать с «Цель»"]),
         "✅ Учёл: «Планы начинать с «Цель»». Сохранено в заметках о вас."),
        (_mem("memory", ["Отчёты сдаём по пятницам"]),
         "✅ Учёл: «Отчёты сдаём по пятницам». Сохранено в памяти агента."),
        (_mem("user", ["А", "Б"]), "✅ Учёл: «А»; «Б». Сохранено в заметках о вас."),
        (_mem("memory", ["А", "Б", "В", "Г", "Д"]),
         "✅ Учёл: «А»; «Б»; «В» и ещё 2. Сохранено в памяти агента."),
        (_mem("user", ["Новая"], ["Старая"]),
         "✅ Учёл: «Новая» (вместо «Старая»). Обновлено в заметках о вас."),
        (_mem("memory", ["Новая"], ["Старая"]),
         "✅ Учёл: «Новая» (вместо «Старая»). Обновлено в памяти агента."),
        (_mem("user", [], ["Лишнее"]), "✅ Учёл: «Лишнее». Удалено из заметок о вас."),
        (_mem("memory", [], ["Лишнее"]), "✅ Учёл: «Лишнее». Удалено из памяти агента."),
    ],
)
def test_memory_notice_grammar(item, expected):
    assert format_notice(_receipt(memory=[item])) == expected


@pytest.mark.parametrize(
    "action,verb", [("create", "создан"), ("update", "обновлён"), ("delete", "удалён")]
)
def test_skill_notice_names_the_skill_and_action(action, verb):
    skill = {"name": "weekly-report", "action": action, "entry_ids": ["e1"]}
    assert format_notice(_receipt(skills=[skill])) == f"✅ Учёл: навык «weekly-report» {verb}."


def test_skills_and_memory_together_start_every_sentence_properly():
    text = format_notice(_receipt(
        skills=[{"name": "a", "action": "create", "entry_ids": []},
                {"name": "b", "action": "update", "entry_ids": []}],
        memory=[_mem("user", ["Правило"])],
    ))
    assert text == (
        "✅ Учёл: навык «a» создан. Навык «b» обновлён. «Правило». Сохранено в заметках о вас."
    )


def test_long_entry_is_cut_at_a_word_boundary():
    text = format_notice(_receipt(memory=[_mem("user", [LONG])]))
    quoted = text.split("«", 1)[1].rsplit("»", 1)[0]
    assert quoted.endswith("…")
    assert 150 < len(quoted) <= 201
    assert LONG.startswith(quoted[:-1])
    assert LONG[len(quoted) - 1] == " "
    assert text.endswith(". Сохранено в заметках о вас.")


def test_verbose_keeps_more_of_the_entry():
    short = format_notice(_receipt(memory=[_mem("user", [LONG])]))
    verbose = format_notice(_receipt(memory=[_mem("user", [LONG])]), "verbose")
    assert len(verbose) > len(short) and LONG in verbose


@pytest.mark.parametrize(
    "skills,memory,expected",
    [
        ([], [_mem("user", ["x"])], "Отменено: заметка удалена"),
        ([], [_mem("user", ["x", "y"])], "Отменено: заметки удалены (2)"),
        ([], [_mem("memory", ["x"])], "Отменено: запись памяти удалена"),
        ([], [_mem("user", [], ["x"])], "Отменено: заметка возвращена"),
        ([], [_mem("memory", ["n"], ["x"])], "Отменено: запись памяти возвращена к прежней версии"),
        ([{"name": "s", "action": "create"}], [], "Отменено: навык «s» удалён"),
        ([{"name": "s", "action": "update"}], [],
         "Отменено: навык «s» возвращён к прежней версии"),
        ([{"name": "s", "action": "update"}], [_mem("user", ["x"])],
         "Отменено: навык «s» возвращён к прежней версии; заметка удалена"),
    ],
)
def test_undo_message_is_one_clear_text(skills, memory, expected):
    assert _undo_message(skills, memory) == expected

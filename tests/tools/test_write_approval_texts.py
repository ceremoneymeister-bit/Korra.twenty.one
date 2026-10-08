"""Ревью № 4 (K21-294): /memory и /skills отвечают по-русски во всех ветках."""

import re

import pytest

from korra_cli.write_approval_commands import handle_pending_subcommand
from tools import write_approval as wa

_ENGLISH = re.compile(r"\b(?:No pending|Pending|Approved|Rejected|Usage|Invalid value|Failed|Set with)\b")


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))


def _run(subsystem, *args, **kwargs):
    out = handle_pending_subcommand(subsystem, list(args), **kwargs)
    assert out is not None and not _ENGLISH.search(out), out
    return out


def test_bare_memory_reports_gate_and_empty_queue():
    out = _run(wa.MEMORY)
    assert "Подтверждение записей в память выключено." in out
    assert "Ожидающих записей в память нет." in out


def test_bare_skills_and_lists():
    assert "Подтверждение записи навыков выключено." in _run(wa.SKILLS)
    assert _run(wa.SKILLS, "pending") == "Ожидающих изменений навыков нет."
    assert _run(wa.MEMORY, "pending") == "Ожидающих записей в память нет."


def test_pending_list_with_records():
    wa.stage_write("memory", {"action": "add", "target": "user", "content": "a"},
                   summary="запомнить a", origin="background_review")
    out = _run(wa.MEMORY, "pending")
    assert "Ожидающие записи в память (1):" in out
    assert "[авто]" in out and "Применить: /memory approve <id>" in out


def test_approve_and_reject_branches():
    assert _run(wa.MEMORY, "approve", "all") == "Ожидающих записей в память нет."
    assert _run(wa.MEMORY, "approve") == "Использование: /memory approve|reject <id> (или all)"
    assert _run(wa.MEMORY, "reject", "zz") == "Ожидающей записи в память с id 'zz' нет."
    assert _run(wa.SKILLS, "reject", "zz") == "Ожидающего изменения навыка с id 'zz' нет."
    assert _run(wa.SKILLS, "diff") == "Использование: /skills diff <id>"
    assert _run(wa.SKILLS, "diff", "zz") == "Ожидающего изменения навыка с id 'zz' нет."
    assert _run(wa.MEMORY, "reject", "all") == "Отклонено ожидающих записей в память: 0."
    assert _run(wa.SKILLS, "reject", "all") == "Отклонено ожидающих изменений навыков: 0."


def test_reject_one_and_approve_without_store():
    rec = wa.stage_write("memory", {"action": "add", "target": "user", "content": "a"},
                         summary="a", origin="foreground")
    rid = rec["id"] if isinstance(rec, dict) else wa.list_pending("memory")[0]["id"]
    out = _run(wa.MEMORY, "approve", rid)
    assert out == "Применено записей в память: 0.\nНе удалось применить:\n  " + rid + ": хранилище памяти недоступно"
    assert _run(wa.MEMORY, "reject", rid) == f"Отклонена ожидающая запись в память '{rid}'."


def test_approval_switch_branches():
    assert "Изменить: /memory approval <on|off>" in _run(wa.MEMORY, "approval")
    assert _run(wa.MEMORY, "approval", "maybe") == "Некорректное значение 'maybe'. Допустимо: on или off."
    assert "hermes config set memory.write_approval true" in _run(wa.MEMORY, "approval", "on")
    seen = []
    assert _run(wa.MEMORY, "approval", "on", set_mode_fn=seen.append) == "Подтверждение записей в память включено."
    assert _run(wa.SKILLS, "approval", "off", set_mode_fn=seen.append) == "Подтверждение записи навыков выключено."
    assert seen == [True, False]

    def boom(_enabled):
        raise OSError("диск")

    assert _run(wa.MEMORY, "approval", "on", set_mode_fn=boom) == "Не удалось изменить memory.write_approval: диск"

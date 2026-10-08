#!/usr/bin/env python3
"""Shared handlers for the /memory and /skills write-approval subcommands.

Both the interactive CLI (``cli.py``) and the gateway (``gateway/run.py``) call
into this module so the pending-review UX (list / approve / reject / diff /
mode) lives in one place. Each caller owns only its surface concerns:
formatting the returned text and, for the gateway, persisting config + evicting
the cached agent on a mode change.

Every public handler returns a plain text string suitable for both a terminal
and a chat message. Skill diffs are intentionally NOT inlined here — the
``diff`` handler returns the full diff for the CLI pager, but on a messaging
platform the gateway truncates it and points the user at the dashboard / file.
"""

from __future__ import annotations

import json
from typing import List, Optional

from tools import write_approval as wa


_GATE_TITLE = {
    wa.MEMORY: "Подтверждение записей в память",
    wa.SKILLS: "Подтверждение записи навыков",
}
_NO_PENDING = {
    wa.MEMORY: "Ожидающих записей в память нет.",
    wa.SKILLS: "Ожидающих изменений навыков нет.",
}
_PENDING_TITLE = {
    wa.MEMORY: "Ожидающие записи в память",
    wa.SKILLS: "Ожидающие изменения навыков",
}
_APPLIED = {
    wa.MEMORY: "Применено записей в память",
    wa.SKILLS: "Применено изменений навыков",
}
_REJECTED_COUNT = {
    wa.MEMORY: "Отклонено ожидающих записей в память",
    wa.SKILLS: "Отклонено ожидающих изменений навыков",
}
_REJECTED_ONE = {
    wa.MEMORY: "Отклонена ожидающая запись в память",
    wa.SKILLS: "Отклонено ожидающее изменение навыка",
}
_NOT_FOUND = {
    wa.MEMORY: "Ожидающей записи в память с id '{id}' нет.",
    wa.SKILLS: "Ожидающего изменения навыка с id '{id}' нет.",
}


def _fmt_state_value(subsystem: str, enabled: bool) -> str:
    return f"{_GATE_TITLE[subsystem]} {'включено' if enabled else 'выключено'}."


def _fmt_state(subsystem: str) -> str:
    return _fmt_state_value(subsystem, wa.write_approval_enabled(subsystem))


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def _fmt_pending_list(subsystem: str) -> str:
    records = wa.list_pending(subsystem)
    if not records:
        return _NO_PENDING[subsystem]
    lines = [f"{_PENDING_TITLE[subsystem]} ({len(records)}):"]
    for r in records:
        origin = r.get("origin", "foreground")
        tag = " [авто]" if origin == "background_review" else ""
        lines.append(f"  {r['id']}{tag}  {r.get('summary', '')}")
    lines.append("")
    lines.append(
        f"Применить: /{subsystem} approve <id>   Отклонить: /{subsystem} reject <id>"
    )
    if subsystem == wa.SKILLS:
        lines.append("Показать целиком: /skills diff <id>")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Subcommand dispatch
# ---------------------------------------------------------------------------

def handle_pending_subcommand(
    subsystem: str,
    args: List[str],
    *,
    memory_store=None,
    set_mode_fn=None,
) -> Optional[str]:
    """Dispatch a /memory or /skills subcommand.

    Args:
        subsystem: ``memory`` or ``skills``.
        args: tokens after the slash command (e.g. ``["approve", "a1b2"]``).
        memory_store: live MemoryStore for applying approved memory writes
            (CLI passes ``self.agent._memory_store``; gateway applies against a
            freshly loaded store).
        set_mode_fn: optional callable ``(enabled: bool) -> None`` that
            persists the new write_approval boolean to config (gateway provides
            this; CLI uses its own ``save_config_value`` and passes a closure).

    Returns a text string to show the user. Returns None when the args are not
    a write-approval subcommand (caller falls through to its other handling,
    e.g. /skills search).
    """
    if not args:
        # Bare /memory or /skills with no sub → show pending + gate state.
        return f"{_fmt_state(subsystem)}\n\n" + _fmt_pending_list(subsystem)

    sub = args[0].lower()
    rest = args[1:]

    if sub == "pending":
        return _fmt_pending_list(subsystem)

    if sub in {"approve", "apply"}:
        return _approve(subsystem, rest, memory_store)

    if sub in {"reject", "deny", "drop"}:
        return _reject(subsystem, rest)

    if sub == "diff" and subsystem == wa.SKILLS:
        return _diff(rest)

    if sub in {"approval", "mode"}:  # 'mode' kept as a back-compat alias
        return _set_approval(subsystem, rest, set_mode_fn)

    return None  # not ours — caller handles


def _resolve_one(subsystem: str, rest: List[str]):
    if not rest:
        return None, f"Использование: /{subsystem} approve|reject <id> (или all)"
    return rest[0], None


def _approve(subsystem: str, rest: List[str], memory_store) -> str:
    target, err = _resolve_one(subsystem, rest)
    if err or target is None:
        return err or f"Использование: /{subsystem} approve <id>"

    records = wa.list_pending(subsystem)
    if not records:
        return _NO_PENDING[subsystem]

    if target.lower() == "all":
        targets = list(records)
    else:
        rec = wa.get_pending(subsystem, target)
        if not rec:
            return _NOT_FOUND[subsystem].format(id=target)
        targets = [rec]

    applied, failed = 0, []
    for rec in targets:
        ok, msg = _apply_one(subsystem, rec, memory_store)
        if ok:
            wa.discard_pending(subsystem, rec["id"])
            applied += 1
        else:
            failed.append(f"{rec['id']}: {msg}")

    out = [f"{_APPLIED[subsystem]}: {applied}."]
    if failed:
        out.append("Не удалось применить:")
        out.extend(f"  {f}" for f in failed)
    return "\n".join(out)


def _apply_one(subsystem: str, rec, memory_store):
    payload = rec.get("payload", {})
    try:
        if subsystem == wa.MEMORY:
            if memory_store is None:
                return False, "хранилище памяти недоступно"
            from tools.memory_tool import apply_memory_pending
            result = apply_memory_pending(payload, memory_store)
            return bool(result.get("success")), result.get("error", "")
        else:
            from tools.skill_manager_tool import apply_skill_pending
            result = json.loads(apply_skill_pending(payload))
            return bool(result.get("success")), result.get("error", "")
    except Exception as e:
        return False, str(e)


def _reject(subsystem: str, rest: List[str]) -> str:
    target, err = _resolve_one(subsystem, rest)
    if err or target is None:
        return err or f"Использование: /{subsystem} reject <id>"
    if target.lower() == "all":
        n = 0
        for rec in wa.list_pending(subsystem):
            if wa.discard_pending(subsystem, rec["id"]):
                n += 1
        return f"{_REJECTED_COUNT[subsystem]}: {n}."
    if wa.discard_pending(subsystem, target):
        return f"{_REJECTED_ONE[subsystem]} '{target}'."
    return _NOT_FOUND[subsystem].format(id=target)


def _diff(rest: List[str]) -> str:
    if not rest:
        return "Использование: /skills diff <id>"
    rec = wa.get_pending(wa.SKILLS, rest[0])
    if not rec:
        return _NOT_FOUND[wa.SKILLS].format(id=rest[0])
    diff = wa.skill_pending_diff(rec)
    header = f"# Ожидающее изменение навыка {rec['id']}: {rec.get('summary', '')}\n"
    return header + "\n" + diff


def _set_approval(subsystem: str, rest: List[str], set_mode_fn) -> str:
    """Turn the approval gate on/off for a subsystem.

    ``set_mode_fn`` (when provided) persists the new boolean to config.
    """
    if not rest:
        return (f"{_fmt_state(subsystem)}\n"
                f"Изменить: /{subsystem} approval <on|off>")
    arg = rest[0].strip().lower()
    truthy = {"on", "true", "yes", "1", "enable", "enabled"}
    falsey = {"off", "false", "no", "0", "disable", "disabled"}
    if arg in truthy:
        enabled = True
    elif arg in falsey:
        enabled = False
    else:
        return f"Некорректное значение '{arg}'. Допустимо: on или off."
    if set_mode_fn is None:
        val = "true" if enabled else "false"
        return (f"Чтобы изменить настройку, выполните в терминале:\n"
                f"  hermes config set {subsystem}.write_approval {val}")
    try:
        set_mode_fn(enabled)
    except Exception as e:
        return f"Не удалось изменить {subsystem}.write_approval: {e}"
    return _fmt_state_value(subsystem, enabled)

"""Квитанция фонового разбора: что агент сохранил и как это отменить.

Разбор (``agent.background_review``) пишет навыки и память штатными
инструментами. Здесь по его сообщениям и по снимку файлов памяти собирается
короткая квитанция: какие навыки изменены (id записей журнала
``tools.skill_ledger``) и какие записи памяти добавлены или убраны. По ней
веб-чат показывает человеку «Учёл: …» и умеет отменить именно это изменение —
через существующий журнал навыков и файлы памяти, без нового хранилища.
"""

from __future__ import annotations

import json
import uuid
from typing import Any, Dict, List, Optional

RECEIPT_KEY = "learning_receipt"
DISPLAY_KIND = "learning"

_MEMORY_FILES = {"memory": "MEMORY.md", "user": "USER.md"}
_MEMORY_LABELS = {"memory": "память агента", "user": "заметки о вас"}
_ACTION_LABELS = {"create": "создан", "delete": "удалён"}


def snapshot_memory() -> Dict[str, Optional[List[str]]]:
    """Записи встроенной памяти по целям; ``None`` — файл не удалось прочитать."""
    from tools.memory_tool import MemoryStore

    snap: Dict[str, Optional[List[str]]] = {}
    for target in _MEMORY_FILES:
        try:
            entries, ok = MemoryStore._read_entries_checked(MemoryStore._path_for(target))
        except Exception:
            entries, ok = [], False
        snap[target] = list(entries) if ok else None
    return snap


def _multiset_diff(before: List[str], after: List[str]) -> List[str]:
    """Записи из ``after``, которых нет в ``before`` (с учётом повторов)."""
    remaining = list(before)
    extra: List[str] = []
    for entry in after:
        if entry in remaining:
            remaining.remove(entry)
        else:
            extra.append(entry)
    return extra


def _skill_changes(review_messages: List[Dict], prior_snapshot: List[Dict]) -> List[Dict[str, Any]]:
    seen_ids = {
        m.get("tool_call_id")
        for m in prior_snapshot or []
        if isinstance(m, dict) and m.get("role") == "tool" and m.get("tool_call_id")
    }
    calls: Dict[str, Dict[str, Any]] = {}
    for msg in review_messages or []:
        if not isinstance(msg, dict) or msg.get("role") != "assistant":
            continue
        for tc in msg.get("tool_calls", []) or []:
            if not isinstance(tc, dict):
                continue
            fn = tc.get("function", {}) or {}
            if fn.get("name") != "skill_manage" or not tc.get("id"):
                continue
            try:
                args = json.loads(fn.get("arguments", "{}"))
            except (json.JSONDecodeError, TypeError):
                args = {}
            calls[tc["id"]] = args if isinstance(args, dict) else {}

    by_name: Dict[str, Dict[str, Any]] = {}

    def _note(name: Any, action: Any, ledger: Any) -> None:
        name = str(name or "").strip()
        if not name:
            return
        item = by_name.setdefault(name, {"name": name, "action": "update", "entry_ids": []})
        if action in _ACTION_LABELS and item["action"] != "create":
            item["action"] = action
        elif action == "create":
            item["action"] = "create"
        if isinstance(ledger, dict) and ledger.get("entry_id"):
            item["entry_ids"].append(str(ledger["entry_id"]))

    for msg in review_messages or []:
        if not isinstance(msg, dict) or msg.get("role") != "tool":
            continue
        tcid = msg.get("tool_call_id")
        if tcid in seen_ids or tcid not in calls:
            continue
        try:
            data = json.loads(msg.get("content", "{}"))
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(data, dict) or not data.get("success") or data.get("staged"):
            continue
        batch = data.get("results")
        if isinstance(batch, list):
            for res in batch:
                if isinstance(res, dict) and res.get("success"):
                    _note(res.get("name"), res.get("action"), res.get("ledger"))
        else:
            args = calls[tcid]
            _note(args.get("name"), args.get("action"), data.get("ledger"))
    return list(by_name.values())


def build_review_receipt(
    review_messages: List[Dict],
    prior_snapshot: List[Dict],
    memory_before: Optional[Dict[str, Optional[List[str]]]],
) -> Optional[Dict[str, Any]]:
    """Квитанция разбора или ``None``, если ничего не сохранено."""
    skills = _skill_changes(review_messages, prior_snapshot)
    memory: List[Dict[str, Any]] = []
    if memory_before:
        after_snap = snapshot_memory()
        for target, before in memory_before.items():
            after = after_snap.get(target)
            if before is None or after is None:
                continue
            added = _multiset_diff(before, after)
            removed = _multiset_diff(after, before)
            if added or removed:
                memory.append({"target": target, "added": added, "removed": removed})
    if not skills and not memory:
        return None
    return {"version": 1, "id": uuid.uuid4().hex[:12], "skills": skills, "memory": memory}


def _preview(text: str, width: int = 100) -> str:
    one_line = " ".join(str(text).split())
    return one_line if len(one_line) <= width else one_line[: width - 1] + "…"


def format_notice(receipt: Dict[str, Any], mode: str = "on") -> str:
    """Короткое сообщение для чата: что учтено и где."""
    parts: List[str] = []
    for item in receipt.get("skills") or []:
        label = _ACTION_LABELS.get(item.get("action"), "обновлён")
        parts.append(f"навык «{item['name']}» {label}")
    verbose = str(mode or "on").lower() == "verbose"
    for item in receipt.get("memory") or []:
        label = _MEMORY_LABELS.get(item.get("target"), "память")
        if verbose and item.get("added"):
            previews = "; ".join(_preview(e) for e in item["added"][:3])
            parts.append(f"{label}: {previews}")
        else:
            parts.append(f"{label} обновлена")
    return "✅ Учёл из нашего разговора: " + ", ".join(parts) + "."


def receipt_has_undo(receipt: Dict[str, Any]) -> bool:
    return bool(receipt.get("memory")) or any(
        s.get("entry_ids") for s in receipt.get("skills") or []
    )


def _conflict(message: str) -> Dict[str, Any]:
    return {"ok": False, "status": "conflict", "message": message}


def undo_receipt(receipt: Dict[str, Any]) -> Dict[str, Any]:
    """Отменить изменения квитанции. Выполняется в домашнем каталоге профиля.

    Возвращает ``{"ok", "status", "message"}``; ``status`` — ``undone``,
    ``already_undone`` или ``conflict`` (чистая отмена невозможна, ничего не
    изменено). Сначала проверяется всё, и только потом меняются файлы.
    """
    if not isinstance(receipt, dict):
        return _conflict("Для этого сообщения нечего отменять.")
    if receipt.get("undone"):
        return {
            "ok": True,
            "status": "already_undone",
            "message": "Это изменение уже отменено.",
        }

    from tools.memory_tool import ENTRY_DELIMITER, MemoryStore, load_on_disk_store
    from tools.skill_ledger import preflight_rollback_chain, rollback_entry

    skills = [s for s in receipt.get("skills") or [] if isinstance(s, dict)]
    memory = [m for m in receipt.get("memory") or [] if isinstance(m, dict)]
    if not skills and not memory:
        return _conflict("Для этого сообщения нечего отменять.")

    for item in skills:
        ids = [str(i) for i in item.get("entry_ids") or []]
        if not ids:
            return _conflict(
                f"Навык «{item.get('name')}» изменён без записи для отката, "
                "поэтому автоматически вернуть его нельзя. Ничего не изменено."
            )
        ready, _detail = preflight_rollback_chain(ids)
        if not ready:
            return _conflict(
                f"Навык «{item.get('name')}» уже менялся после этого разбора или "
                "его прежняя версия недоступна — отмена затёрла бы более новую "
                "работу. Ничего не изменено; отредактируйте навык вручную на "
                "странице «Навыки»."
            )

    store = load_on_disk_store()
    memory_plans = []
    for item in memory:
        target = item.get("target")
        if target not in _MEMORY_FILES:
            continue
        path = MemoryStore._path_for(target)
        current, ok = MemoryStore._read_entries_checked(path)
        if not ok:
            return _conflict("Не удалось прочитать память агента. Ничего не изменено.")
        planned = list(current)
        for entry in item.get("added") or []:
            if entry not in planned:
                return _conflict(
                    "Запись в памяти уже изменена или удалена после этого разбора, "
                    "поэтому чисто отменить её нельзя. Ничего не изменено; "
                    "поправьте память на странице обучения агента."
                )
            planned.remove(entry)
        for entry in item.get("removed") or []:
            if entry not in planned:
                planned.append(entry)
        limit = store._char_limit(target)
        if len(ENTRY_DELIMITER.join(planned)) > limit:
            return _conflict(
                "Память заполнена новыми записями: прежние уже не поместятся. "
                "Ничего не изменено."
            )
        memory_plans.append((target, path, item))

    for item in skills:
        for entry_id in reversed([str(i) for i in item["entry_ids"]]):
            ok, detail = rollback_entry(entry_id, require_current_match=True)
            if not ok:
                return _conflict(
                    f"Навык «{item.get('name')}» не удалось вернуть полностью: {detail}"
                )
    if skills:
        try:
            from agent.prompt_builder import clear_skills_system_prompt_cache

            clear_skills_system_prompt_cache(clear_snapshot=True)
        except Exception:
            pass

    for _target, path, item in memory_plans:
        with MemoryStore._file_lock(path):
            current, ok = MemoryStore._read_entries_checked(path)
            if not ok:
                return _conflict("Не удалось прочитать память агента при записи.")
            final = list(current)
            for entry in item.get("added") or []:
                if entry in final:
                    final.remove(entry)
            for entry in item.get("removed") or []:
                if entry not in final:
                    final.append(entry)
            MemoryStore._write_file(path, final)

    return {"ok": True, "status": "undone", "message": "Готово, изменение отменено."}

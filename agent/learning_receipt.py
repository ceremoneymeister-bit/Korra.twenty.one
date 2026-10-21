"""Квитанция фонового разбора: что агент сохранил и как это отменить.

Разбор (``agent.background_review``) пишет навыки и память штатными
инструментами. Здесь по сообщениям разбора (ответы вызовов навыков и памяти) собирается
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


def _seen_tool_ids(prior_snapshot: List[Dict]) -> set:
    return {
        m.get("tool_call_id")
        for m in prior_snapshot or []
        if isinstance(m, dict) and m.get("role") == "tool" and m.get("tool_call_id")
    }


def _skill_changes(review_messages: List[Dict], prior_snapshot: List[Dict]) -> List[Dict[str, Any]]:
    seen_ids = _seen_tool_ids(prior_snapshot)
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
    review_messages: List[Dict], prior_snapshot: List[Dict]
) -> Optional[Dict[str, Any]]:
    """Квитанция разбора или ``None``, если ничего не сохранено.

    Память — только то, что вернули вызовы ``memory`` самого разбора (сообщения
    форка); без сообщений форка память в квитанцию не попадает.
    """
    return _assemble(
        _skill_changes(review_messages, prior_snapshot),
        _memory_changes(review_messages, prior_snapshot, _last_user_text(prior_snapshot)),
    )


def current_turn_messages(messages: List[Dict]) -> List[Dict]:
    """Сообщения после последнего сообщения пользователя — то, что сделал текущий ход."""
    for index in range(len(messages or []) - 1, -1, -1):
        msg = messages[index]
        if isinstance(msg, dict) and msg.get("role") == "user":
            return list(messages[index + 1 :])
    return []


def _cancel(added: List[str], removed: List[str]) -> None:
    """Запись, добавленная и убранная в одной цепочке изменений, из обоих списков уходит."""
    for entry in list(removed):
        if entry in added:
            added.remove(entry)
            removed.remove(entry)


def _last_user_text(messages: List[Dict]) -> str:
    for msg in reversed(messages or []):
        if isinstance(msg, dict) and msg.get("role") == "user":
            content = msg.get("content") or ""
            if isinstance(content, list):
                content = " ".join(p.get("text", "") for p in content if isinstance(p, dict))
            return " ".join(str(content).casefold().split())
    return ""


def _notice_operations(args: Dict, data: Dict, request: str) -> List[Dict]:
    """Происхождение записей для показа; added/removed остаются контрактом отмены.

    Сохраняем реальные связи, а не угадываем похожесть текстов: новая запись
    может пройти несколько замен в этом ходе. Названный человеком old_text
    защищает соответствующую цепочку от скрытия как служебного сжатия.
    """
    batch = args.get("operations")
    operations = batch or [args]
    result = []
    for index, op in enumerate(operations):
        if not isinstance(op, dict):
            continue
        action = op.get("action")
        old = None
        new = str(op.get("content") or op.get("new_text") or "").strip() or None
        if action in {"replace", "remove"}:
            if batch:
                entries = data.get("replaced_entries" if action == "replace" else "removed_entries") or {}
                old = entries.get(str(index), entries.get(index))
            elif action == "replace":
                old = data.get("replaced_entry")
            else:
                entries = data["changes"].get("removed") or []
                old = entries[0] if len(entries) == 1 else None
            if old is None:
                continue  # Older tool results lack per-operation provenance.
        elif action != "add":
            continue
        if action == "remove":
            new = None
        needle = " ".join(str(op.get("old_text") or "").casefold().split())
        result.append({"old": old, "new": new, "requested": bool(needle and needle in request)})
    return result


def _memory_changes(
    turn_messages: List[Dict], prior_snapshot: Optional[List[Dict]] = None, request: str = ""
) -> List[Dict[str, Any]]:
    """Изменения памяти, которые вернули успешные вызовы ``memory`` этого хода или разбора.

    Инструмент в ответе перечисляет, какие записи он добавил и убрал
    (``changes``), поэтому чужие записи других чатов в квитанцию не попадают.
    """
    seen_ids = _seen_tool_ids(prior_snapshot or [])
    memory_calls = {}
    for msg in turn_messages:
        if isinstance(msg, dict) and msg.get("role") == "assistant":
            for tc in msg.get("tool_calls", []) or []:
                fn = (tc.get("function") or {}) if isinstance(tc, dict) else {}
                if fn.get("name") == "memory":
                    try:
                        args = json.loads(fn.get("arguments") or "{}")
                    except (json.JSONDecodeError, TypeError):
                        args = {}
                    memory_calls[tc.get("id")] = args if isinstance(args, dict) else {}
    by_target: Dict[str, Dict[str, Any]] = {}
    for msg in turn_messages:
        if not isinstance(msg, dict) or msg.get("role") != "tool":
            continue
        if msg.get("tool_call_id") not in memory_calls or msg.get("tool_call_id") in seen_ids:
            continue
        try:
            data = json.loads(msg.get("content", "{}"))
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(data, dict) or not data.get("success") or data.get("staged"):
            continue
        changes = data.get("changes")
        target = data.get("target")
        if target not in _MEMORY_FILES or not isinstance(changes, dict):
            continue
        item = by_target.setdefault(target, {"target": target, "added": [], "removed": [], "notice_operations": []})
        item["added"].extend(str(e) for e in changes.get("added") or [])
        item["removed"].extend(str(e) for e in changes.get("removed") or [])
        _cancel(item["added"], item["removed"])
        operations = _notice_operations(memory_calls[msg.get("tool_call_id")], data, request)
        # Even an empty list distinguishes explicit edits from legacy deltas.
        item["notice_operations"].extend(operations)
    return [item for item in by_target.values() if item["added"] or item["removed"]]


def build_turn_receipt(messages: List[Dict]) -> Optional[Dict[str, Any]]:
    """Квитанция хода, в котором агент сам записал память или навык.

    Берутся только вызовы текущего хода и только то, что они сами изменили, а не
    разница файлов за время хода: параллельные записи других чатов и фонового
    разбора этому ходу не приписываются.
    """
    turn = current_turn_messages(messages)
    return _assemble(_skill_changes(turn, []), _memory_changes(turn, request=_last_user_text(messages)))


def merge_receipts(base: Dict[str, Any], extra: Dict[str, Any]) -> Dict[str, Any]:
    """Объединить две квитанции одного хода в одну (одно сообщение «Учёл»)."""
    skills: Dict[str, Dict[str, Any]] = {}
    for item in list(base.get("skills") or []) + list(extra.get("skills") or []):
        known = skills.get(item["name"])
        if known is None:
            skills[item["name"]] = {**item, "entry_ids": list(item.get("entry_ids") or [])}
            continue
        known["entry_ids"].extend(item.get("entry_ids") or [])
        if item.get("action") == "create":
            known["action"] = "create"
    memory: Dict[str, Dict[str, Any]] = {}
    for item in list(base.get("memory") or []) + list(extra.get("memory") or []):
        known = memory.setdefault(
            item["target"], {"target": item["target"], "added": [], "removed": []}
        )
        known["added"].extend(item.get("added") or [])
        known["removed"].extend(item.get("removed") or [])
        _cancel(known["added"], known["removed"])
        if "notice_operations" in item:
            known.setdefault("notice_operations", []).extend(item["notice_operations"])
    return {
        "version": 1,
        "id": base.get("id") or extra.get("id"),
        "skills": list(skills.values()),
        "memory": [m for m in memory.values() if m["added"] or m["removed"]],
    }


def _assemble(
    skills: List[Dict[str, Any]], memory: List[Dict[str, Any]]
) -> Optional[Dict[str, Any]]:
    if not skills and not memory:
        return None
    return {"version": 1, "id": uuid.uuid4().hex[:12], "skills": skills, "memory": memory}


_NOTICE_LIMIT = 200
_NOTICE_LIMIT_VERBOSE = 600
_NOTICE_MAX_ENTRIES = 3

# Предложный («в …») и родительный («из …») падеж мест хранения.
_PLACE_IN = {"user": "в заметках о вас", "memory": "в памяти агента"}
_PLACE_FROM = {"user": "из заметок о вас", "memory": "из памяти агента"}
_SKILL_VERBS = {"create": "создан", "delete": "удалён"}


def _clip(text: Any, limit: int) -> str:
    """Одна строка, обрезанная по слову до ``limit`` знаков."""
    one_line = " ".join(str(text).split())
    if len(one_line) <= limit:
        return one_line
    cut = one_line[:limit]
    space = cut.rfind(" ")
    if space >= limit // 2:
        cut = cut[:space]
    return cut.rstrip(" ,;:.-—–") + "…"


def _quoted(entries: List[str], limit: int) -> str:
    shown = [f"«{_clip(e, limit)}»" for e in entries[:_NOTICE_MAX_ENTRIES]]
    text = "; ".join(shown)
    rest = len(entries) - _NOTICE_MAX_ENTRIES
    if rest > 0:
        text += f" и ещё {rest}"
    return text


def _memory_sentence(item: Dict[str, Any], limit: int) -> str:
    target = item.get("target")
    place_in = _PLACE_IN.get(target, "в памяти")
    place_from = _PLACE_FROM.get(target, "из памяти")
    added = [e for e in item.get("added") or [] if str(e).strip()]
    removed = [e for e in item.get("removed") or [] if str(e).strip()]
    compacted = set()
    hidden = set()
    new_facts = set()
    operations = item.get("notice_operations") or []
    for index, operation in enumerate(operations):
        start, end = operation["old"], operation["new"]
        requested = operation["requested"]
        for later in operations[index + 1:]:
            if end is not None and later["old"] == end:
                end = later["new"]
                requested = requested or later["requested"]
        if start is None:
            # A duplicate add of an existing, subsequently replaced note is
            # not a new fact. A newly added then refined fact is.
            if operation["new"] not in removed and end in added:
                new_facts.add(end)
        elif start in removed and not requested:
            if end is None or (end in added and len(end) < len(start)):
                compacted.add(start)
                hidden.add(end)
    if not new_facts.difference(hidden):
        # Edits/deletions without a separate addition still name what changed.
        compacted.clear()
        hidden.clear()
    if "notice_operations" not in item and len(added) == 1 and len(removed) > 1:
        # Old persisted receipts have only the aggregate delta. A many-to-one
        # consolidation must not quote every old note as the new fact's past.
        compacted.update(removed)
    added = [e for e in added if e not in hidden]
    removed = [e for e in removed if e not in compacted]
    suffix = ""
    if compacted:
        count = len(compacted)
        form = "старых заметок"
        if count % 100 not in range(11, 15):
            if count % 10 == 1:
                form = "старую заметку"
            elif count % 10 in (2, 3, 4):
                form = "старые заметки"
        suffix = f"Освободил место: сжал {count} {form}."
    if added and removed:
        sentence = f"{_quoted(added, limit)} (вместо {_quoted(removed, limit)}). Обновлено {place_in}."
    elif added:
        sentence = f"{_quoted(added, limit)}. Сохранено {place_in}."
    elif removed:
        sentence = f"{_quoted(removed, limit)}. Удалено {place_from}."
    else:
        sentence = f"Обновлено {place_in}."
    return " ".join(part for part in (sentence, suffix) if part)


def format_notice(receipt: Dict[str, Any], mode: str = "on") -> str:
    """Короткое сообщение для чата: что именно усвоено и где."""
    limit = _NOTICE_LIMIT_VERBOSE if str(mode or "on").lower() == "verbose" else _NOTICE_LIMIT
    sentences: List[str] = []
    for item in receipt.get("skills") or []:
        verb = _SKILL_VERBS.get(item.get("action"), "обновлён")
        sentences.append(f"навык «{item['name']}» {verb}.")
    for item in receipt.get("memory") or []:
        sentences.append(_memory_sentence(item, limit))
    if not sentences:
        return "✅ Учёл."
    text = " ".join(
        sentence if index == 0 else sentence[0].upper() + sentence[1:]
        for index, sentence in enumerate(sentences)
    )
    return f"✅ Учёл: {text}"


def receipt_has_undo(receipt: Dict[str, Any]) -> bool:
    return bool(receipt.get("memory")) or any(
        s.get("entry_ids") for s in receipt.get("skills") or []
    )


def _undo_message(skills: List[Dict[str, Any]], memory: List[Dict[str, Any]]) -> str:
    """Один ясный итог отмены: что именно удалено и что возвращено."""
    parts: List[str] = []
    for item in skills:
        name = item.get("name")
        if item.get("action") == "create":
            parts.append(f"навык «{name}» удалён")
        else:
            parts.append(f"навык «{name}» возвращён к прежней версии")
    for item in memory:
        user = item.get("target") == "user"
        noun_one, noun_many = ("заметка", "заметки") if user else ("запись памяти", "записи памяти")
        added = len(item.get("added") or [])
        removed = len(item.get("removed") or [])
        count = max(added, removed)
        many = count > 1
        noun = noun_many if many else noun_one
        suffix = f" ({count})" if many else ""
        if added and removed:
            verb = "возвращены" if many else "возвращена"
            parts.append(f"{noun} {verb} к прежней версии{suffix}")
        elif added:
            parts.append(f"{noun} {'удалены' if many else 'удалена'}{suffix}")
        elif removed:
            parts.append(f"{noun} {'возвращены' if many else 'возвращена'}{suffix}")
    return "Отменено: " + "; ".join(parts) if parts else "Отменено."


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

    return {"ok": True, "status": "undone", "message": _undo_message(skills, memory)}

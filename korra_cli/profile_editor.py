"""Changes the main agent makes to the installation's agents for the owner.

The owner improves their agents by asking the main agent: "make the lawyer
answer shorter", "remember this for every agent", "give the designer our
brand book". This module is the only write path for that: role (SOUL.md),
memory, reference materials, display name and description.

Every change lands in one installation journal with what is needed to undo
it. Secrets, model, tools, channels, approvals and schedules have no code
path here on purpose: they stay in Settings, where the owner sees them.
Who may call this is decided by the tool layer (``tools/manage_agents_tool``).
"""

from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

SOUL_MAX_CHARS = 40_000
#: Above this the role may be shortened when it is loaded into a small context.
SOUL_SOFT_CHARS = 20_000
DESCRIPTION_MAX_CHARS = 500
JOURNAL_DIR = "agent-changes"
#: Role and memory are read when a chat starts; a long Telegram chat keeps
#: the old ones until the owner starts a new one.
APPLIES = "в новых чатах этого агента; в Telegram — после команды /new"


class ProfileEditError(ValueError):
    """The request cannot be applied as asked."""


class ProfileEditConflict(ProfileEditError):
    """The agent changed since the caller looked; read it again."""


def _root() -> Path:
    from korra_constants import get_default_hermes_root

    return Path(get_default_hermes_root())


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _version(text: str) -> str:
    return _sha(text)[:16]


# ---------------------------------------------------------------------------
# Agents
# ---------------------------------------------------------------------------


def _agents() -> list[dict]:
    from korra_cli.profiles import list_profiles, read_profile_meta

    out = []
    for info in list_profiles():
        meta = read_profile_meta(info.path)
        out.append({
            "agent": info.name,
            "name": meta["display_name"] or info.name,
            "description": meta["description"],
            "model": info.model,
            "main": bool(info.is_default),
            "path": info.path,
        })
    return out


def _resolve(agent: str) -> tuple[str, Path]:
    """An agent by its id or by the name the owner sees."""
    wanted = str(agent or "").strip()
    if not wanted:
        raise ProfileEditError("Укажите агента: его id или название из списка.")
    agents = _agents()
    for item in agents:
        if item["agent"] == wanted.lower():
            return item["agent"], item["path"]
    named = [item for item in agents if item["name"].casefold() == wanted.casefold()]
    if len(named) == 1:
        return named[0]["agent"], named[0]["path"]
    if len(named) > 1:
        raise ProfileEditError(f"Название «{wanted}» у нескольких агентов. Укажите id: " + ", ".join(i["agent"] for i in named))
    raise ProfileEditError(f"Агент «{wanted}» не найден. Посмотрите список агентов.")


@contextlib.contextmanager
def _scoped(home: Path) -> Iterator[None]:
    """Resolve memory, skills and config of ``home`` for this call only.

    The context-local override reaches every call-time resolver
    (``get_hermes_home``) without touching module globals, so parallel turns
    of other agents in the same process keep their own homes.
    """
    from korra_constants import reset_hermes_home_override, set_hermes_home_override

    token = set_hermes_home_override(str(home))
    try:
        yield
    finally:
        reset_hermes_home_override(token)


def _clear_skills_cache() -> None:
    try:
        from agent.prompt_builder import clear_skills_system_prompt_cache

        clear_skills_system_prompt_cache()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Journal
# ---------------------------------------------------------------------------


def _journal_dir() -> Path:
    path = _root() / JOURNAL_DIR
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    return path


@contextlib.contextmanager
def _locked() -> Iterator[None]:
    """One change at a time across the gateway and the dashboard processes."""
    with open(_journal_dir() / ".lock", "a", encoding="utf-8") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def _entries() -> list[dict]:
    path = _journal_dir() / "journal.jsonl"
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            item = json.loads(line)
        except ValueError:
            continue
        if isinstance(item, dict) and item.get("id"):
            out.append(item)
    return out


def _record(agent: str, kind: str, summary: str, reason: str, **data) -> dict:
    entry = {"id": uuid.uuid4().hex[:12], "at": _now(), "agent": agent, "kind": kind,
             "summary": summary, **({"reason": reason.strip()[:500]} if reason and reason.strip() else {}), **data}
    path = _journal_dir() / "journal.jsonl"
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    return entry


def _snapshot(change_id: str, name: str, text: str) -> str:
    folder = _journal_dir() / "snapshots" / change_id
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    (folder / name).write_text(text, encoding="utf-8")
    return str((folder / name).relative_to(_journal_dir()))


def _done(entry: dict, **extra) -> dict:
    return {"ok": True, "change_id": entry["id"], "agent": entry["agent"], "summary": entry["summary"], **extra}


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------


def list_agents() -> dict:
    return {"agents": [{k: v for k, v in item.items() if k != "path"} for item in _agents()]}


def show_agent(agent: str) -> dict:
    from korra_cli import profile_learning

    canon, home = _resolve(agent)
    meta = next(item for item in _agents() if item["agent"] == canon)
    soul_path = home / "SOUL.md"
    soul = soul_path.read_text(encoding="utf-8") if soul_path.exists() else ""
    with _scoped(home):
        memory = profile_learning.read_memory()
        materials = profile_learning.list_materials()["materials"]
    return {
        "agent": canon, "name": meta["name"], "description": meta["description"],
        "model": meta["model"], "main": meta["main"],
        "role": soul, "role_version": _version(soul), "role_chars": len(soul),
        "memory": memory["memory"], "user": memory["user"],
        "memory_limits": memory["limits"], "memory_used": memory["used"],
        "materials": [{"material": m["name"], "title": m["title"], "kind": m["kind"]} for m in materials],
    }


def history(agent: str = "", limit: int = 20) -> dict:
    canon = _resolve(agent)[0] if agent else ""
    entries = _entries()
    undone = {e["reverts"] for e in entries if e.get("kind") == "undo" and e.get("reverts")}
    picked = [e for e in reversed(entries) if not canon or e["agent"] == canon][: max(1, min(int(limit or 20), 100))]
    return {"changes": [{"change_id": e["id"], "at": e["at"], "agent": e["agent"], "kind": e["kind"],
                         "summary": e["summary"], "undone": e["id"] in undone,
                         **({"reason": e["reason"]} if e.get("reason") else {})} for e in picked]}


# ---------------------------------------------------------------------------
# Role
# ---------------------------------------------------------------------------


def _check_role(text: str) -> None:
    from tools.threat_patterns import scan_for_threats

    if not text.strip():
        raise ProfileEditError("Роль не может быть пустой.")
    if len(text) > SOUL_MAX_CHARS:
        raise ProfileEditError(f"Роль длиннее {SOUL_MAX_CHARS} символов. Сократите её; справочные сведения лучше добавить материалом.")
    # Same input as agent/prompt_builder._scan_context_content, which strips
    # a leading UTF-8 BOM (an editor artifact) before scanning.
    findings = scan_for_threats(text[1:] if text.startswith("\ufeff") else text, scope="context")
    if findings:
        # prompt_builder replaces such a SOUL.md with a BLOCKED placeholder at
        # load time: writing it would silently leave the agent without a role.
        raise ProfileEditError("Текст роли похож на встроенные команды (" + ", ".join(findings) + "), агент не сможет его загрузить. Переформулируйте обычными словами.")


def _write_role(path: Path, text: str) -> None:
    from utils import atomic_write_text

    atomic_write_text(path, text, preserve_mode=True, create_mode=0o644)


def update_role(agent: str, *, content: str | None = None, old_text: str = "", new_text: str = "",
                version: str = "", reason: str = "") -> dict:
    canon, home = _resolve(agent)
    path = home / "SOUL.md"
    with _locked():
        before = path.read_text(encoding="utf-8") if path.exists() else ""
        if version and version != _version(before):
            raise ProfileEditConflict("Роль агента изменилась после просмотра. Откройте её заново и повторите правку.")
        if content is not None:
            if old_text:
                raise ProfileEditError("Передайте либо весь новый текст роли, либо фрагмент и его замену.")
            after = content
        else:
            if not old_text:
                raise ProfileEditError("Укажите весь новый текст роли или фрагмент old_text и замену new_text.")
            count = before.count(old_text)
            if count == 0:
                raise ProfileEditConflict("Фрагмент не найден в текущей роли. Откройте роль заново и скопируйте фрагмент точно.")
            if count > 1:
                raise ProfileEditError(f"Фрагмент встречается {count} раза. Возьмите более длинный, уникальный фрагмент.")
            after = before.replace(old_text, new_text, 1)
        if after == before:
            return {"ok": True, "changed": False, "agent": canon}
        _check_role(after)
        change_id = uuid.uuid4().hex[:12]
        snapshot_before = _snapshot(change_id, "SOUL.before.md", before)
        snapshot_after = _snapshot(change_id, "SOUL.after.md", after)
        _write_role(path, after)
        try:
            entry = _record(canon, "role", "Изменена роль агента", reason,
                            before_sha=_sha(before), after_sha=_sha(after),
                            before=snapshot_before, after=snapshot_after)
        except Exception:
            _write_role(path, before)  # an unjournaled change could not be undone
            raise
    extra = {"role_version": _version(after), "applies": APPLIES}
    if len(after) > SOUL_SOFT_CHARS:
        extra["warning"] = f"Роль длиннее {SOUL_SOFT_CHARS} символов: в небольшом контексте середина может сокращаться."
    return _done(entry, **extra)


# ---------------------------------------------------------------------------
# Memory
# ---------------------------------------------------------------------------

_MEMORY_INVERSE = {"add": "remove", "remove": "add", "replace": "replace"}


def _change_memory(home: Path, action: str, target: str, content: str, old_text: str) -> None:
    from korra_cli import profile_learning

    with _scoped(home):
        try:
            profile_learning.change_memory(action, target, content, old_text)
        except profile_learning.LearningConflict as exc:
            raise ProfileEditConflict(str(exc)) from None


def _undo_memory(home: Path, action: str, target: str, content: str, old_text: str) -> None:
    if action == "add":
        _change_memory(home, "remove", target, "", content)
    elif action == "remove":
        _change_memory(home, "add", target, old_text, "")
    else:
        _change_memory(home, "replace", target, old_text, content)


def change_memory(agent: str, *, action: str, target: str = "memory", content: str = "",
                  old_text: str = "", reason: str = "") -> dict:
    canon, home = _resolve(agent)
    if action not in _MEMORY_INVERSE:
        raise ProfileEditError("memory_action: add, replace или remove.")
    with _locked():
        if action == "add" or (action == "replace" and content.strip() == old_text.strip()):
            from korra_cli import profile_learning

            with _scoped(home):
                entries = profile_learning.read_memory().get(target, [])
            if content.strip() in entries:
                # MemoryStore reports success for a duplicate without writing;
                # journaling it would make undo delete the entry that was there.
                return {"ok": True, "changed": False, "agent": canon, "message": "Агент уже помнит это."}
        _change_memory(home, action, target, content, old_text)
        labels = {"add": "Добавлено в память", "replace": "Изменена запись памяти", "remove": "Удалено из памяти"}
        where = "о пользователе" if target == "user" else "агента"
        try:
            entry = _record(canon, "memory", f"{labels[action]} ({where})", reason,
                            action=action, target=target, content=content, old_text=old_text)
        except Exception:
            _undo_memory(home, action, target, content, old_text)
            raise
    return _done(entry, applies=APPLIES)


# ---------------------------------------------------------------------------
# Materials
# ---------------------------------------------------------------------------


def add_material(agent: str, *, title: str, text: str = "", url: str = "", reason: str = "") -> dict:
    from korra_cli import profile_learning

    canon, home = _resolve(agent)
    with _locked(), _scoped(home):
        created = profile_learning.create_material(title, text, url)
        entry = _record(canon, "material_add", f"Добавлен материал «{title.strip()}»", reason,
                        material=created["name"], title=title.strip())
    _clear_skills_cache()
    return _done(entry, material=created["name"], applies=APPLIES)


def remove_material(agent: str, *, material: str, reason: str = "") -> dict:
    from korra_cli import profile_learning

    canon, home = _resolve(agent)
    with _locked(), _scoped(home):
        known = {m["name"]: m for m in profile_learning.list_materials()["materials"]}
        if material not in known:
            raise ProfileEditError("У агента нет такого материала. Посмотрите список материалов.")
        removed = profile_learning.delete_material(material)
        entry = _record(canon, "material_remove", f"Убран материал «{known[material]['title']}»", reason,
                        material=material, title=known[material]["title"],
                        archived_to=removed.get("archived_to", ""))
    _clear_skills_cache()
    return _done(entry)


# ---------------------------------------------------------------------------
# Name and description
# ---------------------------------------------------------------------------


def _meta(home: Path) -> dict:
    from korra_cli.profiles import read_profile_meta

    return read_profile_meta(home)


def rename(agent: str, *, value: str, reason: str = "") -> dict:
    from korra_cli.profiles import set_profile_display_name

    canon, home = _resolve(agent)
    with _locked():
        before = _meta(home)["display_name"]
        after = set_profile_display_name(canon, value)
        if after == before:
            return {"ok": True, "changed": False, "agent": canon}
        entry = _record(canon, "name", f"Название: «{before or canon}» → «{after or canon}»", reason,
                        before=before, after=after)
    return _done(entry)


def describe(agent: str, *, value: str, reason: str = "") -> dict:
    from korra_cli.profiles import write_profile_meta

    canon, home = _resolve(agent)
    value = str(value or "").strip()
    if len(value) > DESCRIPTION_MAX_CHARS:
        raise ProfileEditError(f"Описание длиннее {DESCRIPTION_MAX_CHARS} символов.")
    with _locked():
        meta = _meta(home)
        if value == meta["description"]:
            return {"ok": True, "changed": False, "agent": canon}
        write_profile_meta(home, description=value, description_auto=False)
        entry = _record(canon, "description", "Изменено описание агента", reason,
                        before=meta["description"], before_auto=meta["description_auto"], after=value)
    return _done(entry)


# ---------------------------------------------------------------------------
# Undo
# ---------------------------------------------------------------------------


def undo(change_id: str, reason: str = "") -> dict:
    from korra_cli import profile_learning
    from korra_cli.profiles import set_profile_display_name, write_profile_meta

    change_id = str(change_id or "").strip()
    with _locked():
        entries = _entries()
        entry = next((e for e in entries if e["id"] == change_id), None)
        if entry is None:
            raise ProfileEditError("Изменение не найдено. Посмотрите историю изменений.")
        if entry["kind"] == "undo":
            raise ProfileEditError("Отмену не отменяют. Повторите исходное изменение заново.")
        if any(e.get("kind") == "undo" and e.get("reverts") == change_id for e in entries):
            raise ProfileEditError("Это изменение уже отменено.")
        canon, home = _resolve(entry["agent"])
        kind = entry["kind"]
        if kind == "role":
            path = home / "SOUL.md"
            current = path.read_text(encoding="utf-8") if path.exists() else ""
            if _sha(current) != entry["after_sha"]:
                raise ProfileEditConflict("Роль менялась после этого изменения. Откройте её и исправьте нужное место отдельной правкой.")
            _write_role(path, (_journal_dir() / entry["before"]).read_text(encoding="utf-8"))
        elif kind == "memory":
            _undo_memory(home, entry["action"], entry["target"], entry["content"], entry["old_text"])
        elif kind == "material_add":
            with _scoped(home):
                profile_learning.delete_material(entry["material"])
            _clear_skills_cache()
        elif kind == "material_remove":
            if not entry.get("archived_to"):
                raise ProfileEditError("Для этого материала нет архивной копии.")
            with _scoped(home):
                try:
                    profile_learning.restore_material(entry["material"], entry["archived_to"])
                except profile_learning.LearningConflict as exc:
                    raise ProfileEditConflict(str(exc)) from None
            _clear_skills_cache()
        elif kind == "name":
            if _meta(home)["display_name"] != entry["after"]:
                raise ProfileEditConflict("Название менялось после этого изменения.")
            set_profile_display_name(canon, entry["before"])
        elif kind == "description":
            if _meta(home)["description"] != entry["after"]:
                raise ProfileEditConflict("Описание менялось после этого изменения.")
            write_profile_meta(home, description=entry["before"], description_auto=entry.get("before_auto", False))
        else:
            raise ProfileEditError("Это изменение нельзя отменить автоматически.")
        record = _record(canon, "undo", "Отменено: " + entry["summary"], reason, reverts=change_id)
    return _done(record, reverted=change_id)

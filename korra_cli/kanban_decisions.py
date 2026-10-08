"""Project board decisions into the existing chat approval interface.

The board event is the identity and the board transaction owns the answer.
There is no second approval record or execution queue here.
"""
from __future__ import annotations

import base64
import json
import sqlite3
from pathlib import Path
from urllib.parse import urlencode

from korra_cli import kanban_db as kb
from tools.effect_decisions import EffectDecisionStoreUnavailable


class KanbanDecisionConflict(ValueError):
    pass


def decision_id(board: str, task_id: str, version: int, kind: str) -> str:
    raw = json.dumps([board, task_id, version, kind], separators=(",", ":")).encode()
    return "kb_" + base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _identity(request_id: str) -> tuple[str, str, int, str]:
    try:
        raw = request_id.removeprefix("kb_")
        board, task_id, version, kind = json.loads(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)))
        if (not request_id.startswith("kb_") or not isinstance(board, str)
                or kb._normalize_board_slug(board) != board
                or not isinstance(task_id, str) or not task_id.startswith("t_")
                or not isinstance(version, int) or isinstance(version, bool) or version <= 0
                or kind not in {"question", "approval", "accept"}):
            raise ValueError()
        return board, task_id, version, kind
    except (ValueError, TypeError, UnicodeError):
        raise KanbanDecisionConflict("Некорректная версия вопроса.") from None


def project_task(conn, task, board: str) -> dict | None:
    if task.status == "review" and task.acceptance == "owner":
        kind, version = "accept", kb.submitted_version(conn, task.id)
        question = "Проверьте результат и примите его или верните с замечанием.\n\n" + (task.result or "Результат в карточке.")
    elif ((task.status == "blocked" and task.block_kind != kb.OWNER_PAUSE_KIND)
          or (task.status == "triage" and task.block_kind and task.block_kind != kb.OWNER_PAUSE_KIND)):
        kind = "approval" if task.block_kind == "approval" else "question"
        version = kb.block_revision(conn, task.id)
        question = task.block_reason or "Нужен ваш ответ."
    else:
        return None
    if version is None or not task.session_id:
        return None
    event = conn.execute("SELECT created_at FROM task_events WHERE id=?", (version,)).fetchone()
    url = "/kanban?" + urlencode({"board": board, "task": task.id})
    choices = ["once"] if kind == "question" else ["once", "deny"]
    if kind == "question" and (task.block_kind == "capability" or task.status == "triage"):
        # Continuing "as proposed" changes nothing here: the card needs words.
        choices = []
    description = "Ждёт вас. Ответ относится только к показанной версии поручения."
    if kind == "accept" and not task.assignee:
        choices = ["once"]
        description += " Для доработки сначала назначьте исполнителя в карточке."
    return {
        "request_id": decision_id(board, task.id, version, kind),
        "decision_kind": "kanban_" + kind,
        "source_session_id": task.session_id,
        "command": task.title + "\n\n" + question,
        "description": description,
        "task_url": url,
        "board_version": version,
        "allow_session": False, "allow_permanent": False,
        "choices": choices,
        "timestamp": event["created_at"] if event else task.created_at,
        "created_at": event["created_at"] if event else task.created_at,
        "updated_at": event["created_at"] if event else task.created_at,
        "id": decision_id(board, task.id, version, kind),
    }


def list_pending(session_ids: set[str] | None = None) -> list[dict]:
    """Read existing boards; never create a database just by polling a chat."""
    result = []
    seen_paths = set()
    try:
        for board in kb.list_boards(include_archived=False):
            slug = board["slug"]
            path = kb.kanban_db_path(board=slug)
            if not path.is_file() or path.resolve() in seen_paths:
                continue
            seen_paths.add(path.resolve())
            conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=1)
            conn.row_factory = sqlite3.Row
            try:
                for row in conn.execute("SELECT * FROM tasks WHERE session_id IS NOT NULL AND status IN ('blocked','triage','review')"):
                    if session_ids is not None and row["session_id"] not in session_ids:
                        continue
                    item = project_task(conn, kb.Task.from_row(row), slug)
                    if item:
                        result.append(item)
            finally:
                conn.close()
    except (OSError, sqlite3.Error) as exc:
        # Same polling contract as exact-effect decisions: unreadable is not
        # an empty list of questions that would invite repeating the action.
        raise EffectDecisionStoreUnavailable() from exc
    return result


def session_ids(session_id: str, home: Path | None = None) -> set[str]:
    """Allow only compression continuations of the original owner chat."""
    from korra_constants import get_hermes_home
    from korra_cli.web_server import _open_session_db_at_path

    path = (home or Path(get_hermes_home())) / "state.db"
    if not path.is_file():
        return set()
    db = _open_session_db_at_path(path, read_only=True)
    try:
        row = db.get_session(session_id)
        if not row or row.get("source") in {"kanban", "agent_service", "subagent", "cron", "tool"}:
            return set()
        return set(db.get_compression_lineage(session_id))
    finally:
        db.close()


def resolve(request_id: str, choice: str, *, source_session_id: str, answer: str = "", allowed_sessions: set[str] | None = None) -> dict:
    """Called only by an authenticated human endpoint/callback, never a tool."""
    if choice not in {"once", "deny"}:
        raise KanbanDecisionConflict("Выберите ответ только для этой версии.")
    board, task_id, version, kind = _identity(request_id)
    allowed = allowed_sessions if allowed_sessions is not None else session_ids(source_session_id)
    if not kb.kanban_db_path(board=board).is_file():
        raise KanbanDecisionConflict("Карточка больше недоступна.")
    with kb.connect_closing(board=board) as conn:
        task = kb.get_task(conn, task_id)
        if task is None or task.session_id not in allowed:
            raise KanbanDecisionConflict("Поручение не относится к этому чату.")
        # Same request identity across chat, Telegram and retries. Board CAS also
        # rejects a different channel's answer after the first transition.
        action_id = request_id + ":" + choice
        if kind == "accept":
            if choice == "once":
                outcome = kb.accept_result(conn, task_id, author="Владелец", version=version, request_id=action_id)
            else:
                if not answer.strip():
                    raise KanbanDecisionConflict("Напишите, что исправить, чтобы вернуть результат агенту.")
                outcome = kb.return_for_rework(conn, task_id, author="Владелец", version=version, request_id=action_id, comment=answer)
        else:
            if kind == "question" and choice != "once":
                raise KanbanDecisionConflict("На этот вопрос нужен ответ.")
            outcome = kb.respond_to_block(
                conn, task_id, author="Владелец", request_id=action_id, revision=version,
                answer=answer.strip() or (kb.CONTINUE_AS_PROPOSED if kind == "question" else None),
                decision=("grant" if choice == "once" else "deny") if kind == "approval" else None,
            )
        if not outcome["ok"]:
            if outcome.get("reason") == "answer_required":
                raise KanbanDecisionConflict("Напишите, что изменить или добавить: без этого поручение снова остановится.")
            if outcome.get("reason") == "assignee_required":
                raise KanbanDecisionConflict("Назначьте исполнителя перед возвратом на доработку.")
            raise KanbanDecisionConflict("Вопрос уже решён или версия изменилась. Откройте текущую карточку.")
        return outcome


def subscribe_telegram_owners(conn, task_id: str, profile: str, *, config: dict | None = None,
                             board: str = "default", catch_up_current: bool = False) -> None:
    """Reuse existing subscriptions for verified owners, including pre-update waits.

    Only callers with a known web source/profile use this helper. New routes
    catch up on the current unresolved question, never on historical events.
    """
    from gateway.credential_management import configured_owners, installation_owners
    if config is None:
        from korra_constants import set_hermes_home_override, reset_hermes_home_override
        from korra_cli.profiles import resolve_profile_env
        from korra_cli.config import read_raw_config_readonly
        token = set_hermes_home_override(resolve_profile_env(profile))
        try:
            config = read_raw_config_readonly()
        finally:
            reset_hermes_home_override(token)
    owners = configured_owners(config, "telegram") | installation_owners("telegram")
    task = kb.get_task(conn, task_id)
    pending = project_task(conn, task, board) if catch_up_current and task else None
    for owner in sorted(owners):
        if not owner.isdecimal():
            continue
        exists = conn.execute(
            "SELECT 1 FROM kanban_notify_subs WHERE task_id=? AND platform='telegram' AND chat_id=? AND thread_id=''",
            (task_id, owner),
        ).fetchone()
        if exists:
            continue
        kb.add_notify_sub(conn, task_id=task_id, platform="telegram", chat_id=owner,
                          user_id=owner, chat_type="dm", notifier_profile=profile, delivery_mode="notify",
                          initial_event_id=pending["board_version"] - 1 if pending else None)

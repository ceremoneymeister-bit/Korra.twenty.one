"""Durable idempotency ledger for owner messages from the browser chat."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import threading
import time
from typing import Any, Literal


CLIENT_MESSAGE_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{15,79}")

# ``journal_mode`` and schema setup take SQLite write locks.  The dashboard
# creates a short-lived ``DeliveryLedger`` facade for each request, so without
# a process-wide per-path guard two first claims can both try to initialise the
# same file and one fails with ``database is locked`` before its chat reaches
# the upstream agent.
_INITIALIZE_LOCK = threading.Lock()
_INITIALIZED_PATHS: set[Path] = set()


@dataclass(frozen=True)
class DeliveryRecord:
    message_id: str
    fingerprint: str
    session_id: str
    status: Literal["pending", "completed", "failed"]
    response_body: bytes | None
    status_code: int | None
    content_type: str | None
    updated_at: float
    # Идентификатор процесса панели, который последним брал запись в работу.
    # Нужен, чтобы отличить «панель перезапустилась, прогон точно умер» от
    # «прогон оборвался в этом же процессе, ответ мог дойти».
    boot_id: str | None = None


class DeliveryConflict(RuntimeError):
    pass


class DeliveryLedger:
    """Small SQLite ledger shared across dashboard restarts."""

    def __init__(self, path: Path):
        self.path = path

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            os.chmod(self.path.parent, 0o700)
        except OSError:
            pass
        connection = sqlite3.connect(self.path, timeout=10)
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass
        connection.execute("PRAGMA busy_timeout=10000")
        path_key = self.path.resolve()
        try:
            with _INITIALIZE_LOCK:
                if path_key not in _INITIALIZED_PATHS:
                    connection.execute("PRAGMA journal_mode=WAL")
                    connection.execute(
                        """
                        CREATE TABLE IF NOT EXISTS browser_chat_delivery (
                            message_id TEXT PRIMARY KEY,
                            fingerprint TEXT NOT NULL,
                            session_id TEXT NOT NULL,
                            status TEXT NOT NULL CHECK(
                                status IN ('pending','completed','failed')
                            ),
                            response_body BLOB,
                            status_code INTEGER,
                            content_type TEXT,
                            updated_at REAL NOT NULL
                        )
                        """
                    )
                    columns = {
                        row[1] for row in connection.execute("PRAGMA table_info(browser_chat_delivery)")
                    }
                    if "boot_id" not in columns:
                        connection.execute(
                            "ALTER TABLE browser_chat_delivery ADD COLUMN boot_id TEXT"
                        )
                    connection.commit()
                    _INITIALIZED_PATHS.add(path_key)
        except Exception:
            connection.close()
            raise
        return connection

    @staticmethod
    def _row(row: tuple[Any, ...]) -> DeliveryRecord:
        return DeliveryRecord(
            message_id=str(row[0]),
            fingerprint=str(row[1]),
            session_id=str(row[2]),
            status=row[3],
            response_body=bytes(row[4]) if row[4] is not None else None,
            status_code=int(row[5]) if row[5] is not None else None,
            content_type=str(row[6]) if row[6] is not None else None,
            updated_at=float(row[7]),
            boot_id=str(row[8]) if len(row) > 8 and row[8] is not None else None,
        )

    def claim(
        self, message_id: str, fingerprint: str, session_id: str, boot_id: str | None = None
    ) -> tuple[str, DeliveryRecord]:
        now = time.time()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """SELECT message_id, fingerprint, session_id, status,
                          response_body, status_code, content_type, updated_at, boot_id
                     FROM browser_chat_delivery WHERE message_id = ?""",
                (message_id,),
            ).fetchone()
            if row is None:
                connection.execute(
                    """INSERT INTO browser_chat_delivery
                       (message_id, fingerprint, session_id, status, updated_at, boot_id)
                       VALUES (?, ?, ?, 'pending', ?, ?)""",
                    (message_id, fingerprint, session_id, now, boot_id),
                )
                record = DeliveryRecord(
                    message_id, fingerprint, session_id, "pending", None, None, None, now, boot_id
                )
                return "new", record
            record = self._row(row)
            if record.fingerprint != fingerprint or record.session_id != session_id:
                raise DeliveryConflict("Message ID was already used for different content")
            if record.status == "failed":
                connection.execute(
                    """UPDATE browser_chat_delivery
                          SET status='pending', response_body=NULL, status_code=NULL,
                              content_type=NULL, updated_at=?, boot_id=?
                        WHERE message_id=?""",
                    (now, boot_id, message_id),
                )
                return "retry", DeliveryRecord(
                    message_id, fingerprint, session_id, "pending", None, None, None, now, boot_id
                )
            return record.status, record

    def reclaim_after_restart(self, message_id: str, boot_id: str | None) -> None:
        """Запись «в работе» осталась от прежнего процесса панели — берём её себе."""
        with self._connect() as connection:
            connection.execute(
                """UPDATE browser_chat_delivery
                      SET updated_at=?, boot_id=? WHERE message_id=? AND status='pending'""",
                (time.time(), boot_id, message_id),
            )

    def complete(
        self,
        message_id: str,
        *,
        response_body: bytes,
        status_code: int,
        content_type: str,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """UPDATE browser_chat_delivery
                      SET status='completed', response_body=?, status_code=?,
                          content_type=?, updated_at=?
                    WHERE message_id=?""",
                (response_body, status_code, content_type, time.time(), message_id),
            )
            self._prune(connection)

    def fail(self, message_id: str) -> None:
        with self._connect() as connection:
            connection.execute(
                """UPDATE browser_chat_delivery
                      SET status='failed', updated_at=? WHERE message_id=?""",
                (time.time(), message_id),
            )
            self._prune(connection)

    @staticmethod
    def _prune(connection: sqlite3.Connection) -> None:
        cutoff = time.time() - 30 * 24 * 60 * 60
        connection.execute(
            "DELETE FROM browser_chat_delivery WHERE updated_at < ? AND status != 'pending'",
            (cutoff,),
        )
        connection.execute(
            """DELETE FROM browser_chat_delivery
                 WHERE message_id IN (
                   SELECT message_id FROM browser_chat_delivery
                    WHERE status != 'pending'
                    ORDER BY updated_at DESC LIMIT -1 OFFSET 10000
                 )"""
        )


def _last_user_turn(body: dict[str, Any]) -> Any:
    """Последнее сообщение пользователя (текст + вложения) — единица идемпотентности.

    Раньше отпечаток считался от всего тела запроса вместе с историей; после
    перезагрузки страницы история восстанавливается иначе, отпечаток «Повторить»
    не сходился, и панель отвечала вечным 409 «другому тексту».
    """
    messages = body.get("messages") if isinstance(body, dict) else None
    if isinstance(messages, list):
        for item in reversed(messages):
            if isinstance(item, dict) and item.get("role") == "user":
                return {"content": item.get("content"), "attachments": body.get("attachments")}
    return body


def request_fingerprint(
    body: dict[str, Any], session_id: str, target_profile: str = ""
) -> str:
    canonical = json.dumps(
        {
            "session_id": session_id,
            "target_profile": target_profile,
            "body": _last_user_turn(body),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def validate_client_message_id(value: str) -> str:
    message_id = str(value or "").strip()
    if not CLIENT_MESSAGE_ID_RE.fullmatch(message_id):
        raise ValueError("Invalid client message ID")
    return message_id


def openai_json_to_sse(payload: bytes) -> bytes:
    """Convert one robust non-streaming completion into the UI's SSE contract."""
    parsed = json.loads(payload)
    choice = (parsed.get("choices") or [{}])[0]
    message = choice.get("message") if isinstance(choice, dict) else {}
    content = message.get("content", "") if isinstance(message, dict) else ""
    chunk = {
        "id": parsed.get("id", "chatcmpl-replayed"),
        "object": "chat.completion.chunk",
        "created": parsed.get("created", int(time.time())),
        "model": parsed.get("model", "korra-agent"),
        "choices": [{
            "index": 0,
            "delta": {"role": "assistant", "content": str(content or "")},
            "finish_reason": choice.get("finish_reason", "stop") if isinstance(choice, dict) else "stop",
        }],
    }
    return (
        f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"
        "data: [DONE]\n\n"
    ).encode("utf-8")

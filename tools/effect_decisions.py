"""Durable, exact-payload decisions for externally visible effects.

Dangerous-command approvals intentionally remain an interactive policy layer.
This ledger is different: it records one immutable external effect (currently
an outbound message, later a payment), survives a process restart, and never
turns an approval into a reusable session/permanent grant.

The transport executor owns delivery.  This module owns only the durable state
machine and the invariant that an approved payload cannot be substituted.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
import time
import uuid
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Mapping

from korra_constants import get_hermes_home


_DB_NAME = "effect_decisions.sqlite3"
_PROCESS_INSTANCE = uuid.uuid4().hex
_SCHEMA_LOCK = threading.Lock()

PENDING = "pending"
APPROVED = "approved"
DENIED = "denied"
EXECUTING = "executing"
SUCCEEDED = "succeeded"
FAILED = "failed"
UNKNOWN = "unknown"

class DecisionError(RuntimeError):
    """Base error for an invalid durable-decision operation."""


class DecisionConflict(DecisionError):
    """The requested transition conflicts with the recorded decision."""


class EffectExecutorUnavailable(DecisionError):
    """The exact effect is understood but has no safe executor."""


def _executor_instance() -> str:
    """Distinguish an inherited post-fork child from the claiming process."""
    return f"{os.getpid()}:{_PROCESS_INSTANCE}"


def _db_path(path: str | os.PathLike[str] | None = None) -> Path:
    return Path(path) if path is not None else Path(get_hermes_home()) / _DB_NAME


def canonical_payload(payload: Mapping[str, Any]) -> str:
    """Return the only byte representation used for payload identity."""
    if not isinstance(payload, Mapping):
        raise TypeError("effect payload must be a mapping")
    return json.dumps(
        dict(payload),
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def payload_digest(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_payload(payload).encode("utf-8")).hexdigest()


def _connect(path: str | os.PathLike[str] | None = None) -> sqlite3.Connection:
    db_path = _db_path(path)
    db_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    # sqlite's creation mode follows umask.  Pre-create explicitly so message
    # drafts and recipient identifiers never inherit a permissive process umask.
    fd = os.open(db_path, os.O_CREAT | os.O_RDWR, 0o600)
    os.close(fd)
    os.chmod(db_path, 0o600)
    conn = sqlite3.connect(db_path, timeout=15.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 15000")
    conn.execute("PRAGMA foreign_keys = ON")
    with _SCHEMA_LOCK:
        # CREATE IF NOT EXISTS is cheap and also handles a profile database
        # being restored/replaced while a long-lived gateway process remains
        # alive. A process-local "initialized" cache would return a connection
        # to the replacement file without its schema.
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS effect_decisions (
                id TEXT PRIMARY KEY,
                kind TEXT NOT NULL,
                owner_id TEXT NOT NULL,
                profile TEXT NOT NULL,
                source_session_id TEXT NOT NULL,
                source_session_key TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                payload_sha256 TEXT NOT NULL,
                dedupe_key TEXT NOT NULL,
                status TEXT NOT NULL CHECK (status IN (
                    'pending', 'approved', 'denied', 'executing',
                    'succeeded', 'failed', 'unknown'
                )),
                idempotency_key TEXT NOT NULL UNIQUE,
                executor_instance TEXT,
                outcome_json TEXT,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                decided_at REAL,
                execution_started_at REAL,
                finished_at REAL
            );
            CREATE UNIQUE INDEX IF NOT EXISTS effect_decisions_one_pending
                ON effect_decisions(dedupe_key) WHERE status = 'pending';
            CREATE INDEX IF NOT EXISTS effect_decisions_session_status
                ON effect_decisions(source_session_id, status, created_at);
            """
        )
        conn.commit()
    return conn


def _row_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    data = dict(row)
    try:
        data["payload"] = json.loads(data.pop("payload_json"))
    except Exception as exc:  # fail closed on a corrupted durable payload
        raise DecisionError("stored effect payload is not valid JSON") from exc
    raw_outcome = data.pop("outcome_json", None)
    data["outcome"] = json.loads(raw_outcome) if raw_outcome else None
    return data


def _mark_foreign_execution_unknown(
    conn: sqlite3.Connection, decision_id: str | None = None
) -> None:
    """Fail closed after restart: an in-flight effect is never replayed."""
    now = time.time()
    params: list[Any] = [UNKNOWN, now, now, _executor_instance()]
    where = "status = 'executing' AND COALESCE(executor_instance, '') != ?"
    if decision_id is not None:
        where += " AND id = ?"
        params.append(decision_id)
    conn.execute(
        f"""
        UPDATE effect_decisions
           SET status = ?, updated_at = ?, finished_at = ?,
               outcome_json = '{{"reason":"executor_restarted","retry":false}}'
         WHERE {where}
        """,
        params,
    )


def create_pending(
    *,
    kind: str,
    owner_id: str,
    profile: str,
    source_session_id: str,
    source_session_key: str,
    payload: Mapping[str, Any],
    path: str | os.PathLike[str] | None = None,
) -> tuple[dict[str, Any], bool]:
    """Create one pending exact-payload decision, or return its live duplicate."""
    if kind not in {"outbound_message", "payment"}:
        raise ValueError(f"unsupported effect decision kind: {kind}")
    if kind == "payment":
        recipient = payload.get("recipient")
        currency = payload.get("currency")
        amount = payload.get("amount")
        if not isinstance(recipient, str) or not recipient.strip():
            raise ValueError("payment recipient is required")
        if (
            not isinstance(currency, str)
            or len(currency.strip()) != 3
            or not currency.strip().isalpha()
        ):
            raise ValueError("payment currency must be a three-letter code")
        if isinstance(amount, bool):
            raise ValueError("payment amount must be positive")
        try:
            parsed_amount = Decimal(str(amount))
        except (InvalidOperation, ValueError):
            raise ValueError("payment amount must be positive") from None
        if not parsed_amount.is_finite() or parsed_amount <= 0:
            raise ValueError("payment amount must be positive")
    required = {
        "owner_id": owner_id,
        "profile": profile,
        "source_session_id": source_session_id,
        "source_session_key": source_session_key,
    }
    if any(not isinstance(value, str) or not value.strip() for value in required.values()):
        raise ValueError("owner, profile, source session id and key are required")

    payload_json = canonical_payload(payload)
    digest = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
    dedupe_source = "\0".join(
        (kind, owner_id, profile, source_session_id, source_session_key, digest)
    )
    dedupe_key = hashlib.sha256(dedupe_source.encode("utf-8")).hexdigest()
    now = time.time()
    decision_id = f"effect_{uuid.uuid4().hex}"
    idempotency_key = str(uuid.uuid4())

    with _connect(path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        _mark_foreign_execution_unknown(conn)
        existing = conn.execute(
            "SELECT * FROM effect_decisions WHERE dedupe_key = ? AND status = 'pending'",
            (dedupe_key,),
        ).fetchone()
        if existing is not None:
            conn.commit()
            return _row_dict(existing), False  # type: ignore[return-value]
        conn.execute(
            """
            INSERT INTO effect_decisions (
                id, kind, owner_id, profile, source_session_id,
                source_session_key, payload_json, payload_sha256, dedupe_key,
                status, idempotency_key, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?)
            """,
            (
                decision_id,
                kind,
                owner_id,
                profile,
                source_session_id,
                source_session_key,
                payload_json,
                digest,
                dedupe_key,
                idempotency_key,
                now,
                now,
            ),
        )
        row = conn.execute(
            "SELECT * FROM effect_decisions WHERE id = ?", (decision_id,)
        ).fetchone()
        conn.commit()
        return _row_dict(row), True  # type: ignore[return-value]


def get_decision(
    decision_id: str, *, path: str | os.PathLike[str] | None = None
) -> dict[str, Any] | None:
    with _connect(path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        _mark_foreign_execution_unknown(conn, decision_id)
        row = conn.execute(
            "SELECT * FROM effect_decisions WHERE id = ?", (decision_id,)
        ).fetchone()
        conn.commit()
        return _row_dict(row)


def list_session_decisions(
    source_session_id: str,
    *,
    profile: str = "",
    statuses: tuple[str, ...] = (PENDING,),
    path: str | os.PathLike[str] | None = None,
) -> list[dict[str, Any]]:
    if not statuses:
        return []
    placeholders = ",".join("?" for _ in statuses)
    profile_where = " AND profile = ?" if profile else ""
    params: list[Any] = [source_session_id, *statuses]
    if profile:
        params.append(profile)
    with _connect(path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        _mark_foreign_execution_unknown(conn)
        rows = conn.execute(
            f"""
            SELECT * FROM effect_decisions
             WHERE source_session_id = ? AND status IN ({placeholders}){profile_where}
             ORDER BY created_at, id
            """,
            params,
        ).fetchall()
        conn.commit()
        return [_row_dict(row) for row in rows]  # type: ignore[misc]


def list_profile_decisions(
    *,
    profile: str = "",
    statuses: tuple[str, ...] = (
        PENDING,
        APPROVED,
        DENIED,
        EXECUTING,
        SUCCEEDED,
        FAILED,
        UNKNOWN,
    ),
    limit: int = 100,
    path: str | os.PathLike[str] | None = None,
) -> list[dict[str, Any]]:
    """Return recent decisions for the current profile's decision center.

    The database itself lives inside one profile home.  ``profile`` is an
    additional identity check used by multiplexed callers, not a mechanism to
    cross profile boundaries.
    """
    allowed = {
        PENDING,
        APPROVED,
        DENIED,
        EXECUTING,
        SUCCEEDED,
        FAILED,
        UNKNOWN,
    }
    if not statuses or any(status not in allowed for status in statuses):
        raise ValueError("invalid effect decision status filter")
    safe_limit = max(1, min(int(limit), 500))
    placeholders = ",".join("?" for _ in statuses)
    profile_where = " AND profile = ?" if profile else ""
    params: list[Any] = [*statuses]
    if profile:
        params.append(profile)
    params.append(safe_limit)
    with _connect(path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        _mark_foreign_execution_unknown(conn)
        rows = conn.execute(
            f"""
            SELECT * FROM effect_decisions
             WHERE status IN ({placeholders}){profile_where}
             ORDER BY (status = 'pending') DESC, created_at DESC, id DESC
             LIMIT ?
            """,
            params,
        ).fetchall()
        conn.commit()
        return [_row_dict(row) for row in rows]  # type: ignore[misc]


def decide(
    decision_id: str,
    *,
    source_session_id: str = "",
    source_session_key: str = "",
    profile: str = "",
    choice: str,
    path: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    """Record an exact one-shot approval or denial, idempotently."""
    if choice not in {"once", "deny"}:
        raise DecisionConflict("external effects allow only once or deny")
    if not source_session_id and not source_session_key:
        raise ValueError("source session id or key is required")
    target = APPROVED if choice == "once" else DENIED
    now = time.time()
    with _connect(path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        _mark_foreign_execution_unknown(conn, decision_id)
        row = conn.execute(
            "SELECT * FROM effect_decisions WHERE id = ?", (decision_id,)
        ).fetchone()
        belongs = row is not None
        if belongs and source_session_id:
            belongs = row["source_session_id"] == source_session_id
        if belongs and source_session_key:
            belongs = row["source_session_key"] == source_session_key
        if belongs and profile:
            belongs = row["profile"] == profile
        if not belongs:
            raise DecisionConflict("effect decision does not belong to this session")
        status = row["status"]
        if status == PENDING:
            conn.execute(
                """
                UPDATE effect_decisions
                   SET status = ?, updated_at = ?, decided_at = ?
                 WHERE id = ? AND status = 'pending'
                """,
                (target, now, now, decision_id),
            )
        elif status != target:
            raise DecisionConflict(f"effect decision is already {status}")
        updated = conn.execute(
            "SELECT * FROM effect_decisions WHERE id = ?", (decision_id,)
        ).fetchone()
        conn.commit()
        return _row_dict(updated)  # type: ignore[return-value]


def claim_execution(
    decision_id: str,
    *,
    expected_payload_sha256: str,
    path: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    """Atomically claim an approved payload for its only delivery attempt."""
    now = time.time()
    with _connect(path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        _mark_foreign_execution_unknown(conn, decision_id)
        row = conn.execute(
            "SELECT * FROM effect_decisions WHERE id = ?", (decision_id,)
        ).fetchone()
        if row is None:
            raise DecisionConflict("effect decision was not found")
        if row["payload_sha256"] != expected_payload_sha256:
            raise DecisionConflict("effect payload changed after approval")
        if row["status"] != APPROVED:
            raise DecisionConflict(f"effect decision is {row['status']}, not approved")
        changed = conn.execute(
            """
            UPDATE effect_decisions
               SET status = 'executing', executor_instance = ?,
                   execution_started_at = ?, updated_at = ?
             WHERE id = ? AND status = 'approved'
            """,
            (_executor_instance(), now, now, decision_id),
        ).rowcount
        if changed != 1:
            raise DecisionConflict("effect decision was claimed concurrently")
        updated = conn.execute(
            "SELECT * FROM effect_decisions WHERE id = ?", (decision_id,)
        ).fetchone()
        conn.commit()
        return _row_dict(updated)  # type: ignore[return-value]


def finish_execution(
    decision_id: str,
    *,
    status: str,
    outcome: Mapping[str, Any] | None = None,
    path: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    if status not in {SUCCEEDED, FAILED, UNKNOWN}:
        raise ValueError("execution can finish only as succeeded, failed, or unknown")
    outcome_json = canonical_payload(outcome or {})
    now = time.time()
    with _connect(path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT * FROM effect_decisions WHERE id = ?", (decision_id,)
        ).fetchone()
        if row is None:
            raise DecisionConflict("effect decision was not found")
        if row["status"] == status:
            conn.commit()
            return _row_dict(row)  # type: ignore[return-value]
        if row["status"] != EXECUTING or row["executor_instance"] != _executor_instance():
            raise DecisionConflict(f"effect decision cannot finish from {row['status']}")
        conn.execute(
            """
            UPDATE effect_decisions
               SET status = ?, outcome_json = ?, updated_at = ?, finished_at = ?
             WHERE id = ?
            """,
            (status, outcome_json, now, now, decision_id),
        )
        updated = conn.execute(
            "SELECT * FROM effect_decisions WHERE id = ?", (decision_id,)
        ).fetchone()
        conn.commit()
        return _row_dict(updated)  # type: ignore[return-value]


def resolve_effect_decision(
    decision_id: str,
    choice: str,
    *,
    source_session_id: str = "",
    source_session_key: str = "",
    profile: str = "",
) -> dict[str, Any]:
    """Resolve an effect through its registered exact-payload executor.

    Outbound messages have a transport executor. Payment records are supported
    by the ledger/UI contract but Korra currently exposes no agent-callable
    payment executor; approval therefore fails closed without changing the
    pending record, while denial remains durable and unambiguous.
    """
    decision = get_decision(decision_id)
    if decision is None:
        raise DecisionConflict("effect decision was not found")
    if profile and decision["profile"] != profile:
        raise DecisionConflict("effect decision does not belong to this profile")
    if decision["kind"] == "outbound_message":
        from tools.send_message_tool import resolve_outbound_message_decision

        return resolve_outbound_message_decision(
            decision_id,
            choice,
            source_session_id=source_session_id,
            source_session_key=source_session_key,
            profile=profile,
        )
    if decision["kind"] == "payment":
        if choice == "deny":
            return decide(
                decision_id,
                source_session_id=source_session_id,
                source_session_key=source_session_key,
                profile=profile,
                choice=choice,
            )
        if choice != "once":
            raise DecisionConflict("external effects allow only once or deny")
        raise EffectExecutorUnavailable(
            "payment executor is not available; no payment was performed"
        )
    raise DecisionConflict("unsupported effect decision kind")


def approval_payload(decision: Mapping[str, Any]) -> dict[str, Any]:
    """Project a durable effect into the existing chat approval wire shape."""
    payload = decision.get("payload") or {}
    if decision.get("kind") == "payment":
        command = (
            f"Получатель: {payload.get('recipient') or 'не указан'}\n"
            f"Сумма: {payload.get('amount')} "
            f"{str(payload.get('currency') or '').upper()}"
        )
        description = "Проверьте получателя, сумму и валюту перед оплатой."
    else:
        target = str(payload.get("target_label") or payload.get("target") or "адресат")
        text = str(payload.get("message") or "")
        attachments = payload.get("attachments") or []
        attachment_lines = [
            f"Вложение: {item.get('name') or item.get('path') or 'файл'}"
            for item in attachments
            if isinstance(item, Mapping)
        ]
        command = f"Кому: {target}\n\n{text}"
        if attachment_lines:
            command += "\n\n" + "\n".join(attachment_lines)
        description = "Проверьте адресата, текст и вложения перед внешней отправкой."
    return {
        "request_id": str(decision["id"]),
        "decision_kind": str(decision["kind"]),
        "effect_status": str(decision["status"]),
        "source_session_id": str(decision["source_session_id"]),
        "command": command,
        "description": description,
        "pattern_key": str(decision["kind"]),
        "pattern_keys": [str(decision["kind"])],
        "allow_session": False,
        "allow_permanent": False,
        "choices": ["once", "deny"],
        "timestamp": decision.get("created_at"),
        "updated_at": decision.get("updated_at"),
    }

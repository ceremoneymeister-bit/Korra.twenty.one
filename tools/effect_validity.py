"""Validity of an exact result, independent of permission to send it.

Payloads and their hashes never change when a result expires. Uncertain or
already started deliveries are never retired/replayed by this module.
"""
from __future__ import annotations

import json
import math
import re
import sqlite3
from collections.abc import Mapping

RETIREMENT_STATES = {"expired", "superseded", "needs_review"}


def _result_index(conn):
    columns = {row[1] for row in conn.execute("PRAGMA table_info(effect_decisions)")}
    for name, kind in (("result_key", "TEXT"), ("occurrence_at", "REAL")):
        if name not in columns:
            conn.execute(f"ALTER TABLE effect_decisions ADD COLUMN {name} {kind}")
    conn.execute("CREATE INDEX IF NOT EXISTS effect_decisions_result ON effect_decisions(kind,owner_id,profile,result_key,occurrence_at DESC,created_at DESC)")


def migrate_lifecycle_states(conn: sqlite3.Connection) -> None:
    """Transactionally extend the old CHECK, retaining every column and index."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        sql = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='effect_decisions'").fetchone()[0]
        if all(f"'{status}'" in sql for status in RETIREMENT_STATES):
            _result_index(conn)
            conn.commit()
            return
        updated, count = re.subn(r"'unknown'(\s*\))", "'unknown', 'expired', 'superseded', 'needs_review'\\1", sql)
        if count != 1:
            raise sqlite3.DatabaseError("Unrecognized effect decision status constraint")
        objects = conn.execute("SELECT sql FROM sqlite_master WHERE tbl_name='effect_decisions' AND type IN ('index','trigger') AND sql IS NOT NULL").fetchall()
        conn.execute("ALTER TABLE effect_decisions RENAME TO effect_decisions_v1")
        conn.execute(updated)
        conn.execute("INSERT INTO effect_decisions SELECT * FROM effect_decisions_v1")
        conn.execute("DROP TABLE effect_decisions_v1")
        for item in objects:
            conn.execute(item[0])
        _result_index(conn)
        conn.commit()
    except BaseException:
        conn.rollback()
        raise


def validate_validity(value: object) -> None:
    if value is None:
        return
    if not isinstance(value, Mapping) or value.get("version") != 1:
        raise ValueError("unsupported result validity")
    for key in ("expires_at", "occurrence_at"):
        number = value.get(key)
        if number is not None and (isinstance(number, bool) or not isinstance(number, (int, float)) or not math.isfinite(number) or number <= 0):
            raise ValueError(f"invalid result {key}")
    key = value.get("supersession_key")
    if key is not None and (not isinstance(key, str) or not key or len(key) > 256 or value.get("occurrence_at") is None):
        raise ValueError("replacement requires a key and an occurrence time")


def retire(conn, row, status: str, now: float, *, replacement: str | None = None) -> None:
    outcome = json.dumps({"reason": status, "superseded_by": replacement, "retry": False})
    conn.execute("""UPDATE effect_decisions SET status=?, outcome_json=?,
                    updated_at=?, finished_at=? WHERE id=? AND status IN ('pending','approved')""",
                 (status, outcome, now, now, row["id"]))


def refresh_validity(conn: sqlite3.Connection, now: float) -> None:
    """Refresh before listing, approving or claiming; no external side effects."""
    rows = conn.execute("SELECT * FROM effect_decisions WHERE status IN ('pending','approved') ORDER BY created_at, id").fetchall()
    replaceable: dict[tuple, list] = {}
    for row in rows:
        payload = json.loads(row["payload_json"])
        validity = payload.get("validity")
        if validity is None:
            if row["kind"] == "outbound_message" and payload.get("source_label") == "cron":
                # No guessed deadline: a historical report may still represent
                # an obligation. Keep it for review, without an active send button.
                retire(conn, row, "needs_review", now)
            continue
        validate_validity(validity)
        if validity.get("expires_at") is not None and now >= validity["expires_at"]:
            retire(conn, row, "expired", now)
            continue
        key = validity.get("supersession_key")
        if key:
            group = (row["kind"], row["owner_id"], row["profile"], key)
            replaceable.setdefault(group, []).append((validity["occurrence_at"], row))
    for group in replaceable.values():
        candidate = group[0][1]
        # Include already sent / in-flight results: an older slow run must not
        # become actionable merely because the newer run has left pending.
        newest = conn.execute("""SELECT id, occurrence_at FROM effect_decisions
            WHERE kind=? AND owner_id=? AND profile=? AND result_key=?
            ORDER BY occurrence_at DESC, created_at DESC, id DESC LIMIT 1""",
            (candidate["kind"], candidate["owner_id"], candidate["profile"], candidate["result_key"])).fetchone()
        if newest is None:
            continue
        for occurrence, row in group:
            if row["id"] != newest["id"] and occurrence < newest["occurrence_at"]:
                retire(conn, row, "superseded", now, replacement=newest["id"])

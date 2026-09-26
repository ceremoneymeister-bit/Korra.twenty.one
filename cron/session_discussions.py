"""Keep human continuations of scheduled runs visible without changing transcripts."""
from __future__ import annotations

import json


def _compression_child(child, parent) -> bool:
    if not parent or parent["end_reason"] != "compression" or child["profile_name"] != parent["profile_name"]:
        return False
    try:
        config = json.loads(child["model_config"] or "{}")
    except (TypeError, ValueError):
        config = {}
    return not (isinstance(config, dict) and (
        config.get("_branched_from") == parent["id"]
        or config.get("_delegate_from") == parent["id"]))


def promote(conn, session_id: str) -> None:
    """Called inside the same transaction that saves the human turn."""
    initial = conn.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
    if not initial or (initial["source"] not in {"cron", "cron_discussion"} and not initial["parent_session_id"]):
        return
    pending, seen = [initial], set()
    while pending:
        row = pending.pop()
        if row["id"] in seen:
            continue
        seen.add(row["id"])
        conn.execute("UPDATE sessions SET source='cron_discussion' WHERE id=? AND source='cron'",
                     (row["id"],))
        if row["parent_session_id"]:
            parent = conn.execute("SELECT * FROM sessions WHERE id=?", (row["parent_session_id"],)).fetchone()
            if _compression_child(row, parent):
                pending.append(parent)
        if row["end_reason"] == "compression":
            for child in conn.execute("SELECT * FROM sessions WHERE parent_session_id=?", (row["id"],)).fetchall():
                if _compression_child(child, row):
                    pending.append(child)


def migrate_legacy(conn) -> None:
    """One-time conservative recovery; old stores lack a human-origin flag.

    More than one ordinary user row, or a platform message ID, is sufficient
    evidence to preserve a discussion. Ambiguous old continuations may remain
    visible; never delete, merge, or rewrite their messages.
    """
    key = "cron_discussions_v1"
    if conn.execute("SELECT 1 FROM state_meta WHERE key=?", (key,)).fetchone():
        return
    candidates = conn.execute("""SELECT s.id FROM sessions s JOIN messages m ON m.session_id=s.id
        WHERE s.source='cron' AND m.role='user' AND COALESCE(m._compressed_summary,0)=0
          AND COALESCE(m.display_kind,'') NOT IN ('internal_notification','hidden')
        GROUP BY s.id HAVING COUNT(*)>1 OR MAX(CASE WHEN m.platform_message_id IS NOT NULL THEN 1 ELSE 0 END)=1""").fetchall()
    for row in candidates:
        promote(conn, row[0])
    conn.execute("INSERT OR IGNORE INTO state_meta (key,value) VALUES (?, '1')", (key,))

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


def promote(conn, session_id: str) -> list[str]:
    """Called inside the same transaction that saves the human turn.

    Returns the ids this call moved from ``cron`` to ``cron_discussion``.
    """
    moved: list[str] = []
    initial = conn.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
    if not initial or (initial["source"] not in {"cron", "cron_discussion"} and not initial["parent_session_id"]):
        return moved
    pending, seen = [initial], set()
    while pending:
        row = pending.pop()
        if row["id"] in seen:
            continue
        seen.add(row["id"])
        if conn.execute("UPDATE sessions SET source='cron_discussion' WHERE id=? AND source='cron'",
                        (row["id"],)).rowcount:
            moved.append(row["id"])
        if row["parent_session_id"]:
            parent = conn.execute("SELECT * FROM sessions WHERE id=?", (row["parent_session_id"],)).fetchone()
            if _compression_child(row, parent):
                pending.append(parent)
        if row["end_reason"] == "compression":
            for child in conn.execute("SELECT * FROM sessions WHERE parent_session_id=?", (row["id"],)).fetchall():
                if _compression_child(child, row):
                    pending.append(child)
    return moved


def _is_engine_user_row(content) -> bool:
    """A user-role row the engine wrote itself: a continuation after a cut-off
    answer, a Codex nudge, the iteration-limit summary request, a compaction
    summary. SessionDB keeps them, so they are not evidence of a person."""
    try:
        from agent.context_compressor import ContextCompressor

        return ContextCompressor._is_synthetic_compression_user_turn(
            {"role": "user", "content": content}
        )
    except Exception:
        return False


def migrate_legacy(conn) -> None:
    """One-time conservative recovery; old stores lack a human-origin flag.

    A cron run counts as a discussion when, beyond its own prompt, it holds
    another user row a person wrote, or any row with a platform message ID.
    Rows the engine writes as ``user`` (continuations, nudges, summary
    requests) do not count (0.21.15 review P2-A). The ids moved are journaled
    in ``state_meta`` so the step can be audited or undone. Messages are never
    deleted, merged or rewritten.
    """
    key = "cron_discussions_v1"
    if conn.execute("SELECT 1 FROM state_meta WHERE key=?", (key,)).fetchone():
        return
    rows = conn.execute("""SELECT s.id, m.content, m.platform_message_id
        FROM sessions s JOIN messages m ON m.session_id=s.id
        WHERE s.source='cron' AND m.role='user' AND COALESCE(m._compressed_summary,0)=0
          AND COALESCE(m.display_kind,'') NOT IN ('internal_notification','hidden')
        ORDER BY s.id, m.id""").fetchall()
    evidence: dict[str, list[int]] = {}
    for session_id, content, platform_message_id in rows:
        counts = evidence.setdefault(session_id, [0, 0])
        if platform_message_id is not None:
            counts[1] = 1
        if not _is_engine_user_row(content):
            counts[0] += 1
    moved: list[str] = []
    for session_id, (people_rows, from_platform) in evidence.items():
        if people_rows > 1 or from_platform:
            moved.extend(promote(conn, session_id))
    conn.execute("INSERT OR IGNORE INTO state_meta (key,value) VALUES (?, ?)",
                 ("cron_discussions_v1_moved", json.dumps(sorted(set(moved)))))
    conn.execute("INSERT OR IGNORE INTO state_meta (key,value) VALUES (?, '1')", (key,))

"""Unified, read-only dashboard projection of durable agent activity.

The projector intentionally owns no persistence.  It joins the stores that
already own the facts: browser delivery intents, cross-process turn leases,
gateway routing markers, session metadata/read watermarks, and pending exact
effect decisions.  This keeps panel restarts and Telegram/CLI work visible
without inventing a fourth activity ledger.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Iterable

import psutil

from korra_constants import get_hermes_home

_STALE_RETENTION_SECONDS = 60 * 60
_GATEWAY_HEARTBEAT_TTL_SECONDS = 90


def _wire_profile(name: str) -> str:
    return "" if not name or name == "default" else name


def _stable_id(kind: str, profile: str, *parts: object) -> str:
    raw = "\0".join([kind, profile, *(str(part) for part in parts)])
    digest = hashlib.sha256(raw.encode("utf-8", errors="replace")).hexdigest()[:24]
    return f"activity-{kind}-{digest}"


def _profile_targets(profile: str | None) -> list[tuple[str, Path]]:
    from korra_cli import profiles

    try:
        candidates = list(profiles.profiles_to_serve(multiplex=True))
    except Exception:
        candidates = []
    if not candidates:
        candidates = [("default", Path(get_hermes_home()))]

    result: list[tuple[str, Path]] = []
    seen: set[tuple[str, str]] = set()
    for name, home in candidates:
        wire = _wire_profile(str(name))
        resolved = Path(home).resolve()
        key = (wire, str(resolved))
        if key in seen or (profile is not None and wire != profile):
            continue
        seen.add(key)
        result.append((wire, resolved))
    return result


def _gateway_heartbeat_live(home: Path, now: float) -> bool:
    """Fresh file + live producer PID is the minimum liveness evidence.

    Ambiguous, malformed, stale, or foreign heartbeat data always returns
    false.  The release UI may then say ``stale`` but never ``running``.
    """
    path = home / "state" / "gateway.heartbeat"
    try:
        if now - path.stat().st_mtime > _GATEWAY_HEARTBEAT_TTL_SECONDS:
            return False
        payload = json.loads(path.read_text(encoding="utf-8"))
        pid = int(payload.get("pid", 0))
        if pid <= 0:
            return False
        return bool(psutil.pid_exists(pid))
    except (OSError, psutil.Error, ValueError, TypeError, json.JSONDecodeError):
        return False


def _pending_decisions(home: Path) -> list[dict[str, Any]]:
    path = home / "effect_decisions.sqlite3"
    if not path.is_file():
        return []
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=1.0)
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            "SELECT id, source_session_id, source_session_key, created_at, updated_at "
            "FROM effect_decisions WHERE status = 'pending' "
            "ORDER BY created_at DESC LIMIT 100"
        ).fetchall()
        return [dict(row) for row in rows]
    except (OSError, sqlite3.Error):
        return []
    finally:
        if connection is not None:
            connection.close()


def _session_meta(row: dict[str, Any] | None) -> dict[str, Any]:
    row = row or {}
    source = str(row.get("source") or "").strip()
    title = str(row.get("title") or "").strip()
    preview = str(row.get("preview") or "").strip()
    updated_at = float(row.get("last_active") or row.get("started_at") or 0)
    return {
        "source": source or None,
        "channel": source or None,
        "title": title or None,
        "updated_at": updated_at,
        "started_at": float(row.get("started_at") or updated_at),
        "history_count": max(0, int(row.get("message_count") or 0) - 1),
        "user_message": {
            "role": "user",
            "content": (preview or title or "Фоновая задача")[:160],
        },
        "unread": bool(row.get("unread")),
        "event_revision": f"{updated_at:.6f}",
    }


def _load_profile_snapshot(profile: str, home: Path, now: float) -> dict[str, Any]:
    from korra_cli.web_server import _open_session_db_at_path

    db_path = home / "state.db"
    if not db_path.is_file():
        return {"metadata": {}, "activity": []}
    db = _open_session_db_at_path(db_path, read_only=True)
    try:
        rows = db.list_sessions_rich(
            limit=100,
            offset=0,
            order_by_last_active=True,
            compact_rows=True,
            include_pinned=True,
        )
        leases = db.list_session_turn_leases()
        routing = db.load_gateway_routing_entries(
            scope=str((home / "sessions").resolve())
        )
    finally:
        db.close()

    metadata: dict[str, dict[str, Any]] = {}
    roots: dict[str, str] = {}
    for raw in rows:
        row = dict(raw)
        sid = str(row.get("id") or "")
        if not sid:
            continue
        metadata[sid] = _session_meta(row)
        root = str(row.get("_lineage_root_id") or sid)
        roots[root] = sid

    activity: list[dict[str, Any]] = []
    lease_statuses: dict[str, str] = {}
    for lease in leases:
        root = str(lease.get("conversation_id") or "")
        if not root:
            continue
        sid = roots.get(root, root)
        expires_at = float(lease.get("expires_at") or 0)
        if expires_at <= now - _STALE_RETENTION_SECONDS:
            continue
        meta = metadata.get(sid, _session_meta(None))
        status = "running" if expires_at > now else "stale"
        lease_statuses[sid] = status
        activity.append({
            **meta,
            "message_id": _stable_id("lease", profile, root, lease.get("holder")),
            "session_id": sid,
            "profile": profile,
            "status": status,
            "updated_at": max(
                float(meta.get("updated_at") or 0),
                float(lease.get("acquired_at") or 0),
            ),
            "delivery": "pending" if status == "running" else "unknown",
            "unread": False,
        })

    heartbeat_live = _gateway_heartbeat_live(home, now)
    for session_key, raw in routing.items():
        try:
            entry = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            continue
        token = entry.get("active_turn_token")
        sid = str(entry.get("session_id") or "")
        if not token or not sid or lease_statuses.get(sid) == "running":
            continue
        started_raw = entry.get("active_turn_started_at")
        try:
            from datetime import datetime

            started_at = datetime.fromisoformat(str(started_raw)).timestamp()
        except (TypeError, ValueError):
            started_at = 0.0
        if started_at and started_at <= now - _STALE_RETENTION_SECONDS:
            continue
        origin = entry.get("origin") if isinstance(entry.get("origin"), dict) else {}
        platform = str(origin.get("platform") or entry.get("platform") or "").strip()
        meta = metadata.get(sid, _session_meta(None))
        if not meta.get("source") and platform:
            meta = {**meta, "source": platform, "channel": platform}
        status = "running" if heartbeat_live else "stale"
        if lease_statuses.get(sid) == "stale":
            if status == "stale":
                continue
            # The exact gateway marker plus a fresh producer heartbeat is
            # stronger than an expired generic lease.  Replace, do not show
            # contradictory duplicate states for one turn.
            activity = [
                item
                for item in activity
                if not (item["session_id"] == sid and item["status"] == "stale")
            ]
        activity.append({
            **meta,
            "message_id": _stable_id("gateway", profile, session_key, token),
            "session_id": sid,
            "profile": profile,
            "status": status,
            "updated_at": max(float(meta.get("updated_at") or 0), started_at),
            "started_at": started_at or meta.get("started_at") or 0,
            "delivery": "pending" if status == "running" else "unknown",
            "unread": False,
        })

    for decision in _pending_decisions(home):
        sid = str(decision.get("source_session_id") or "")
        session_key = str(decision.get("source_session_key") or "")
        if not sid and not session_key:
            continue
        sid = sid or session_key
        meta = metadata.get(sid, _session_meta(None))
        activity.append({
            **meta,
            "message_id": _stable_id("decision", profile, decision.get("id")),
            "session_id": sid,
            "profile": profile,
            "status": "waiting_decision",
            "updated_at": float(decision.get("updated_at") or decision.get("created_at") or 0),
            "started_at": float(decision.get("created_at") or 0),
            "delivery": "pending",
            "unread": False,
            "user_message": {"role": "user", "content": "Ожидает вашего решения"},
        })

    # A completed Telegram/CLI/browser conversation is surfaced only while
    # its server read watermark says it has new activity.  Old history is not
    # replayed as a notification on first rollout.
    for sid, meta in metadata.items():
        if not meta.get("unread"):
            continue
        activity.append({
            **meta,
            "message_id": _stable_id("unread", profile, sid, meta["event_revision"]),
            "session_id": sid,
            "profile": profile,
            "status": "completed",
            "delivery": "delivered",
        })

    return {"metadata": metadata, "activity": activity}


def project_chat_activity(
    browser_runs: Iterable[dict[str, Any]],
    *,
    profile: str | None,
    session_id: str | None,
    now: float | None = None,
) -> list[dict[str, Any]]:
    """Enrich browser runs and add cross-channel global activity rows."""
    current = time.time() if now is None else float(now)
    snapshots: dict[str, dict[str, Any]] = {}
    for wire_profile, home in _profile_targets(profile):
        try:
            snapshots[wire_profile] = _load_profile_snapshot(wire_profile, home, current)
        except Exception:
            # Status polling must survive one damaged/locked profile.  The
            # browser ledger remains visible and the caller can mark the poll
            # reachable; it just lacks optional cross-channel enrichment.
            continue

    by_session_status: dict[tuple[str, str], str] = {}
    for wire_profile, snapshot in snapshots.items():
        for item in snapshot["activity"]:
            status = item["status"]
            key = (wire_profile, item["session_id"])
            if status == "waiting_decision" or (
                status == "running" and by_session_status.get(key) != "waiting_decision"
            ) or key not in by_session_status:
                by_session_status[key] = status

    result: list[dict[str, Any]] = []
    browser_sessions: set[tuple[str, str]] = set()
    for raw in browser_runs:
        item = dict(raw)
        key = (str(item.get("profile") or ""), str(item.get("session_id") or ""))
        browser_sessions.add(key)
        meta = snapshots.get(key[0], {}).get("metadata", {}).get(key[1])
        if meta:
            item = {**meta, **item, "unread": bool(meta.get("unread"))}
        else:
            item.setdefault("unread", False)
        durable_status = by_session_status.get(key)
        if item.get("status") == "interrupted" and durable_status in {
            "running", "waiting_decision"
        }:
            item["status"] = durable_status
        item["delivery"] = {
            "completed": "delivered",
            "failed": "failed",
            "running": "pending",
            "queued": "pending",
            "waiting_decision": "pending",
        }.get(str(item.get("status")), "unknown")
        result.append(item)

    # Exact conversation reads are used by stream recovery and must contain
    # only its DeliveryLedger intents.  Synthetic rows would otherwise be
    # mistaken for a resumable browser message ID.
    if session_id is not None:
        return result

    seen_ids = {str(item.get("message_id")) for item in result}
    for wire_profile, snapshot in snapshots.items():
        for item in snapshot["activity"]:
            if item["message_id"] in seen_ids:
                continue
            # Do not duplicate the same completed notification already owned
            # by a browser delivery row.  Concurrent/running cross-channel
            # work remains separate and is intentionally retained.
            key = (wire_profile, item["session_id"])
            if item["status"] == "completed" and key in browser_sessions:
                continue
            result.append(item)
            seen_ids.add(item["message_id"])

    result.sort(key=lambda item: float(item.get("updated_at") or 0), reverse=True)
    return result

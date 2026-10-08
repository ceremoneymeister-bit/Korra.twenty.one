"""Helpers for rendering gateway message timestamps exactly once.

Gateway messages need timestamps in the LLM context for temporal awareness, but
persisted message content should stay clean so replay does not accumulate
``[timestamp] [timestamp] ...`` prefixes across turns.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Optional, Tuple


# Current gateway format: [Tue 2026-04-28 13:40:53 CEST]
_HUMAN_TIMESTAMP_RE = re.compile(
    r"^\[(?P<dow>[A-Z][a-z]{2}) "
    r"(?P<date>\d{4}-\d{2}-\d{2}) "
    r"(?P<time>\d{2}:\d{2}:\d{2})"
    r"(?: (?P<tz>[A-Za-z0-9_+\-/:]+))?\]\s*"
)

# Older gateway format: [2026-04-13T17:02:06+0200] or [+02:00]
_ISO_TIMESTAMP_RE = re.compile(
    r"^\[(?P<iso>\d{4}-\d{2}-\d{2}T[^\]]+)\]\s*"
)


def coerce_message_timestamp(ts_value: Any, tz=None) -> Optional[float]:
    """Coerce a timestamp-like value to Unix epoch seconds.

    Accepts Unix epoch numbers, datetime objects, ISO strings, and the gateway's
    bracketed human-readable timestamp format. Returns ``None`` when the value
    cannot be interpreted.
    """
    if ts_value is None:
        return None

    if isinstance(ts_value, (int, float)):
        return float(ts_value)

    if hasattr(ts_value, "timestamp"):
        try:
            return float(ts_value.timestamp())
        except Exception:
            return None

    if isinstance(ts_value, str):
        text = ts_value.strip()
        if not text:
            return None
        parsed = _parse_timestamp_prefix(text, tz=tz)
        if parsed is not None:
            return parsed
        try:
            return float(text)
        except (TypeError, ValueError):
            pass
        try:
            dt = datetime.fromisoformat(text)
        except (TypeError, ValueError):
            try:
                dt = datetime.strptime(text, "%Y-%m-%dT%H:%M:%S%z")
            except (TypeError, ValueError):
                return None
        if dt.tzinfo is None:
            if tz is not None:
                dt = dt.replace(tzinfo=tz)
            else:
                dt = dt.astimezone()
        return float(dt.timestamp())

    return None


DEFAULT_AFTER_PAUSE_HOURS = 6.0


def timestamp_policy(user_config: Any) -> Tuple[bool, float]:
    """``(always, pause_seconds)`` from ``gateway.message_timestamps``.

    ``enabled: true`` stamps every user message. Otherwise a message is
    stamped only when it follows a pause of ``after_pause_hours`` (default 6,
    ``0`` turns the labels off), so the model learns the real date after a
    long silence without a prefix on every message.
    """
    gw = user_config.get("gateway") if isinstance(user_config, dict) else None
    mt = gw.get("message_timestamps") if isinstance(gw, dict) else None
    if mt is None or isinstance(mt, dict):
        mt = mt or {}
        try:
            hours = float(mt.get("after_pause_hours", DEFAULT_AFTER_PAUSE_HOURS))
        except (TypeError, ValueError):
            hours = DEFAULT_AFTER_PAUSE_HOURS
        return bool(mt.get("enabled", False)), max(hours, 0.0) * 3600
    return bool(mt), 0.0


def last_message_timestamp(history: Any, tz=None) -> Optional[float]:
    """Time of the latest stored message that carries one."""
    for msg in reversed(history or []):
        epoch = coerce_message_timestamp(msg.get("timestamp"), tz=tz) if isinstance(msg, dict) else None
        if epoch is not None:
            return epoch
    return None


def should_stamp(policy: Tuple[bool, float], ts_value: Any, prev_value: Any, tz=None) -> bool:
    always, pause_seconds = policy
    if always:
        return True
    ts = coerce_message_timestamp(ts_value, tz=tz)
    prev = coerce_message_timestamp(prev_value, tz=tz)
    return bool(pause_seconds and ts is not None and prev is not None and ts - prev >= pause_seconds)


def format_message_timestamp(ts_value: Any, tz=None) -> str:
    """Format a timestamp value as ``[Tue 2026-04-28 13:40:53 CEST]``."""
    epoch = coerce_message_timestamp(ts_value, tz=tz)
    if epoch is None:
        return ""
    if tz is not None:
        dt = datetime.fromtimestamp(epoch, tz=tz)
    else:
        dt = datetime.fromtimestamp(epoch).astimezone()
    return "[" + dt.strftime("%a %Y-%m-%d %H:%M:%S %Z") + "]"


def strip_leading_message_timestamps(content: str, tz=None) -> Tuple[str, Optional[float]]:
    """Strip one or more leading gateway timestamp prefixes from ``content``.

    Returns ``(clean_content, embedded_epoch)``.  If multiple timestamp prefixes
    are present, the timestamp closest to the actual message text wins.  That
    preserves the original platform-send time for legacy contaminated rows like
    ``[processing time] [platform time] [sender] message``.
    """
    if not isinstance(content, str) or not content:
        return content, None

    text = content
    embedded_epoch: Optional[float] = None

    while True:
        match = _HUMAN_TIMESTAMP_RE.match(text) or _ISO_TIMESTAMP_RE.match(text)
        if not match:
            break
        parsed = _parse_timestamp_match(match, tz=tz)
        if parsed is not None:
            embedded_epoch = parsed
        text = text[match.end():]

    return text, embedded_epoch


def render_user_content_with_timestamp(content: str, ts_value: Any = None, tz=None) -> str:
    """Render a user message for LLM context with exactly one timestamp prefix.

    Existing leading timestamp prefixes are removed first.  If such a prefix was
    present, its parsed time wins over ``ts_value``; otherwise ``ts_value`` is
    formatted and prepended.  If no timestamp is available, the cleaned content is
    returned unchanged.
    """
    clean_content, embedded_epoch = strip_leading_message_timestamps(content, tz=tz)
    effective_ts = embedded_epoch if embedded_epoch is not None else ts_value
    prefix = format_message_timestamp(effective_ts, tz=tz)
    if not prefix:
        return clean_content
    if clean_content:
        return f"{prefix} {clean_content}"
    return prefix


def _parse_timestamp_prefix(text: str, tz=None) -> Optional[float]:
    match = _HUMAN_TIMESTAMP_RE.match(text) or _ISO_TIMESTAMP_RE.match(text)
    if not match:
        return None
    return _parse_timestamp_match(match, tz=tz)


def _parse_timestamp_match(match: re.Match, tz=None) -> Optional[float]:
    if "iso" in match.groupdict() and match.group("iso"):
        iso_text = match.group("iso")
        try:
            dt = datetime.fromisoformat(iso_text)
        except ValueError:
            try:
                dt = datetime.strptime(iso_text, "%Y-%m-%dT%H:%M:%S%z")
            except ValueError:
                return None
        if dt.tzinfo is None:
            if tz is not None:
                dt = dt.replace(tzinfo=tz)
            else:
                dt = dt.astimezone()
        return float(dt.timestamp())

    date_part = match.group("date")
    time_part = match.group("time")
    try:
        dt = datetime.strptime(f"{date_part} {time_part}", "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None
    if tz is not None:
        dt = dt.replace(tzinfo=tz)
    else:
        dt = dt.astimezone()
    return float(dt.timestamp())

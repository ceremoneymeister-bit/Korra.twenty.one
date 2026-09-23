"""Rate limit tracking for inference API responses.

Captures x-ratelimit-* headers from provider responses and provides
formatted display for the /usage slash command.  Currently supports
the Nous Portal header format (also used by OpenRouter and OpenAI-compatible
APIs that follow the same convention).

Header schema (12 headers total):
    x-ratelimit-limit-requests          RPM cap
    x-ratelimit-limit-requests-1h       RPH cap
    x-ratelimit-limit-tokens            TPM cap
    x-ratelimit-limit-tokens-1h         TPH cap
    x-ratelimit-remaining-requests      requests left in minute window
    x-ratelimit-remaining-requests-1h   requests left in hour window
    x-ratelimit-remaining-tokens        tokens left in minute window
    x-ratelimit-remaining-tokens-1h     tokens left in hour window
    x-ratelimit-reset-requests          seconds until minute request window resets
    x-ratelimit-reset-requests-1h       seconds until hour request window resets
    x-ratelimit-reset-tokens            seconds until minute token window resets
    x-ratelimit-reset-tokens-1h         seconds until hour token window resets
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional


@dataclass
class RateLimitBucket:
    """One rate-limit window (e.g. requests per minute)."""

    limit: int = 0
    remaining: int = 0
    reset_seconds: float = 0.0
    captured_at: float = 0.0  # time.time() when this was captured

    @property
    def used(self) -> int:
        return max(0, self.limit - self.remaining)

    @property
    def usage_pct(self) -> float:
        if self.limit <= 0:
            return 0.0
        return (self.used / self.limit) * 100.0

    @property
    def remaining_seconds_now(self) -> float:
        """Estimated seconds remaining until reset, adjusted for elapsed time."""
        elapsed = time.time() - self.captured_at
        return max(0.0, self.reset_seconds - elapsed)


@dataclass
class RateLimitState:
    """Full rate-limit state parsed from response headers."""

    requests_min: RateLimitBucket = field(default_factory=RateLimitBucket)
    requests_hour: RateLimitBucket = field(default_factory=RateLimitBucket)
    tokens_min: RateLimitBucket = field(default_factory=RateLimitBucket)
    tokens_hour: RateLimitBucket = field(default_factory=RateLimitBucket)
    captured_at: float = 0.0  # when the headers were captured
    provider: str = ""

    @property
    def has_data(self) -> bool:
        return self.captured_at > 0

    @property
    def age_seconds(self) -> float:
        if not self.has_data:
            return float("inf")
        return time.time() - self.captured_at


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def parse_rate_limit_headers(
    headers: Mapping[str, str],
    provider: str = "",
) -> Optional[RateLimitState]:
    """Parse x-ratelimit-* headers into a RateLimitState.

    Returns None if no rate limit headers are present.
    """
    # Normalize to lowercase so lookups work regardless of how the server
    # capitalises headers (HTTP header names are case-insensitive per RFC 7230).
    lowered = {k.lower(): v for k, v in headers.items()}

    # Quick check: at least one rate limit header must exist
    has_any = any(k.startswith("x-ratelimit-") for k in lowered)
    if not has_any:
        return None

    now = time.time()

    def _bucket(resource: str, suffix: str = "") -> RateLimitBucket:
        # e.g. resource="requests", suffix="" -> per-minute
        #      resource="tokens", suffix="-1h" -> per-hour
        tag = f"{resource}{suffix}"
        return RateLimitBucket(
            limit=_safe_int(lowered.get(f"x-ratelimit-limit-{tag}")),
            remaining=_safe_int(lowered.get(f"x-ratelimit-remaining-{tag}")),
            reset_seconds=_safe_float(lowered.get(f"x-ratelimit-reset-{tag}")),
            captured_at=now,
        )

    return RateLimitState(
        requests_min=_bucket("requests"),
        requests_hour=_bucket("requests", "-1h"),
        tokens_min=_bucket("tokens"),
        tokens_hour=_bucket("tokens", "-1h"),
        captured_at=now,
        provider=provider,
    )


# ── Formatting ──────────────────────────────────────────────────────────


def _fmt_count(n: int) -> str:
    """Human-friendly number: 7999856 -> '8.0M', 33599 -> '33.6K', 799 -> '799'."""
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 10_000:
        return f"{n / 1_000:.1f}K"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(n)


def _fmt_seconds(seconds: float) -> str:
    """Seconds -> human-friendly duration: '58s', '2m 14s', '58m 57s', '1h 2m'."""
    s = max(0, int(seconds))
    if s < 60:
        return f"{s}s"
    if s < 3600:
        m, sec = divmod(s, 60)
        return f"{m}m {sec}s" if sec else f"{m}m"
    h, remainder = divmod(s, 3600)
    m = remainder // 60
    return f"{h}h {m}m" if m else f"{h}h"


def _bar(pct: float, width: int = 20) -> str:
    """ASCII progress bar: [████████░░░░░░░░░░░░] 40%."""
    filled = int(pct / 100.0 * width)
    filled = max(0, min(width, filled))
    empty = width - filled
    return f"[{'█' * filled}{'░' * empty}]"


def _bucket_line(label: str, bucket: RateLimitBucket, label_width: int = 14) -> str:
    """Format one bucket as a single line."""
    if bucket.limit <= 0:
        return f"  {label:<{label_width}}  (no data)"

    pct = bucket.usage_pct
    used = _fmt_count(bucket.used)
    limit = _fmt_count(bucket.limit)
    remaining = _fmt_count(bucket.remaining)
    reset = _fmt_seconds(bucket.remaining_seconds_now)

    bar = _bar(pct)
    return f"  {label:<{label_width}} {bar} {pct:5.1f}%  {used}/{limit} used  ({remaining} left, resets in {reset})"


def format_rate_limit_display(state: RateLimitState) -> str:
    """Format rate limit state for terminal/chat display."""
    if not state.has_data:
        return "No rate limit data yet — make an API request first."

    age = state.age_seconds
    if age < 5:
        freshness = "just now"
    elif age < 60:
        freshness = f"{int(age)}s ago"
    else:
        freshness = f"{_fmt_seconds(age)} ago"

    provider_label = state.provider.title() if state.provider else "Provider"

    lines = [
        f"{provider_label} Rate Limits (captured {freshness}):",
        "",
        _bucket_line("Requests/min", state.requests_min),
        _bucket_line("Requests/hr", state.requests_hour),
        "",
        _bucket_line("Tokens/min", state.tokens_min),
        _bucket_line("Tokens/hr", state.tokens_hour),
    ]

    # Add warnings if any bucket is getting hot
    warnings = []
    for label, bucket in [
        ("requests/min", state.requests_min),
        ("requests/hr", state.requests_hour),
        ("tokens/min", state.tokens_min),
        ("tokens/hr", state.tokens_hour),
    ]:
        if bucket.limit > 0 and bucket.usage_pct >= 80:
            reset = _fmt_seconds(bucket.remaining_seconds_now)
            warnings.append(f"  ⚠ {label} at {bucket.usage_pct:.0f}% — resets in {reset}")

    if warnings:
        lines.append("")
        lines.extend(warnings)

    return "\n".join(lines)


def format_rate_limit_compact(state: RateLimitState) -> str:
    """One-line compact summary for status bars / gateway messages."""
    if not state.has_data:
        return "No rate limit data."

    rm = state.requests_min
    tm = state.tokens_min
    rh = state.requests_hour
    th = state.tokens_hour

    parts = []
    if rm.limit > 0:
        parts.append(f"RPM: {rm.remaining}/{rm.limit}")
    if rh.limit > 0:
        parts.append(f"RPH: {_fmt_count(rh.remaining)}/{_fmt_count(rh.limit)} (resets {_fmt_seconds(rh.remaining_seconds_now)})")
    if tm.limit > 0:
        parts.append(f"TPM: {_fmt_count(tm.remaining)}/{_fmt_count(tm.limit)}")
    if th.limit > 0:
        parts.append(f"TPH: {_fmt_count(th.remaining)}/{_fmt_count(th.limit)} (resets {_fmt_seconds(th.remaining_seconds_now)})")

    return " | ".join(parts)


# ── Codex subscription quota ────────────────────────────────────────────
#
# The ChatGPT/Codex backend does not use the ``x-ratelimit-*`` family above.
# It reports how much of the subscription window is used:
#
# * as response headers ``x-codex-primary-used-percent``,
#   ``x-codex-primary-window-minutes``, ``x-codex-primary-reset-at`` (and the
#   same ``secondary`` trio; older builds send ``reset-after-seconds``);
# * inside the stream body as a ``rate_limits`` object — the shape the
#   Codex CLI itself records::
#
#       "rate_limits": {"limit_id": "codex",
#                       "primary": {"used_percent": 55.0,
#                                   "window_minutes": 10080,
#                                   "resets_at": 1790412544},
#                       "secondary": null, "plan_type": "pro"}
#
#   (the app-server protocol spells the same fields in camelCase).
#
# The owner sees this number on the dashboard, so the last known value is
# persisted per installation and survives a container restart. Nothing here
# ever makes a request of its own: the value only arrives with real traffic.

CODEX_QUOTA_FILE = "codex_quota.json"
_CODEX_QUOTA_VERSION = 1
# Rewrite an unchanged snapshot at most this often: the file carries the
# "captured at" time the dashboard shows, but every API call must not become
# a disk write.
_CODEX_QUOTA_REFRESH_SECONDS = 300.0
_codex_quota_memo: dict = {}


@dataclass(frozen=True)
class CodexQuotaWindow:
    """One subscription window: how much is used and when it resets."""

    used_percent: float
    window_minutes: Optional[int] = None
    resets_at: Optional[float] = None  # unix seconds

    def to_dict(self) -> dict:
        return {
            "used_percent": self.used_percent,
            "window_minutes": self.window_minutes,
            "resets_at": self.resets_at,
        }


@dataclass(frozen=True)
class CodexQuotaSnapshot:
    """The last subscription usage the backend reported."""

    primary: Optional[CodexQuotaWindow]
    secondary: Optional[CodexQuotaWindow] = None
    limit_id: str = "codex"
    plan_type: Optional[str] = None
    captured_at: float = 0.0
    source: str = ""

    def to_dict(self) -> dict:
        return {
            "version": _CODEX_QUOTA_VERSION,
            "limit_id": self.limit_id,
            "plan_type": self.plan_type,
            "captured_at": self.captured_at,
            "source": self.source,
            "primary": self.primary.to_dict() if self.primary else None,
            "secondary": self.secondary.to_dict() if self.secondary else None,
        }


def _first(mapping: Mapping[str, Any], *names: str) -> Any:
    for name in names:
        if name in mapping and mapping[name] is not None:
            return mapping[name]
    return None


def _as_float(value: Any) -> Optional[float]:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number in (float("inf"), float("-inf")):
        return None
    return number


def _epoch(value: Any) -> Optional[float]:
    """Unix seconds from an epoch number (s or ms) or an ISO-8601 string."""
    number = _as_float(value)
    if number is not None:
        if number <= 0:
            return None
        return number / 1000.0 if number > 1e11 else number
    if isinstance(value, str) and value.strip():
        from datetime import datetime, timezone

        text = value.strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.timestamp()
    return None


def _window(
    used: Any,
    minutes: Any,
    resets_at: Any,
    reset_after: Any,
    now: float,
) -> Optional[CodexQuotaWindow]:
    used_percent = _as_float(used)
    if used_percent is None:
        return None
    window_minutes = _as_float(minutes)
    reset = _epoch(resets_at)
    if reset is None:
        after = _as_float(reset_after)
        if after is not None and after >= 0:
            reset = now + after
    return CodexQuotaWindow(
        used_percent=max(0.0, min(100.0, used_percent)),
        window_minutes=int(window_minutes) if window_minutes and window_minutes > 0 else None,
        resets_at=reset,
    )


def _body_window(raw: Any, now: float) -> Optional[CodexQuotaWindow]:
    if not isinstance(raw, Mapping):
        return None
    return _window(
        _first(raw, "used_percent", "usedPercent"),
        _first(raw, "window_minutes", "windowDurationMins", "window_mins"),
        _first(raw, "resets_at", "reset_at", "resetsAt", "resetAt"),
        _first(raw, "reset_after_seconds", "resets_in_seconds", "resetAfterSeconds"),
        now,
    )


def parse_codex_rate_limits_body(
    payload: Any, *, now: Optional[float] = None
) -> Optional[CodexQuotaSnapshot]:
    """Parse a ``rate_limits`` object (or an event/response that carries one).

    Accepts the bare object, ``{"rate_limits": {...}}`` / ``{"rateLimits": ...}``
    wrappers and objects with attribute access (SDK models). Returns ``None``
    when nothing usable is present — never raises.
    """
    try:
        current = time.time() if now is None else float(now)
        data = payload
        if not isinstance(data, Mapping):
            extra = getattr(data, "model_extra", None)
            if isinstance(extra, Mapping) and (
                "rate_limits" in extra or "rateLimits" in extra
            ):
                data = extra
            else:
                limits = getattr(data, "rate_limits", None)
                if limits is None:
                    return None
                data = {"rate_limits": limits}
        outer = data
        inner = _first(outer, "rate_limits", "rateLimits")
        if inner is not None and not isinstance(inner, Mapping):
            dump = getattr(inner, "model_dump", None)
            inner = dump() if callable(dump) else None
        limits = inner if isinstance(inner, Mapping) else outer
        primary = _body_window(_first(limits, "primary", "primary_window"), current)
        secondary = _body_window(_first(limits, "secondary", "secondary_window"), current)
        if primary is None and secondary is None:
            return None
        if primary is None:
            primary, secondary = secondary, None
        plan = _first(limits, "plan_type", "planType") or _first(outer, "plan_type", "planType")
        limit_id = _first(limits, "limit_id", "limitId") or "codex"
        return CodexQuotaSnapshot(
            primary=primary,
            secondary=secondary,
            limit_id=str(limit_id),
            plan_type=str(plan) if isinstance(plan, str) and plan.strip() else None,
            captured_at=current,
            source="stream",
        )
    except Exception:
        return None


def parse_codex_rate_limit_headers(
    headers: Optional[Mapping[str, str]], *, now: Optional[float] = None
) -> Optional[CodexQuotaSnapshot]:
    """Parse ``x-<limit>-primary-used-percent`` style headers; never raises."""
    if not headers:
        return None
    try:
        current = time.time() if now is None else float(now)
        lowered = {str(k).lower(): v for k, v in headers.items()}
        suffix = "-primary-used-percent"
        prefixes = [
            key[: -len(suffix)]
            for key in lowered
            if key.startswith("x-") and key.endswith(suffix)
        ]
        if not prefixes:
            return None
        active = str(lowered.get("x-codex-active-limit") or "").strip().lower()
        prefix = (
            f"x-{active}" if active and f"x-{active}" in prefixes
            else "x-codex" if "x-codex" in prefixes
            else sorted(prefixes)[0]
        )

        def _headers_window(kind: str) -> Optional[CodexQuotaWindow]:
            base = f"{prefix}-{kind}"
            return _window(
                lowered.get(f"{base}-used-percent"),
                lowered.get(f"{base}-window-minutes"),
                lowered.get(f"{base}-reset-at"),
                lowered.get(f"{base}-reset-after-seconds"),
                current,
            )

        primary = _headers_window("primary")
        if primary is None:
            return None
        plan = lowered.get(f"{prefix}-plan-type") or lowered.get("x-codex-plan-type")
        return CodexQuotaSnapshot(
            primary=primary,
            secondary=_headers_window("secondary"),
            limit_id=prefix[2:] or "codex",
            plan_type=str(plan).strip() or None if plan else None,
            captured_at=current,
            source="headers",
        )
    except Exception:
        return None


def codex_quota_path(root: Optional[Any] = None):
    """Installation-wide location of the last known Codex quota."""
    from pathlib import Path

    if root is None:
        from korra_constants import get_default_hermes_root

        root = get_default_hermes_root()
    return Path(root) / "state" / CODEX_QUOTA_FILE


def _quota_signature(snapshot: CodexQuotaSnapshot) -> tuple:
    def _sig(window: Optional[CodexQuotaWindow]) -> tuple:
        if window is None:
            return ()
        reset = round(window.resets_at) if window.resets_at else None
        return (round(window.used_percent, 1), window.window_minutes, reset)

    return (snapshot.limit_id, snapshot.plan_type, _sig(snapshot.primary), _sig(snapshot.secondary))


def record_codex_quota(snapshot: Optional[CodexQuotaSnapshot], *, root: Optional[Any] = None) -> bool:
    """Persist the snapshot if it changed; return whether a write happened.

    Best effort by contract: a read-only disk or a race with another profile
    process must never disturb the agent turn that carried the value.
    """
    if snapshot is None or snapshot.primary is None:
        return False
    try:
        path = codex_quota_path(root)
        key = str(path)
        signature = _quota_signature(snapshot)
        last = _codex_quota_memo.get(key)
        if (
            last is not None
            and last[0] == signature
            and snapshot.captured_at - last[1] < _CODEX_QUOTA_REFRESH_SECONDS
        ):
            return False
        import json

        from utils import atomic_write_text

        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(
            path,
            json.dumps(snapshot.to_dict(), ensure_ascii=False, sort_keys=True),
            create_mode=0o600,
        )
        _codex_quota_memo[key] = (signature, snapshot.captured_at)
        return True
    except Exception:
        return False


def load_codex_quota(*, root: Optional[Any] = None) -> Optional[dict]:
    """Return the persisted snapshot as a plain dict, or ``None``.

    A damaged file reads as "no value": the dashboard then waits for the next
    real response instead of showing an invented percentage.
    """
    try:
        import json

        path = codex_quota_path(root)
        if not path.is_file():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(data, dict) or data.get("version") != _CODEX_QUOTA_VERSION:
        return None
    primary = data.get("primary")
    if not isinstance(primary, dict) or _as_float(primary.get("used_percent")) is None:
        return None
    if _as_float(data.get("captured_at")) is None:
        return None
    return data


def observe_codex_quota(
    *,
    headers: Optional[Mapping[str, str]] = None,
    body: Any = None,
    root: Optional[Any] = None,
) -> Optional[CodexQuotaSnapshot]:
    """Parse whatever the response carried and persist it. Never raises."""
    snapshot = None
    if body is not None:
        snapshot = parse_codex_rate_limits_body(body)
    if snapshot is None and headers is not None:
        snapshot = parse_codex_rate_limit_headers(headers)
    if snapshot is not None:
        record_codex_quota(snapshot, root=root)
    return snapshot

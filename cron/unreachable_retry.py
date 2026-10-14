"""Bounded automatic re-runs for a scheduled fire that never reached the model.

A recurring job whose run fails on a transient network/DNS error before ANY model call
completed (tunnel or VPN down, sleep/wake) used to sit out a whole period: a daily report
was simply lost. Now ``mark_job_run`` pulls ``next_run_at`` earlier along a 5/15/30-minute
ladder, and the scheduler holds the failure notice while a re-run is pending.
Provider congestion (429/503/capacity) before a completed call uses the same
bounded ladder and does not ask the owner to reconnect a healthy login.

Deliberately narrow: zero completed API calls means the MODEL did nothing and cost nothing,
but a job's own script (``script``) and monitor source (``monitor_script``/``monitor_url``,
whose new hash is committed before the agent starts) already ran, so such jobs are never
re-run automatically: the owner gets the ordinary failure notice. Recurring jobs only
(finite one-shots are pre-claimed by ``claim_dispatch``), never for manual runs,
and never later than the schedule's own next occurrence. A run that reaches the model
(either outcome) resets the ladder. ``cron.retry_unreachable: false`` disables.

State on the job (``unreachable_retry``) is ignored by older versions; they only see a
``next_run_at`` that is a few minutes ahead.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, Optional

RETRY_DELAYS_SECONDS: tuple[int, ...] = (300, 900, 1800)
STATE_KEY = "unreachable_retry"
_PRE_MODEL_ACTION_KEYS = ("script", "monitor_script", "monitor_url")

# A failure that surfaced as plain text (the agent loop reports "Connection error." without
# an exception chain) is matched by these phrases; transport exceptions go through
# ``_is_transient_provider_resolve_error``.
_UNREACHABLE_PHRASES = (
    "connection error",
    "network is unreachable",
    "name or service not known",
    "temporary failure in name resolution",
    "nodename nor servname",
    "connection refused",
    "cannot connect to host",
)


def retry_enabled() -> bool:
    try:
        from korra_cli.config import load_config

        cron_cfg = (load_config() or {}).get("cron")
    except Exception:
        return True
    return not isinstance(cron_cfg, dict) or cron_cfg.get("retry_unreachable") is not False


def is_model_unreachable_failure(exc: BaseException, agent: Any = None) -> bool:
    """True when *exc* is a transient network failure and *agent* (may be None) never
    completed a model call."""
    if int(getattr(agent, "session_api_calls", 0) or 0) > 0:
        return False
    from cron.incidents import is_transient_service_failure

    if is_transient_service_failure(exc):
        return True
    from cron.scheduler import _is_transient_provider_resolve_error

    if _is_transient_provider_resolve_error(exc):
        return True
    text = str(exc).lower()
    return any(phrase in text for phrase in _UNREACHABLE_PHRASES)


def retry_instant(job: Dict[str, Any], now: datetime) -> Optional[datetime]:
    """The instant the next ladder re-run of this failed, flagged run is parked at, or None
    when none is: not recurring, paused, manual run, disabled, a script or monitor source
    already ran before the model, ladder exhausted, or the schedule's own next occurrence
    is not later than the rung."""
    from cron.jobs import _ensure_aware, compute_next_run

    schedule = job.get("schedule") or {}
    if schedule.get("kind") not in ("cron", "interval") or job.get("state") == "paused":
        return None
    if job.get("manual_run_at") or not retry_enabled():
        return None
    if any(str(job.get(key) or "").strip() for key in _PRE_MODEL_ACTION_KEYS):
        return None
    attempt = int((job.get(STATE_KEY) or {}).get("attempt") or 0)
    if attempt >= len(RETRY_DELAYS_SECONDS):
        return None
    retry_dt = now + timedelta(seconds=RETRY_DELAYS_SECONDS[attempt])
    try:
        natural = _ensure_aware(datetime.fromisoformat(compute_next_run(schedule, now.isoformat())))
    except (TypeError, ValueError):
        return None  # no computable next occurrence: leave the schedule alone
    if natural <= retry_dt:
        return None
    return retry_dt


def will_retry(job: Dict[str, Any]) -> bool:
    from korra_time import now

    return retry_instant(job, now()) is not None


def park(job: Dict[str, Any], retry_dt: datetime) -> None:
    attempt = int((job.get(STATE_KEY) or {}).get("attempt") or 0)
    at = retry_dt.isoformat()
    job[STATE_KEY] = {"attempt": attempt + 1, "at": at, "expr": (job.get("schedule") or {}).get("expr")}
    job["next_run_at"] = at


def clear_state(job: Dict[str, Any]) -> None:
    job.pop(STATE_KEY, None)


def is_retry_fire(job: Dict[str, Any], next_run: Any) -> bool:
    """True for the exact instant parked by ``park``. It is off the cron lattice, so the
    stale-expression guard must let it fire; the expression fingerprint keeps a direct
    ``jobs.json`` schedule edit from inheriting the exemption."""
    state = job.get(STATE_KEY)
    return (
        isinstance(state, dict)
        and state.get("at") == next_run
        and state.get("expr") == (job.get("schedule") or {}).get("expr")
    )

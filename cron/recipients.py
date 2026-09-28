"""Confirmed recipients of an automation the owner set up.

Dmitry, 28.09.2026: automations the owner sets up run without
confirmations. Sending to somebody other than the owner — a client, a
colleague, a group — needs those recipients confirmed once, when the
automation is created or changed: the owner either picks them in the cabinet
form or approves the card the agent shows. A changing audience («clients with
a birthday today, from Bitrix») is confirmed once as a source; the job then
messages those people itself and reports whom it wrote to.

An unconfirmed recipient never sits in ``deliver``. It waits in
``recipients_pending`` together with the requested ``deliver`` value, and the
job is paused until the owner answers. So neither a scheduler tick between
two writes, a failed card, nor rolling the engine back to 0.21.14 (which knows
nothing of this policy) can send to somebody unconfirmed, and a one-shot
reminder is not spent before the answer (0.21.15 Astra review P1-2, P1-3,
P2-6). Approving restores the requested ``deliver`` and resumes the job.

Jobs saved before this policy, and jobs from the cabinet form or the REST API,
carry no ``recipients_policy`` and keep delivering as configured.
"""
from __future__ import annotations

import contextvars
import hashlib
import json
import logging
import os
import secrets
import threading
import time
from typing import Any

logger = logging.getLogger(__name__)

#: 1 — third-party targets of this job deliver only once confirmed.
POLICY_KEY = "recipients_policy"
#: {"targets": [...], "audience": str, "decision_id": str, "confirmed_at": float}
CONFIRMED_KEY = "recipients_confirmed"
#: {"targets": [...], "audience": str, "deliver": str|None, "decision_id": str}
PENDING_KEY = "recipients_pending"
#: Free-text source of people the job messages itself, as the owner described it.
AUDIENCE_KEY = "audience"

DECISION_KIND = "automation_recipients"
PAUSE_REASON = "Ждёт подтверждения получателей"
DENIED_REASON = "Получатели не подтверждены"
#: Bridged into the terminal of one running job (gateway.session_context).
RUNNING_JOB_ENV = "KORRA_CRON_RUN_TOKEN"

_RUNNING_JOB: contextvars.ContextVar[dict | None] = contextvars.ContextVar(
    "korra_running_cron_job", default=None
)


def bind_running_job(job: dict | None) -> contextvars.Token:
    """Bind the scheduled job this run belongs to (reset with the token)."""
    return _RUNNING_JOB.set(dict(job) if isinstance(job, dict) else None)


def reset_running_job(token: contextvars.Token) -> None:
    _RUNNING_JOB.reset(token)


#: Secrets of runs executing in THIS scheduler process: sha256 → grant.
#: Kept in memory only — nothing on disk to forge (0.21.15 Astra review A).
_LIVE_RUNS: dict[str, dict[str, str]] = {}
_LIVE_RUNS_LOCK = threading.Lock()
GRANT_ROUTE = "/api/cron/run-grant"


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _current_profile() -> str:
    try:
        from korra_cli.profiles import get_active_profile_name

        return get_active_profile_name() or "default"
    except Exception:
        return "default"


def issue_run_token(job: dict) -> str:
    """A secret for this run of ``job``; its terminal gets it through the bridge."""
    execution_id = str(job.get("execution_id") or "")
    if not execution_id:
        return ""
    token = secrets.token_urlsafe(32)
    with _LIVE_RUNS_LOCK:
        _LIVE_RUNS[_digest(token)] = {"job_id": str(job["id"]), "execution_id": execution_id,
                                      "profile": _current_profile()}
    return token


def retire_run_token(token: str) -> None:
    if not token:
        return
    with _LIVE_RUNS_LOCK:
        _LIVE_RUNS.pop(_digest(token), None)


def lookup_live_run(token: str, profile: str | None = None) -> dict | None:
    """The grant of a run this process issued and still executes, if any."""
    if not token:
        return None
    with _LIVE_RUNS_LOCK:
        grant = dict(_LIVE_RUNS.get(_digest(token)) or {})
    if not grant or (profile is not None and grant.get("profile") != profile):
        return None
    if not _execution_is_running(grant["execution_id"]):
        return None
    return grant


def _execution_is_running(execution_id: str) -> bool:
    """A live scheduler process holds this exact execution as running."""
    try:
        from cron.executions import _owner_is_live, _transaction

        with _transaction() as conn:
            row = conn.execute(
                "SELECT pid, process_started_at FROM executions WHERE id=? AND status='running'",
                (execution_id,),
            ).fetchone()
        return bool(row) and _owner_is_live(int(row["pid"]), row["process_started_at"])
    except Exception:
        logger.debug("Execution check for %s failed", execution_id, exc_info=True)
        return False


def _ask_scheduler(token: str) -> dict | None:
    """Ask the gateway that runs the scheduler whether it issued this secret.

    The answer comes from the scheduler's memory over its own authenticated
    local API; a file or an id the terminal could write proves nothing.
    """
    import urllib.request

    from agent.secret_scope import get_secret

    key = str(get_secret("API_SERVER_KEY", "") or "").strip()
    if not key:
        return None
    base = (os.environ.get("API_SERVER_PROXY_TARGET")
            or f"http://127.0.0.1:{os.environ.get('API_SERVER_PORT', '8642')}").rstrip("/")
    profile = _current_profile()
    prefix = "" if profile == "default" else f"/p/{profile}"
    request = urllib.request.Request(
        base + prefix + GRANT_ROUTE,
        data=json.dumps({"token": token}).encode(),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            grant = json.loads(response.read() or b"{}")
    except Exception:
        logger.debug("Run grant lookup failed", exc_info=True)
        return None
    return grant if isinstance(grant, dict) and grant.get("job_id") else None


def running_job() -> dict | None:
    """The scheduled job this code runs for, in the scheduler or its terminal.

    ``korra send`` from an agent's terminal is a child process: the context
    variable is gone, but the cron bridge carries this run's secret. Only the
    scheduler process that issued it can vouch for it, and only while its exact
    execution runs (0.21.15 Astra review P1-1 and re-checks A).
    """
    job = _RUNNING_JOB.get()
    if job:
        return job
    try:
        from gateway.session_context import get_session_env

        if get_session_env("KORRA_CRON_SESSION", "") != "1":
            return None
        token = str(get_session_env(RUNNING_JOB_ENV, "") or "").strip()
        if not token:
            return None
        grant = lookup_live_run(token) or _ask_scheduler(token)
        if not grant or not _execution_is_running(str(grant.get("execution_id") or "")):
            return None
        from cron.jobs import get_job

        job = get_job(str(grant.get("job_id") or ""))
        return job if job and job.get("id") == grant.get("job_id") else None
    except Exception:
        logger.debug("Running job lookup failed", exc_info=True)
        return None


def target_label(platform: str, chat_id, thread_id=None) -> str:
    label = f"{str(platform or '').strip().lower()}:{str(chat_id or '').strip()}"
    if thread_id not in (None, ""):
        label += f":{thread_id}"
    return label


def _confirmed(job: dict) -> dict:
    value = job.get(CONFIRMED_KEY)
    return value if isinstance(value, dict) else {}


def pending(job: dict | None) -> dict:
    value = (job or {}).get(PENDING_KEY)
    return value if isinstance(value, dict) else {}


def confirmed_audience(job: dict | None) -> str:
    return str(_confirmed(job or {}).get("audience") or "").strip()


def third_party_labels(job: dict) -> list[str]:
    """Delivery targets of ``job`` that are neither the owner's nor its creator's."""
    from cron.scheduler import BOT_CHAT_PLATFORM, _is_owner_side_chat, _resolve_delivery_targets

    labels = []
    for target in _resolve_delivery_targets(job):
        platform = str(target.get("platform") or "")
        if platform == BOT_CHAT_PLATFORM or _is_owner_side_chat(job, platform, target.get("chat_id")):
            continue
        labels.append(target_label(platform, target.get("chat_id"), target.get("thread_id")))
    return list(dict.fromkeys(labels))


def _label_confirmed(job: dict, label: str) -> bool:
    confirmed = {str(item) for item in (_confirmed(job).get("targets") or [])}
    # A confirmed chat covers its topics; a confirmed topic covers only itself.
    return label in confirmed or label.rsplit(":", 1)[0] in confirmed


def split_deliver(job: dict, deliver: str | None) -> tuple[str | None, list[str]]:
    """Split a requested ``deliver`` into what may go out now and what waits.

    Every comma part that reaches an unconfirmed third party is held back
    whole; the rest is returned as the value safe to store now.
    """
    held: list[str] = []
    keep: list[str] = []
    parts = [part.strip() for part in str(deliver or "origin").split(",") if part.strip()]
    for part in parts:
        unconfirmed = [label for label in third_party_labels({**job, "deliver": part})
                       if not _label_confirmed(job, label)]
        if unconfirmed:
            held.extend(unconfirmed)
        else:
            keep.append(part)
    if not held:
        return deliver, []
    return (",".join(keep) or "local"), list(dict.fromkeys(held))


def delivery_allowed(job: dict, platform: str, chat_id, thread_id=None) -> bool:
    """Whether a scheduled result may go to this target without a confirmation."""
    if job.get(POLICY_KEY) != 1:
        return True
    from cron.scheduler import BOT_CHAT_PLATFORM, _is_owner_side_chat

    if platform == BOT_CHAT_PLATFORM or _is_owner_side_chat(job, platform, chat_id):
        return True
    return _label_confirmed(job, target_label(platform, chat_id, thread_id))


def send_allowed_in_run(platform: str, chat_id, thread_id=None) -> bool:
    """A ``send_message`` inside a running job the owner set up needs no decision
    for recipients confirmed with the job, or for anyone when the owner
    confirmed the job's audience as a source."""
    job = running_job()
    if not job:
        return False
    try:
        from gateway.principal import cron_job_acts_for_owner

        if not cron_job_acts_for_owner(job):
            return False
    except Exception:
        return False
    if confirmed_audience(job):
        return True
    return _label_confirmed(job, target_label(platform, chat_id, thread_id))


def audience_run_note(job: dict) -> str:
    """Instruction appended to the run prompt of a job with a confirmed audience."""
    audience = confirmed_audience(job)
    if not audience:
        return ""
    return (
        "\n\n## Confirmed recipients\n"
        f"The owner confirmed that this automation may message: {audience}. "
        "Message only people from that source. End your final answer with the "
        "list of people you messaged in this run (name and chat), or say that "
        "nobody matched today."
    )


def pause_fields() -> dict[str, Any]:
    from cron.jobs import _hermes_now

    return {"enabled": False, "state": "paused", "paused_at": _hermes_now().isoformat(),
            "paused_reason": PAUSE_REASON}


def _resume_fields(job: dict) -> dict[str, Any]:
    """Resume a job paused for confirmation; a one-shot that is due fires now."""
    from cron.jobs import _hermes_now, compute_next_run

    next_run = compute_next_run(job["schedule"])
    if next_run is None and (job.get("schedule") or {}).get("kind") == "once":
        # The owner just confirmed: the reminder goes out now, not never.
        next_run = _hermes_now().isoformat()
    return {"enabled": True, "state": "scheduled", "paused_at": None,
            "paused_reason": None, "next_run_at": next_run}


def request_confirmation(job: dict, *, targets: list[str], audience: str) -> dict:
    """Show the owner one card confirming these recipients for this job."""
    from tools.send_message_tool import _decision_session_identity
    from tools.approval import notify_gateway_request
    from tools.effect_decisions import approval_payload, create_pending

    session_id, session_key, profile, owner_id = _decision_session_identity()
    payload = {
        "job_id": str(job["id"]),
        "job_name": str(job.get("name") or job["id"]),
        "schedule": str(job.get("schedule_display") or ""),
        "targets": list(targets),
        "audience": audience,
        "requested_at": time.time(),
    }
    decision, created = create_pending(
        kind=DECISION_KIND,
        owner_id=owner_id,
        profile=profile,
        source_session_id=session_id,
        source_session_key=session_key,
        payload=payload,
    )
    if created and decision["status"] == "pending":
        notify_gateway_request(session_key, approval_payload(decision))
    return decision


def retire_card(decision_id: str) -> None:
    """Close a card whose setting changed; its answer must not apply any more."""
    if not decision_id:
        return
    try:
        from tools import effect_decisions as decisions

        decision = decisions.get_decision(decision_id)
        if decision and decision["status"] == decisions.PENDING:
            decisions.decide(decision_id, source_session_id=decision["source_session_id"],
                             choice="deny")
    except Exception:
        logger.warning("Could not retire recipients card %s", decision_id, exc_info=True)


def accept_owner_form_edit(job_id: str, previous_deliver: Any = None) -> dict | None:
    """The owner chose recipients in the cabinet form or REST: that choice is
    the confirmation (0.21.15 Astra review P2-5).

    Only an actual change of ``deliver`` counts — the form re-sends the field
    on every save — and it answers only the recipients part of a wait: a
    pending audience keeps its card (re-check E).
    """
    from cron.jobs import mutate_job

    def compute(job: dict) -> dict | None:
        if job.get(POLICY_KEY) != 1 or job.get("deliver") == previous_deliver:
            return None
        confirmed = _confirmed(job)
        targets = list(dict.fromkeys([*(confirmed.get("targets") or []), *third_party_labels(job)]))
        updates: dict[str, Any] = {CONFIRMED_KEY: {**confirmed, "targets": targets,
                                                   "confirmed_at": time.time()}}
        waiting = pending(job)
        if waiting.get("targets"):
            if str(waiting.get("audience") or "").strip():
                updates[PENDING_KEY] = {**waiting, "targets": [], "deliver": None}
            else:
                retire_card(str(waiting.get("decision_id") or ""))
                updates[PENDING_KEY] = None
                if job.get("paused_reason") == PAUSE_REASON:
                    updates.update(_resume_fields(job))
        return updates

    return mutate_job(job_id, compute)


def resolve_recipient_decision(
    decision_id: str,
    choice: str,
    *,
    source_session_id: str = "",
    source_session_key: str = "",
    profile: str = "",
) -> dict:
    """Record the owner's answer; an approval confirms the recipients on the job."""
    from cron.jobs import get_job, mutate_job
    from tools import effect_decisions as decisions

    decision = decisions.get_decision(decision_id)
    if decision is None or decision.get("kind") != DECISION_KIND:
        raise decisions.DecisionConflict("recipients decision was not found")
    if decision["status"] in {decisions.EXECUTING, decisions.SUCCEEDED, decisions.FAILED,
                              decisions.UNKNOWN, decisions.EXPIRED, decisions.SUPERSEDED}:
        return decision
    if decision["status"] == decisions.DENIED:
        if choice != "deny":
            raise decisions.DecisionConflict("effect decision is already denied")
        return decision

    decision = decisions.decide(
        decision_id,
        source_session_id=source_session_id,
        source_session_key=source_session_key,
        profile=profile,
        choice=choice,
    )
    payload = decision["payload"]
    job_id = str(payload.get("job_id") or "")

    def current(job: dict) -> bool:
        return pending(job).get("decision_id") == decision_id

    if choice == "deny":
        def compute_deny(job: dict) -> dict | None:
            if not current(job):
                return None
            updates: dict[str, Any] = {PENDING_KEY: None}
            if job.get("paused_reason") == PAUSE_REASON:
                if _has_delivery(job) and job.get("schedule", {}).get("kind") != "once":
                    updates.update(_resume_fields(job))
                else:
                    updates["paused_reason"] = DENIED_REASON
            return updates

        mutate_job(job_id, compute_deny)
        return decision

    decisions.claim_execution(decision_id, expected_payload_sha256=decision["payload_sha256"])
    applied: dict[str, Any] = {}

    def compute_approve(job: dict) -> dict | None:
        # Checked and written under the store lock: a change that lands
        # between reading the job and saving the answer wins (re-check C).
        if not current(job):
            return None
        waiting = pending(job)
        confirmed = _confirmed(job)
        targets = list(dict.fromkeys([*(confirmed.get("targets") or []), *(waiting.get("targets") or [])]))
        audience = str(waiting.get("audience") or "").strip() or str(confirmed.get("audience") or "")
        updates = {
            CONFIRMED_KEY: {"targets": targets, "audience": audience,
                            "decision_id": decision_id, "confirmed_at": time.time()},
            PENDING_KEY: None,
        }
        if waiting.get("targets") and waiting.get("deliver") is not None:
            updates["deliver"] = waiting["deliver"]
        if job.get("paused_reason") == PAUSE_REASON:
            updates.update(_resume_fields(job))
        applied.update(targets=targets, audience=audience)
        return updates

    if get_job(job_id) is None:
        return decisions.finish_execution(
            decision_id, status=decisions.FAILED, outcome={"reason": "job_removed", "retry": False},
        )
    if mutate_job(job_id, compute_approve) is None:
        # The automation changed after this card was shown (0.21.15 Astra review P2-4).
        return decisions.finish_execution(
            decision_id, status=decisions.FAILED,
            outcome={"reason": "setting_changed", "retry": False},
        )
    return decisions.finish_execution(
        decision_id, status=decisions.SUCCEEDED, outcome={"job_id": job_id, **applied},
    )


def _has_delivery(job: dict) -> bool:
    return str(job.get("deliver") or "origin").strip().lower() not in {"", "local"}

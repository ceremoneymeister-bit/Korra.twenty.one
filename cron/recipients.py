"""Confirmed recipients of an automation the owner set up.

Dmitry, 28.09.2026: automations the owner sets up run without
confirmations. Sending to somebody other than the owner — a client, a
colleague, a group — needs those recipients confirmed once, when the
automation is created or changed: the owner either picks them in the cabinet
form or approves the card the agent shows. A changing audience («clients with
a birthday today, from Bitrix») is confirmed once as a source; the job then
messages those people itself and reports whom it wrote to.

Jobs saved before this policy, and jobs from the cabinet form or the REST
API, carry no ``recipients_policy`` and keep delivering as configured.
"""
from __future__ import annotations

import contextvars
import logging
import time
from typing import Any

logger = logging.getLogger(__name__)

#: 1 — third-party targets of this job deliver only once confirmed.
POLICY_KEY = "recipients_policy"
#: {"targets": [...], "audience": str, "decision_id": str, "confirmed_at": float}
CONFIRMED_KEY = "recipients_confirmed"
#: {"targets": [...], "audience": str, "decision_id": str}
PENDING_KEY = "recipients_pending"
#: Free-text source of people the job messages itself, as the owner described it.
AUDIENCE_KEY = "audience"

DECISION_KIND = "automation_recipients"

_RUNNING_JOB: contextvars.ContextVar[dict | None] = contextvars.ContextVar(
    "korra_running_cron_job", default=None
)


def bind_running_job(job: dict | None) -> contextvars.Token:
    """Bind the scheduled job this run belongs to (reset with the token)."""
    return _RUNNING_JOB.set(dict(job) if isinstance(job, dict) else None)


def reset_running_job(token: contextvars.Token) -> None:
    _RUNNING_JOB.reset(token)


def running_job() -> dict | None:
    return _RUNNING_JOB.get()


def target_label(platform: str, chat_id, thread_id=None) -> str:
    label = f"{str(platform or '').strip().lower()}:{str(chat_id or '').strip()}"
    if thread_id not in (None, ""):
        label += f":{thread_id}"
    return label


def _confirmed(job: dict) -> dict:
    value = job.get(CONFIRMED_KEY)
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


def request_confirmation(job: dict, *, targets: list[str], audience: str) -> dict | None:
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


def resolve_recipient_decision(
    decision_id: str,
    choice: str,
    *,
    source_session_id: str = "",
    source_session_key: str = "",
    profile: str = "",
) -> dict:
    """Record the owner's answer; an approval confirms the recipients on the job."""
    from cron.jobs import get_job, update_job
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
    job = get_job(str(payload.get("job_id") or ""))
    pending = job.get(PENDING_KEY) if job else None
    if choice == "deny":
        if job and isinstance(pending, dict) and pending.get("decision_id") == decision_id:
            update_job(job["id"], {PENDING_KEY: None})
        return decision

    decisions.claim_execution(decision_id, expected_payload_sha256=decision["payload_sha256"])
    if job is None:
        return decisions.finish_execution(
            decision_id, status=decisions.FAILED, outcome={"reason": "job_removed", "retry": False},
        )
    confirmed = _confirmed(job)
    targets = list(dict.fromkeys([*(confirmed.get("targets") or []), *(payload.get("targets") or [])]))
    audience = str(payload.get("audience") or "").strip() or str(confirmed.get("audience") or "")
    updates: dict[str, Any] = {
        CONFIRMED_KEY: {"targets": targets, "audience": audience,
                        "decision_id": decision_id, "confirmed_at": time.time()},
    }
    if isinstance(pending, dict) and pending.get("decision_id") == decision_id:
        updates[PENDING_KEY] = None
    update_job(job["id"], updates)
    return decisions.finish_execution(
        decision_id, status=decisions.SUCCEEDED,
        outcome={"job_id": job["id"], "targets": targets, "audience": audience},
    )

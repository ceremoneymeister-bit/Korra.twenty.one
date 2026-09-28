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
#: Sends the gateway made for a live run: execution id → send key → state.
#: A repeat of the same send — a retried request or `korra send` run again
#: after a lost answer — gets the stored result instead of a second delivery
#: (0.21.15 Astra review R1). The record belongs to the execution, not to one
#: stage's secret: a pre-check script and the agent turn after it are one run,
#: and a repeat across them must not deliver twice (third clean review P1-1).
#: It is dropped once the execution has finished.
_RUN_SENDS: dict[str, dict[str, dict]] = {}
_RUN_SENDS_CHANGED = threading.Condition(_LIVE_RUNS_LOCK)
#: The gateway sends for a live run here (gateway.platforms.api_server).
SEND_ROUTE = "/api/cron/run-send"
#: A send with attachments may take a while; the terminal waits for the answer.
SEND_TIMEOUT_SECONDS = 120


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
        _drop_finished_sends_locked()
        _LIVE_RUNS[_digest(token)] = {"job_id": str(job["id"]), "execution_id": execution_id,
                                      "profile": _current_profile()}
    return token


def _drop_finished_sends_locked() -> None:
    """Forget the sends of executions that no secret holds and that have ended."""
    live = {grant.get("execution_id") for grant in _LIVE_RUNS.values()}
    for execution_id in list(_RUN_SENDS):
        if execution_id not in live and not _execution_is_running(execution_id):
            _RUN_SENDS.pop(execution_id, None)


def retire_run_token(token: str) -> None:
    if not token:
        return
    with _RUN_SENDS_CHANGED:
        _LIVE_RUNS.pop(_digest(token), None)
        _drop_finished_sends_locked()
        _RUN_SENDS_CHANGED.notify_all()


def run_token_execution(token: str) -> str | None:
    """Выполнение, которому этот процесс выдал секрет, — без проверки живости."""
    if not token:
        return None
    with _LIVE_RUNS_LOCK:
        grant = _LIVE_RUNS.get(_digest(token))
    return str(grant.get("execution_id")) if grant else None


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


def running_job() -> dict | None:
    """The scheduled job this code runs for, in the process that runs it.

    ``korra send`` from an agent's terminal is a child process: it has this
    run's secret, but no way to tell the gateway from a server its own
    terminal started, so no answer about the secret is a right to send here.
    It hands the send to the gateway instead (:func:`send_through_scheduler`;
    0.21.15 Astra review P1-1 and re-checks A).
    """
    job = _RUNNING_JOB.get()
    if job:
        return job
    try:
        from gateway.session_context import get_session_env

        if get_session_env("KORRA_CRON_SESSION", "") != "1":
            return None
        token = str(get_session_env(RUNNING_JOB_ENV, "") or "").strip()
        grant = lookup_live_run(token)
        if not grant:
            return None
        from cron.jobs import get_job

        job = get_job(str(grant.get("job_id") or ""))
        return job if job and job.get("id") == grant.get("job_id") else None
    except Exception:
        logger.debug("Running job lookup failed", exc_info=True)
        return None


def _outcome_unknown() -> dict:
    return {
        "success": False,
        "status": "outcome_unknown",
        "error": ("Шлюз принял отправку, но не подтвердил результат. Проверьте, "
                  "дошло ли сообщение, прежде чем повторять."),
    }


def send_through_scheduler(target: str, message: str) -> dict | None:
    """Hand a send from a cron run's terminal to the gateway that runs the job.

    The gateway checks the secret against its own memory and the job's
    confirmed recipients, and sends itself. Nothing is sent from here: a
    server that answers in the gateway's place receives the message but no
    way to deliver it (0.21.15 Astra review A).

    Returns the gateway's send result; ``outcome_unknown`` when the request
    left but no answer came back, so no second copy goes out through a
    decision; or None when this is not a run's terminal or the gateway
    refused or could not be reached — the caller then asks the owner, as for
    any send an agent proposes.
    """
    if _RUNNING_JOB.get():
        return None
    try:
        from gateway.session_context import get_session_env

        if get_session_env("KORRA_CRON_SESSION", "") != "1":
            return None
        token = str(get_session_env(RUNNING_JOB_ENV, "") or "").strip()
    except Exception:
        return None
    if not token:
        return None
    import socket
    import urllib.error
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
        base + prefix + SEND_ROUTE,
        data=json.dumps({"token": token, "target": target, "message": message}).encode(),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=SEND_TIMEOUT_SECONDS) as response:
            result = json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as exc:
        # 4xx: not this gateway's live run, another profile, or a recipient the
        # job has not confirmed — nothing was sent. 5xx: it may have been.
        return _outcome_unknown() if exc.code >= 500 else None
    except urllib.error.URLError as exc:
        if isinstance(exc.reason, (TimeoutError, socket.timeout)):
            return _outcome_unknown()
        return None
    except (TimeoutError, socket.timeout):
        return _outcome_unknown()
    except Exception:
        logger.debug("Run send through the scheduler failed", exc_info=True)
        return _outcome_unknown()
    return result if isinstance(result, dict) and "success" in result else _outcome_unknown()


def send_for_live_run(grant: dict, target: str, message: str) -> dict | None:
    """Send in the gateway for the live run ``grant`` belongs to.

    Returns the send result, ``{"status": "not_confirmed"}`` when the job has
    not confirmed that recipient, or None when the job is gone.
    """
    from cron.jobs import get_job

    job = get_job(str(grant.get("job_id") or ""))
    if not job or job.get("id") != grant.get("job_id"):
        return None
    bound = bind_running_job({**job, "execution_id": grant.get("execution_id")})
    try:
        from tools.send_message_tool import send_for_running_job

        return json.loads(send_for_running_job({"action": "send", "target": target,
                                                "message": message}))
    finally:
        reset_running_job(bound)


def mask_outgoing_text(text: str) -> str:
    """Текст для постороннего получателя — с замаскированными секретами.

    Решение Дмитрия 28.09: автоматизации шлют подтверждённым получателям без
    карточки, но маскирование секретов сохраняется (чистое ревью Astra,
    P1-3). Граница выхода данных наружу, поэтому ``force``: общая настройка
    журналов её не отключает.
    """
    from agent.redact import redact_sensitive_text

    return redact_sensitive_text(text or "", force=True)


def _normalized_target(target: str) -> str:
    """Один получатель — один адрес: ``TELEGRAM:555`` и ``telegram:555``
    не две разные отправки (чистое ревью Astra, P1-2)."""
    platform, _, ref = str(target or "").partition(":")
    platform, ref = platform.strip().lower(), ref.strip()
    try:
        from tools.send_message_tool import (
            home_channel_chat_id,
            prepare_send_message_platforms,
            resolve_send_target,
        )

        prepare_send_message_platforms()
        if ref:
            chat_id, thread_id, error = resolve_send_target(platform, ref)
        else:
            # Адрес без чата уходит в домашний канал — тот же, что возьмёт
            # отправка (второе чистое ревью Astra, P2-1).
            from gateway.config import Platform, load_gateway_config

            chat_id = home_channel_chat_id(load_gateway_config(), Platform(platform), platform)
            thread_id, error = None, None
        if not error and chat_id:
            return target_label(platform, chat_id, thread_id)
    except Exception:
        logger.debug("Target normalisation failed for %s", platform, exc_info=True)
    return f"{platform}:{ref}"


def _send_key(target: str, message: str) -> str:
    return hashlib.sha256(json.dumps([_normalized_target(target), message],
                                     ensure_ascii=False).encode()).hexdigest()


def _sent(result) -> bool:
    return isinstance(result, dict) and result.get("success") is True


def _maybe_delivered(result) -> bool:
    """Сервис мог принять сообщение: отправка дошла до транспорта и не
    подтвердилась (``send_for_running_job`` помечает это ``outcome_unknown``)."""
    return isinstance(result, dict) and result.get("status") == "outcome_unknown"


def _unknown_repeat(entry: dict) -> dict:
    return {**entry.get("result", {}), "success": False, "status": "outcome_unknown", "repeat": True,
            "error": ("Сервис мог уже получить это сообщение; повторно не отправлялось. "
                      "Проверьте доставку, прежде чем отправлять снова.")}


def send_once_for_live_run(token: str, grant: dict, target: str, message: str,
                           *, wait_seconds: float = SEND_TIMEOUT_SECONDS) -> dict | None:
    """:func:`send_for_live_run`, at most once per run for the same send.

    The same recipient and content within one run is one send: a repeat
    returns the stored result marked ``repeat``, and a repeat that arrives
    while the first is still sending waits for it rather than sending in
    parallel. Different content or another recipient is another send.

    A delivered send is remembered. So is one the service may have accepted —
    a transport error or exception after the send left (``outcome_unknown``):
    repeating it could deliver a second copy, so a repeat reports the unknown
    outcome instead of sending (clean Astra review P1-2). A refusal before
    anything was sent — an unconfirmed recipient, a bad target, a platform
    that is not set up — is forgotten, and the next attempt tries again.
    Nothing outlives the execution: its record goes once it has finished.

    The text is masked before anything else, so the key and the send both see
    what actually goes out (P1-3).
    """
    message = mask_outgoing_text(message)
    digest, key = _digest(token), _send_key(target, message)
    deadline = time.monotonic() + wait_seconds
    with _RUN_SENDS_CHANGED:
        while True:
            live = _LIVE_RUNS.get(digest)
            if live is None:
                return None
            run = str(live.get("execution_id") or digest)
            entry = _RUN_SENDS.setdefault(run, {}).get(key)
            if entry is None:
                _RUN_SENDS[run][key] = {"state": "sending"}
                break
            if entry["state"] == "sent":
                return {**entry["result"], "repeat": True,
                        "note": "Это сообщение уже отправлено в этом запуске; повторно не отправлялось."}
            if entry["state"] == "unknown":
                return _unknown_repeat(entry)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return {"success": False, "status": "in_progress",
                        "error": "Это сообщение ещё отправляется в этом запуске. Повторять не нужно."}
            _RUN_SENDS_CHANGED.wait(remaining)
    result = None
    try:
        result = send_for_live_run(grant, target, message)
        return result
    finally:
        with _RUN_SENDS_CHANGED:
            sends = _RUN_SENDS.get(run)
            if sends is not None:
                if _sent(result):
                    sends[key] = {"state": "sent", "result": dict(result)}
                elif _maybe_delivered(result):
                    sends[key] = {"state": "unknown", "result": dict(result)}
                else:
                    sends.pop(key, None)
            _RUN_SENDS_CHANGED.notify_all()


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
    """Может ли отправка изнутри запуска задания обойтись без решения.

    Тот же договор, что у автодоставки результата (``delivery_allowed``), чтобы
    `korra send` и результат не расходились (третье чистое ревью Astra, P1-2):

    1. собственный личный чат создателя — у любого задания: человек
       автоматизирует для себя (решение Дмитрия 28.09);
    2. задание владельца — его каналы, подтверждённая аудитория и цели;
    3. задание, созданное формой или до правил получателей, — ровно
       настроенные цели ``deliver``: получателя выбрал владелец, но
       произвольный адрес из `korra send` этим не разрешён.
    """
    job = running_job()
    if not job:
        return False
    try:
        from cron.scheduler import _is_creator_private_chat, _is_owner_side_chat, _resolve_delivery_targets
    except Exception:
        return False
    if _is_creator_private_chat(job, platform, chat_id):
        return True
    try:
        from gateway.principal import cron_job_acts_for_owner

        if not cron_job_acts_for_owner(job):
            return False
    except Exception:
        return False
    try:
        if _is_owner_side_chat(job, platform, chat_id):
            return True
    except Exception:
        pass
    if confirmed_audience(job):
        return True
    label = target_label(platform, chat_id, thread_id)
    if _label_confirmed(job, label):
        return True
    if job.get(POLICY_KEY) != 1:
        try:
            targets = _resolve_delivery_targets(job)
        except Exception:
            targets = []
        return any(
            target_label(t.get("platform"), t.get("chat_id"), t.get("thread_id")) == label
            for t in targets
        )
    return False


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

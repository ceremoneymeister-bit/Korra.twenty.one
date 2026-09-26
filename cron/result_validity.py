"""Explicit lifetime of scheduled results; absence never implies expiry."""
from __future__ import annotations

import hashlib
import json
import time

EXPIRED_DELIVERY = "Результат устарел: срок отправки истёк. Текст сохранён в истории."


def validate_policy(job: dict) -> None:
    ttl = job.get("delivery_ttl_seconds")
    if ttl is not None and (isinstance(ttl, bool) or not isinstance(ttl, int) or not 1 <= ttl <= 31536000):
        raise ValueError("Срок актуальности должен быть от 1 секунды до 365 дней или отсутствовать.")
    if job.get("pending_result_policy", "all") not in ("all", "latest"):
        raise ValueError("Режим очереди результатов: all или latest.")


def result_validity(job: dict, target: str, account: str = "") -> dict:
    validate_policy(job)
    occurrence = job.get("_result_started_at") or time.time()
    value = {"version": 1, "occurrence_at": occurrence,
             "job_id": str(job.get("id") or ""), "execution_id": str(job.get("execution_id") or "")}
    if job.get("delivery_ttl_seconds") is not None:
        value["expires_at"] = occurrence + job["delivery_ttl_seconds"]
    if job.get("pending_result_policy") == "latest":
        binding = [value["job_id"], target, account]
        value["supersession_key"] = hashlib.sha256(json.dumps(binding).encode()).hexdigest()
    return value


def delivery_expired(job: dict) -> bool:
    validity = result_validity(job, "")
    return "expires_at" in validity and time.time() >= validity["expires_at"]


def project_delivery(executions: list[dict]) -> list[dict]:
    """Resolve approvals at read time without rewriting completed executions."""
    from tools.effect_decisions import execution_delivery_states, EffectDecisionStoreUnavailable

    try:
        receipts = execution_delivery_states(executions)
    except EffectDecisionStoreUnavailable:
        receipts = {run["id"]: ["unknown"] for run in executions
                    if run.get("delivery_outcome") == "waiting_decision"}
    results = []
    for run in executions:
        states = set(receipts.get(run["id"], []))
        if not states:
            results.append(run)
            continue
        if states.intersection({"pending", "approved", "executing"}):
            outcome = "waiting_decision"
        elif "unknown" in states:
            outcome = "unknown"
        elif len(states) > 1:
            outcome = "partial"
        else:
            state = next(iter(states))
            outcome = "delivered" if state == "succeeded" else state
        results.append({**run, "delivery_outcome": outcome, "decision_statuses": sorted(states)})
    return results

"""Learning-candidate receipts projected from the existing skill ledger.

The skill ledger remains the only durable store.  A skill mutation may carry
an ephemeral ``_learning_request``; :mod:`tools.skill_ledger` converts it to a
small, anonymised receipt before appending JSONL.  Raw source/corrected examples
and client-specific markers are hashed and are never written to the ledger.

Verification, revision and cancellation are later append-only ledger events.
This module projects those events into the owner-facing state used by the
profile learning panel.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional

from korra_constants import get_hermes_home


LEARNING_RECEIPT_VERSION = 1
LEARNING_SCOPES = frozenset({"owner_preference", "reusable_method"})
MAX_RULE_CHARS = 1_000
MAX_APPLIES_TO_CHARS = 240
MAX_RUBRIC_ITEMS = 8
MAX_RUBRIC_ITEM_CHARS = 240
MAX_EXAMPLE_CHARS = 100_000
MAX_PRIVATE_MARKERS = 20


class LearningReceiptError(ValueError):
    """A requested learning receipt is incomplete or unsafe to persist."""


def _normalise_text(value: Any) -> str:
    return " ".join(str(value or "").replace("\x00", "").split())


def text_hash(value: str) -> str:
    """Stable SHA-256 for source/result examples without retaining content."""
    return hashlib.sha256(_normalise_text(value).encode("utf-8")).hexdigest()


def _relative_snapshot_path(path: str) -> str:
    candidate = Path(path)
    try:
        return candidate.relative_to(get_hermes_home()).as_posix()
    except ValueError:
        # Ledger path validation is the security boundary.  This fallback only
        # keeps the digest deterministic for legacy/test snapshots.
        return candidate.name


def snapshot_digest(items: Iterable[Mapping[str, Any]]) -> str:
    """Digest a complete file manifest using profile-relative paths."""
    manifest = sorted(
        (
            _relative_snapshot_path(str(item.get("path", ""))),
            str(item.get("sha256", "")),
        )
        for item in items
        if item.get("path") and item.get("sha256")
    )
    payload = json.dumps(manifest, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _rubric(raw: Any) -> list[str]:
    if not isinstance(raw, list):
        raise LearningReceiptError("learning.rubric must be a non-empty list")
    result: list[str] = []
    for item in raw:
        text = _normalise_text(item)
        if not text:
            continue
        if len(text) > MAX_RUBRIC_ITEM_CHARS:
            raise LearningReceiptError(
                f"each learning rubric item is limited to {MAX_RUBRIC_ITEM_CHARS} characters"
            )
        if text not in result:
            result.append(text)
    if not result:
        raise LearningReceiptError("learning.rubric needs at least one observable check")
    if len(result) > MAX_RUBRIC_ITEMS:
        raise LearningReceiptError(
            f"learning.rubric is limited to {MAX_RUBRIC_ITEMS} checks"
        )
    return result


def sanitise_for_staging(request: Mapping[str, Any]) -> dict[str, Any]:
    """Remove raw examples/markers before an approval queue persists them.

    The queued mutation still needs enough information to create the normal
    receipt after approval. Exact target-file validation remains deferred
    until the mutation has actually written the rule.
    """
    if not isinstance(request, Mapping):
        raise LearningReceiptError("learning must be an object")
    source = str(request.get("source_example") or "")
    approved = str(request.get("approved_example") or "")
    if not source.strip() or not approved.strip():
        raise LearningReceiptError(
            "learning.source_example and learning.approved_example are required"
        )
    if len(source) > MAX_EXAMPLE_CHARS or len(approved) > MAX_EXAMPLE_CHARS:
        raise LearningReceiptError(
            f"learning examples are limited to {MAX_EXAMPLE_CHARS} characters each"
        )
    source_hash = text_hash(source)
    approved_hash = text_hash(approved)
    if source_hash == approved_hash:
        raise LearningReceiptError("source and approved examples must differ")

    rule = _normalise_text(request.get("rule"))
    applies_to = _normalise_text(request.get("applies_to"))
    rubric = _rubric(request.get("rubric"))
    markers_raw = request.get("private_markers") or []
    if not isinstance(markers_raw, list):
        raise LearningReceiptError("learning.private_markers must be a list")
    markers = [
        marker
        for marker in (_normalise_text(value) for value in markers_raw)
        if marker
    ]
    if len(markers) > MAX_PRIVATE_MARKERS:
        raise LearningReceiptError(
            f"learning.private_markers is limited to {MAX_PRIVATE_MARKERS} values"
        )
    persisted_guidance = " ".join([rule, applies_to, *rubric]).casefold()
    if any(marker.casefold() in persisted_guidance for marker in markers):
        raise LearningReceiptError(
            "learning guidance contains a client-specific marker; generalise it first"
        )

    return {
        "scope": request.get("scope"),
        "applies_to": applies_to,
        "rule": rule,
        "file_path": request.get("file_path"),
        "rubric": rubric,
        "source_hash": source_hash,
        "approved_hash": approved_hash,
        "private_marker_hashes": sorted({text_hash(marker) for marker in markers}),
    }


def prepare_candidate(
    request: Mapping[str, Any],
    *,
    skill: str,
    action: str,
    before: list[dict[str, str]],
    after: list[dict[str, str]],
    file_path: Optional[str] = None,
    prehashed: bool = False,
) -> dict[str, Any]:
    """Validate and anonymise model-supplied learning metadata.

    The reusable ``rule`` must occur verbatim in the resulting target file.
    That invariant makes later owner edits precise instead of turning a lesson
    card into metadata disconnected from the actual skill.
    """
    if not isinstance(request, Mapping):
        raise LearningReceiptError("learning must be an object")

    scope = _normalise_text(request.get("scope"))
    if scope not in LEARNING_SCOPES:
        raise LearningReceiptError(
            "learning.scope must be owner_preference or reusable_method; "
            "one-off corrections do not belong in a reusable skill"
        )
    rule = _normalise_text(request.get("rule"))
    if len(rule) < 10 or len(rule) > MAX_RULE_CHARS:
        raise LearningReceiptError(
            f"learning.rule must contain 10-{MAX_RULE_CHARS} characters"
        )
    applies_to = _normalise_text(request.get("applies_to"))
    if not applies_to or len(applies_to) > MAX_APPLIES_TO_CHARS:
        raise LearningReceiptError(
            f"learning.applies_to must contain 1-{MAX_APPLIES_TO_CHARS} characters"
        )
    rubric = _rubric(request.get("rubric"))

    if prehashed:
        source_hash = str(request.get("source_hash") or "")
        approved_hash = str(request.get("approved_hash") or "")
        if not all(
            len(value) == 64 and all(char in "0123456789abcdef" for char in value)
            for value in (source_hash, approved_hash)
        ):
            raise LearningReceiptError("staged learning receipt has invalid example hashes")
    else:
        source = str(request.get("source_example") or "")
        approved = str(request.get("approved_example") or "")
        if not source.strip() or not approved.strip():
            raise LearningReceiptError(
                "learning.source_example and learning.approved_example are required"
            )
        if len(source) > MAX_EXAMPLE_CHARS or len(approved) > MAX_EXAMPLE_CHARS:
            raise LearningReceiptError(
                f"learning examples are limited to {MAX_EXAMPLE_CHARS} characters each"
            )
        source_hash = text_hash(source)
        approved_hash = text_hash(approved)
    if source_hash == approved_hash:
        raise LearningReceiptError("source and approved examples must differ")

    if prehashed:
        private_marker_hashes = request.get("private_marker_hashes") or []
        if not isinstance(private_marker_hashes, list) or not all(
            isinstance(value, str)
            and len(value) == 64
            and all(char in "0123456789abcdef" for char in value)
            for value in private_marker_hashes
        ):
            raise LearningReceiptError("staged learning receipt has invalid marker hashes")
        if len(private_marker_hashes) > MAX_PRIVATE_MARKERS:
            raise LearningReceiptError(
                f"learning.private_marker_hashes is limited to {MAX_PRIVATE_MARKERS} values"
            )
    else:
        private_markers_raw = request.get("private_markers") or []
        if not isinstance(private_markers_raw, list):
            raise LearningReceiptError("learning.private_markers must be a list")
        private_markers = [
            marker
            for marker in (_normalise_text(value) for value in private_markers_raw)
            if marker
        ]
        if len(private_markers) > MAX_PRIVATE_MARKERS:
            raise LearningReceiptError(
                f"learning.private_markers is limited to {MAX_PRIVATE_MARKERS} values"
            )
        persisted_guidance = " ".join([rule, applies_to, *rubric]).casefold()
        leaked = [
            marker
            for marker in private_markers
            if marker.casefold() in persisted_guidance
        ]
        if leaked:
            raise LearningReceiptError(
                "learning guidance contains a client-specific marker; generalise it first"
            )
        private_marker_hashes = [text_hash(marker) for marker in private_markers]

    target_path = str(file_path or request.get("file_path") or "SKILL.md")
    target = next(
        (Path(str(item.get("path", ""))) for item in after if str(item.get("path", "")).endswith(target_path)),
        None,
    )
    try:
        target_text = target.read_text(encoding="utf-8") if target else ""
    except (OSError, UnicodeError):
        target_text = ""
    if rule not in target_text:
        raise LearningReceiptError(
            "learning.rule must exactly match reusable guidance written to the target skill file"
        )

    pair_hash = hashlib.sha256(
        f"{source_hash}:{approved_hash}".encode("ascii")
    ).hexdigest()
    dedupe_key = hashlib.sha256(
        f"{skill}:{scope}:{pair_hash}".encode("utf-8")
    ).hexdigest()
    return {
        "version": LEARNING_RECEIPT_VERSION,
        "scope": scope,
        "applies_to": applies_to,
        "rule": rule,
        "skill": skill,
        "action": action,
        "target_path": target_path,
        "source_hash": source_hash,
        "approved_hash": approved_hash,
        "source_pair_hash": pair_hash,
        "dedupe_key": dedupe_key,
        "base_skill_version": snapshot_digest(before),
        "applied_skill_version": snapshot_digest(after),
        "rubric": rubric,
        "private_marker_hashes": sorted(set(private_marker_hashes)),
        "verification": {"status": "pending", "outcome": None},
    }


def _status_label(status: str) -> str:
    return {
        "saved_unverified": "сохранено, ещё не проверено",
        "verified": "проверено",
        "verification_failed": "проверка не пройдена",
        "cancelled": "отменено",
    }.get(status, status)


def project_candidates(rows_newest_first: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Project append-only ledger rows into current learning candidates."""
    candidates: dict[str, dict[str, Any]] = {}
    by_dedupe: dict[str, str] = {}

    for row in reversed(rows_newest_first):
        evidence = row.get("evidence") if isinstance(row.get("evidence"), dict) else {}
        candidate = evidence.get("learning_candidate")
        if isinstance(candidate, dict):
            dedupe_key = str(candidate.get("dedupe_key") or "")
            existing_id = by_dedupe.get(dedupe_key)
            if existing_id and candidates.get(existing_id, {}).get("status") != "cancelled":
                continue
            candidate_id = str(row.get("id") or "")
            if not candidate_id:
                continue
            state = {
                **candidate,
                "id": candidate_id,
                "created_at": row.get("ts"),
                "updated_at": row.get("ts"),
                "status": "saved_unverified",
                "status_label": _status_label("saved_unverified"),
                "revision": candidate_id,
                "mutation_ids": [candidate_id],
                "verification": {"status": "pending", "outcome": None},
                "rollback_available": True,
            }
            candidates[candidate_id] = state
            if dedupe_key:
                by_dedupe[dedupe_key] = candidate_id
            continue

        revision = evidence.get("learning_revision")
        if isinstance(revision, dict):
            candidate_id = str(revision.get("candidate_id") or "")
            state = candidates.get(candidate_id)
            if not state:
                continue
            mutation_id = str(
                evidence.get("learning_mutation_id") or row.get("id") or ""
            )
            event_id = str(row.get("id") or mutation_id)
            state.update({
                "rule": revision.get("rule", state.get("rule")),
                "applies_to": revision.get("applies_to", state.get("applies_to")),
                "applied_skill_version": revision.get(
                    "applied_skill_version", state.get("applied_skill_version")
                ),
                "target_path": revision.get("target_path", state.get("target_path")),
                "updated_at": row.get("ts"),
                "status": "saved_unverified",
                "status_label": _status_label("saved_unverified"),
                "revision": event_id,
                "verification": {"status": "pending", "outcome": None},
            })
            if mutation_id and mutation_id not in state["mutation_ids"]:
                state["mutation_ids"].append(mutation_id)
            continue

        event_candidate_id = str(evidence.get("learning_candidate_id") or "")
        state = candidates.get(event_candidate_id)
        if not state:
            continue
        action = row.get("action")
        if action == "learning-verification":
            outcome = str(evidence.get("outcome") or "")
            status = "verified" if outcome == "pass" else "verification_failed"
            state.update({
                "status": status,
                "status_label": _status_label(status),
                "revision": row.get("id"),
                "updated_at": row.get("ts"),
                "verification": {
                    "status": status,
                    "outcome": outcome,
                    "example_hash": evidence.get("example_hash"),
                    "response_hash": evidence.get("response_hash"),
                    "checks": evidence.get("checks") or [],
                    "no_foreign_identifiers": evidence.get("no_foreign_identifiers"),
                    "corrections_count": evidence.get("corrections_count", 0),
                    "elapsed_ms": evidence.get("elapsed_ms", 0),
                    "model_usage": evidence.get("model_usage"),
                    "verified_at": row.get("ts"),
                },
            })
        elif action == "learning-cancelled":
            state.update({
                "status": "cancelled",
                "status_label": _status_label("cancelled"),
                "revision": row.get("id"),
                "updated_at": row.get("ts"),
                "rollback_available": False,
            })

    result = list(candidates.values())
    result.sort(key=lambda item: str(item.get("updated_at") or ""), reverse=True)
    return result


def find_candidate(
    rows_newest_first: list[dict[str, Any]], candidate_id: str
) -> Optional[dict[str, Any]]:
    return next(
        (item for item in project_candidates(rows_newest_first) if item.get("id") == candidate_id),
        None,
    )


def find_active_by_dedupe(
    rows_newest_first: list[dict[str, Any]], dedupe_key: str
) -> Optional[dict[str, Any]]:
    return next(
        (
            item
            for item in project_candidates(rows_newest_first)
            if item.get("dedupe_key") == dedupe_key and item.get("status") != "cancelled"
        ),
        None,
    )

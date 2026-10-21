"""Gateway response filtering helpers.

These helpers operate at the gateway boundary: they decide whether a completed
agent turn should be delivered to the chat, not what should be persisted in the
conversation history.
"""

from __future__ import annotations

import unicodedata
from typing import Any

# Canonical model-emitted control token for intentional silence.
SILENT_REPLY_TOKEN = "NO_REPLY"

# Exact whole-response markers that mean "the agent intentionally chose not to
# reply".  Keep this list small and explicit; arbitrary empty output remains an
# error/empty-response path, not silence.
LIVE_GATEWAY_SILENT_MARKERS = frozenset({
    "[SILENT]",
    "SILENT",
    "NO_REPLY",
    "NO REPLY",
})


def _canonical_silence_candidate(text: str) -> str:
    return " ".join(text.strip().upper().split())


def _strip_edge_silence_punctuation(text: str) -> str:
    """Strip stray edge punctuation without erasing marker structure.

    Models sometimes emit ``.NO_REPLY`` or ``*NO_REPLY*`` instead of the exact
    marker. Keep square brackets structural so malformed ``[SILENT`` does not
    become ``SILENT``.
    """
    start = 0
    end = len(text)
    while start < end and text[start] not in "[]" and unicodedata.category(text[start]).startswith("P"):
        start += 1
    while end > start and text[end - 1] not in "[]" and unicodedata.category(text[end - 1]).startswith("P"):
        end -= 1
    return text[start:end].strip()


def _canonical_silence_candidates(text: str) -> tuple[str, ...]:
    exact = _canonical_silence_candidate(text)
    stripped = _strip_edge_silence_punctuation(text.strip())
    if stripped == text.strip():
        return (exact,)
    fallback = _canonical_silence_candidate(stripped)
    return (exact, fallback)


def is_intentional_silence_response(response: Any) -> bool:
    """Return True only when ``response`` is exactly a silence marker.

    Substantive prose that merely mentions ``NO_REPLY`` or ``[SILENT]`` must be
    delivered normally.  A blank response is also not silence; blank output is
    handled by the empty-response failure path.
    """
    if not isinstance(response, str):
        return False
    stripped = response.strip()
    if not stripped:
        return False
    if len(stripped) > 64:
        return False
    return any(candidate in LIVE_GATEWAY_SILENT_MARKERS for candidate in _canonical_silence_candidates(stripped))


def is_autonomous_silence_response(response: Any) -> bool:
    """Loose silence matcher for autonomous lanes (cron, webhook).

    Autonomous lanes instruct the agent to emit ``[SILENT]`` when a tick
    produced nothing worth a human's attention, and models reliably bracket
    the marker with a short note explaining why they stayed quiet.  Unlike
    :func:`is_intentional_silence_response` (the interactive-chat rule, which
    demands the response be EXACTLY a marker), this suppresses when a marker
    is the whole response, sits on its own first or last line, or the
    bracketed sentinel opens the response (the documented
    ``[SILENT] No changes detected`` pattern).  A token buried mid-sentence
    in a genuine report is still delivered.

    Shares :data:`LIVE_GATEWAY_SILENT_MARKERS` so the interactive and
    autonomous marker sets can never drift apart.
    """
    if not isinstance(response, str):
        return False
    stripped = response.strip()
    if not stripped:
        return False

    def _is_token(line: str) -> bool:
        return _canonical_silence_candidate(line) in LIVE_GATEWAY_SILENT_MARKERS

    # Whole response is exactly a token.
    if _is_token(stripped):
        return True
    # Marker on its own first or last line (leading/trailing note on a
    # separate line — e.g. "2 deals filtered\n\n[SILENT]").
    lines = [ln for ln in stripped.splitlines() if ln.strip()]
    if lines and (_is_token(lines[0]) or _is_token(lines[-1])):
        return True
    # Bracketed sentinel used as a same-line prefix — the documented pattern
    # "[SILENT] No changes detected".  Restricted to the bracketed form so a
    # bare word like "Silent retry succeeded" is NOT swallowed.
    if stripped.upper().startswith("[SILENT]"):
        return True
    return False


def is_intentional_silence_agent_result(agent_result: dict | None, response: Any) -> bool:
    """Silence markers suppress delivery only for successful agent turns."""
    if not isinstance(agent_result, dict):
        return False
    if agent_result.get("failed"):
        return False
    return is_intentional_silence_response(response)


# Служебные ходы шлёт сам шлюз, а не человек: только им разрешено молчать.
MACHINERY_DISPLAY_KINDS = frozenset({"internal_notification", "model_switch", "auto_continue"})

# Дописывается к уведомлению о фоновой работе, которое шлюз вбрасывает служебным ходом
# (мессенджер — MessageEvent(internal=True), веб-панель — self-post с
# X-Hermes-Internal-Notification): модель должна знать, что молчание здесь разрешено (случай «Оператора терминала»
# 19.10 — итог уже сообщён, модель отвечала одними рассуждениями, повтор-напоминание
# выдавало владельцу второе сообщение «это уже учтено»). Шлюз глушит NO_REPLY только на
# таком ходу (should_swallow_silence + MACHINERY_DISPLAY_KINDS).
SERVICE_TURN_SILENCE_HINT = (
    "[If you already told the user this outcome and have nothing new for them, "
    "reply with exactly NO_REPLY — it is not shown to the user. Otherwise report "
    "the result briefly.]"
)


def is_successful_background_completion(evt: Any) -> bool:
    """Успешно ли завершилась фоновая работа, о которой служебный ход.

    Подсказка о NO_REPLY даётся только успеху: упавший, убитый, потерянный или не
    запустившийся процесс, неуспешный или обрезанный дочерний агент, совпадение watch —
    всё это модель обязана сообщить владельцу, молчать здесь нельзя.
    """
    if not isinstance(evt, dict):
        return False
    evt_type = evt.get("type", "completion")
    if evt_type == "completion":
        reason = evt.get("completion_reason") or "exited"
        return reason == "exited" and str(evt.get("exit_code")) == "0"
    if evt_type != "async_delegation":
        return False
    if evt.get("error") or evt.get("truncated") or evt.get("exit_reason") == "max_iterations":
        return False
    ok = ("completed", "success")
    results = evt.get("results")
    if evt.get("is_batch") or isinstance(results, list):
        return bool(results) and all(
            isinstance(r, dict)
            and r.get("status") in ok
            and not r.get("error")
            and not r.get("truncated")
            and r.get("exit_reason") != "max_iterations"
            for r in results
        )
    return (evt.get("status") or "completed") in ok


def with_service_turn_silence_hint(text: Any) -> Any:
    """Дописать SERVICE_TURN_SILENCE_HINT к тексту служебного хода о фоновой работе.

    Одна точка для всех путей пробуждения (фоновый процесс, дочерний агент, канбан;
    мессенджер и self-post веб-панели), чтобы поведение не расходилось. Вызывается только
    для успешно завершённой работы (is_successful_background_completion). Повторный вызов
    подсказку не удваивает; слэш-команды и пустой текст не трогаются.
    """
    if not isinstance(text, str) or not text.strip() or text.lstrip().startswith("/"):
        return text
    if SERVICE_TURN_SILENCE_HINT in text:
        return text
    return f"{text}\n{SERVICE_TURN_SILENCE_HINT}"

# Показывается человеку, чьё сообщение модель оставила без ответа.
UNEXPECTED_SILENCE_REPLY = (
    "⚠️ Не получилось ответить на ваше сообщение. Попробуйте ещё раз или задайте вопрос иначе."
)


def should_swallow_silence(
    agent_result: dict | None,
    response: Any,
    *,
    display_kind: Any = None,
    reply_expected: Any = None,
) -> bool:
    """Bare silence is allowed for a service turn or a message not addressed to the bot.

    ``display_kind`` is the persisted kind of the CURRENT turn: a previous
    service row in the transcript must never authorize silence for a human turn.
    ``reply_expected=False`` is set by an adapter for a message it admitted
    without the bot being addressed (e.g. free-response group chatter), where
    a deliberate marker is the right answer and a notice would be spam.
    """
    if not is_intentional_silence_agent_result(agent_result, response):
        return False
    if isinstance(display_kind, str) and display_kind in MACHINERY_DISPLAY_KINDS:
        return True
    return reply_expected is False


def is_partial_silence_marker(text: Any) -> bool:
    """Return True while ``text`` could still resolve to a silence marker.

    The streaming path accumulates the reply delta-by-delta and must decide,
    before the whole response is known, whether to show what it has so far.
    A buffer whose canonical form is a non-empty *prefix* of a silence marker
    (e.g. ``"NO"`` on the way to ``"NO_REPLY"``, or an exact marker that has
    not yet been terminated by stream-end) is held back so a raw marker is
    never edited onto the screen and then belatedly retracted.

    Anything that has already diverged from every marker (ordinary prose) —
    and anything longer than the marker cap — returns False so normal
    streaming resumes immediately.  This is the streaming counterpart to
    :func:`is_intentional_silence_response`, sharing the same marker set and
    canonicalization so the two never drift.
    """
    if not isinstance(text, str):
        return False
    stripped = text.strip()
    if not stripped or len(stripped) > 64:
        return False
    for candidate in _canonical_silence_candidates(stripped):
        if candidate and any(marker.startswith(candidate) for marker in LIVE_GATEWAY_SILENT_MARKERS):
            return True
    return False

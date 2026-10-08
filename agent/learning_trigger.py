"""Event trigger for the automatic background learning review (K21-230).

Before 0.21.17 the review fork started on counters (every ~10 model steps or
10 user messages) regardless of what happened in the conversation, and most
such reviews found nothing. Now the review starts on an explicit event found
by a deterministic text check (no model call): a request or rule to remember,
or a direct correction of the agent's result. Long tool work is not an event.

An owner who set ``memory.nudge_interval`` / ``skills.creation_nudge_interval``
explicitly keeps the counter behaviour for that part; ``/refine`` and direct
memory/skill writes by the agent are unchanged.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

SIGNAL_REMEMBER = "remember"
SIGNAL_CORRECTION = "correction"
SIGNAL_REFINE = "refine"
SIGNAL_COUNTER = "counter"

# Event kinds that run the short bounded review (excerpt, few iterations).
BOUNDED_EVENT_KINDS = frozenset({SIGNAL_REMEMBER, SIGNAL_CORRECTION})

# Iteration cap of an event review fork; the legacy fork keeps 16.
EVENT_MAX_ITERATIONS = 6

# Two empty reviews in a row pause correction-triggered reviews in a session
# until an explicit remember request or /refine.
EMPTY_REVIEW_PAUSE_AFTER = 2

# Smallest free space (chars) that still lets the review save one entry.
MIN_MEMORY_ROOM = 80

EXCERPT_MAX_CHARS = 6000
_SCAN_CHARS = 1500
_CORRECTION_MAX_CHARS = 800
_EVENT_LOG_CAP = 20

_FLAGS = re.IGNORECASE | re.UNICODE

_RU_RULE_VERBS = (
    r"делай|используй|пиши|отвечай|проверяй|добавляй|указывай|называй|говори|"
    r"присылай|показывай|отправляй|начинай|заканчивай|спрашивай|предлагай|"
    r"форматируй|ставь|пользуйся|выбирай|сохраняй|оформляй|подтверждай|"
    r"уточняй|трогай|удаляй|пересказывай|объясняй"
)

_REMEMBER_PATTERNS = [
    r"\bзапомни(?:те)?\b",
    r"\bзапомнить\b",
    r"\bне\s+забудь(?:те)?\b",
    r"\bне\s+забывай(?:те)?\b",
    r"\bвпредь\b",
    r"\bв\s+следующий\s+раз\b",
    r"\bна\s+будущее\b",
    r"\bв\s+дальнейшем\b",
    r"\bотныне\b",
    r"\bс\s+этого\s+момента\b",
    rf"\bвсегда\s+(?:{_RU_RULE_VERBS})",
    rf"\bникогда\s+не\s+(?:{_RU_RULE_VERBS})",
    rf"\bбольше\s+не\s+(?:{_RU_RULE_VERBS}|надо|нужно|стоит|смей)",
    r"\bremember\s+(?:that|to|this|my|our)\b",
    r"\bplease\s+remember\b",
    r"\bfrom\s+now\s+on\b",
    r"\bnext\s+time\b",
    r"\bgoing\s+forward\b",
    r"\bin\s+the\s+future\b",
    r"\balways\s+(?:do|use|write|reply|respond|check|add|include|make|ask|keep|format|start|end)\b",
    r"\bnever\s+(?:do|use|write|reply|respond|add|include|send|touch|delete|ask)\b",
    r"\bdon'?t\s+ever\b",
    r"\bno\s+more\s+\w+ing\b",
]

_CORRECTION_PATTERNS = [
    r"\bне\s+так\b(?!\s+(?:важно|уж|страшно|много|быстро))",
    r"\bнеправильн\w*",
    r"\bневерн\w*",
    r"\bне\s+то\b",
    r"\bты\s+(?:ошибся|ошиблась|неправ|неправа)\b",
    r"\bнадо\s+было\b",
    r"\bнужно\s+было\b",
    r"\bследовало\b",
    r"\bпеределай(?:те)?\b",
    r"\bя\s+(?:же\s+)?(?:просил|просила|говорил|говорила)\s+(?:тебя\s+)?(?:не|что)\b",
    r"^\s*исправь(?:те)?\W*$",
    r"\bисправь(?:те)?\s+(?:это|так|ошибк\w*|ответ|результат|текст)\b",
    r"\bthat'?s\s+(?:not\s+(?:right|correct|what)|wrong|incorrect)\b",
    r"\bnot\s+what\s+i\s+(?:asked|wanted|meant)\b",
    r"\byou\s+(?:should|shouldn'?t)\s+have\b",
    r"\byou(?:'re|\s+are)\s+wrong\b",
    r"\bi\s+(?:asked|told)\s+you\s+(?:to|not)\b",
]

_REMEMBER_RE = re.compile("|".join(f"(?:{p})" for p in _REMEMBER_PATTERNS), _FLAGS)
_CORRECTION_RE = re.compile("|".join(f"(?:{p})" for p in _CORRECTION_PATTERNS), _FLAGS)


def content_text(content: Any) -> str:
    """Plain text of a message ``content`` (string or typed parts)."""
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and isinstance(block.get("text"), str):
                parts.append(block["text"])
        return " ".join(parts).strip()
    return ""


def detect_learning_signal(text: Any, has_prior_answer: bool = True) -> Optional[str]:
    """Return ``"remember"``, ``"correction"`` or ``None`` for a user message.

    Deterministic, no model call. A correction needs an earlier agent answer
    to correct and a short message; slash commands are never events.
    """
    body = content_text(text)
    if not body or body.startswith("/"):
        return None
    if _REMEMBER_RE.search(body[:_SCAN_CHARS]):
        return SIGNAL_REMEMBER
    if (
        has_prior_answer
        and len(body) <= _CORRECTION_MAX_CHARS
        and _CORRECTION_RE.search(body)
    ):
        return SIGNAL_CORRECTION
    return None


def _explicit_owner_keys() -> Tuple[bool, bool]:
    """Whether the owner set the memory / skills nudge interval explicitly.

    ``save_config`` drops values equal to the defaults, so a key present in the
    raw config.yaml is an owner decision. Unreadable config means "not set".
    """
    try:
        from korra_cli.config import read_raw_config_readonly

        raw = read_raw_config_readonly()
        mem = raw.get("memory") if hasattr(raw, "get") else None
        skills = raw.get("skills") if hasattr(raw, "get") else None
        return (
            isinstance(mem, dict) and "nudge_interval" in mem,
            isinstance(skills, dict) and "creation_nudge_interval" in skills,
        )
    except Exception:
        return False, False


def apply_event_trigger_mode(agent: Any) -> None:
    """Replace default counters with event triggers; keep explicit settings.

    Zeroing the interval disables the legacy counter path through its existing
    ``interval > 0`` guards, so no other code needs to know about the mode.
    """
    memory_explicit, skills_explicit = _explicit_owner_keys()
    agent._memory_nudge_event = not memory_explicit
    agent._skill_nudge_event = not skills_explicit
    if agent._memory_nudge_event:
        agent._memory_nudge_interval = 0
    if agent._skill_nudge_event:
        agent._skill_nudge_interval = 0
    agent._learning_signal = None
    agent._learning_empty_streak = 0


def note_user_turn(
    agent: Any,
    messages: List[Dict[str, Any]],
    user_text: Any,
    synthetic: bool = False,
) -> None:
    """Record this turn's learning signal on the agent (reset every turn)."""
    agent._learning_signal = None
    if synthetic:
        return
    if not (
        getattr(agent, "_memory_nudge_event", False)
        or getattr(agent, "_skill_nudge_event", False)
    ):
        return
    has_prior_answer = any(
        isinstance(m, dict) and m.get("role") == "assistant" and content_text(m.get("content"))
        for m in messages[:-1]
    )
    try:
        agent._learning_signal = detect_learning_signal(user_text, has_prior_answer)
    except Exception:
        agent._learning_signal = None


def _agent_saved_this_turn(messages: List[Dict[str, Any]]) -> bool:
    """True if the agent itself called memory/skill_manage after the last user message."""
    for msg in reversed(messages):
        if not isinstance(msg, dict):
            continue
        role = msg.get("role")
        if role == "user":
            return False
        if role == "assistant":
            for call in msg.get("tool_calls") or []:
                fn = (call.get("function") or {}) if isinstance(call, dict) else {}
                if fn.get("name") in ("memory", "skill_manage"):
                    return True
    return False


def fold_learning_signal(
    agent: Any,
    messages: List[Dict[str, Any]],
    review_memory: bool,
    review_skills: bool,
) -> Tuple[bool, bool, Optional[str]]:
    """Merge the turn's event signal into the legacy counter decision.

    Returns ``(review_memory, review_skills, trigger)``; ``trigger`` is the
    event kind when the event started the review, ``"counter"`` for an
    explicit-interval counter, ``None`` when nothing is due.
    """
    signal = getattr(agent, "_learning_signal", None)
    agent._learning_signal = None
    if signal not in BOUNDED_EVENT_KINDS:
        signal = None
    trigger = SIGNAL_COUNTER if (review_memory or review_skills) else None
    if not signal:
        return review_memory, review_skills, trigger
    if _agent_saved_this_turn(messages):
        record_learning_event(agent, signal, "skipped", "already_saved")
        return review_memory, review_skills, trigger
    valid = getattr(agent, "valid_tool_names", set())
    event_memory = (
        getattr(agent, "_memory_nudge_event", False)
        and "memory" in valid
        and getattr(agent, "_memory_store", None) is not None
    )
    event_skills = getattr(agent, "_skill_nudge_event", False) and "skill_manage" in valid
    if not (event_memory or event_skills):
        return review_memory, review_skills, trigger
    return (
        review_memory or bool(event_memory),
        review_skills or bool(event_skills),
        signal,
    )


def record_learning_event(
    agent: Any, trigger: Optional[str], outcome: str, reason: Optional[str] = None
) -> None:
    """Log a review decision (started / skipped / result) and keep a short tail."""
    logger.info(
        "Background review %s: trigger=%s reason=%s session=%s",
        outcome,
        trigger or "unknown",
        reason or "-",
        getattr(agent, "session_id", None) or "-",
    )
    try:
        events = getattr(agent, "_learning_events", None)
        if not isinstance(events, list):
            events = []
            agent._learning_events = events
        events.append({"trigger": trigger, "outcome": outcome, "reason": reason})
        del events[:-_EVENT_LOG_CAP]
    except Exception:
        pass


def review_paused(agent: Any, trigger: Optional[str]) -> bool:
    """Correction-triggered reviews pause after repeated empty results."""
    return (
        trigger == SIGNAL_CORRECTION
        and int(getattr(agent, "_learning_empty_streak", 0) or 0) >= EMPTY_REVIEW_PAUSE_AFTER
    )


def note_review_result(agent: Any, trigger: Optional[str], result: str) -> None:
    """Count consecutive empty event reviews; any write or explicit signal resets."""
    try:
        if trigger in BOUNDED_EVENT_KINDS and result == "none":
            agent._learning_empty_streak = int(getattr(agent, "_learning_empty_streak", 0) or 0) + 1
        elif trigger in BOUNDED_EVENT_KINDS and result == "error":
            pass
        else:
            agent._learning_empty_streak = 0
    except Exception:
        pass


def memory_has_room(store: Any, min_room: int = MIN_MEMORY_ROOM) -> bool:
    """False when every enabled built-in memory file is too full for one entry.

    Reads the files fresh: the store on the agent may predate other sessions'
    writes. Any failure answers True, so an unknown state never blocks learning.
    """
    if store is None:
        return True
    try:
        from tools.memory_tool import ENTRY_DELIMITER, MemoryStore

        fresh = MemoryStore(
            memory_char_limit=store.memory_char_limit,
            user_char_limit=store.user_char_limit,
            memory_enabled=store.memory_enabled,
            user_profile_enabled=store.user_profile_enabled,
        )
        fresh.load_from_disk()
        targets = [t for t in ("memory", "user") if fresh.target_enabled(t)]
        if not targets:
            return True
        for target in targets:
            free = fresh._char_limit(target) - fresh._char_count(target) - len(ENTRY_DELIMITER)
            if free >= min_room:
                return True
        return False
    except Exception:
        logger.debug("memory capacity check failed; assuming room", exc_info=True)
        return True


def _clip(text: str, limit: int) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    head = limit * 2 // 3
    tail = limit - head
    return text[:head].rstrip() + " … " + text[-tail:].lstrip()


def _tool_outcomes(messages: List[Dict[str, Any]], start: int, end: int) -> List[str]:
    names: Dict[str, str] = {}
    lines: List[str] = []
    for msg in messages[start:end]:
        if not isinstance(msg, dict):
            continue
        if msg.get("role") == "assistant":
            for call in msg.get("tool_calls") or []:
                if isinstance(call, dict):
                    names[str(call.get("id"))] = str((call.get("function") or {}).get("name") or "?")
        elif msg.get("role") == "tool":
            name = names.get(str(msg.get("tool_call_id")), "tool")
            body = " ".join(content_text(msg.get("content")).split())
            lines.append(f"- {name}: {_clip(body, 160)}")
    return lines[-4:]


def build_event_excerpt(messages: List[Dict[str, Any]]) -> str:
    """Bounded text around the learning event (never the whole history).

    Holds the user's earlier request, the agent's answer and the last tool
    outcomes before the event, the event message itself, and the agent's reply
    to it. Capped at :data:`EXCERPT_MAX_CHARS`.
    """
    msgs = [m for m in messages or [] if isinstance(m, dict)]
    event_idx = next((i for i in range(len(msgs) - 1, -1, -1) if msgs[i].get("role") == "user"), None)
    if event_idx is None:
        return ""
    answer_idx = next(
        (
            i for i in range(event_idx - 1, -1, -1)
            if msgs[i].get("role") == "assistant" and content_text(msgs[i].get("content"))
        ),
        None,
    )
    prev_user_idx = None
    if answer_idx is not None:
        prev_user_idx = next(
            (i for i in range(answer_idx - 1, -1, -1) if msgs[i].get("role") == "user"), None
        )
    sections: List[str] = []
    if prev_user_idx is not None:
        sections.append("USER (earlier request):\n" + _clip(content_text(msgs[prev_user_idx].get("content")), 600))
    if answer_idx is not None:
        tools = _tool_outcomes(msgs, (prev_user_idx or 0), answer_idx)
        if tools:
            sections.append("TOOL OUTCOMES (before the agent's answer):\n" + "\n".join(tools))
        sections.append(
            "AGENT (answer the user reacted to):\n" + _clip(content_text(msgs[answer_idx].get("content")), 1600)
        )
    sections.append("USER (event message):\n" + _clip(content_text(msgs[event_idx].get("content")), 1500))
    after = msgs[event_idx + 1:]
    tools_after = _tool_outcomes(msgs, event_idx + 1, len(msgs))
    if tools_after:
        sections.append("TOOL OUTCOMES (after the event):\n" + "\n".join(tools_after))
    reply = next(
        (content_text(m.get("content")) for m in reversed(after)
         if m.get("role") == "assistant" and content_text(m.get("content"))),
        "",
    )
    if reply:
        sections.append("AGENT (reply to the event; its own claim, not confirmed by the user):\n" + _clip(reply, 500))
    return _clip("\n\n".join(sections), EXCERPT_MAX_CHARS)

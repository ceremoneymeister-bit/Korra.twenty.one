"""
Contextual first-touch onboarding hints.

Instead of blocking first-run questionnaires, show a one-time hint the *first*
time a user hits a behavior fork — message-while-running, first long-running
tool, etc.  Each hint is shown once per install (tracked in ``config.yaml`` under
``onboarding.seen.<flag>``) and then never again.

Keep this module tiny and dependency-free so both the CLI and gateway can import
it without pulling in heavy modules.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Mapping, Optional

logger = logging.getLogger(__name__)


# -------------------------------------------------------------------------
# Flag names (stable — used as config.yaml keys under onboarding.seen)
# -------------------------------------------------------------------------

BUSY_INPUT_FLAG = "busy_input_prompt"
TOOL_PROGRESS_FLAG = "tool_progress_prompt"
OPENCLAW_RESIDUE_FLAG = "openclaw_residue_cleanup"
PROFILE_BUILD_FLAG = "profile_build_offered"


# -------------------------------------------------------------------------
# Hint content
# -------------------------------------------------------------------------

def busy_input_hint_gateway(mode: str) -> str:
    """Hint shown the first time a user messages while the agent is busy.

    ``mode`` is the effective busy_input_mode that was just applied, so the
    message matches reality ("I just interrupted…" vs "I just queued…").
    """
    if mode == "queue":
        return (
            "💡 Подсказка: сообщение поставлено в очередь после текущей задачи. Чтобы новые сообщения сразу прерывали задачу, отправьте `/busy interrupt`. Текущий режим: `/busy status`. Эта подсказка появляется один раз."
        )
    if mode == "steer":
        return (
            "💡 Подсказка: сообщение будет передано в текущую работу после ближайшего вызова инструмента. Режим можно сменить через `/busy interrupt` или `/busy queue`, проверить — через `/busy status`. Эта подсказка появляется один раз."
        )
    if mode == "redirect":
        return (
            "💡 Подсказка: ваше сообщение изменило направление текущей работы. Уже выполненное сохранено; `/stop` останавливает задачу. Для отдельных запросов по очереди используйте `/busy queue`, для проверки режима — `/busy status`. Эта подсказка появляется один раз."
        )
    return (
        "💡 Подсказка: я прервала текущую задачу, чтобы ответить вам. `/busy queue` ставит новые сообщения в очередь, `/busy steer` передаёт их в текущую работу без остановки, `/busy status` показывает режим. Эта подсказка появляется один раз."
    )


def busy_input_hint_cli(mode: str) -> str:
    """CLI version of the busy-input hint (plain text, no markdown)."""
    if mode == "queue":
        return (
            "Подсказка: сообщение поставлено в очередь. `/busy interrupt` разрешает Enter прерывать текущую работу, `/busy steer` передаёт сообщение в работу без остановки. Эта подсказка появляется один раз."
        )
    if mode == "steer":
        return (
            "Подсказка: сообщение будет передано в текущую работу после ближайшего вызова инструмента. Сменить режим: /busy interrupt или /busy queue. Эта подсказка появляется один раз."
        )
    if mode == "redirect":
        return (
            "Подсказка: ваше уточнение изменило направление работы; уже выполненное сохранено. `/stop` остановит задачу, `/busy queue` включит очередь отдельных запросов. Эта подсказка появляется один раз."
        )
    return (
        "Подсказка: сообщение прервало текущую работу. `/busy queue` ставит новые сообщения в очередь, `/busy steer` передаёт их в работу без остановки. Эта подсказка появляется один раз."
    )


def tool_progress_hint_gateway() -> str:
    return (
        "💡 Подсказка: инструмент работает долго, поэтому я показываю ход выполнения. Команда `/verbose` меняет объём уведомлений: каждый вызов → только смена инструмента → выключено. Эта подсказка появляется один раз."
    )


def tool_progress_hint_cli() -> str:
    return (
        "Подсказка: инструмент работал долго. `/verbose` меняет объём уведомлений: каждый вызов → смена инструмента → выключено → подробно. Эта подсказка появляется один раз."
    )


def openclaw_residue_hint_cli() -> str:
    """Banner shown the first time Hermes starts and finds ``~/.openclaw/``.

    Points users at ``hermes claw migrate`` (non-destructive port of config,
    memory, and skills) first. ``hermes claw cleanup`` is mentioned as the
    follow-up step for users who have already migrated and want to archive
    the old directory — with a warning that archiving breaks OpenClaw.
    """
    return (
        "Найдена старая папка OpenClaw: ~/.openclaw/.\nЧтобы перенести настройки, память и навыки в Korra, выполните `korra claw migrate`.\nПосле переноса можно убрать старую папку командой `korra claw cleanup`: она будет переименована в ~/.openclaw.pre-migration, и OpenClaw перестанет работать.\nЭта подсказка появляется один раз."
    )


def detect_openclaw_residue(home: Optional[Path] = None) -> bool:
    """Return True if an OpenClaw workspace directory is present in ``$HOME``.

    Pure filesystem check — no side effects. ``home`` override exists for tests.
    """
    base = home or Path.home()
    try:
        return (base / ".openclaw").is_dir()
    except OSError:
        return False


# -------------------------------------------------------------------------
# Onboarding profile-build path (opt-in, consent-gated)
# -------------------------------------------------------------------------

def profile_build_mode(config: Mapping[str, Any]) -> str:
    """Resolve the onboarding profile-build mode from config.

    Returns one of:
      ``"ask"``  — on first contact, OFFER to build a profile (default).
      ``"off"``  — never offer; the first-message note stays a plain intro.

    Read from ``config.onboarding.profile_build``. Unknown / missing values
    fall back to ``"ask"`` so the default experience offers the flow. Any
    network/account lookups inside the flow are separately consented to in
    conversation — this setting only governs whether the offer is made.
    """
    if not isinstance(config, Mapping):
        return "ask"
    onboarding = config.get("onboarding")
    if not isinstance(onboarding, Mapping):
        return "ask"
    mode = onboarding.get("profile_build")
    if isinstance(mode, str) and mode.strip().lower() == "off":
        return "off"
    return "ask"


def profile_build_directive() -> str:
    """System-note directive appended to the very first message ever.

    Instructs the agent to run a short, opt-in, consent-gated profile-build
    flow and persist confirmed facts to the user-profile memory store
    (``memory`` tool, ``target="user"``). Phrased so the agent ASKS before any
    lookup and never silently reads connected accounts — directly addressing
    the privacy concern that reading email/accounts unprompted feels invasive.
    """
    return (
        "\n\n[System note: This is the user's very first message ever. "
        "After a one-sentence introduction (mention /help shows commands), "
        "OFFER — do not assume — to build a short profile of them so you can "
        "be more useful, and explain they can decline or do it later. If and "
        "ONLY IF they accept:\n"
        "  1. Ask for whatever they're comfortable sharing (name, what they "
        "do, how they like you to work). Volunteered facts come first.\n"
        "  2. Before ANY external lookup, say what you intend to look up and "
        "get explicit consent for that step. Never read their connected "
        "accounts (email, calendar, etc.) silently — ask each time.\n"
        "  3. With consent, you may use web_search to confirm public details "
        "(e.g. employer, public profiles) from the data points they gave.\n"
        "  4. Save each confirmed, durable fact with the memory tool using "
        "target=\"user\" — keep entries compact and high-signal.\n"
        "If they decline at any point, stop immediately and continue normally. "
        "Keep the whole exchange light and conversational, not an interrogation.]"
    )


# -------------------------------------------------------------------------
# State read / write
# -------------------------------------------------------------------------

def _get_seen_dict(config: Mapping[str, Any]) -> Mapping[str, Any]:
    onboarding = config.get("onboarding") if isinstance(config, Mapping) else None
    if not isinstance(onboarding, Mapping):
        return {}
    seen = onboarding.get("seen")
    return seen if isinstance(seen, Mapping) else {}


def is_seen(config: Mapping[str, Any], flag: str) -> bool:
    """Return True if the user has already been shown this first-touch hint."""
    return bool(_get_seen_dict(config).get(flag))


def mark_seen(config_path: Path, flag: str) -> bool:
    """Persist ``onboarding.seen.<flag> = True`` to ``config_path``.

    Uses the atomic YAML writer so a concurrent process can't observe a
    partially-written file.  Returns True on success, False on any error
    (including the config file being absent — onboarding is best-effort).
    """
    try:
        import yaml
        from korra_cli.config import atomic_config_write
    except Exception as e:  # pragma: no cover — dependency issue
        logger.debug("onboarding: failed to import yaml/utils: %s", e)
        return False

    try:
        cfg: dict = {}
        if config_path.exists():
            with open(config_path, encoding="utf-8") as f:
                cfg = yaml.safe_load(f) or {}
        if not isinstance(cfg.get("onboarding"), dict):
            cfg["onboarding"] = {}
        seen = cfg["onboarding"].get("seen")
        if not isinstance(seen, dict):
            seen = {}
            cfg["onboarding"]["seen"] = seen
        if seen.get(flag) is True:
            return True  # already marked — nothing to do
        seen[flag] = True
        atomic_config_write(config_path, cfg)
        return True
    except Exception as e:
        logger.debug("onboarding: failed to mark flag %s: %s", flag, e)
        return False


__all__ = [
    "BUSY_INPUT_FLAG",
    "TOOL_PROGRESS_FLAG",
    "OPENCLAW_RESIDUE_FLAG",
    "PROFILE_BUILD_FLAG",
    "busy_input_hint_gateway",
    "busy_input_hint_cli",
    "tool_progress_hint_gateway",
    "tool_progress_hint_cli",
    "openclaw_residue_hint_cli",
    "detect_openclaw_residue",
    "profile_build_mode",
    "profile_build_directive",
    "is_seen",
    "mark_seen",
]

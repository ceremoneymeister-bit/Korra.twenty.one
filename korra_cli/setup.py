"""
Interactive setup wizard for Hermes Agent.

Modular wizard with independently-runnable sections:
  1. Model & Provider — choose your AI provider and model
  2. Terminal Backend — where your agent runs commands
  3. Agent Settings — iterations, compression, session reset
  4. Messaging Platforms — connect Telegram, Discord, etc.
  5. Tools — configure TTS, web search, image generation, etc.

Config files are stored in ~/.hermes/ for easy access.
"""

import importlib.util
import json
import logging
import os
import re
import shutil
import sys
import copy
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Optional, Dict, Any, Callable

from korra_cli.curses_ui import MenuNavigationEvent, MenuNavigationStart
from korra_cli.nous_subscription import get_nous_subscription_features
from tools.tool_backend_helpers import managed_nous_tools_enabled
from korra_constants import get_optional_skills_dir, korra_env

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).parent.parent.resolve()

_DOCS_BASE = "https://hermes-agent.nousresearch.com/docs"


def _model_config_dict(config: Dict[str, Any]) -> Dict[str, Any]:
    current_model = config.get("model")
    if isinstance(current_model, dict):
        return dict(current_model)
    if isinstance(current_model, str) and current_model.strip():
        return {"default": current_model.strip()}
    return {}


def _get_credential_pool_strategies(config: Dict[str, Any]) -> Dict[str, str]:
    strategies = config.get("credential_pool_strategies")
    return dict(strategies) if isinstance(strategies, dict) else {}


def _set_credential_pool_strategy(config: Dict[str, Any], provider: str, strategy: str) -> None:
    if not provider:
        return
    strategies = _get_credential_pool_strategies(config)
    strategies[provider] = strategy
    config["credential_pool_strategies"] = strategies


def _supports_same_provider_pool_setup(provider: str) -> bool:
    if not provider or provider == "custom":
        return False
    if provider == "openrouter":
        return True
    from korra_cli.auth import PROVIDER_REGISTRY

    pconfig = PROVIDER_REGISTRY.get(provider)
    if not pconfig:
        return False
    return pconfig.auth_type in {"api_key", "oauth_device_code"}


# Default model lists per provider — used as fallback when the live
# /models endpoint can't be reached.
_DEFAULT_PROVIDER_MODELS = {
    "copilot-acp": [
        "copilot-acp",
    ],
    "copilot": [
        "gpt-5.4",
        "gpt-5.4-mini",
        "gpt-5-mini",
        "gpt-5.3-codex",
        "gpt-5.2-codex",
        "gpt-4.1",
        "gpt-4o",
        "gpt-4o-mini",
        "claude-opus-4.6",
        "claude-sonnet-5",
        "claude-sonnet-4.6",
        "claude-sonnet-4.5",
        "claude-haiku-4.5",
        "gemini-2.5-pro",
    ],
    "gemini": [
        "gemini-3.1-pro-preview", "gemini-3-pro-preview",
        "gemini-3.6-flash", "gemini-3.1-flash-lite-preview",
    ],
    "vertex": [
        "google/gemini-3.1-pro-preview", "google/gemini-3-pro-preview",
        "google/gemini-3-flash-preview", "google/gemini-3.1-flash-lite-preview",
        "google/gemini-2.5-pro", "google/gemini-2.5-flash",
    ],
    "zai": ["glm-5.3", "glm-5.3-flash", "glm-5.2", "glm-5.1", "glm-5", "glm-4.7", "glm-4.5", "glm-4.5-flash"],
    "kimi-coding": ["kimi-k3", "kimi-k2.6", "kimi-k2.5", "kimi-k2-thinking", "kimi-k2-turbo-preview"],
    "kimi-coding-cn": ["kimi-k3", "kimi-k2.6", "kimi-k2.5", "kimi-k2-thinking", "kimi-k2-turbo-preview"],
    "stepfun": ["step-3.5-flash", "step-3.5-flash-2603"],
    "arcee": ["trinity-large-thinking", "trinity-large-preview", "trinity-mini"],
    "minimax": ["MiniMax-M2.7", "MiniMax-M2.5", "MiniMax-M2.1", "MiniMax-M2"],
    "minimax-cn": ["MiniMax-M2.7", "MiniMax-M2.5", "MiniMax-M2.1", "MiniMax-M2"],
    "ai-gateway": ["anthropic/claude-opus-4.6", "anthropic/claude-sonnet-4.6", "openai/gpt-5", "google/gemini-3-flash"],
    "kilocode": ["anthropic/claude-sonnet-5", "anthropic/claude-opus-4.6", "anthropic/claude-sonnet-4.6", "openai/gpt-5.4", "google/gemini-3-pro-preview", "google/gemini-3-flash-preview"],
    "opencode-zen": ["x-preview-f-free", "gpt-5.6-sol", "gpt-5.4", "gpt-5.3-codex", "claude-opus-5", "claude-sonnet-5", "gemini-3.7-flash", "glm-5.2", "kimi-k3", "minimax-m3"],
    "opencode-free": ["deepseek-v4-flash-free", "hy3-free", "mimo-v2.5-free", "laguna-s-2.1-free", "nemotron-3-ultra-free", "nemotron-3.5-lightning-free", "muse-spark-1.2-contributor-free"],
    "opencode-go": ["kimi-k3", "kimi-k2.7-code", "kimi-k2.6", "gpt-5.6-luna", "grok-4.5", "glm-5.3", "glm-5.3-flash", "glm-5.2", "mimo-v2.5-pro", "mimo-v2.5", "minimax-m3", "minimax-m2.7", "qwen3.8-max", "qwen3.7-max", "deepseek-v4-pro", "hy3"],
    "huggingface": [
        "Qwen/Qwen3.5-397B-A17B", "Qwen/Qwen3-235B-A22B-Thinking-2507",
        "Qwen/Qwen3-Coder-480B-A35B-Instruct", "deepseek-ai/DeepSeek-R1-0528",
        "deepseek-ai/DeepSeek-V3.2", "moonshotai/Kimi-K2.5",
    ],
}


def _current_reasoning_effort(config: Dict[str, Any]) -> str:
    agent_cfg = config.get("agent")
    if isinstance(agent_cfg, dict):
        return str(agent_cfg.get("reasoning_effort") or "").strip().lower()
    return ""


def _set_reasoning_effort(config: Dict[str, Any], effort: str) -> None:
    agent_cfg = config.get("agent")
    if not isinstance(agent_cfg, dict):
        agent_cfg = {}
        config["agent"] = agent_cfg
    agent_cfg["reasoning_effort"] = effort




# Import config helpers
from korra_cli.config import (
    cfg_get,
    DEFAULT_CONFIG,
    get_hermes_home,
    get_config_path,
    get_env_path,
    load_config,
    save_config,
    save_env_value,
    remove_env_value,
    get_env_value,
    ensure_hermes_home,
)
# display_hermes_home imported lazily at call sites (stale-module safety during hermes update)

from korra_cli.colors import Colors, color


def print_header(title: str):
    """Print a section header."""
    print()
    print(color(f"◆ {title}", Colors.CYAN, Colors.BOLD))


from korra_cli.cli_output import (  # noqa: E402
    print_error,
    print_info,
    print_success,
    print_warning,
)
from korra_cli.secret_prompt import masked_secret_prompt  # noqa: E402


def is_interactive_stdin() -> bool:
    """Return True when stdin looks like a usable interactive TTY."""
    stdin = getattr(sys, "stdin", None)
    if stdin is None:
        return False
    try:
        return bool(stdin.isatty())
    except Exception:
        return False


def print_noninteractive_setup_guidance(reason: str | None = None) -> None:
    """Print guidance for headless/non-interactive setup flows."""
    print()
    print(color('⚕ Настройка Korra — без интерактивного мастера', Colors.CYAN, Colors.BOLD))
    print()
    if reason:
        print_info(reason)
    print_info('Здесь нельзя открыть интерактивный мастер настройки.')
    print()
    print_info('Настройте Korra командами:')
    print_info('  korra config set model.provider custom')
    print_info('  korra config set model.base_url http://localhost:8080/v1')
    print_info('  korra config set model.default название-модели')
    print()
    print_info('Ключ OPENROUTER_API_KEY или OPENAI_API_KEY можно добавить в файл .env выбранного профиля.')
    print_info('Чтобы открыть полный мастер, запустите `korra setup` в интерактивном терминале.')
    print()


def prompt(question: str, default: str = None, password: bool = False) -> str:
    """Prompt for input with optional default."""
    if default:
        display = f"{question} [{default}]: "
    else:
        display = f"{question}: "

    try:
        if password:
            value = masked_secret_prompt(color(display, Colors.YELLOW))
        else:
            from korra_cli.cli_output import line_input

            value = line_input(color(display, Colors.YELLOW))

        cleaned = _sanitize_pasted_input(value)
        return cleaned.strip() or default or ""
    except (KeyboardInterrupt, EOFError):
        print()
        sys.exit(1)


class _SetupControlFlow(BaseException):
    """Bypass provider error handlers that intentionally catch ``Exception``.

    Provider setup contains broad compatibility boundaries around network,
    plugin, and credential integrations. Navigation must cross those layers
    unchanged so the outer setup state machine can replay the prior prompt.
    """


class _SetupCancelled(_SetupControlFlow):
    """Internal control flow for cancelling the interactive setup wizard."""


class _SetupGoBack(_SetupControlFlow):
    """Internal control flow for returning to an earlier setup choice."""

    def __init__(self, prompt_index: int):
        super().__init__(prompt_index)
        self.prompt_index = prompt_index


class _SetupNavigationState:
    """Per-invocation navigation state for the synchronous setup wizard."""

    def __init__(self, *, section_index: int = -1, prompt_index: int = 0):
        self.section_index = section_index
        self.prompt_index = prompt_index
        self.active_prompt_index = -1
        self.resolved_choices: list[object] = []
        self.replay_choices: list[object] = []


_SETUP_NAVIGATION: ContextVar[_SetupNavigationState | None] = ContextVar(
    "hermes_setup_navigation", default=None
)


def _handle_setup_menu_navigation(
    event: MenuNavigationEvent,
    value: object = None,
) -> MenuNavigationStart | None:
    """Translate shared curses menu events into setup control flow."""
    state = _SETUP_NAVIGATION.get()
    if state is None:
        return None
    if event is MenuNavigationEvent.BEGIN:
        if state.section_index < 0:
            state.active_prompt_index = -1
            return MenuNavigationStart()
        state.active_prompt_index = state.prompt_index
        state.prompt_index += 1
        allow_back = state.section_index > 0 or state.active_prompt_index > 0
        if state.active_prompt_index < len(state.replay_choices):
            return MenuNavigationStart(
                allow_back=allow_back,
                replay_value=copy.deepcopy(
                    state.replay_choices[state.active_prompt_index]
                ),
            )
        return MenuNavigationStart(allow_back=allow_back)
    if event is MenuNavigationEvent.RESOLVE:
        prompt_index = state.active_prompt_index
        if prompt_index < 0:
            return None
        resolved = copy.deepcopy(value)
        if prompt_index < len(state.resolved_choices):
            state.resolved_choices[prompt_index] = resolved
            del state.resolved_choices[prompt_index + 1 :]
        else:
            state.resolved_choices.append(resolved)
        return None
    if event is MenuNavigationEvent.CANCEL:
        raise _SetupCancelled()
    if event is MenuNavigationEvent.BACK:
        raise _SetupGoBack(state.active_prompt_index)
    return None


_BRACKETED_PASTE_PATTERN = re.compile(r"\x1b\[\s*200~|\x1b\[\s*201~")


def _sanitize_pasted_input(value: str) -> str:
    """Strip terminal bracketed-paste control markers from pasted text."""
    if not isinstance(value, str) or not value:
        return value
    return _BRACKETED_PASTE_PATTERN.sub("", value)


def _curses_prompt_choice(question: str, choices: list, default: int = 0, description: str | None = None) -> int:
    """Single-select menu using curses. Delegates to curses_radiolist."""
    from korra_cli.curses_ui import curses_radiolist
    return curses_radiolist(
        question,
        choices,
        selected=default,
        cancel_returns=-1,
        description=description,
    )



def prompt_choice(question: str, choices: list, default: int = 0, description: str | None = None) -> int:
    """Prompt for a choice from a list with arrow key navigation.

    Escape cancels an active setup wizard. Outside setup it keeps the current
    default. The curses component owns its own numbered fallback, so a cancel
    result must never be mistaken for a request to open another prompt.
    Ctrl+C exits the wizard.
    """
    idx = _curses_prompt_choice(question, choices, default, description=description)
    if idx >= 0:
        if idx == default:
            print_info('  Пропущено. Текущее значение сохранено.')
            print()
            return default
        print()
        return idx

    return default


def is_noninteractive() -> bool:
    """True when no human is available to answer a prompt.

    The dashboard/desktop spawn CLI actions with ``stdin=DEVNULL`` and
    ``HERMES_NONINTERACTIVE=1`` (see ``korra_cli/web_server.py``). In that
    context an ``input()`` raises ``EOFError`` immediately, so a prompt that
    aborts on EOF kills the spawned action — this is what made the desktop
    "restart gateway" fail when the Windows gateway service was not yet
    installed (the start path asks "Install it now?" with no one to answer).
    Honour the explicit env flag here so callers fall back to their default.
    """
    return korra_env("KORRA_NONINTERACTIVE", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def prompt_yes_no(question: str, default: bool = True) -> bool:
    """Prompt for yes/no. Ctrl+C exits, empty input returns default.

    Non-interactive callers (``HERMES_NONINTERACTIVE=1`` or a closed/redirected
    stdin) have no one to answer, so fall back to ``default`` instead of
    aborting the whole process.
    """
    if is_noninteractive():
        return default

    # Setup owns a scoped curses navigation handler. Route binary selections
    # through the same menu surface so ESC and left-arrow work consistently,
    # while preserving the traditional line prompt for every other caller.
    if _SETUP_NAVIGATION.get() is not None:
        default_index = 0 if default else 1
        return _curses_prompt_choice(
            question,
            ["Да", "Нет"],
            default_index,
        ) == 0

    default_str = "Да/нет" if default else "да/Нет"

    while True:
        try:
            value = (
                input(color(f"{question} [{default_str}]: ", Colors.YELLOW))
                .strip()
                .lower()
            )
        except KeyboardInterrupt:
            print()
            sys.exit(1)
        except EOFError:
            # No stdin to read (closed/redirected, e.g. a spawned action with
            # stdin=DEVNULL). Accept the default rather than exit so the caller
            # can proceed unattended instead of failing the whole command.
            print()
            return default

        if not value:
            return default
        if value in {"y", "yes", "д", "да"}:
            return True
        if value in {"n", "no", "н", "нет"}:
            return False
        print_error("Введите «да» или «нет» (можно y/n).")


def prompt_checklist(title: str, items: list, pre_selected: list = None) -> list:
    """
    Display a multi-select checklist and return the indices of selected items.

    Each item in `items` is a display string. `pre_selected` is a list of
    indices that should be checked by default. A "Continue →" option is
    appended at the end — the user toggles items with Space and confirms
    with Enter on "Continue →".

    Falls back to a numbered toggle interface when curses is
    unavailable.

    Returns:
        List of selected indices (not including the Continue option).
    """
    if pre_selected is None:
        pre_selected = []

    from korra_cli.curses_ui import curses_checklist

    chosen = curses_checklist(
        title,
        items,
        set(pre_selected),
        cancel_returns=set(pre_selected),
    )
    return sorted(chosen)


def _prompt_api_key(var: dict):
    """Display a nicely formatted API key input screen for a single env var."""
    tools = var.get("tools", [])
    tools_str = ", ".join(tools[:3])
    if len(tools) > 3:
        tools_str += f", +{len(tools) - 3} more"

    print()
    print(color(f"  ─── {var.get('description', var['name'])} ───", Colors.CYAN))
    print()
    if tools_str:
        print_info(f'  Возможности: {tools_str}')
    if var.get("url"):
        print_info(f"  Получить ключ: {var['url']}")
    print()

    if var.get("password"):
        value = prompt(f"  {var.get('prompt', var['name'])}", password=True)
    else:
        value = prompt(f"  {var.get('prompt', var['name'])}")

    if value:
        save_env_value(var["name"], value)
        print_success('  ✓ Сохранено')
    else:
        print_warning('  Пропущено. Можно настроить позже: `korra setup`.')


def _print_setup_summary(config: dict, hermes_home):
    """Print the setup completion summary."""
    # Provider readiness — the one thing setup absolutely must produce.
    # Previously a user could cancel the API-key prompt mid-wizard (Enter →
    # "Cancelled."), watch the wizard continue through Terminal/Gateway/Tools,
    # and exit "successfully" with NO working model — believing they were set
    # up. Say so loudly instead (consumer-onboarding audit finding #7).
    try:
        from korra_cli.auth import resolve_provider

        resolve_provider()
        _provider_ready = True
    except Exception:
        _provider_ready = False
    if not _provider_ready:
        print()
        print_warning('Сервис модели не настроен. Корра пока не может отвечать.')
        print_info('  Завершите подключение одним из способов:')
        print_info('    korra model            — выбрать провайдера и модель')
        print_info('    korra setup --portal   — вход в Nous Portal без ключа API')

    # Tool availability summary
    print()
    print_header('Доступность инструментов')

    tool_status = []
    subscription_features = get_nous_subscription_features(config)

    # Vision — use the same runtime resolver as the actual vision tools
    try:
        from agent.auxiliary_client import get_available_vision_backends

        _vision_backends = get_available_vision_backends()
    except Exception:
        _vision_backends = []

    if _vision_backends:
        tool_status.append(('Анализ изображений', True, None))
    else:
        tool_status.append(('Анализ изображений', False, 'настроить: korra setup'))


    # Web tools (Exa, Parallel, Firecrawl, or Keenable)
    if subscription_features.web.managed_by_nous:
        tool_status.append(('Поиск и чтение сайтов (подписка Nous)', True, None))
    elif subscription_features.web.available:
        label = 'Поиск и чтение сайтов'
        if subscription_features.web.current_provider:
            label = f'Поиск и чтение сайтов ({subscription_features.web.current_provider})'
        tool_status.append((label, True, None))
    else:
        tool_status.append(('Поиск и чтение сайтов', False, 'EXA_API_KEY, PARALLEL_API_KEY, FIRECRAWL_API_KEY/FIRECRAWL_API_URL, KEENABLE_API_KEY или SEARXNG_URL'))

    # Browser tools (local Chromium, Camofox, Browserbase, Browser Use, or Firecrawl)
    browser_provider = subscription_features.browser.current_provider
    if subscription_features.browser.managed_by_nous:
        tool_status.append(('Управление браузером (Nous Browser Use)', True, None))
    elif subscription_features.browser.available:
        label = 'Управление браузером'
        if browser_provider:
            label = f'Управление браузером ({browser_provider})'
        tool_status.append((label, True, None))
    else:
        missing_browser_hint = 'выполните npm install -g agent-browser, укажите CAMOFOX_URL или настройте Browser Use / Browserbase'
        if browser_provider == "Browserbase":
            missing_browser_hint = (
                'выполните npm install -g agent-browser и укажите BROWSERBASE_API_KEY/BROWSERBASE_PROJECT_ID'
            )
        elif browser_provider == "Browser Use":
            missing_browser_hint = (
                'выполните npm install -g agent-browser и укажите BROWSER_USE_API_KEY'
            )
        elif browser_provider == "Camofox":
            missing_browser_hint = "CAMOFOX_URL"
        elif browser_provider == "Local browser":
            missing_browser_hint = (
                "npm install -g agent-browser && agent-browser install --with-deps"
            )
        tool_status.append(
            ('Управление браузером', False, missing_browser_hint)
        )

    # Image generation — FAL (direct or via Nous), or any plugin-registered
    # provider (OpenAI, etc.)
    if subscription_features.image_gen.managed_by_nous:
        tool_status.append(('Создание изображений (подписка Nous)', True, None))
    elif subscription_features.image_gen.available:
        tool_status.append(('Создание изображений', True, None))
    else:
        # Fall back to probing plugin-registered providers so OpenAI-only
        # setups don't show as "missing FAL_KEY".
        _img_backend = None
        try:
            from agent.image_gen_registry import list_providers
            from korra_cli.plugins import _ensure_plugins_discovered

            _ensure_plugins_discovered()
            for _p in list_providers():
                if _p.name == "fal":
                    continue
                try:
                    if _p.is_available():
                        _img_backend = _p.display_name
                        break
                except Exception:
                    continue
        except Exception:
            pass
        if _img_backend:
            tool_status.append((f'Создание изображений ({_img_backend})', True, None))
        else:
            tool_status.append(('Создание изображений', False, 'FAL_KEY или OPENAI_API_KEY'))

    # Video generation — opt-in via `hermes tools` → Video Generation.
    # Only show the row when a plugin reports available so we don't badger
    # users who don't care about video gen with a "missing" status line.
    if subscription_features.video_gen.managed_by_nous:
        tool_status.append(('Создание видео (FAL по подписке Nous)', True, None))
    else:
        try:
            from agent.video_gen_registry import list_providers as _list_video_providers
            from korra_cli.plugins import _ensure_plugins_discovered as _ensure_plugins
            _ensure_plugins()
            _video_backend = None
            for _vp in _list_video_providers():
                try:
                    if _vp.is_available():
                        _video_backend = _vp.display_name
                        break
                except Exception:
                    continue
        except Exception:
            _video_backend = None
        if _video_backend:
            tool_status.append((f'Создание видео ({_video_backend})', True, None))

    # TTS — show configured provider
    tts_provider = cfg_get(config, "tts", "provider", default="edge")
    if subscription_features.tts.managed_by_nous:
        tool_status.append(('Озвучивание (OpenAI по подписке Nous)', True, None))
    elif tts_provider == "elevenlabs" and get_env_value("ELEVENLABS_API_KEY"):
        tool_status.append(('Озвучивание (ElevenLabs)', True, None))
    elif tts_provider == "openai" and (
        get_env_value("VOICE_TOOLS_OPENAI_KEY") or get_env_value("OPENAI_API_KEY")
    ):
        tool_status.append(('Озвучивание (OpenAI)', True, None))
    elif tts_provider == "minimax" and get_env_value("MINIMAX_API_KEY"):
        tool_status.append(('Озвучивание (MiniMax)', True, None))
    elif tts_provider == "mistral" and get_env_value("MISTRAL_API_KEY"):
        tool_status.append(('Озвучивание (Mistral Voxtral)', True, None))
    elif tts_provider == "gemini" and (get_env_value("GEMINI_API_KEY") or get_env_value("GOOGLE_API_KEY")):
        tool_status.append(('Озвучивание (Google Gemini)', True, None))
    elif tts_provider == "neutts":
        try:
            neutts_ok = importlib.util.find_spec("neutts") is not None
        except Exception:
            neutts_ok = False
        if neutts_ok:
            tool_status.append(('Озвучивание (NeuTTS на этом компьютере)', True, None))
        else:
            tool_status.append(('Озвучивание (NeuTTS не установлен)', False, 'настроить: korra setup tts'))
    elif tts_provider == "kittentts":
        try:
            kittentts_ok = importlib.util.find_spec("kittentts") is not None
        except Exception:
            kittentts_ok = False
        if kittentts_ok:
            tool_status.append(('Озвучивание (KittenTTS на этом компьютере)', True, None))
        else:
            tool_status.append(('Озвучивание (KittenTTS не установлен)', False, 'настроить: korra setup tts'))
    else:
        tool_status.append(('Озвучивание (Edge TTS)', True, None))

    # STT — show configured provider
    stt_provider = cfg_get(config, "stt", "provider", default="local") or "local"
    _stt_feature = subscription_features.features.get("stt")
    if _stt_feature is not None and _stt_feature.managed_by_nous:
        tool_status.append(('Распознавание речи (OpenAI по подписке Nous)', True, None))
    elif stt_provider == "openai" and (
        get_env_value("VOICE_TOOLS_OPENAI_KEY") or get_env_value("OPENAI_API_KEY")
    ):
        tool_status.append(('Распознавание речи (OpenAI)', True, None))
    elif stt_provider == "groq" and get_env_value("GROQ_API_KEY"):
        tool_status.append(('Распознавание речи (Groq Whisper)', True, None))
    elif stt_provider == "elevenlabs" and get_env_value("ELEVENLABS_API_KEY"):
        tool_status.append(('Распознавание речи (ElevenLabs Scribe)', True, None))
    elif stt_provider == "xai":
        tool_status.append(('Распознавание речи (xAI)', True, None))
    elif stt_provider == "deepinfra" and get_env_value("DEEPINFRA_API_KEY"):
        tool_status.append(('Распознавание речи (DeepInfra)', True, None))
    else:
        try:
            fw_ok = importlib.util.find_spec("faster_whisper") is not None
        except Exception:
            fw_ok = False
        if fw_ok:
            tool_status.append(('Распознавание речи (Whisper на этом компьютере)', True, None))
        else:
            tool_status.append(
                ('Распознавание речи (Whisper не установлен)', False, 'настроить: korra tools → «Распознавание речи»')
            )

    if subscription_features.modal.managed_by_nous:
        tool_status.append(('Выполнение команд Modal (подписка Nous)', True, None))
    elif cfg_get(config, "terminal", "backend") == "modal":
        if subscription_features.modal.direct_override:
            tool_status.append(('Выполнение команд Modal (прямое подключение)', True, None))
        else:
            tool_status.append(('Выполнение команд Modal', False, 'настроить: korra setup terminal'))
    elif managed_nous_tools_enabled() and subscription_features.nous_auth_present:
        tool_status.append(('Выполнение команд Modal (можно подключить по подписке Nous)', True, None))

    # Home Assistant
    if get_env_value("HASS_TOKEN"):
        tool_status.append(('Умный дом (Home Assistant)', True, None))

    # Spotify (OAuth via hermes auth spotify — check auth.json, not env vars)
    try:
        from korra_cli.auth import get_provider_auth_state
        _spotify_state = get_provider_auth_state("spotify") or {}
        if _spotify_state.get("access_token") or _spotify_state.get("refresh_token"):
            tool_status.append(("Spotify (PKCE OAuth)", True, None))
    except Exception:
        pass

    # Skills Hub
    if get_env_value("GITHUB_TOKEN"):
        tool_status.append(('Каталог навыков (GitHub)', True, None))
    else:
        tool_status.append(('Каталог навыков (GitHub)', False, "GITHUB_TOKEN"))

    # Terminal (always available if system deps met)
    tool_status.append(('Терминал и команды', True, None))

    # Task planning (always available, in-memory)
    tool_status.append(('Планирование задач', True, None))

    # Skills (always available -- bundled skills + user-created skills)
    tool_status.append(('Навыки: просмотр, создание, изменение', True, None))

    # Print status
    available_count = sum(1 for _, avail, _ in tool_status if avail)
    total_count = len(tool_status)

    print_info(f'Доступно категорий инструментов: {available_count}/{total_count}')
    print()

    for name, available, missing_var in tool_status:
        if available:
            print(f"   {color('✓', Colors.GREEN)} {name}")
        else:
            print(
                f"   {color('✗', Colors.RED)} {name} {color(f'(не хватает: {missing_var})', Colors.DIM)}"
            )

    print()

    disabled_tools = [(name, var) for name, avail, var in tool_status if not avail]
    if disabled_tools:
        print_warning(
            'Часть инструментов отключена. Настройте их командой `korra setup tools`'
        )
        from korra_constants import display_hermes_home as _dhh
        print_warning(f'или добавьте нужные ключи API в {_dhh()}/.env.')
        print()

    # Done banner
    print()
    print(
        color(
            "┌─────────────────────────────────────────────────────────┐", Colors.GREEN
        )
    )
    print(
        color(
            '│              ✓ Настройка завершена!                    │', Colors.GREEN
        )
    )
    print(
        color(
            "└─────────────────────────────────────────────────────────┘", Colors.GREEN
        )
    )
    print()

    # Show file locations prominently
    from korra_constants import display_hermes_home as _dhh
    print(color(f'📁 Все ваши файлы находятся в {_dhh()}/:', Colors.CYAN, Colors.BOLD))
    print()
    print(f"   {color('Настройки:', Colors.YELLOW)}  {get_config_path()}")
    print(f"   {color('Ключи API:', Colors.YELLOW)}  {get_env_path()}")
    print(
        f"   {color('Данные:', Colors.YELLOW)}      {hermes_home}/cron/, sessions/, logs/"
    )
    print()

    print(color("─" * 60, Colors.DIM))
    print()
    print(color('📝 Изменить настройки:', Colors.CYAN, Colors.BOLD))
    print()
    print(f"   {color('korra setup', Colors.GREEN)}          Пройти мастер заново")
    print(f"   {color('korra setup model', Colors.GREEN)}    Сменить модель или провайдера")
    print(f"   {color('korra setup terminal', Colors.GREEN)} Выбрать среду выполнения команд")
    print(f"   {color('korra setup gateway', Colors.GREEN)}  Подключить мессенджеры")
    print(f"   {color('korra setup tools', Colors.GREEN)}    Настроить сервисы инструментов")
    print()
    print(f"   {color('korra config', Colors.GREEN)}         Посмотреть настройки")
    print(
        f"   {color('korra config edit', Colors.GREEN)}    Открыть настройки в редакторе"
    )
    print(f"   {color('korra config set <ключ> <значение>', Colors.GREEN)}")
    print('                          Изменить отдельное значение')
    print()
    print('   Также можно отредактировать файлы:')
    print(f"   {color(f'nano {get_config_path()}', Colors.DIM)}")
    print(f"   {color(f'nano {get_env_path()}', Colors.DIM)}")
    print()

    print(color("─" * 60, Colors.DIM))
    print()
    print(color('🚀 Всё готово к работе!', Colors.CYAN, Colors.BOLD))
    print()
    print(f"   {color('korra', Colors.GREEN)}              Начать диалог")
    print(f"   {color('korra gateway', Colors.GREEN)}      Запустить шлюз мессенджеров")
    print(f"   {color('korra doctor', Colors.GREEN)}       Проверить неполадки")
    print()


def _prompt_container_resources(config: dict):
    """Prompt for container resource settings (Docker, Singularity, Modal, Daytona)."""
    terminal = config.setdefault("terminal", {})

    print()
    print_info('Ресурсы контейнера:')

    # Persistence
    current_persist = terminal.get("container_persistent", True)
    persist_label = "да" if current_persist else "нет"
    print_info('  Постоянное хранилище сохраняет файлы между диалогами.')
    print_info("  Ответьте «нет», если файлы нужно удалять после каждого диалога.")
    persist_str = prompt(
        '  Сохранять файлы между диалогами? (да/нет)', persist_label
    )
    terminal["container_persistent"] = persist_str.lower() in {"yes", "true", "y", "1", "д", "да"}

    # CPU
    current_cpu = terminal.get("container_cpu", 1)
    cpu_str = prompt('  Ядра процессора', str(current_cpu))
    try:
        terminal["container_cpu"] = float(cpu_str)
    except ValueError:
        pass

    # Memory
    current_mem = terminal.get("container_memory", 5120)
    mem_str = prompt('  Память в МБ (5120 = 5 ГБ)', str(current_mem))
    try:
        terminal["container_memory"] = int(mem_str)
    except ValueError:
        pass

    # Disk
    current_disk = terminal.get("container_disk", 51200)
    disk_str = prompt('  Диск в МБ (51200 = 50 ГБ)', str(current_disk))
    try:
        terminal["container_disk"] = int(disk_str)
    except ValueError:
        pass


def _prompt_vercel_sandbox_settings(config: dict):
    """Prompt for Vercel Sandbox settings without exposing unsupported disk sizing."""
    terminal = config.setdefault("terminal", {})

    print()
    print_info('Настройки Vercel Sandbox:')
    print_info('  Файлы сохраняются в снимках Vercel.')
    print_info('  Снимки восстанавливают только файлы. Запущенные процессы после пересоздания среды не продолжаются.')

    from tools.terminal_tool import _SUPPORTED_VERCEL_RUNTIMES

    current_runtime = terminal.get("vercel_runtime") or "node24"
    supported_label = ", ".join(_SUPPORTED_VERCEL_RUNTIMES)
    runtime = prompt(f'  Среда ({supported_label})', current_runtime).strip() or current_runtime
    if runtime not in _SUPPORTED_VERCEL_RUNTIMES:
        print_warning(f"Среда Vercel '{runtime}' не поддерживается. Оставляю {current_runtime}.")
        runtime = current_runtime if current_runtime in _SUPPORTED_VERCEL_RUNTIMES else "node24"
    terminal["vercel_runtime"] = runtime
    save_env_value("TERMINAL_VERCEL_RUNTIME", runtime)

    current_persist = terminal.get("container_persistent", True)
    persist_label = "да" if current_persist else "нет"
    terminal["container_persistent"] = prompt(
        '  Сохранять файлы в снимках? (да/нет)', persist_label
    ).lower() in {"yes", "true", "y", "1", "д", "да"}

    current_cpu = terminal.get("container_cpu", 1)
    cpu_str = prompt('  Ядра процессора', str(current_cpu))
    try:
        terminal["container_cpu"] = float(cpu_str)
    except ValueError:
        pass

    current_mem = terminal.get("container_memory", 5120)
    mem_str = prompt('  Память в МБ (5120 = 5 ГБ)', str(current_mem))
    try:
        terminal["container_memory"] = int(mem_str)
    except ValueError:
        pass

    if terminal.get("container_disk", 51200) not in {0, 51200}:
        print_warning('Vercel Sandbox не позволяет менять размер диска. container_disk сброшен до 51200.')
    terminal["container_disk"] = 51200

    print()
    print_info('Вход в Vercel:')
    print_info('  Укажите постоянный токен доступа Vercel, ID проекта и команды.')
    linked_project = _read_nearest_vercel_project()
    if linked_project:
        print_info('  Настройки найдены в ближайшем файле .vercel/project.json.')

    remove_env_value("VERCEL_OIDC_TOKEN")
    token = prompt('    Токен доступа Vercel', get_env_value("VERCEL_TOKEN") or "", password=True)
    project = prompt(
        '    ID проекта Vercel',
        get_env_value("VERCEL_PROJECT_ID") or linked_project.get("projectId", ""),
    )
    team = prompt(
        '    ID команды Vercel',
        get_env_value("VERCEL_TEAM_ID") or linked_project.get("orgId", ""),
    )
    if token:
        save_env_value("VERCEL_TOKEN", token)
    if project:
        save_env_value("VERCEL_PROJECT_ID", project)
    if team:
        save_env_value("VERCEL_TEAM_ID", team)


def _read_nearest_vercel_project(start: Path | None = None) -> dict[str, str]:
    """Read project/team defaults from the nearest Vercel link file."""
    current = (start or Path.cwd()).resolve()
    if current.is_file():
        current = current.parent

    for directory in (current, *current.parents):
        project_file = directory / ".vercel" / "project.json"
        if not project_file.exists():
            continue
        try:
            data = json.loads(project_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        if not isinstance(data, dict):
            return {}
        return {
            key: value
            for key, value in {
                "projectId": data.get("projectId"),
                "orgId": data.get("orgId"),
            }.items()
            if isinstance(value, str) and value.strip()
        }
    return {}


# Tool categories and provider config are now in tools_config.py (shared
# between `hermes tools` and `hermes setup tools`).


# =============================================================================
# Section 1: Model & Provider Configuration
# =============================================================================



def setup_model_provider(config: dict, *, quick: bool = False):
    """Configure the inference provider and default model.

    Delegates to ``cmd_model()`` (the same flow used by ``hermes model``)
    for provider selection, credential prompting, and model picking.
    This ensures a single code path for all provider setup — any new
    provider added to ``hermes model`` is automatically available here.

    When *quick* is True, skips credential rotation, vision, and TTS
    configuration — used by the streamlined first-time quick setup.
    """
    from korra_cli.config import load_config, save_config

    print_header('Сервис модели')
    print_info('Выберите, как подключить основную модель для диалогов.')
    print_info(f'   Инструкция: {_DOCS_BASE}/integrations/providers')
    print()

    # Delegate to the shared hermes model flow — handles provider picker,
    # credential prompting, model selection, and config persistence.
    from korra_cli.main import select_provider_and_model
    try:
        select_provider_and_model()
    except (SystemExit, KeyboardInterrupt):
        print()
        print_info('Настройка провайдера пропущена.')
    except Exception as exc:
        logger.debug("select_provider_and_model error during setup: %s", exc)
        print_warning(f'Ошибка настройки провайдера: {exc}')
        print_info('Попробуйте позже: korra model')

    # Re-sync the wizard's config dict from what cmd_model saved to disk.
    # This is critical: cmd_model writes to disk via its own load/save cycle,
    # and the wizard's final save_config(config) must not overwrite those
    # changes with stale values (#4172). Refresh the dict in place so callers
    # that keep the same object see every section the shared model picker may
    # have changed (model, custom_providers, auxiliary, provider metadata, etc.).
    _refreshed = load_config()
    config.clear()
    config.update(_refreshed)

    # Credential rotation, vision-backend selection, and TTS provider are no
    # longer prompted here. They have safe defaults (rotation off, vision
    # auto-detected from the main provider, TTS = Edge) and are configurable
    # on demand via `hermes auth add`, `hermes setup` vision, and
    # `hermes setup tts`. This keeps both quick and full setup thin.


    # Tool Gateway prompt is already shown by _model_flow_nous() above.
    save_config(config)


# =============================================================================
# Section 1b: TTS Provider Configuration
# =============================================================================


def _check_espeak_ng() -> bool:
    """Check if espeak-ng is installed."""
    return shutil.which("espeak-ng") is not None or shutil.which("espeak") is not None


def _install_neutts_deps() -> bool:
    """Install NeuTTS dependencies with user approval. Returns True on success."""
    import subprocess
    import sys

    # Check espeak-ng
    if not _check_espeak_ng():
        print()
        print_warning('Для NeuTTS нужен espeak-ng — компонент обработки произношения.')
        if sys.platform == "darwin":
            print_info('Установка: brew install espeak-ng')
        elif sys.platform == "win32":
            print_info('Установка: choco install espeak-ng')
        else:
            print_info('Установка: sudo apt install espeak-ng')
        print()
        if prompt_yes_no('Установить espeak-ng сейчас?', True):
            try:
                if sys.platform == "darwin":
                    subprocess.run(["brew", "install", "espeak-ng"], check=True)
                elif sys.platform == "win32":
                    subprocess.run(["choco", "install", "espeak-ng", "-y"], check=True)
                else:
                    subprocess.run(["sudo", "apt", "install", "-y", "espeak-ng"], check=True)
                print_success('espeak-ng установлен')
            except (subprocess.CalledProcessError, FileNotFoundError) as e:
                print_warning(f'Не удалось установить espeak-ng автоматически: {e}')
                print_info('Установите его вручную и запустите настройку ещё раз.')
                return False
        else:
            print_warning('espeak-ng нужен для NeuTTS. Установите его вручную перед использованием.')

    # Install neutts Python package
    print()
    print_info('Устанавливаю пакет Python neutts…')
    print_info('При первом запуске также загрузится модель озвучивания (около 300 МБ).')
    print()

    # Route through the canonical uv → pip → ensurepip ladder so pip-less
    # venvs (Ubuntu 25.10 `python -m venv`, `uv venv`) work out of the box.
    from korra_cli.tools_config import _pip_install

    try:
        result = _pip_install(["-U", "neutts[all]", "--quiet"], timeout=300)
    except Exception as e:
        print_error(f'Не удалось установить neutts: {e}')
        print_info("Попробуйте вручную: uv pip install -U 'neutts[all]'")
        return False
    if result.returncode == 0:
        print_success('neutts установлен')
        return True
    err = (result.stderr or "").strip()
    print_error(f"Не удалось установить neutts: {(err[:300] if err else 'установка не удалась')}")
    print_info("Попробуйте вручную: uv pip install -U 'neutts[all]'")
    return False


def _install_kittentts_deps() -> bool:
    """Install KittenTTS dependencies with user approval. Returns True on success."""

    wheel_url = (
        "https://github.com/KittenML/KittenTTS/releases/download/"
        "0.8.1/kittentts-0.8.1-py3-none-any.whl"
    )
    print()
    print_info('Устанавливаю пакет Python kittentts. При первом запуске загрузится модель размером около 25–80 МБ…')
    print()

    from korra_cli.tools_config import _pip_install

    try:
        result = _pip_install(["-U", wheel_url, "soundfile", "--quiet"], timeout=300)
    except Exception as e:
        print_error(f'Не удалось установить kittentts: {e}')
        print_info(f"Попробуйте вручную: uv pip install -U '{wheel_url}' soundfile")
        return False
    if result.returncode == 0:
        print_success('kittentts установлен')
        return True
    err = (result.stderr or "").strip()
    print_error(f"Не удалось установить kittentts: {(err[:300] if err else 'установка не удалась')}")
    print_info(f"Попробуйте вручную: uv pip install -U '{wheel_url}' soundfile")
    return False


def _xai_oauth_logged_in_for_setup() -> bool:
    """True iff xAI Grok OAuth credentials are already stored locally.

    Lets TTS / STT setup skip the API-key prompt for users who logged in
    through ``hermes model`` -> xAI Grok OAuth (SuperGrok / Premium+).
    """
    try:
        from korra_cli.auth import get_xai_oauth_auth_status

        return bool(get_xai_oauth_auth_status().get("logged_in"))
    except Exception:
        return False


def _run_xai_oauth_login_from_setup() -> bool:
    """Run the xAI Grok OAuth device-code login from inside the setup wizard.

    Saves OAuth tokens only. Does **not** switch the active inference
    provider or rewrite ``model.provider`` — callers (TTS setup, tools
    config) only need credentials for side tools.

    Returns True on success, False on any failure (the caller falls back
    to whatever the user picked next, e.g. Edge TTS).
    """
    try:
        from korra_cli.auth import (
            _is_remote_session,
            _save_xai_oauth_tokens,
            _xai_oauth_device_code_login,
            unsuppress_credential_source,
        )
    except Exception as exc:
        print_warning(f'Компоненты входа xAI Grok OAuth недоступны: {exc}')
        return False

    open_browser = not _is_remote_session()
    print()
    print_info('Выполняю вход через xAI Grok OAuth (SuperGrok / Premium+)…')
    try:
        creds = _xai_oauth_device_code_login(open_browser=open_browser)
        _save_xai_oauth_tokens(
            creds["tokens"],
            discovery=creds.get("discovery"),
            redirect_uri=creds.get("redirect_uri", ""),
            last_refresh=creds.get("last_refresh"),
            auth_mode="oauth_device_code",
            set_active=False,
        )
        # Mirror model/dashboard re-login: clear device_code suppression so
        # the pool can seed from the singleton after a prior `auth remove`.
        unsuppress_credential_source("xai-oauth", "device_code")
        return True
    except Exception as exc:
        print_warning(f'Не удалось войти через xAI Grok OAuth: {exc}')
        return False


def _setup_tts_provider(config: dict):
    """Interactive TTS provider selection with install flow for NeuTTS."""
    tts_config = config.get("tts", {})
    current_provider = tts_config.get("provider", "edge")
    subscription_features = get_nous_subscription_features(config)

    provider_labels = {
        "edge": "Edge TTS",
        "elevenlabs": "ElevenLabs",
        "openai": "OpenAI TTS",
        "xai": "xAI TTS",
        "minimax": "MiniMax TTS",
        "mistral": "Mistral Voxtral TTS",
        "gemini": "Google Gemini TTS",
        "neutts": "NeuTTS",
        "kittentts": "KittenTTS",
    }
    current_label = provider_labels.get(current_provider, current_provider)

    print()
    print_header('Озвучивание ответов (необязательно)')
    print_info(f'Сейчас: {current_label}')
    print()

    choices = []
    providers = []
    if managed_nous_tools_enabled() and subscription_features.nous_auth_present:
        choices.append('Подписка Nous: озвучивание OpenAI, оплата по подписке')
        providers.append("nous-openai")
    choices.extend(
        [
            'Edge TTS — бесплатно, в облаке, без настройки',
            'ElevenLabs — высокое качество, нужен ключ API',
            'OpenAI TTS — хорошее качество, нужен ключ API',
            'xAI TTS — голоса Grok, вход через браузер или ключ API',
            'MiniMax TTS — высокое качество, копирование голоса, нужен ключ API',
            'Mistral Voxtral TTS — разные языки, формат Opus, нужен ключ API',
            'Google Gemini TTS — 30 голосов, управление описанием, нужен ключ API',
            'NeuTTS — бесплатно на вашем компьютере, загрузка около 300 МБ',
            'KittenTTS — бесплатно на вашем компьютере, лёгкая модель 25–80 МБ',
        ]
    )
    providers.extend(["edge", "elevenlabs", "openai", "xai", "minimax", "mistral", "gemini", "neutts", "kittentts"])
    choices.append(f'Оставить текущее значение ({current_label})')
    keep_current_idx = len(choices) - 1
    idx = prompt_choice('Выберите сервис озвучивания:', choices, keep_current_idx)

    if idx == keep_current_idx:
        return

    selected = providers[idx]
    selected_via_nous = selected == "nous-openai"
    if selected == "nous-openai":
        selected = "openai"
        print_info('OpenAI TTS будет работать через шлюз Nous. Оплата — по вашей подписке.')
        if get_env_value("VOICE_TOOLS_OPENAI_KEY") or get_env_value("OPENAI_API_KEY"):
            print_warning(
                'Ключи прямого доступа OpenAI ещё настроены и могут иметь приоритет. При необходимости удалите их из .env выбранного профиля.'
            )

    if selected == "neutts":
        # Check if already installed
        try:
            already_installed = importlib.util.find_spec("neutts") is not None
        except Exception:
            already_installed = False

        if already_installed:
            print_success('NeuTTS уже установлен')
        else:
            print()
            print_info('Для NeuTTS нужны:')
            print_info('  • Пакет Python neutts (около 50 МБ; модель при первом запуске — около 300 МБ)')
            print_info('  • Системный пакет espeak-ng для обработки произношения')
            print()
            if prompt_yes_no('Установить компоненты NeuTTS сейчас?', True):
                if not _install_neutts_deps():
                    print_warning('Установка NeuTTS не завершена. Использую Edge TTS.')
                    selected = "edge"
            else:
                print_info('Установка пропущена. После ручной установки укажите tts.provider: neutts.')
                selected = "edge"

    elif selected == "elevenlabs":
        existing = get_env_value("ELEVENLABS_API_KEY")
        if not existing:
            print()
            api_key = prompt('Ключ API ElevenLabs', password=True)
            if api_key:
                save_env_value("ELEVENLABS_API_KEY", api_key)
                print_success('Ключ API ElevenLabs сохранён')
            else:
                print_warning('Ключ API не указан. Использую Edge TTS.')
                selected = "edge"

    elif selected == "openai" and not selected_via_nous:
        existing = get_env_value("VOICE_TOOLS_OPENAI_KEY") or get_env_value("OPENAI_API_KEY")
        if not existing:
            print()
            api_key = prompt('Ключ API OpenAI для озвучивания', password=True)
            if api_key:
                save_env_value("VOICE_TOOLS_OPENAI_KEY", api_key)
                print_success('Ключ API OpenAI TTS сохранён')
            else:
                print_warning('Ключ API не указан. Использую Edge TTS.')
                selected = "edge"

    elif selected == "xai":
        # Resolution order: existing OAuth tokens (free for SuperGrok subscribers
        # via the Hermes auth store) > existing XAI_API_KEY > prompt the user.
        # When neither is configured, offer both options instead of forcing the
        # API-key path — xAI TTS works fine with OAuth bearer tokens too.
        oauth_logged_in = _xai_oauth_logged_in_for_setup()
        existing_api_key = get_env_value("XAI_API_KEY")

        if oauth_logged_in:
            print_success(
                'Для озвучивания xAI TTS будут использованы данные входа xAI Grok OAuth (SuperGrok / Premium+).'
            )
        elif existing_api_key:
            print_success('Для xAI TTS будет использован существующий XAI_API_KEY.')
        else:
            print()
            choice_idx = prompt_choice(
                'Как войти в xAI для озвучивания?',
                choices=[
                    'Войти в xAI Grok OAuth через браузер (SuperGrok / Premium+)',
                    'Вставить ключ API xAI (console.x.ai)',
                    'Пропустить и использовать Edge TTS',
                ],
                default=0,
            )
            if choice_idx == 0:
                if _run_xai_oauth_login_from_setup():
                    print_success(
                        'Вход выполнен. xAI TTS будет использовать эти данные OAuth.'
                    )
                else:
                    print_warning(
                        'Вход в xAI Grok OAuth не завершён. Использую Edge TTS.'
                    )
                    selected = "edge"
            elif choice_idx == 1:
                api_key = prompt('Ключ API xAI для озвучивания', password=True)
                if api_key:
                    save_env_value("XAI_API_KEY", api_key)
                    print_success('Ключ API xAI TTS сохранён')
                else:
                    from korra_constants import display_hermes_home as _dhh
                    print_warning(
                        f'Ключ API для xAI TTS не указан. Настройте XAI_API_KEY через `korra setup model` или файл {_dhh()}/.env. Пока используется Edge TTS.'
                    )
                    selected = "edge"
            else:
                print_warning('Настройка xAI TTS пропущена. Использую Edge TTS.')
                selected = "edge"

        if selected == "xai":
            print()
            voice_id = prompt("ID голоса xAI (Enter — 'eve'; можно указать ID своего голоса)")
            if voice_id and voice_id.strip():
                config.setdefault("tts", {}).setdefault("xai", {})["voice_id"] = voice_id.strip()
                print_success(f'ID голоса xAI: {voice_id.strip()}')


    elif selected == "minimax":
        existing = get_env_value("MINIMAX_API_KEY")
        if not existing:
            print()
            api_key = prompt('Ключ API MiniMax для озвучивания', password=True)
            if api_key:
                save_env_value("MINIMAX_API_KEY", api_key)
                print_success('Ключ API MiniMax TTS сохранён')
            else:
                print_warning('Ключ API не указан. Использую Edge TTS.')
                selected = "edge"

    elif selected == "mistral":
        existing = get_env_value("MISTRAL_API_KEY")
        if not existing:
            print()
            api_key = prompt('Ключ API Mistral для озвучивания', password=True)
            if api_key:
                save_env_value("MISTRAL_API_KEY", api_key)
                print_success('Ключ API Mistral TTS сохранён')
            else:
                print_warning('Ключ API не указан. Использую Edge TTS.')
                selected = "edge"

    elif selected == "gemini":
        existing = get_env_value("GEMINI_API_KEY") or get_env_value("GOOGLE_API_KEY")
        if not existing:
            print()
            print_info('Получить бесплатный ключ API: https://aistudio.google.com/app/apikey')
            api_key = prompt('Ключ API Gemini для озвучивания', password=True)
            if api_key:
                save_env_value("GEMINI_API_KEY", api_key)
                print_success('Ключ API Gemini TTS сохранён')
            else:
                print_warning('Ключ API не указан. Использую Edge TTS.')
                selected = "edge"

    elif selected == "kittentts":
        # Check if already installed
        try:
            already_installed = importlib.util.find_spec("kittentts") is not None
        except Exception:
            already_installed = False

        if already_installed:
            print_success('KittenTTS уже установлен')
        else:
            print()
            print_info('KittenTTS — лёгкая локальная модель (около 25–80 МБ), работает на процессоре без ключа API.')
            print_info('Голоса: Jasper, Bella, Luna, Bruno, Rosie, Hugo, Kiki, Leo')
            print()
            if prompt_yes_no('Установить KittenTTS сейчас?', True):
                if not _install_kittentts_deps():
                    print_warning('Установка KittenTTS не завершена. Использую Edge TTS.')
                    selected = "edge"
            else:
                print_info('Установка пропущена. После ручной установки укажите tts.provider: kittentts.')
                selected = "edge"

    # Save the selection
    if "tts" not in config:
        config["tts"] = {}
    config["tts"]["provider"] = selected
    save_config(config)
    print_success(f'Сервис озвучивания: {provider_labels.get(selected, selected)}')


def setup_tts(config: dict):
    """Standalone TTS setup (for 'hermes setup tts')."""
    _setup_tts_provider(config)


# =============================================================================
# Section 2: Terminal Backend Configuration
# =============================================================================


def setup_terminal_backend(config: dict):
    """Configure the terminal execution backend."""
    import platform as _platform
    print_header('Среда выполнения команд')
    print_info('Выберите, где Корра будет выполнять команды и код.')
    print_info('Это определяет доступ к файлам и изоляцию выполняемых действий.')
    print_info(f'   Инструкция: {_DOCS_BASE}/user-guide/configuration#terminal-backend-configuration')
    print()

    current_backend = cfg_get(config, "terminal", "backend", default="local")
    is_linux = _platform.system() == "Linux"

    # Build backend choices with descriptions
    terminal_choices = [
        'Этот компьютер — выполнять команды здесь (по умолчанию)',
        'Docker — изолированный контейнер с настройкой ресурсов',
        'Modal — облачная среда без настройки сервера',
        'SSH — выполнять команды на удалённом компьютере',
        'Daytona — постоянная облачная среда разработки',
        'Vercel Sandbox — облачная микровиртуальная машина со снимками файлов',
    ]
    idx_to_backend = {0: "local", 1: "docker", 2: "modal", 3: "ssh", 4: "daytona", 5: "vercel_sandbox"}
    backend_to_idx = {"local": 0, "docker": 1, "modal": 2, "ssh": 3, "daytona": 4, "vercel_sandbox": 5}

    next_idx = 6
    if is_linux:
        terminal_choices.append('Singularity/Apptainer — контейнер для вычислительных кластеров')
        idx_to_backend[next_idx] = "singularity"
        backend_to_idx["singularity"] = next_idx
        next_idx += 1

    # Plugin-registered terminal backends (standalone plugin repos installed
    # under ~/.hermes/plugins/). Fail-soft: a broken plugin must not take the
    # setup wizard down.
    plugin_backend_names = []
    try:
        from korra_cli.plugins import discover_plugins

        discover_plugins()  # idempotent — plugin state may not be loaded yet
        from agent.terminal_env_registry import list_providers

        for _provider in list_providers():
            _pname = _provider.name.strip().lower()
            terminal_choices.append(f"{_provider.display_name} - {_provider.description}")
            idx_to_backend[next_idx] = _pname
            backend_to_idx[_pname] = next_idx
            plugin_backend_names.append(_pname)
            next_idx += 1
    except Exception:
        pass

    # Add keep current option
    keep_current_idx = next_idx
    terminal_choices.append(f'Оставить текущее значение ({current_backend})')
    idx_to_backend[keep_current_idx] = current_backend

    terminal_idx = prompt_choice(
        'Выберите среду выполнения:', terminal_choices, keep_current_idx
    )

    selected_backend = idx_to_backend.get(terminal_idx)

    if terminal_idx == keep_current_idx:
        print_info(f'Текущая среда сохранена: {current_backend}')
        return

    config.setdefault("terminal", {})["backend"] = selected_backend

    if selected_backend == "local":
        print_success('Среда выполнения: этот компьютер')
        print_info('Команды выполняются прямо на этом компьютере.')
        # Gateway working directory defaults to home; sudo stays off. Both are
        # configurable later via `hermes setup terminal` / config.yaml.
        config["terminal"].setdefault("cwd", str(Path.home()))

    elif selected_backend == "docker":
        print_success('Среда выполнения: Docker')

        # Check if Docker is available
        docker_bin = shutil.which("docker")
        if not docker_bin:
            print_warning('Docker не найден в PATH.')
            print_info('Установка Docker: https://docs.docker.com/get-docker/')
        else:
            print_info(f'Найден Docker: {docker_bin}')

        # Image and resource limits use defaults; tune via `hermes setup terminal`.
        config["terminal"].setdefault(
            "docker_image", "nikolaik/python-nodejs:python3.11-nodejs20"
        )
        print()
        print_info('Изолированные среды Docker можно защитить сетевым фильтром ключей доступа.')
        print_info(
            'Он направляет трафик через iron-proxy: контейнеры получают токены прокси вместо настоящих ключей API.'
        )
        print_info(
            '   Пока доступно только для Docker. Modal, SSH, Daytona и Singularity ещё не поддерживаются.'
        )
        if prompt_yes_no('  Включить сетевую защиту ключей для Docker?', False):
            proxy_cfg = config.setdefault("proxy", {})
            proxy_cfg["enabled"] = True
            proxy_cfg.setdefault("enforce_on_docker", True)
            print_success('Сетевая защита ключей включена в настройках')
            print_info(
                'Выполните `korra egress setup`, затем `korra egress start`, чтобы создать токены и запустить прокси.'
            )
        else:
            print_info(
                'Сетевая защита ключей пропущена. Включить позже: `korra egress setup`.'
            )

    elif selected_backend == "singularity":
        print_success('Среда выполнения: Singularity/Apptainer')

        # Check if singularity/apptainer is available
        sing_bin = shutil.which("apptainer") or shutil.which("singularity")
        if not sing_bin:
            print_warning('Singularity/Apptainer не найден в PATH.')
            print_info(
                'Установка: https://apptainer.org/docs/admin/main/installation.html'
            )
        else:
            print_info(f'Найдено: {sing_bin}')

        # Image and resource limits use defaults; tune via `hermes setup terminal`.
        config["terminal"].setdefault(
            "singularity_image",
            "docker://nikolaik/python-nodejs:python3.11-nodejs20",
        )

    elif selected_backend == "modal":
        print_success('Среда выполнения: Modal')
        print_info('Облачная среда без настройки сервера. Каждый диалог получает свой контейнер.')
        from tools.managed_tool_gateway import is_managed_tool_gateway_ready
        from tools.tool_backend_helpers import normalize_modal_mode

        managed_modal_available = bool(
            managed_nous_tools_enabled()
            and
            get_nous_subscription_features(config).nous_auth_present
            and is_managed_tool_gateway_ready("modal")
        )
        modal_mode = normalize_modal_mode(cfg_get(config, "terminal", "modal_mode"))
        use_managed_modal = False
        if managed_modal_available:
            modal_choices = [
                'Использовать подписку Nous',
                'Использовать мой аккаунт Modal',
            ]
            if modal_mode == "managed":
                default_modal_idx = 0
            elif modal_mode == "direct":
                default_modal_idx = 1
            else:
                default_modal_idx = 1 if get_env_value("MODAL_TOKEN_ID") else 0
            modal_mode_idx = prompt_choice(
                'Как оплачивать выполнение команд в Modal?',
                modal_choices,
                default_modal_idx,
            )
            use_managed_modal = modal_mode_idx == 0

        if use_managed_modal:
            config["terminal"]["modal_mode"] = "managed"
            print_info('Modal будет работать через шлюз Nous. Оплата — по вашей подписке.')
            if get_env_value("MODAL_TOKEN_ID") or get_env_value("MODAL_TOKEN_SECRET"):
                print_info(
                    'Ключи прямого доступа Modal ещё сохранены, но эта среда настроена на работу через управляемый шлюз.'
                )
        else:
            config["terminal"]["modal_mode"] = "direct"
            print_info('Нужен аккаунт Modal: https://modal.com')

            # Check if modal SDK is installed
            try:
                __import__("modal")
            except ImportError:
                print_info('Устанавливаю пакет Modal…')
                from korra_cli.tools_config import _pip_install

                result = _pip_install(["modal"])
                if result.returncode == 0:
                    print_success('Пакет Modal установлен')
                else:
                    print_warning('Установка не удалась. Выполните вручную: uv pip install modal')

            # Modal token
            print()
            print_info('Вход в Modal:')
            print_info('  Получить токен: https://modal.com/settings')
            existing_token = get_env_value("MODAL_TOKEN_ID")
            if existing_token:
                print_info('  Токен Modal уже настроен')
                if prompt_yes_no('  Обновить данные входа Modal?', False):
                    token_id = prompt('    ID токена Modal', password=True)
                    token_secret = prompt('    Секрет токена Modal', password=True)
                    if token_id:
                        save_env_value("MODAL_TOKEN_ID", token_id)
                    if token_secret:
                        save_env_value("MODAL_TOKEN_SECRET", token_secret)
            else:
                token_id = prompt('    ID токена Modal', password=True)
                token_secret = prompt('    Секрет токена Modal', password=True)
                if token_id:
                    save_env_value("MODAL_TOKEN_ID", token_id)
                if token_secret:
                    save_env_value("MODAL_TOKEN_SECRET", token_secret)

    elif selected_backend == "daytona":
        print_success('Среда выполнения: Daytona')
        print_info('Постоянная облачная среда разработки.')
        print_info('Каждый диалог получает отдельную среду с сохранением файлов.')
        print_info('Регистрация: https://daytona.io')

        # Check if daytona SDK is installed
        try:
            __import__("daytona")
        except ImportError:
            print_info('Устанавливаю пакет Daytona…')
            from korra_cli.tools_config import _pip_install

            result = _pip_install(["daytona"])
            if result.returncode == 0:
                print_success('Пакет Daytona установлен')
            else:
                print_warning('Установка не удалась. Выполните вручную: uv pip install daytona')
                if result.stderr:
                    print_info(f'  Ошибка: {result.stderr.strip().splitlines()[-1]}')

        # Daytona API key
        print()
        existing_key = get_env_value("DAYTONA_API_KEY")
        if existing_key:
            print_info('  Ключ API Daytona уже настроен')
            if prompt_yes_no('  Обновить ключ API?', False):
                api_key = prompt('    Ключ API Daytona', password=True)
                if api_key:
                    save_env_value("DAYTONA_API_KEY", api_key)
                    print_success('    Обновлено')
        else:
            api_key = prompt('    Ключ API Daytona', password=True)
            if api_key:
                save_env_value("DAYTONA_API_KEY", api_key)
                print_success('    Настроено')

        # Image and resource limits use defaults; tune via `hermes setup terminal`.
        config["terminal"].setdefault(
            "daytona_image", "nikolaik/python-nodejs:python3.11-nodejs20"
        )

    elif selected_backend == "vercel_sandbox":
        print_success('Среда выполнения: Vercel Sandbox')
        print_info('Облачные микровиртуальные машины с сохранением файлов в снимках.')
        print_info("Нужен дополнительный пакет: pip install 'hermes-agent[vercel]'")

        try:
            __import__("vercel")
        except ImportError:
            print_info('Устанавливаю пакет Vercel…')
            import subprocess

            # Managed uv first: $HERMES_HOME/bin is never on PATH, so a bare
            # which() misses the uv Hermes installed. Bootstrapping one is
            # welcome here — this is the interactive setup wizard, already
            # mid-install, and the alternative tier is a pip that a `uv venv`
            # venv may not even have.
            from korra_cli.managed_uv import ensure_uv

            uv_bin = ensure_uv()
            if uv_bin:
                result = subprocess.run(
                    [uv_bin, "pip", "install", "--python", sys.executable, "vercel"],
                    capture_output=True,
                    text=True,
                )
            else:
                result = subprocess.run(
                    [sys.executable, "-m", "pip", "install", "vercel"],
                    capture_output=True,
                    text=True,
                )
            if result.returncode == 0:
                print_success('Пакет Vercel установлен')
            else:
                print_warning("Установка не удалась. Выполните вручную: pip install 'hermes-agent[vercel]'")
                if result.stderr:
                    print_info(f'  Ошибка: {result.stderr.strip().splitlines()[-1]}')

        _prompt_vercel_sandbox_settings(config)

    elif selected_backend in plugin_backend_names:
        try:
            from agent.terminal_env_registry import get_provider

            _provider = get_provider(selected_backend)
            print_success(f'Среда выполнения: {_provider.display_name}')
            for _line in _provider.setup_instructions():
                print_info(_line)
            _provider.post_setup()
        except Exception as exc:
            print_warning(f'Ошибка настройки расширения среды: {exc}')

    elif selected_backend == "ssh":
        print_success('Среда выполнения: SSH')
        print_info('Команды выполняются на удалённом компьютере через SSH.')

        # SSH host
        current_host = get_env_value("TERMINAL_SSH_HOST") or ""
        host = prompt('  Сервер SSH (имя или IP-адрес)', current_host)
        if host:
            save_env_value("TERMINAL_SSH_HOST", host)

        # SSH user
        current_user = get_env_value("TERMINAL_SSH_USER") or ""
        user = prompt('  Пользователь SSH', current_user or os.getenv("USER", ""))
        if user:
            save_env_value("TERMINAL_SSH_USER", user)

        # SSH port
        current_port = get_env_value("TERMINAL_SSH_PORT") or "22"
        port = prompt('  Порт SSH', current_port)
        if port and port != "22":
            save_env_value("TERMINAL_SSH_PORT", port)

        # SSH key
        current_key = get_env_value("TERMINAL_SSH_KEY") or ""
        default_key = str(Path.home() / ".ssh" / "id_rsa")
        ssh_key = prompt('  Путь к закрытому ключу SSH', current_key or default_key)
        if ssh_key:
            save_env_value("TERMINAL_SSH_KEY", ssh_key)

        # Test connection
        if host and prompt_yes_no('  Проверить подключение SSH?', True):
            print_info('  Проверяю подключение…')
            import subprocess

            ssh_cmd = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5"]
            if ssh_key:
                ssh_cmd.extend(["-i", ssh_key])
            if port and port != "22":
                ssh_cmd.extend(["-p", port])
            ssh_cmd.append(f"{user}@{host}" if user else host)
            ssh_cmd.append("echo ok")
            result = subprocess.run(ssh_cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10)
            if result.returncode == 0:
                print_success('  Подключение SSH установлено!')
            else:
                print_warning(f'  Не удалось подключиться по SSH: {result.stderr.strip()}')
                print_info('  Проверьте сервер SSH и ключ доступа.')

    # Sync terminal backend to .env so terminal_tool picks it up directly.
    # config.yaml is the source of truth, but terminal_tool reads TERMINAL_ENV.
    save_env_value("TERMINAL_ENV", selected_backend)
    if selected_backend == "modal":
        save_env_value("TERMINAL_MODAL_MODE", config["terminal"].get("modal_mode", "auto"))
    if selected_backend == "vercel_sandbox":
        save_env_value("TERMINAL_VERCEL_RUNTIME", config["terminal"].get("vercel_runtime", "node24"))
    save_config(config)
    print()
    print_success(f'Среда выполнения: {selected_backend}')


# =============================================================================
# Section 3: Agent Settings
# =============================================================================


def _apply_default_agent_settings(config: dict):
    """Apply recommended defaults for all agent settings without prompting."""
    config.setdefault("agent", {})["max_turns"] = 150
    # config.yaml is the authoritative source for max_turns; the gateway
    # bridges it into HERMES_MAX_ITERATIONS at startup. We no longer write
    # to .env to avoid the dual-source inconsistency that caused the
    # 60-vs-500 bug (stale .env entry silently shadowing config.yaml).
    remove_env_value("HERMES_MAX_ITERATIONS")

    config.setdefault("display", {})["tool_progress"] = "all"

    config.setdefault("compression", {})["enabled"] = True
    config["compression"]["threshold"] = 0.50

    # Default: never auto-reset sessions. This matches the gateway's own
    # default (SessionResetPolicy.mode = "none"); we still write it
    # explicitly so the choice is visible/editable in config.yaml.
    config.setdefault("session_reset", {})["mode"] = "none"

    save_config(config)
    print_success('Применены рекомендуемые настройки:')
    print_info('  Максимум действий: 150')
    print_info('  Показ действий: all')
    print_info('  Порог сжатия истории: 0.50')
    print_info('  Автосброс диалога: отключён (используйте /reset или сжатие)')
    print_info('  Изменить позже: `korra setup agent`.')


def setup_agent_settings(config: dict):
    """Configure agent behavior: iterations, progress display, compression, session reset."""

    print_header('Настройки агента')
    print_info(f'   Инструкция: {_DOCS_BASE}/user-guide/configuration')
    print()

    # ── Max Iterations ──
    # config.yaml is authoritative; read from there. If a legacy .env
    # entry is still around (from pre-PR#18413 setups), prefer the
    # config value so we don't surface a stale number to the user.
    current_max = str(cfg_get(config, "agent", "max_turns", default=90))
    print_info('Максимальное число обращений к инструментам за диалог.')
    print_info('Чем больше лимит, тем сложнее могут быть задачи и тем выше расход токенов.')
    print_info(
        f'Нажмите Enter, чтобы оставить {current_max}. Для обычных задач подходит 90, для длительного исследования — 150 и больше.'
    )

    max_iter_str = prompt('Максимум действий', current_max)
    try:
        max_iter = int(max_iter_str)
        if max_iter > 0:
            # Write to config.yaml (authoritative) only. Also clean up any
            # stale .env entry from earlier setup runs — the gateway's
            # bridge in gateway/run.py now unconditionally derives
            # HERMES_MAX_ITERATIONS from agent.max_turns at startup.
            config.setdefault("agent", {})["max_turns"] = max_iter
            config.pop("max_turns", None)
            remove_env_value("HERMES_MAX_ITERATIONS")
            print_success(f'Максимум действий: {max_iter}')
    except ValueError:
        print_warning('Некорректное число. Текущее значение сохранено.')

    # ── Tool Progress Display ──
    print_info("")
    print_info('Показ действий инструментов')
    print_info('Сколько подробностей показывать в терминале и мессенджерах.')
    print_info('  off     — только итоговый ответ')
    print_info('  new     — название инструмента, только когда он меняется')
    print_info('  all     — каждое действие с кратким описанием')
    print_info('  verbose — полные параметры, результаты и отладочные сообщения')
    print_info('  log     — без сообщений в чате; все действия записываются в logs/tool_calls.log выбранного профиля (только шлюз)')

    current_mode = cfg_get(config, "display", "tool_progress", default="all")
    mode = prompt('Режим показа действий', current_mode)
    if mode.lower() in {"off", "new", "all", "verbose", "log"}:
        if "display" not in config:
            config["display"] = {}
        config["display"]["tool_progress"] = mode.lower()
        save_config(config)
        print_success(f'Режим показа действий: {mode.lower()}')
    else:
        print_warning(f"Неизвестный режим '{mode}'. Оставляю '{current_mode}'.")

    # ── Context Compression ──
    print_header('Сжатие истории')
    print_info('Длинная история автоматически заменяется кратким содержанием.')
    print_info(
        'Чем выше порог, тем позже сжимается история. Низкий порог запускает сжатие раньше.'
    )

    config.setdefault("compression", {})["enabled"] = True

    current_threshold = cfg_get(config, "compression", "threshold", default=0.50)
    threshold_str = prompt('Порог сжатия (0.5–0.95)', str(current_threshold))
    try:
        threshold = float(threshold_str)
        if 0.5 <= threshold <= 0.95:
            config["compression"]["threshold"] = threshold
    except ValueError:
        pass

    print_success(
        f"Порог сжатия истории: {config['compression'].get('threshold', 0.5)}"
    )

    # ── Session Reset Policy ──
    print_header('Автоматический сброс диалогов')
    print_info(
        'История переписки в Telegram, Discord и других мессенджерах со временем растёт.'
    )
    print_info(
        'С каждым сообщением увеличивается объём истории и расходы на сервис модели.'
    )
    print_info("")
    print_info(
        'Диалоги можно автоматически сбрасывать после простоя'
    )
    print_info(
        'или ежедневно в указанное время. Перед сбросом Корра сохраняет важное'
    )
    print_info(
        'в постоянную память, а историю текущего диалога очищает.'
    )
    print_info("")
    print_info('В любой момент можно сбросить диалог командой /reset.')
    print_info("")

    reset_choices = [
        'После простоя или ежедневно — что наступит раньше',
        'Только после простоя — через указанное число минут без сообщений',
        'Ежедневно — в указанное время',
        'Не сбрасывать автоматически (рекомендуется) — только /reset или сжатие',
        'Сохранить текущие настройки',
    ]

    current_policy = config.get("session_reset", {})
    current_mode = current_policy.get("mode", "none")
    current_idle = current_policy.get("idle_minutes", 1440)
    current_hour = current_policy.get("at_hour", 4)

    default_reset = {"both": 0, "idle": 1, "daily": 2, "none": 3}.get(current_mode, 3)

    reset_idx = prompt_choice('Когда сбрасывать диалог:', reset_choices, default_reset)

    config.setdefault("session_reset", {})

    if reset_idx == 0:  # Both
        config["session_reset"]["mode"] = "both"
        idle_str = prompt('  Сбрасывать после простоя (минуты)', str(current_idle))
        try:
            idle_val = int(idle_str)
            if idle_val > 0:
                config["session_reset"]["idle_minutes"] = idle_val
        except ValueError:
            pass
        hour_str = prompt('  Час ежедневного сброса (0–23, местное время)', str(current_hour))
        try:
            hour_val = int(hour_str)
            if 0 <= hour_val <= 23:
                config["session_reset"]["at_hour"] = hour_val
        except ValueError:
            pass
        print_success(
            f"Диалог сбрасывается после {config['session_reset'].get('idle_minutes', 1440)} мин бездействия или ежедневно в {config['session_reset'].get('at_hour', 4)}:00."
        )
    elif reset_idx == 1:  # Idle only
        config["session_reset"]["mode"] = "idle"
        idle_str = prompt('  Сбрасывать после простоя (минуты)', str(current_idle))
        try:
            idle_val = int(idle_str)
            if idle_val > 0:
                config["session_reset"]["idle_minutes"] = idle_val
        except ValueError:
            pass
        print_success(
            f"Диалог сбрасывается после {config['session_reset'].get('idle_minutes', 1440)} мин бездействия."
        )
    elif reset_idx == 2:  # Daily only
        config["session_reset"]["mode"] = "daily"
        hour_str = prompt('  Час ежедневного сброса (0–23, местное время)', str(current_hour))
        try:
            hour_val = int(hour_str)
            if 0 <= hour_val <= 23:
                config["session_reset"]["at_hour"] = hour_val
        except ValueError:
            pass
        print_success(
            f"Диалог сбрасывается ежедневно в {config['session_reset'].get('at_hour', 4)}:00."
        )
    elif reset_idx == 3:  # None
        config["session_reset"]["mode"] = "none"
        print_info(
            'Автосброс диалогов отключён. История будет управляться только сжатием.'
        )
        print_warning(
            'Длинные диалоги увеличивают расходы. При необходимости используйте /reset.'
        )
    # else: keep current (idx == 4)

    save_config(config)


# =============================================================================
# Section 4: Messaging Platforms (Gateway)
# =============================================================================


_TELEGRAM_BOT_TOKEN_RE = re.compile(r"^\d+:[A-Za-z0-9_-]{30,}$")


def _is_valid_telegram_bot_token(token: str) -> bool:
    return bool(_TELEGRAM_BOT_TOKEN_RE.match(token))


def _setup_telegram_auto_result():
    """Attempt automatic Telegram bot creation via managed QR onboarding."""
    try:
        from korra_cli.telegram_managed_bot import auto_setup_telegram_bot_result
    except ImportError:
        return None

    profile_name: str | None = None
    try:
        profile_name = _profile_name_from_hermes_home(Path(get_hermes_home()))
    except Exception:
        pass

    return auto_setup_telegram_bot_result(profile_name=profile_name)


def _profile_name_from_hermes_home(hermes_home) -> str | None:
    """Return the active profile name when HERMES_HOME is a profile dir."""
    if hermes_home.parent.name == "profiles":
        return hermes_home.name
    return None


def _setup_telegram_auto() -> str | None:
    """Attempt automatic Telegram bot creation and return only the token."""
    result = _setup_telegram_auto_result()
    return result.token if result else None


def _prompt_telegram_bot_token() -> str | None:
    print_info('Создайте бота через @BotFather в Telegram.')
    while True:
        token = prompt('Токен бота Telegram', password=True)
        if not token:
            return None
        if not _is_valid_telegram_bot_token(token):
            print_error(
                'Некорректный токен. Нужен формат <числовой_ID>:<секрет>, например 123456789:ABCdefGHI-jklMNOpqrSTUvwxYZ.'
            )
            continue
        return token


def _setup_telegram():
    """Configure Telegram bot credentials and allowlist."""
    print_header("Telegram")
    existing = get_env_value("TELEGRAM_BOT_TOKEN")
    if existing:
        print_info('Telegram уже настроен')
        if not prompt_yes_no('Настроить Telegram заново?', False):
            # Check missing allowlist on existing config
            if not get_env_value("TELEGRAM_ALLOWED_USERS"):
                print_info('⚠️ У Telegram-бота нет списка доступа. Им может пользоваться любой.')
                if prompt_yes_no('Добавить разрешённых пользователей сейчас?', True):
                    print_info('   Чтобы узнать ваш ID Telegram, напишите @userinfobot.')
                    allowed_users = prompt('Разрешённые ID пользователей через запятую')
                    if allowed_users:
                        save_env_value("TELEGRAM_ALLOWED_USERS", allowed_users.replace(" ", ""))
                        print_success('Список доступа Telegram настроен')
            return

    print_info('Как создать бота Telegram?')
    print()
    print_info('  [1] Автоматически (рекомендуется)')
    print_info('      Отсканируйте QR-код и подтвердите действие в Telegram.')
    print_info('      Копировать токен не потребуется.')
    print()
    print_info('  [2] Вручную')
    print_info('      Создайте бота через @BotFather и вставьте его токен.')
    print()

    choice = prompt('Ваш выбор [1/2]', default="1")
    token = None
    setup_result = None

    if choice.strip() == "1":
        setup_result = _setup_telegram_auto_result()
        if setup_result:
            token = setup_result.token
            if not _is_valid_telegram_bot_token(token):
                print_error('Автоматическая настройка вернула некорректный токен бота Telegram.')
                token = None
                setup_result = None
        else:
            token = None
        if not token:
            print()
            print_info('Перехожу к ручной настройке…')
            print()

    if not token:
        token = _prompt_telegram_bot_token()
    if not token:
        return

    save_env_value("TELEGRAM_BOT_TOKEN", token)
    print_success('Токен Telegram сохранён')

    print()
    print_info('🔒 Доступ: выберите, кто сможет пользоваться ботом.')
    print_info('   Как узнать ваш ID пользователя Telegram:')
    print_info('   1. Напишите боту @userinfobot в Telegram.')
    print_info('   2. Он пришлёт ваш числовой ID, например 123456789.')
    print()

    detected_user_id = getattr(setup_result, "owner_user_id", None)
    if detected_user_id:
        detected_id = str(detected_user_id)
        print_success(f'Ваш ID пользователя Telegram: {detected_id}')
        if prompt_yes_no('Разрешить этому аккаунту Telegram пользоваться ботом?', True):
            extra = prompt('Дополнительные разрешённые ID пользователей через запятую (необязательно)')
            ids = [detected_id]
            for uid in extra.replace(" ", "").split(","):
                if uid and uid not in ids:
                    ids.append(uid)
            allowed_users = ",".join(ids)
        else:
            allowed_users = prompt(
                'Разрешённые ID пользователей через запятую (пусто — доступ для всех)'
            )
    else:
        allowed_users = prompt(
            'Разрешённые ID пользователей через запятую (пусто — доступ для всех)'
        )

    if allowed_users:
        allowed_users = allowed_users.replace(" ", "")
        save_env_value("TELEGRAM_ALLOWED_USERS", allowed_users)
        print_success('Доступ к Telegram-боту разрешён только указанным пользователям')
    else:
        print_info('⚠️ Список доступа не задан. Ботом сможет пользоваться любой, кто его найдёт.')

    print()
    print_info('📬 Основной чат: сюда Корра отправляет результаты задач по расписанию,')
    print_info('   сообщения с других платформ и уведомления.')
    print_info('   Для личной переписки Telegram это ваш ID пользователя (см. выше).')

    first_user_id = allowed_users.split(",")[0].strip() if allowed_users else ""
    if first_user_id:
        if prompt_yes_no(f'Использовать ваш ID ({first_user_id}) как основной чат?', True):
            save_env_value("TELEGRAM_HOME_CHANNEL", first_user_id)
            print_success(f'Основной чат Telegram: {first_user_id}')
        else:
            home_channel = prompt('ID основного чата (можно настроить позже командой /set-home в Telegram)')
            if home_channel:
                save_env_value("TELEGRAM_HOME_CHANNEL", home_channel)
    else:
        print_info('   Можно настроить позже командой /set-home в чате Telegram.')
        home_channel = prompt('ID основного чата (можно оставить пустым и настроить позже)')
        if home_channel:
            save_env_value("TELEGRAM_HOME_CHANNEL", home_channel)


# _setup_slack and _write_slack_manifest_and_instruct moved to the slack
# plugin: plugins/platforms/slack/adapter.py::interactive_setup (registered
# via setup_fn and dispatched through the plugin path). #41112 / #3823.


# _setup_matrix moved to plugins/platforms/matrix/adapter.py::interactive_setup
# (registered via setup_fn, dispatched through the plugin path). #41112.


def _setup_bluebubbles():
    """Configure BlueBubbles iMessage gateway."""
    print_header("BlueBubbles (iMessage)")
    existing = get_env_value("BLUEBUBBLES_SERVER_URL")
    if existing:
        print_info('BlueBubbles уже настроен')
        if not prompt_yes_no('Настроить BlueBubbles заново?', False):
            return

    print_info('BlueBubbles подключает Корру к iMessage. Это бесплатный сервер')
    print_info('для macOS с открытым кодом, который позволяет пользоваться iMessage на других устройствах.')
    print_info('   Нужен Mac с BlueBubbles Server версии 1.0.0 или новее.')
    print_info('   Скачать: https://bluebubbles.app/')
    print()
    print_info('В BlueBubbles Server откройте Settings → API и найдите адрес сервера и пароль.')
    print()

    server_url = prompt('Адрес сервера BlueBubbles, например http://192.168.1.10:1234')
    if not server_url:
        print_warning('Без адреса сервера настроить BlueBubbles нельзя. Настройка пропущена.')
        return
    save_env_value("BLUEBUBBLES_SERVER_URL", server_url.rstrip("/"))

    password = prompt('Пароль сервера BlueBubbles', password=True)
    if not password:
        print_warning('Без пароля настроить BlueBubbles нельзя. Настройка пропущена.')
        return
    save_env_value("BLUEBUBBLES_PASSWORD", password)
    print_success('Данные подключения BlueBubbles сохранены')

    print()
    print_info('🔒 Доступ: выберите, кто сможет писать боту.')
    print_info('   Укажите адреса iMessage: почту (user@icloud.com) или телефон (+15551234567).')
    print()
    allowed_users = prompt('Разрешённые адреса iMessage через запятую (пусто — доступ для всех)')
    if allowed_users:
        save_env_value("BLUEBUBBLES_ALLOWED_USERS", allowed_users.replace(" ", ""))
        print_success('Список доступа BlueBubbles настроен')
    else:
        print_info('⚠️ Список доступа не задан. Любой, кто напишет вам в iMessage, сможет пользоваться ботом.')

    print()
    print_info('📬 Основной чат: телефон или почта для результатов задач и уведомлений.')
    print_info('   Можно настроить позже командой /set-home в чате iMessage.')
    home_channel = prompt('Адрес основного чата (можно оставить пустым и настроить позже)')
    if home_channel:
        save_env_value("BLUEBUBBLES_HOME_CHANNEL", home_channel)

    print()
    print_info('Дополнительные настройки (обычно подходят значения по умолчанию):')
    if prompt_yes_no('Настроить приём вебхуков?', False):
        webhook_port = prompt('Порт приёма вебхуков (по умолчанию 8645)')
        if webhook_port:
            try:
                save_env_value("BLUEBUBBLES_WEBHOOK_PORT", str(int(webhook_port)))
                print_success(f'Порт вебхуков: {webhook_port}')
            except ValueError:
                print_warning('Некорректный порт. Использую 8645.')

    print()
    print_info('Для индикатора набора, уведомлений о прочтении и реакций')
    print_info('нужен помощник BlueBubbles Private API. Обычная переписка работает без него.')
    print_info('   Установка: https://docs.bluebubbles.app/helper-bundle/installation')


def _setup_qqbot():
    """Configure QQ Bot (Official API v2) via gateway setup."""
    from korra_cli.gateway import _setup_qqbot as _gateway_setup_qqbot
    _gateway_setup_qqbot()


def _setup_webhooks():
    """Configure webhook integration."""
    print_header('Вебхуки')
    existing = get_env_value("WEBHOOK_ENABLED")
    if existing:
        print_info('Вебхуки уже настроены')
        if not prompt_yes_no('Настроить вебхуки заново?', False):
            return

    print()
    print_warning('⚠  Для вебхуков и SMS нужен доступ к портам шлюза')
    print_warning('   из интернета. Запускайте шлюз в изолированной среде')
    print_warning('   (например, Docker или виртуальной машине), чтобы ограничить доступ при вредоносных инструкциях.')
    print()
    print_info('   Инструкция: https://hermes-agent.nousresearch.com/docs/user-guide/messaging/webhooks/')
    print()

    port = prompt('Порт вебхуков (по умолчанию 8644)')
    if port:
        try:
            save_env_value("WEBHOOK_PORT", str(int(port)))
            print_success(f'Порт вебхуков: {port}')
        except ValueError:
            print_warning('Некорректный порт. Использую 8644.')

    secret = prompt('Общий секрет HMAC для всех маршрутов', password=True)
    if secret:
        save_env_value("WEBHOOK_SECRET", secret)
        print_success('Секрет вебхуков сохранён')
    else:
        print_warning('Секрет не задан. Укажите отдельные секреты маршрутов в config.yaml.')

    save_env_value("WEBHOOK_ENABLED", "true")
    print()
    print_success('Вебхуки включены. Следующие шаги:')
    from korra_constants import display_hermes_home as _dhh
    print_info(f'   1. Опишите маршруты вебхуков в {_dhh()}/config.yaml.')
    print_info('   2. Укажите в сервисе (GitHub, GitLab и т. д.) адрес:')
    print_info('      http://ваш-сервер:8644/webhooks/<имя-маршрута>')
    print()
    print_info('   Настройка маршрутов:')
    print_info("   https://hermes-agent.nousresearch.com/docs/user-guide/messaging/webhooks/#configuring-routes")
    print()
    print_info('   Открыть настройки в редакторе: korra config edit')
    print_info('   Открыть настройки в редакторе: korra config edit')


def _setup_platform_status_label(status: str) -> str:
    """Translate display status while setup decisions retain canonical markers."""
    labels = {
        "configured": "настроено",
        "not configured": "не настроено",
        "partially configured": "настроено частично",
        "configured + paired": "настроено и подключено",
        "enabled, not paired": "включено, требуется подключение",
        "configured + E2EE": "настроено, шифрование включено",
        "plugin disabled": "плагин отключён",
    }
    return labels.get(status, status)


def setup_gateway(config: dict):
    """Configure messaging platform integrations."""
    from korra_cli.gateway import _all_platforms, _platform_status, _configure_platform

    print_header('Мессенджеры')
    print_info('Подключите мессенджеры, чтобы общаться с Коррой откуда угодно.')
    print_info('Пробел — выбрать, Enter — подтвердить.')
    print()

    platforms = _all_platforms()

    # Build checklist, pre-selecting already-configured platforms.
    items = []
    pre_selected = []
    for i, plat in enumerate(platforms):
        status = _platform_status(plat)
        items.append(f"{plat['emoji']} {plat['label']}  ({_setup_platform_status_label(status)})")
        if status == "configured":
            pre_selected.append(i)

    selected = prompt_checklist('Выберите платформы для настройки:', items, pre_selected)

    if not selected:
        print_info('Платформы не выбраны. Настроить их позже: `korra setup gateway`.')
    else:
        for idx in selected:
            _configure_platform(platforms[idx])

    # ── Gateway Service Setup ──
    # Count any platform (built-in or plugin) the user configured during this
    # setup pass — reuses ``_platform_status`` so plugin platforms like IRC
    # are picked up without another hard-coded env-var list.
    def _is_progress(status: str) -> bool:
        s = status.lower()
        return not (
            s == "not configured"
            or s.startswith("partially")
            or s.startswith("plugin disabled")
        )

    any_messaging = any(
        _is_progress(_platform_status(p)) for p in _all_platforms()
    )
    if any_messaging:
        print()
        print_info("━" * 50)
        print_success('Мессенджеры настроены!')

        # Check if any home channels are missing
        missing_home = []
        if get_env_value("TELEGRAM_BOT_TOKEN") and not get_env_value(
            "TELEGRAM_HOME_CHANNEL"
        ):
            missing_home.append("Telegram")
        if get_env_value("DISCORD_BOT_TOKEN") and not get_env_value(
            "DISCORD_HOME_CHANNEL"
        ):
            missing_home.append("Discord")
        if get_env_value("SLACK_BOT_TOKEN") and not get_env_value("SLACK_HOME_CHANNEL"):
            missing_home.append("Slack")
        if get_env_value("BLUEBUBBLES_SERVER_URL") and not get_env_value("BLUEBUBBLES_HOME_CHANNEL"):
            missing_home.append("BlueBubbles")
        if get_env_value("QQ_APP_ID") and not (
            get_env_value("QQBOT_HOME_CHANNEL") or get_env_value("QQ_HOME_CHANNEL")
        ):
            missing_home.append("QQBot")

        if missing_home:
            print()
            print_warning(f"Основной чат не настроен для: {', '.join(missing_home)}")
            print_info('   Без него результаты задач по расписанию и сообщения')
            print_info('   с других платформ не будут доставлены в эти мессенджеры.')
            print_info('   Настроить позже: /set-home в чате или команда:')
            for plat in missing_home:
                print_info(
                    f'     korra config set {plat.upper()}_HOME_CHANNEL <ID_чата>'
                )

    # ── Gateway Service Setup ──
    # Runs UNCONDITIONALLY — even with zero platforms configured. A gateway
    # without platforms is a supported mode (cron scheduler keeps running,
    # and adapters come up automatically once tokens are added later, e.g.
    # via `hermes import` or `hermes setup gateway`). Gating this on
    # messaging config was the bug that left install-then-import machines
    # with registered cron jobs and restored bot tokens but no process to
    # serve them.
    from korra_cli.gateway import (
        _is_service_running,
        supports_systemd_services,
        ensure_gateway_service,
        systemd_restart,
        launchd_restart,
        UserSystemdUnavailableError,
        SystemScopeRequiresRootError,
        _system_scope_wizard_would_need_root,
        _print_system_scope_remediation,
    )
    import platform as _platform

    _is_macos = _platform.system() == "Darwin"
    _is_windows = _platform.system() == "Windows"
    supports_systemd = supports_systemd_services()

    print()
    if _is_service_running():
        # Already running: only offer a restart when this setup pass may
        # have changed platform config — a restart interrupts any active
        # session, so it stays behind a prompt.
        if supports_systemd and _system_scope_wizard_would_need_root():
            _print_system_scope_remediation("restart")
        elif any_messaging and prompt_yes_no(
            '  Перезапустить шлюз, чтобы применить изменения?', True
        ):
            try:
                if supports_systemd:
                    systemd_restart()
                elif _is_macos:
                    launchd_restart()
                elif _is_windows:
                    from korra_cli import gateway_windows
                    gateway_windows.restart()
            except UserSystemdUnavailableError as e:
                print_error('  Не удалось перезапустить: пользовательская служба systemd недоступна.')
                for line in str(e).splitlines():
                    print(f"  {line}")
            except SystemScopeRequiresRootError as e:
                # Defense in depth: the pre-check above should have
                # caught this, but a race (unit file appearing mid-run)
                # could still land here. Previously this exited the
                # whole wizard via sys.exit(1).
                print_error(f'  Не удалось перезапустить: {e}')
                _print_system_scope_remediation("restart")
            except Exception as e:
                print_error(f'  Не удалось перезапустить: {e}')
    else:
        # Not running: install (if needed) and start, no questions asked.
        ensure_gateway_service(context="setup")

    print_info("━" * 50)


# =============================================================================
# Section 5: Tool Configuration (delegates to unified tools_config.py)
# =============================================================================


def setup_tools(config: dict, first_install: bool = False):
    """Configure tools — delegates to the unified tools_command() in tools_config.py.

    Both `hermes setup tools` and `hermes tools` use the same flow:
    platform selection → toolset toggles → provider/API key configuration.

    Args:
        first_install: When True, uses the simplified first-install flow
            (no platform menu, prompts for all unconfigured API keys).
    """
    from korra_cli.tools_config import tools_command

    tools_command(first_install=first_install, config=config)


# =============================================================================
# Shared Metrics
# =============================================================================


def setup_telemetry(config: dict):
    """Configure the local, privacy-safe shared-metrics subscriber."""
    print_header('Локальные метрики')
    print_info('Метрики содержат только счётчики и сводные показатели.')
    print_info('Данные остаются в этом профиле Korra и никуда не отправляются.')

    telemetry = config.get("telemetry")
    if not isinstance(telemetry, dict):
        telemetry = {}
        config["telemetry"] = telemetry
    shared_metrics = telemetry.get("shared_metrics")
    if not isinstance(shared_metrics, dict):
        shared_metrics = {}
        telemetry["shared_metrics"] = shared_metrics

    current = shared_metrics.get("enabled") is True
    shared_metrics["enabled"] = prompt_yes_no(
        'Включить локальные метрики?',
        default=current,
    )
    if shared_metrics["enabled"]:
        print_success('Локальные метрики включены.')
    else:
        print_info('Локальные метрики отключены.')


# =============================================================================
# Post-Migration Section Skip Logic
# =============================================================================


def _model_section_has_credentials(config: dict) -> bool:
    """Return True when any known inference provider has usable credentials.

    Sources of truth:
      * ``PROVIDER_REGISTRY`` in ``korra_cli.auth`` — lists every supported
        provider along with its ``api_key_env_vars``.
      * ``active_provider`` in the auth store — covers OAuth device-code /
        external-OAuth providers (Nous, Codex, Qwen, Gemini CLI, ...).
      * The legacy OpenRouter aggregator env vars, which route generic
        ``OPENAI_API_KEY`` / ``OPENROUTER_API_KEY`` values through OpenRouter.
    """
    try:
        from korra_cli.auth import get_active_provider
        if get_active_provider():
            return True
    except Exception:
        pass

    try:
        from korra_cli.auth import PROVIDER_REGISTRY
    except Exception:
        PROVIDER_REGISTRY = {}  # type: ignore[assignment]

    def _has_key(pconfig) -> bool:
        for env_var in pconfig.api_key_env_vars:
            # CLAUDE_CODE_OAUTH_TOKEN is set by Claude Code itself, not by
            # the user — mirrors is_provider_explicitly_configured in auth.py.
            if env_var == "CLAUDE_CODE_OAUTH_TOKEN":
                continue
            if get_env_value(env_var):
                return True
        return False

    # Prefer the provider declared in config.yaml, avoids false positives
    # from stray env vars (GH_TOKEN, etc.) when the user has already picked
    # a different provider.
    model_cfg = config.get("model") if isinstance(config, dict) else None
    if isinstance(model_cfg, dict):
        provider_id = (model_cfg.get("provider") or "").strip().lower()
        if provider_id in PROVIDER_REGISTRY:
            if _has_key(PROVIDER_REGISTRY[provider_id]):
                return True
        if provider_id == "openrouter":
            for env_var in ("OPENROUTER_API_KEY", "OPENAI_API_KEY"):
                if get_env_value(env_var):
                    return True

    # OpenRouter aggregator fallback (no provider declared in config).
    for env_var in ("OPENROUTER_API_KEY", "OPENAI_API_KEY"):
        if get_env_value(env_var):
            return True

    for pid, pconfig in PROVIDER_REGISTRY.items():
        # Skip copilot in auto-detect: GH_TOKEN / GITHUB_TOKEN are
        # commonly set for git tooling.  Mirrors resolve_provider in auth.py.
        if pid == "copilot":
            continue
        if _has_key(pconfig):
            return True
    return False


def _gateway_platform_short_label(label: str) -> str:
    """Strip trailing parenthetical qualifiers from a gateway platform label."""
    base = label.split("(", 1)[0].strip()
    return base or label


def _get_section_config_summary(config: dict, section_key: str) -> Optional[str]:
    """Return a short summary if a setup section is already configured, else None.

    Used after OpenClaw migration to detect which sections can be skipped.
    ``get_env_value`` is the module-level import from korra_cli.config
    so that test patches on ``setup_mod.get_env_value`` take effect.
    """
    if section_key == "model":
        if not _model_section_has_credentials(config):
            return None
        model = config.get("model")
        if isinstance(model, str) and model.strip():
            return model.strip()
        if isinstance(model, dict):
            return str(model.get("default") or model.get("model") or "настроено")
        return "настроено"

    elif section_key == "terminal":
        backend = cfg_get(config, "terminal", "backend", default="local")
        return f'среда: {backend}'

    elif section_key == "agent":
        max_turns = cfg_get(config, "agent", "max_turns", default=90)
        return f'максимум запросов: {max_turns}'

    elif section_key == "gateway":
        from korra_cli.gateway import _all_platforms, _platform_status
        # Count any non-empty status other than the "not configured" sentinel —
        # platforms like WhatsApp ("enabled, not paired"), Matrix ("configured
        # + E2EE"), and Signal ("partially configured") all indicate the user
        # has already started setup and we shouldn't force the section to rerun.
        configured = [
            _gateway_platform_short_label(plat["label"])
            for plat in _all_platforms()
            if _platform_status(plat) and _platform_status(plat) != "not configured"
        ]
        if configured:
            return ", ".join(configured)
        return None  # No platforms configured — section must run

    elif section_key == "tools":
        tools = []
        if get_env_value("ELEVENLABS_API_KEY"):
            tools.append('Озвучивание ElevenLabs')
        if get_env_value("BROWSERBASE_API_KEY"):
            tools.append('Браузер')
        if get_env_value("FIRECRAWL_API_KEY"):
            tools.append("Firecrawl")
        if tools:
            return ", ".join(tools)
        return None

    return None


def _skip_configured_section(
    config: dict, section_key: str, label: str
) -> bool:
    """Show an already-configured section summary and offer to skip.

    Returns True if the user chose to skip, False if the section should run.
    """
    summary = _get_section_config_summary(config, section_key)
    if not summary:
        return False
    print()
    print_success(f"  {label}: {summary}")
    return not prompt_yes_no(f'  Настроить {label.lower()} заново?', default=False)


# =============================================================================
# OpenClaw Migration
# =============================================================================


_OPENCLAW_SCRIPT = (
    get_optional_skills_dir(PROJECT_ROOT / "optional-skills")
    / "migration"
    / "openclaw-migration"
    / "scripts"
    / "openclaw_to_hermes.py"
)


def _load_openclaw_migration_module():
    """Load the openclaw_to_hermes migration script as a module.

    Returns the loaded module, or None if the script can't be loaded.
    """
    if not _OPENCLAW_SCRIPT.exists():
        return None

    spec = importlib.util.spec_from_file_location(
        "openclaw_to_hermes", _OPENCLAW_SCRIPT
    )
    if spec is None or spec.loader is None:
        return None

    mod = importlib.util.module_from_spec(spec)
    # Register in sys.modules so @dataclass can resolve the module
    # (Python 3.11+ requires this for dynamically loaded modules)
    import sys as _sys
    _sys.modules[spec.name] = mod
    try:
        spec.loader.exec_module(mod)
    except Exception:
        _sys.modules.pop(spec.name, None)
        raise
    return mod


# Item kinds that represent high-impact changes warranting explicit warnings.
# Gateway tokens/channels can hijack messaging platforms from the old agent.
# Config values may have different semantics between OpenClaw and Hermes.
# Instruction/context files (.md) can contain incompatible setup procedures.
_HIGH_IMPACT_KIND_KEYWORDS = {
    "gateway": '⚠ Мессенджеры: Korra подключится к каналам OpenClaw.',
    "telegram": '⚠ Telegram: Korra подключится к боту OpenClaw.',
    "slack": '⚠ Slack: Korra подключится к рабочему пространству OpenClaw.',
    "discord": '⚠ Discord: Korra подключится к боту OpenClaw.',
    "whatsapp": '⚠ WhatsApp: Korra подключится к аккаунту OpenClaw.',
    "config": '⚠ Параметры OpenClaw могут отличаться по смыслу от настроек Korra.',
    "soul": '⚠ Файл инструкций может содержать команды настройки и перезапуска для OpenClaw.',
    "memory": '⚠ Файл памяти или контекста может ссылаться на инфраструктуру OpenClaw.',
    "context": '⚠ Файл контекста может содержать инструкции для OpenClaw.',
}


def _print_migration_preview(report: dict):
    """Print a detailed dry-run preview of what migration would do.

    Groups items by category and adds explicit warnings for high-impact
    changes like gateway token takeover and config value differences.
    """
    items = report.get("items", [])
    if not items:
        print_info('Нечего переносить.')
        return

    migrated_items = [i for i in items if i.get("status") == "migrated"]
    conflict_items = [i for i in items if i.get("status") == "conflict"]
    skipped_items = [i for i in items if i.get("status") == "skipped"]

    warnings_shown = set()

    if migrated_items:
        print(color('  Будет импортировано:', Colors.GREEN))
        for item in migrated_items:
            kind = item.get("kind", "unknown")
            dest = item.get("destination", "")
            if dest:
                dest_short = str(dest).replace(str(Path.home()), "~")
                print(f"      {kind:<22s} → {dest_short}")
            else:
                print(f"      {kind}")

            # Check for high-impact items and collect warnings
            kind_lower = kind.lower()
            dest_lower = str(dest).lower()
            for keyword, warning in _HIGH_IMPACT_KIND_KEYWORDS.items():
                if keyword in kind_lower or keyword in dest_lower:
                    warnings_shown.add(warning)
        print()

    if conflict_items:
        print(color('  Будет заменено (уже есть в настройках Korra):', Colors.YELLOW))
        for item in conflict_items:
            kind = item.get("kind", "unknown")
            reason = item.get("reason", 'уже существует')
            print(f"      {kind:<22s}  {reason}")
        print()

    if skipped_items:
        print(color('  Будет пропущено:', Colors.DIM))
        for item in skipped_items:
            kind = item.get("kind", "unknown")
            reason = item.get("reason", "")
            print(f"      {kind:<22s}  {reason}")
        print()

    # Print collected warnings
    if warnings_shown:
        print(color('  ── Предупреждения ──', Colors.YELLOW))
        for warning in sorted(warnings_shown):
            print(color(f"    {warning}", Colors.YELLOW))
        print()
        print(color('  Параметры OpenClaw могут иметь другое значение в Korra.', Colors.YELLOW))
        print(color('  Например, tool_call_execution: "auto" в OpenClaw не равен режиму yolo в Korra.', Colors.YELLOW))
        print(color('  Файлы инструкций .md из OpenClaw могут содержать несовместимые указания.', Colors.YELLOW))
        print()


def _offer_openclaw_migration(hermes_home: Path) -> bool:
    """Detect ~/.openclaw and offer to migrate during first-time setup.

    Runs a dry-run first to show the user exactly what would be imported,
    overwritten, or taken over. Only executes after explicit confirmation.

    Returns True if migration ran successfully, False otherwise.
    """
    openclaw_dir = Path.home() / ".openclaw"
    if not openclaw_dir.is_dir():
        return False

    if not _OPENCLAW_SCRIPT.exists():
        return False

    print()
    print_header('Найдена установка OpenClaw')
    print_info(f'Данные OpenClaw: {openclaw_dir}')
    print_info('Перед переносом Корра покажет, что будет импортировано.')
    print()

    if not prompt_yes_no('Показать, что можно перенести?', default=True):
        print_info(
            'Перенос пропущен. Предпросмотр позже: korra claw migrate --dry-run'
        )
        return False

    # Ensure config.yaml exists before migration tries to read it
    config_path = get_config_path()
    if not config_path.exists():
        save_config(load_config())

    # Load the migration module
    try:
        mod = _load_openclaw_migration_module()
        if mod is None:
            print_warning('Не удалось загрузить скрипт переноса.')
            return False
    except Exception as e:
        print_warning(f'Не удалось загрузить скрипт переноса: {e}')
        logger.debug("OpenClaw migration module load error", exc_info=True)
        return False

    # ── Phase 1: Dry-run preview ──
    try:
        selected = mod.resolve_selected_options(None, None, preset="full")
        dry_migrator = mod.Migrator(
            source_root=openclaw_dir.resolve(),
            target_root=hermes_home.resolve(),
            execute=False,  # dry-run — no files modified
            workspace_target=None,
            overwrite=True,  # show everything including conflicts
            migrate_secrets=True,
            output_dir=None,
            selected_options=selected,
            preset_name="full",
        )
        preview_report = dry_migrator.migrate()
    except Exception as e:
        print_warning(f'Не удалось подготовить предпросмотр переноса: {e}')
        logger.debug("OpenClaw migration preview error", exc_info=True)
        return False

    # Display the full preview
    preview_summary = preview_report.get("summary", {})
    preview_count = preview_summary.get("migrated", 0)

    if preview_count == 0:
        print()
        print_info('В OpenClaw нет данных для переноса.')
        return False

    print()
    print_header(f'Предпросмотр переноса: элементов к импорту — {preview_count}')
    print_info('Изменения пока не внесены. Проверьте список:')
    print()
    _print_migration_preview(preview_report)

    # ── Phase 2: Confirm and execute ──
    if not prompt_yes_no('Начать перенос?', default=False):
        print_info(
            'Перенос отменён. Запустить позже: korra claw migrate'
        )
        print_info(
            'Повторный предпросмотр: --dry-run. Облегчённый перенос: --preset minimal.'
        )
        return False

    # Execute the migration — overwrite=False so existing Hermes configs are
    # preserved. The user saw the preview; conflicts are skipped by default.
    try:
        migrator = mod.Migrator(
            source_root=openclaw_dir.resolve(),
            target_root=hermes_home.resolve(),
            execute=True,
            workspace_target=None,
            overwrite=False,  # preserve existing Hermes config
            migrate_secrets=True,
            output_dir=None,
            selected_options=selected,
            preset_name="full",
        )
        report = migrator.migrate()
    except Exception as e:
        print_warning(f'Не удалось перенести данные: {e}')
        logger.debug("OpenClaw migration error", exc_info=True)
        return False

    # Print final summary
    summary = report.get("summary", {})
    migrated = summary.get("migrated", 0)
    skipped = summary.get("skipped", 0)
    conflicts = summary.get("conflict", 0)
    errors = summary.get("error", 0)

    print()
    if migrated:
        print_success(f'Из OpenClaw импортировано элементов: {migrated}.')
    if conflicts:
        print_info(f'Уже существуют в Korra и пропущены: {conflicts}. Чтобы заменить их: korra claw migrate --overwrite.')
    if skipped:
        print_info(f'Пропущено элементов: {skipped} (не найдены или не изменились).')
    if errors:
        print_warning(f'Элементов с ошибками: {errors}. Проверьте отчёт о переносе.')

    output_dir = report.get("output_dir")
    if output_dir:
        print_info(f'Полный отчёт сохранён: {output_dir}')

    print_success('Перенос завершён. Продолжаем настройку…')
    return True


# =============================================================================
# Main Wizard Orchestrator
# =============================================================================

SETUP_SECTIONS = [
    ("model", 'Провайдер и модель', setup_model_provider),
    ("tts", 'Озвучивание', setup_tts),
    ("terminal", 'Среда выполнения команд', setup_terminal_backend),
    ("gateway", 'Мессенджеры (шлюз)', setup_gateway),
    ("tools", 'Инструменты', setup_tools),
    ("telemetry", 'Локальные метрики', setup_telemetry),
    ("agent", 'Настройки агента', setup_agent_settings),
]


def _run_portal_one_shot(config: dict) -> None:
    """One-shot Nous Portal setup — OAuth + model pick + provider + Tool Gateway.

    Wired into ``hermes setup --portal`` and ``hermes portal``. This is the
    Nous-Portal slice of the first-time quick setup, collapsed into a single
    shareable command so a brand-new user goes from zero to a fully working
    Hermes session — model selected, provider set, and web/image/tts/browser
    tools routed via their Portal sub — without being told to run
    ``hermes setup`` and hunt for the quick-setup option.

    The login + model selection + provider switch + Tool Gateway opt-in are all
    delegated to ``_model_flow_nous`` — the exact same flow quick setup uses
    (``_run_first_time_quick_setup``) and the same one ``hermes model`` runs
    when you pick Nous. Routing through it (instead of hand-rolling the auth +
    provider write here) means ``hermes portal`` always offers a model picker,
    and there is a single source of truth for the Nous onboarding steps.
    """
    from korra_cli.config import load_config

    print()
    print(
        color(
            "┌─────────────────────────────────────────────────────────┐",
            Colors.MAGENTA,
        )
    )
    print(color('│     ⚕ Настройка Korra — Nous Portal                    │', Colors.MAGENTA))
    print(
        color(
            "└─────────────────────────────────────────────────────────┘",
            Colors.MAGENTA,
        )
    )
    print()
    print_info('  Одна подписка: более 300 моделей и сервисы инструментов —')
    print_info('    поиск в интернете, изображения, озвучивание и управление браузером.')
    print_info('    Всё оплачивается вашей подпиской Nous Portal.')
    print()
    print_info('  Регистрация: https://portal.nousresearch.com/manage-subscription')
    print()

    # _model_flow_nous handles BOTH the logged-out path (device-code OAuth,
    # which selects a model internally) and the already-logged-in path (curated
    # Nous model picker), then offers the Tool Gateway opt-in and sets
    # provider=nous via the login/model save. This is the same routine quick
    # setup calls, so `hermes portal` == quick setup's Nous step.
    try:
        from korra_cli.main import _model_flow_nous

        _model_flow_nous(config)
    except (KeyboardInterrupt, EOFError, SystemExit):
        # _login_nous raises SystemExit(130)/(1) on cancel/failure; the
        # logged-out path inside _model_flow_nous catches it, but the
        # expired-session re-login path only catches Exception, so a
        # SystemExit there would otherwise escape and kill the whole CLI.
        # Treat all of these as a graceful cancel/abort for the portal flow.
        print()
        print_info('  Настройка отменена.')
        print_info('  Повторить позже: `korra portal`.')
        return
    except Exception as exc:
        logger.debug("_model_flow_nous error during `hermes portal`: %s", exc)
        print()
        print_error(f'  Ошибка настройки Nous Portal: {exc}')
        print_info('  Повторить позже: `korra portal`.')
        return

    # Re-sync the in-memory config from disk — _model_flow_nous (and the
    # underlying login/model save) write via their own load/save cycle, so any
    # later save_config(config) by a caller must not clobber those values.
    try:
        _refreshed = load_config()
        if isinstance(_refreshed, dict):
            config.clear()
            config.update(_refreshed)
    except Exception:
        pass

    print()
    print_success('Nous Portal настроен.')
    print_info('  Проверить подключение: `korra portal info`.')
    print_info('  Начать диалог: `korra`.')


@contextmanager
def _setup_navigation_scope():
    """Install and reliably restore the setup menu navigation context."""
    from korra_cli.curses_ui import (
        reset_menu_navigation_handler,
        set_menu_navigation_handler,
    )

    token = _SETUP_NAVIGATION.set(_SetupNavigationState())
    menu_token = set_menu_navigation_handler(_handle_setup_menu_navigation)
    try:
        yield
    finally:
        reset_menu_navigation_handler(menu_token)
        _SETUP_NAVIGATION.reset(token)


def run_setup_wizard(args):
    """Run setup with navigation control scoped to this invocation."""
    with _setup_navigation_scope():
        try:
            return _run_setup_wizard_impl(args)
        except _SetupCancelled:
            print()
            print_info('Настройка отменена. Остальные разделы не изменены.')
            return None


def _run_setup_steps(
    steps: list[tuple[str, Callable[[], None]]],
) -> None:
    """Run setup sections with left-arrow navigation between choices.

    Left arrow at a section's first choice returns to the previous section.
    From a later, nested choice it replays earlier selections invisibly and
    reopens only the immediately preceding prompt.
    """
    state = _SETUP_NAVIGATION.get()
    section_index = 0
    answers_by_section: dict[int, list[object]] = {}
    replay_by_section: dict[int, list[object]] = {}
    try:
        while section_index < len(steps):
            label, action = steps[section_index]
            if state is not None:
                state.section_index = section_index
                state.prompt_index = 0
                state.active_prompt_index = -1
                state.resolved_choices = []
                state.replay_choices = copy.deepcopy(
                    replay_by_section.pop(section_index, [])
                )
            try:
                action()
            except _SetupGoBack as navigation:
                if state is not None:
                    answers_by_section[section_index] = copy.deepcopy(
                        state.resolved_choices
                    )
                if navigation.prompt_index > 0:
                    previous_index = section_index
                    target_prompt = navigation.prompt_index - 1
                    replay_by_section[previous_index] = copy.deepcopy(
                        answers_by_section.get(previous_index, [])[:target_prompt]
                    )
                else:
                    previous_index = max(0, section_index - 1)
                    previous_answers = answers_by_section.get(previous_index, [])
                    target_prompt = max(0, len(previous_answers) - 1)
                    replay_by_section[previous_index] = copy.deepcopy(
                        previous_answers[:target_prompt]
                    )
                previous_label = steps[previous_index][0]
                print()
                if previous_index == section_index:
                    print_info(f'Возврат к предыдущему выбору в разделе {label}…')
                else:
                    print_info(f'Возврат к разделу {previous_label}…')
                section_index = previous_index
                continue
            if state is not None:
                answers_by_section[section_index] = copy.deepcopy(
                    state.resolved_choices
                )
            section_index += 1
    finally:
        if state is not None:
            state.section_index = -1
            state.prompt_index = 0
            state.active_prompt_index = -1
            state.resolved_choices = []
            state.replay_choices = []


def run_setup_action_with_navigation(
    label: str,
    action: Callable[[], None],
    *,
    cancelled_message: str = 'Настройка отменена.',
) -> None:
    """Run a setup-style menu flow with Escape and nested Left navigation.

    Shared commands such as ``hermes model`` use the same provider/model
    pickers as the setup wizard, but run outside ``run_setup_wizard``.  This
    installs the setup navigation context for that standalone command and
    reuses the same prompt replay state machine.
    """
    with _setup_navigation_scope():
        try:
            _run_setup_steps([(label, action)])
        except _SetupCancelled:
            print()
            print_info(cancelled_message)


def _run_setup_wizard_impl(args):
    """Run the interactive setup wizard.

    Supports full, quick, and section-specific setup:
      hermes setup           — full or quick (auto-detected)
      hermes setup model     — just model/provider
      hermes setup tts       — just text-to-speech
      hermes setup terminal  — just terminal backend
      hermes setup gateway   — just messaging platforms
      hermes setup tools     — just tool configuration
      hermes setup telemetry — just local shared metrics
      hermes setup agent     — just agent settings
    """
    from korra_cli.config import is_managed, managed_error
    if is_managed():
        managed_error("запустить мастер настройки")
        return
    ensure_hermes_home()

    reset_requested = bool(getattr(args, "reset", False))
    if reset_requested:
        save_config(copy.deepcopy(DEFAULT_CONFIG))
        print_success('Восстановлены настройки по умолчанию.')

    reconfigure_requested = bool(getattr(args, "reconfigure", False))
    quick_requested = bool(getattr(args, "quick", False))

    config = load_config()
    hermes_home = get_hermes_home()

    # Back up existing config before setup modifies it (#3522)
    config_path = get_config_path()
    if config_path.exists():
        from datetime import datetime as _dt
        _backup_path = config_path.with_suffix(
            f".yaml.bak.{_dt.now().strftime('%Y%m%d_%H%M%S')}"
        )
        try:
            import shutil
            shutil.copy2(config_path, _backup_path)
        except Exception:
            _backup_path = None
    else:
        _backup_path = None

    # Detect non-interactive environments (headless SSH, Docker, CI/CD)
    non_interactive = getattr(args, 'non_interactive', False)
    if not non_interactive and not is_interactive_stdin():
        non_interactive = True

    if non_interactive:
        print_noninteractive_setup_guidance(
            'Интерактивный терминал недоступен.'
        )
        return

    # --portal: one-shot Nous Portal setup. Skips the rest of the wizard.
    if bool(getattr(args, "portal", False)):
        _run_portal_one_shot(config)
        return

    # Check if a specific section was requested
    section = getattr(args, "section", None)
    if section:
        for key, label, func in SETUP_SECTIONS:
            if key == section:
                print()
                print(
                    color(
                        "┌─────────────────────────────────────────────────────────┐",
                        Colors.MAGENTA,
                    )
                )
                print(color(f'│     ⚕ Настройка Korra — {label:<35s} │', Colors.MAGENTA))
                print(
                    color(
                        "└─────────────────────────────────────────────────────────┘",
                        Colors.MAGENTA,
                    )
                )
                _run_setup_steps(
                    [(label, lambda setup_func=func: setup_func(config))]
                )
                save_config(config)
                print()
                print_success(f'Раздел {label} настроен!')
                return

        print_error(f'Неизвестный раздел настройки: {section}')
        print_info(f"Доступные разделы: {', '.join((k for k, _, _ in SETUP_SECTIONS))}")
        return

    # Check if this is an existing installation with a provider configured
    from korra_cli.auth import get_active_provider

    active_provider = get_active_provider()
    is_existing = (
        bool(get_env_value("OPENROUTER_API_KEY"))
        or bool(get_env_value("OPENAI_BASE_URL"))
        or active_provider is not None
    )

    print()
    print(
        color(
            "┌─────────────────────────────────────────────────────────┐",
            Colors.MAGENTA,
        )
    )
    print(
        color(
            '│             ⚕ Мастер настройки Korra                  │', Colors.MAGENTA
        )
    )
    print(
        color(
            "├─────────────────────────────────────────────────────────┤",
            Colors.MAGENTA,
        )
    )
    print(
        color(
            '│  Давайте настроим вашу Korra.                          │', Colors.MAGENTA
        )
    )
    print(
        color(
            '│  Для выхода в любой момент нажмите Ctrl+C.             │', Colors.MAGENTA
        )
    )
    print(
        color(
            "└─────────────────────────────────────────────────────────┘",
            Colors.MAGENTA,
        )
    )

    migration_ran = False

    if is_existing:
        # Existing install — default is the full-wizard reconfigure flow.
        # Every prompt shows the current value as its default, so pressing
        # Enter keeps it.  Opt into `--quick` for the narrow "just fill in
        # missing items" flow (useful after a partial OpenClaw migration
        # or when a required API key got cleared).
        if quick_requested:
            _run_setup_steps(
                [('Быстрая настройка', lambda: _run_quick_setup(config, hermes_home))]
            )
            return

        print()
        print_header('Повторная настройка')
        print_success('Korra уже настроена.')
        print_info('Открываю полный мастер. В каждом пункте показано текущее значение.')
        print_info('Нажмите Enter, чтобы сохранить его, или введите новое.')
        print_info("")
        print_info('Подсказка: сразу открыть раздел — `korra setup model|terminal|')
        print_info('     gateway|tools|agent`; заполнить только недостающее — `korra setup --quick`.')
        # Fall through to the "Full Setup — run all sections" block below.
        # --reconfigure is now the default on existing installs; the flag
        # is preserved for backwards compatibility but is a no-op here.
    else:
        # ── First-Time Setup ──
        print()

        # --reconfigure / --quick on a fresh install are meaningless — fall
        # through to the normal first-time flow.
        if reconfigure_requested or quick_requested:
            print_info('Настройки пока не найдены. Запускаю первоначальную настройку.')
            print()

        # Offer OpenClaw migration before configuration begins
        migration_ran = _offer_openclaw_migration(hermes_home)
        if migration_ran:
            config = load_config()

        setup_mode = prompt_choice(
            'Как настроить Korra?',
            [
                'Быстрая настройка Nous Portal — вход через браузер без ключей API, модель и инструменты (рекомендуется)',
                'Полная настройка — выбрать провайдеров, инструменты и параметры, использовать свои ключи',
                'Минимальная настройка — только необходимое, остальные возможности включаются отдельно',
            ],
            0,
        )

        if setup_mode == 0:
            _run_setup_steps(
                [
                    (
                        'Быстрая настройка',
                        lambda: _run_first_time_quick_setup(
                            config, hermes_home, is_existing
                        ),
                    )
                ]
            )
            return
        if setup_mode == 2:
            _run_setup_steps(
                [
                    (
                        'Минимальная настройка',
                        lambda: _run_blank_slate_setup(
                            config, hermes_home, is_existing
                        ),
                    )
                ]
            )
            return

    # ── Full Setup — run all sections ──
    print_header('Где хранятся настройки')
    print_info(f'Файл настроек: {get_config_path()}')
    print_info(f'Файл секретов: {get_env_path()}')
    print_info(f'Папка данных:  {hermes_home}')
    print_info(f'Папка установки: {PROJECT_ROOT}')
    print()
    print_info('Файлы можно изменить вручную или командой `korra config edit`.')

    if migration_ran:
        print()
        print_info('Настройки импортированы из OpenClaw.')
        print_info('В каждом разделе показаны перенесённые значения. Enter — сохранить,')
        print_info('либо выберите повторную настройку.')

    # Section 3: Agent Settings — no longer prompted. First installs get the
    # recommended defaults silently; existing installs keep whatever they have.
    # Tune later with `hermes setup agent`.
    if not is_existing:
        _apply_default_agent_settings(config)

    def _model_step() -> None:
        if not (
            migration_ran
            and _skip_configured_section(config, "model", 'Провайдер и модель')
        ):
            setup_model_provider(config)

    def _terminal_step() -> None:
        if not (
            migration_ran
            and _skip_configured_section(config, "terminal", 'Среда выполнения команд')
        ):
            setup_terminal_backend(config)

    def _gateway_step() -> None:
        if not (
            migration_ran
            and _skip_configured_section(config, "gateway", 'Мессенджеры')
        ):
            setup_gateway(config)
            return

        # A migrated gateway section can be skipped, but its service still
        # needs to exist so imported platforms and cron jobs become active.
        from korra_cli.gateway import ensure_gateway_service

        ensure_gateway_service(context="setup")

    def _tools_step() -> None:
        if not (
            migration_ran
            and _skip_configured_section(config, "tools", 'Инструменты')
        ):
            setup_tools(config, first_install=not is_existing)

    _run_setup_steps(
        [
            ('Провайдер и модель', _model_step),
            ('Среда выполнения команд', _terminal_step),
            ('Мессенджеры', _gateway_step),
            ('Инструменты', _tools_step),
        ]
    )

    # Save and show summary
    save_config(config)
    if _backup_path and _backup_path.exists():
        print_info(f'Предыдущие настройки сохранены: {_backup_path}')
        print_info('Если изменились ваши настройки, восстановите их командой:')
        print_info(f"  cp {_backup_path} {config_path}")
    _print_setup_summary(config, hermes_home)


def _run_first_time_quick_setup(config: dict, hermes_home, is_existing: bool):
    """Streamlined first-time setup via Nous Portal: OAuth, model, terminal & messaging.

    Routes straight to the Nous Portal provider — runs the device-code OAuth
    login, picks a Nous model, then configures the terminal backend and (optionally)
    a messaging platform. Applies sensible defaults for everything else (agent
    settings, tools); the user can customize later via ``hermes setup <section>``
    or switch providers with ``hermes model``.
    """
    from korra_cli.config import load_config

    # Step 1: Nous Portal — OAuth login + model selection.
    # _model_flow_nous() handles both the logged-out path (device-code OAuth,
    # which selects a model internally) and the already-logged-in path (curated
    # Nous model picker). Provider is set to "nous" by the login/model save.
    print()
    print_header("Nous Portal")
    print_info('Одна подписка: более 300 моделей и сервисы инструментов —')
    print_info('  поиск в интернете, изображения, озвучивание и управление браузером.')
    print_info('Регистрация: https://portal.nousresearch.com/manage-subscription')
    print()
    try:
        from korra_cli.main import _model_flow_nous
        _model_flow_nous(config)
    except (KeyboardInterrupt, EOFError):
        print()
        print_info('Настройка Nous Portal отменена.')
    except Exception as exc:
        logger.debug("_model_flow_nous error during quick setup: %s", exc)
        print_warning(f'Ошибка настройки Nous Portal: {exc}')
        print_info('Попробуйте позже: korra model')

    # Re-sync the wizard's config dict from disk — _model_flow_nous (and the
    # underlying login/model save) write via their own load/save cycle, and the
    # wizard's later save_config(config) must not clobber those values (#4172).
    _refreshed = load_config()
    config.clear()
    config.update(_refreshed)

    # Step 2: Terminal Backend — where commands run is a core decision
    setup_terminal_backend(config)

    # Step 3: Apply defaults for everything else
    _apply_default_agent_settings(config)

    save_config(config)

    # Step 4: Offer messaging gateway setup
    print()
    gateway_choice = prompt_choice(
        'Подключить мессенджер (Telegram, Discord и т. д.)?',
        [
            'Настроить мессенджеры сейчас (рекомендуется)',
            'Пропустить. Настроить позже: `korra setup gateway`.',
        ],
        0,
    )

    if gateway_choice == 0:
        setup_gateway(config)
        save_config(config)
    else:
        # Messaging skipped — still install/start the gateway service so cron
        # jobs run and platforms come alive as soon as tokens are added later
        # (e.g. via `hermes import` from another machine).
        from korra_cli.gateway import ensure_gateway_service
        ensure_gateway_service(context="setup")

    print()
    print_success('Настройка завершена. Можно начинать работу.')
    print()
    print_info('  Все настройки: korra setup')
    if gateway_choice != 0:
        print_info('  Подключить Telegram/Discord: korra setup gateway')
    _print_macos_fda_tip()
    print()

    _print_setup_summary(config, hermes_home)


def _print_macos_fda_tip() -> None:
    """One-time macOS onboarding tip: a single Full Disk Access grant kills
    every per-folder permission prompt, permanently (issue #52010 follow-up).

    Uses the same prompt-free probe as doctor's check_macos_full_disk_access
    (the TCC db dir is FDA-gated but probing it never triggers a dialog).
    Silent on non-macOS and when FDA is already granted or indeterminate.
    """
    if sys.platform != "darwin":
        return
    tcc_dir = Path.home() / "Library" / "Application Support" / "com.apple.TCC"
    try:
        os.listdir(tcc_dir)
        return  # already granted — nothing to teach
    except PermissionError:
        pass
    except OSError:
        return  # indeterminate — don't nag
    print()
    print_info('  Подсказка для macOS: чтобы убрать запросы доступа к папкам,')
    print_info('  откройте «Системные настройки» → «Конфиденциальность и безопасность» → «Полный доступ к диску».')
    print_info('  Разрешите доступ вашему терминалу и настольному приложению Korra. Открыть этот раздел командой:')
    print_info("    open \"x-apple.systempreferences:com.apple.preference"
               ".security?Privacy_AllFiles\"")
    print_info('  Разрешение сохраняется после обновления Korra.')


def _blank_slate_minimal_toolsets(config: dict):
    """Write the minimal toolset state for a Blank Slate install.

    Only ``file``, ``terminal``, ``vision``, and ``skills`` are enabled.
    Vision is part of
    the core surface: ``read_file`` cannot read images and its own description
    points at ``vision_analyze``, so an agent without it can't see screenshots
    or image files at all. Skills stay on because the essential
    ``hermes-agent`` skill (the agent's operating manual for driving,
    configuring, and troubleshooting Hermes) is always seeded — without
    ``skill_view`` it would be unloadable. Two layers enforce the selection:

    1. ``platform_toolsets["cli"] = ["file", "skills", "terminal", "vision"]``
       — an explicit list of
       configurable keys, which the resolver treats as authoritative
       (``has_explicit_config``) so default toolsets aren't re-expanded.
    2. ``agent.disabled_toolsets`` — a global hard-suppression list (applied last
       in ``_get_platform_tools``, overriding every other path including the
       non-configurable platform-toolset recovery that would otherwise re-add
       toolsets like ``kanban``). We list every known toolset except the ones we
       keep, guaranteeing a true blank slate regardless of platform/recovery
       quirks. The user re-enables any of them later via ``hermes tools`` (which
       rewrites ``platform_toolsets``) or by editing ``agent.disabled_toolsets``.
    """
    keep = {"file", "terminal", "vision", "skills"}
    config.setdefault("platform_toolsets", {})["cli"] = sorted(keep)

    try:
        from toolsets import TOOLSETS
        from korra_cli.tools_config import CONFIGURABLE_TOOLSETS, _get_plugin_toolset_keys

        all_keys = set()
        all_keys.update(k for k, _, _ in CONFIGURABLE_TOOLSETS)
        all_keys.update(_get_plugin_toolset_keys())
        # Plain (non-composite) TOOLSETS entries — catches recovered toolsets
        # like ``kanban`` that aren't in CONFIGURABLE_TOOLSETS but get re-added.
        for k, tdef in TOOLSETS.items():
            if k.startswith("hermes-"):
                continue  # platform composites — not user-facing toolsets
            if isinstance(tdef, dict) and tdef.get("includes"):
                continue  # composite groupings, not leaf toolsets
            if isinstance(tdef, dict) and tdef.get("posture"):
                continue  # posture toolsets (e.g. coding) are session-level
                # selections made by agent/coding_context.py — not permanent
                # user-facing disables. Adding them here causes model_tools
                # to subtract their tools (terminal, read_file, …) from the
                # minimal Blank Slate surface (#57315).
            all_keys.add(k)

        disabled = sorted(all_keys - keep)
        if disabled:
            config.setdefault("agent", {})["disabled_toolsets"] = disabled
    except Exception as exc:
        logger.debug("blank-slate disabled_toolsets computation skipped: %s", exc)


def _blank_slate_minimize_config(config: dict):
    """Turn OFF the optional config features for a Blank Slate install.

    Everything here is opt-in afterwards via ``hermes setup agent`` /
    ``hermes config set``. We keep only what's needed to run.
    """
    config.setdefault("agent", {})["max_turns"] = 90

    # Compression off — minimal footprint; user opts in if they want long sessions.
    config.setdefault("compression", {})["enabled"] = False

    # No automatic memory / user-profile capture.
    mem = config.setdefault("memory", {})
    mem["memory_enabled"] = False
    mem["user_profile_enabled"] = False

    # No filesystem checkpoints, no smart model routing, no auto session reset.
    config.setdefault("checkpoints", {})["enabled"] = False
    config.setdefault("smart_model_routing", {})["enabled"] = False
    config.setdefault("session_reset", {})["mode"] = "none"

    # Quiet, minimal display.
    config.setdefault("display", {})["tool_progress"] = "all"


def _run_blank_slate_setup(config: dict, hermes_home, is_existing: bool):
    """Blank Slate setup — start with everything off except the bare minimum.

    Forces only the essentials to run an agent (provider + model, the file and
    terminal toolsets) and turns every other tool/skill/plugin/MCP/config
    feature OFF. After applying that minimal baseline, the user chooses one of
    two paths:

      1. Start with everything disabled — finish now with the minimal agent.
      2. Walk through every configuration — opt each capability back in.

    Either way nothing is enabled that the user did not explicitly choose.
    """

    print()
    print_header('Минимальная настройка')
    print_info('По умолчанию всё отключено. Сначала включим только необходимое')
    print_info('для работы агента, затем вы решите, завершить настройку')
    print_info('или подключить дополнительные возможности.')
    print_info("")
    print_info('Обязательно включены: модель, работа с файлами, терминал, анализ изображений и навыки.')
    print_info('Остальное — интернет, браузер, выполнение кода, память,')
    print_info('помощники, расписание, плагины, MCP — пока отключено.')
    print_info('Базовый навык управления Коррой сохраняется,')
    print_info('чтобы агент помогал вам пользоваться системой и настраивать её.')
    print()

    # ── Step 1: Provider & Model (REQUIRED — the agent cannot run without it) ──
    print_header('Шаг 1 — провайдер и модель (обязательно)')
    setup_model_provider(config)
    save_config(config)

    # ── Step 2: Terminal backend (where commands run — a core decision) ──
    print_header('Шаг 2 — среда выполнения команд')
    setup_terminal_backend(config)

    # ── Step 3: Lock in the minimal toolset + minimized config knobs ──
    _blank_slate_minimal_toolsets(config)
    _blank_slate_minimize_config(config)
    save_config(config)
    print()
    print_success('Применён минимальный набор:')
    print_info('  Инструменты: файлы, терминал, изображения, навыки. Остальные отключены.')
    print_info('  Сжатие, память, контрольные точки и выбор модели: отключены.')

    # ── The fork: stop here, or walk through enabling things ──
    print()
    print_header('Продолжить настройку?')
    path = prompt_choice(
        'Минимальный агент готов. Что дальше?',
        [
            'Завершить сейчас с минимальным набором',
            'Пройти все разделы: инструменты, навыки, плагины, MCP',
        ],
        0,
    )

    if path == 0:
        save_config(config)
        # Blank Slate means no bundled skills; record the opt-out so future
        # `hermes update` runs don't re-inject them. Essential skills (the
        # `hermes-agent` operating manual) are still seeded by the sync.
        try:
            from tools.skills_sync import set_bundled_skills_opt_out, sync_skills
            set_bundled_skills_opt_out(True)
            sync_skills(quiet=True)
        except Exception as exc:
            logger.debug("blank-slate skill opt-out error: %s", exc)
        print()
        print_success('Минимальная настройка завершена. Агент готов.')
        print_info('Остальное можно включить позже:')
        print_info('  Инструменты: korra tools')
        print_info('  Навыки: korra skills opt-in --sync')
        print_info('  Серверы MCP: korra mcp add')
        print_info('  Плагины: korra plugins')
        print_info('  Настройки агента: korra setup agent')
        print()
        _print_setup_summary(config, hermes_home)
        return

    # ── Walkthrough path — opt in to each capability ──
    _blank_slate_walkthrough(config, hermes_home)


def _blank_slate_walkthrough(config: dict, hermes_home):
    """Opt-in walkthrough for Blank Slate: skills, tools, plugins, MCP, gateway."""
    from korra_cli.config import load_config

    # ── Bundled skills — default to NONE, offer to seed all ──
    print()
    print_header('Встроенные навыки')
    print_info('При минимальной настройке дополнительные встроенные навыки не устанавливаются.')
    seed_skills = prompt_yes_no(
        'Добавить полный каталог встроенных навыков? (Нет — оставить минимальный набор)',
        default=False,
    )
    try:
        from tools.skills_sync import set_bundled_skills_opt_out, sync_skills
        if seed_skills:
            # Make sure no stale opt-out marker blocks the seed, then sync.
            set_bundled_skills_opt_out(False)
            result = sync_skills(quiet=True)
            copied = len(result.get("copied", [])) if isinstance(result, dict) else 0
            print_success(f'Добавлено встроенных навыков: {copied}.')
        else:
            set_bundled_skills_opt_out(True)
            # Essential skills (the `hermes-agent` operating manual) are
            # still seeded even for an opted-out profile.
            sync_skills(quiet=True)
            print_info('Дополнительные навыки не добавлены. Базовый навык управления Коррой')
            print_info('сохранён. Маркер .no-bundled-skills защищает от установки')
            print_info('навыков при обновлениях `korra update`. Чтобы вернуть полный каталог,')
            print_info('выполните `korra skills opt-in --sync`.')
    except Exception as exc:
        logger.debug("blank-slate skill handling error: %s", exc)
        print_warning(f'Ошибка настройки навыков: {exc}')

    # ── Walk through enabling additional tools ──
    print()
    print_header('Инструменты')
    print_info('Выберите дополнительные инструменты.')
    print_info('Работа с файлами и терминал уже включены. Остальное')
    print_info('можно оставить отключённым.')
    if prompt_yes_no('Открыть выбор дополнительных инструментов?', default=False):
        try:
            from korra_cli.tools_config import tools_command
            tools_command(first_install=False, config=config)
            # tools_command saves via its own load/save cycle — re-sync.
            _refreshed = load_config()
            config.clear()
            config.update(_refreshed)
        except Exception as exc:
            logger.debug("blank-slate tools_command error: %s", exc)
            print_warning(f'Ошибка выбора инструментов: {exc}')
    else:
        print_info('Сохранён минимальный набор инструментов. Добавить инструменты: `korra tools`.')

    # ── Built-in plugins (off unless chosen) ──
    print()
    print_header('Плагины')
    if prompt_yes_no('Выбрать и включить встроенные плагины сейчас?', default=False):
        print_info('Управление плагинами: `korra plugins list` / `korra plugins install`.')
    else:
        print_info('Плагины не включены. Добавить позже: `korra plugins`.')

    # ── MCP servers (off unless chosen) ──
    print()
    print_header('Серверы MCP')
    if prompt_yes_no('Добавить сервер MCP сейчас?', default=False):
        print_info('Добавить сервер: `korra mcp add <имя> --url ... | --command ...`.')
    else:
        print_info('Серверы MCP не настроены. Добавить позже: `korra mcp add`.')

    # ── Optional messaging gateway ──
    print()
    if prompt_yes_no('Подключить мессенджер (Telegram, Discord и т. д.)?', default=False):
        setup_gateway(config)

    save_config(config)

    print()
    print_success('Минимальная настройка завершена. Агент готов.')
    print_info('  Подключить инструменты: korra tools')
    print_info('  Добавить навыки: korra skills opt-in --sync')
    print_info('  Добавить сервер MCP: korra mcp add')
    print_info('  Настроить агента: korra setup agent')
    print()

    _print_setup_summary(config, hermes_home)


def _run_quick_setup(config: dict, hermes_home):
    """Quick setup — only configure items that are missing."""
    from korra_cli.config import (
        get_missing_env_vars,
        get_missing_config_fields,
        check_config_version,
    )

    print()
    print_header('Быстрая настройка — только недостающие параметры')

    # Check what's missing
    missing_required = [
        v for v in get_missing_env_vars(required_only=False) if v.get("is_required")
    ]
    missing_optional = [
        v for v in get_missing_env_vars(required_only=False) if not v.get("is_required")
    ]
    missing_config = get_missing_config_fields()
    current_ver, latest_ver = check_config_version()

    has_anything_missing = (
        missing_required
        or missing_optional
        or missing_config
        or current_ver < latest_ver
    )

    if not has_anything_missing:
        print_success('Всё настроено. Дополнительных действий не требуется.')
        print()
        print_info('Чтобы настроить заново, запустите `korra setup` и выберите полную настройку')
        print_info('или отдельный раздел меню.')
        return

    # Handle missing required env vars
    if missing_required:
        print()
        print_info(f'Не заполнено обязательных настроек: {len(missing_required)}.')
        for var in missing_required:
            print(f"     • {var['name']}")
        print()

        for var in missing_required:
            print()
            print(color(f"  {var['name']}", Colors.CYAN))
            print_info(f"  {var.get('description', '')}")
            if var.get("url"):
                print_info(f"  Получить ключ: {var['url']}")

            if var.get("password"):
                value = prompt(f"  {var.get('prompt', var['name'])}", password=True)
            else:
                value = prompt(f"  {var.get('prompt', var['name'])}")

            if value:
                save_env_value(var["name"], value)
                print_success(f"  Сохранено: {var['name']}")
            else:
                print_warning(f"  Пропущено: {var['name']}")

    # Split missing optional vars by category
    missing_tools = [v for v in missing_optional if v.get("category") == "tool"]
    missing_messaging = [
        v
        for v in missing_optional
        if v.get("category") == "messaging" and not v.get("advanced")
    ]

    # ── Tool API keys (checklist) ──
    if missing_tools:
        print()
        print_header('Ключи API инструментов')

        checklist_labels = []
        for var in missing_tools:
            tools = var.get("tools", [])
            tools_str = f" → {', '.join(tools[:2])}" if tools else ""
            checklist_labels.append(f"{var.get('description', var['name'])}{tools_str}")

        selected_indices = prompt_checklist(
            'Какие инструменты настроить?',
            checklist_labels,
        )

        for idx in selected_indices:
            var = missing_tools[idx]
            _prompt_api_key(var)

    # ── Messaging platforms (checklist then prompt for selected) ──
    if missing_messaging:
        print()
        print_header('Мессенджеры')
        print_info('Подключите мессенджеры, чтобы общаться с Коррой откуда угодно.')
        print_info('Можно настроить позже: `korra setup gateway`.')

        # Group by platform (preserving order)
        platform_order = []
        platforms = {}
        for var in missing_messaging:
            name = var["name"]
            if "TELEGRAM" in name:
                plat = "Telegram"
            elif "DISCORD" in name:
                plat = "Discord"
            elif "SLACK" in name:
                plat = "Slack"
            else:
                continue
            if plat not in platforms:
                platform_order.append(plat)
            platforms.setdefault(plat, []).append(var)

        platform_labels = [
            {
                "Telegram": "📱 Telegram",
                "Discord": "💬 Discord",
                "Slack": "💼 Slack",
            }.get(p, p)
            for p in platform_order
        ]

        selected_indices = prompt_checklist(
            'Какие платформы подключить?',
            platform_labels,
        )

        for idx in selected_indices:
            plat = platform_order[idx]
            vars_list = platforms[plat]
            emoji = {"Telegram": "📱", "Discord": "💬", "Slack": "💼"}.get(plat, "")
            print()
            print(color(f"  ─── {emoji} {plat} ───", Colors.CYAN))
            print()
            for var in vars_list:
                print_info(f"  {var.get('description', '')}")
                if var.get("url"):
                    print_info(f"  {var['url']}")
                if var.get("password"):
                    value = prompt(f"  {var.get('prompt', var['name'])}", password=True)
                else:
                    value = prompt(f"  {var.get('prompt', var['name'])}")
                if value:
                    save_env_value(var["name"], value)
                    print_success('  ✓ Сохранено')
                else:
                    print_warning('  Пропущено')
                print()

    # Handle missing config fields
    if missing_config:
        print()
        print_info(
            f'Добавляю новые параметры со значениями по умолчанию: {len(missing_config)}…'
        )
        for field in missing_config:
            print_success(f"  Добавлено: {field['key']} = {field['default']}")

        # Update config version
        config["_config_version"] = latest_ver
        save_config(config)

    # Jump to summary
    _print_setup_summary(config, hermes_home)

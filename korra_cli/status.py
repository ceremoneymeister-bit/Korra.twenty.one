"""
Status command for hermes CLI.

Shows the status of all Hermes Agent components.
"""

import os
import sys
import time
import importlib.util
import subprocess  # noqa: F401 — re-exported for tests that monkeypatch status.subprocess to guard against regressions
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent.resolve()

from korra_cli.auth import AuthError, resolve_provider
from korra_cli.colors import Colors, color
from korra_cli.config import get_env_path, get_env_value, get_hermes_home, load_config
from korra_cli.models import provider_label
from korra_cli.nous_account import (
    format_nous_portal_entitlement_message,
    get_nous_portal_account_info,
)
from korra_cli.nous_subscription import get_nous_subscription_features
from korra_cli.runtime_provider import resolve_requested_provider
from korra_cli.vercel_auth import describe_vercel_auth
from korra_constants import OPENROUTER_MODELS_URL
from tools.tool_backend_helpers import managed_nous_tools_enabled

def check_mark(ok: bool) -> str:
    if ok:
        return color("✓", Colors.GREEN)
    return color("✗", Colors.RED)

def redact_key(key: str) -> str:
    """Redact an API key for display.

    Thin wrapper over :func:`agent.redact.mask_secret`. Preserves the
    "(not set)" placeholder in dim color to match ``hermes config``'s
    output (previously this variant was missing the DIM color —
    consolidated via PR that also introduced ``mask_secret``).
    """
    from agent.redact import mask_secret
    return mask_secret(key, empty=color('(не задано)', Colors.DIM))


def _format_iso_timestamp(value) -> str:
    """Format ISO timestamps for status output, converting to local timezone."""
    if not value or not isinstance(value, str):
        return "(неизвестно)"
    from datetime import datetime, timezone
    text = value.strip()
    if not text:
        return "(неизвестно)"
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
    except Exception:
        return value
    return parsed.astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")


def _format_relative_ts(ts: float) -> str:
    """Format an epoch timestamp as a short relative age for status output."""
    from korra_cli.timefmt import relative_time

    return relative_time(ts)


def _configured_model_label(config: dict) -> str:
    """Return the configured default model from config.yaml."""
    model_cfg = config.get("model")
    if isinstance(model_cfg, dict):
        model = (model_cfg.get("default") or model_cfg.get("name") or "").strip()
    elif isinstance(model_cfg, str):
        model = model_cfg.strip()
    else:
        model = ""
    return model or '(не задано)'


def _effective_provider_label() -> str:
    """Return the provider label matching current CLI runtime resolution."""
    requested = resolve_requested_provider()
    try:
        effective = resolve_provider(requested)
    except AuthError:
        effective = requested or "auto"

    if effective == "openrouter":
        # A custom endpoint may be configured either in config.yaml
        # (model.base_url — the canonical location; the runtime treats
        # config.yaml as the single source of truth) or via the legacy
        # OPENAI_BASE_URL env var. Either way, labeling it "OpenRouter"
        # is misleading (#3296).
        config_base_url = ""
        try:
            model_cfg = load_config().get("model")
            if isinstance(model_cfg, dict):
                config_base_url = (model_cfg.get("base_url") or "").strip()
        except Exception:
            pass
        if config_base_url or get_env_value("OPENAI_BASE_URL"):
            effective = "custom"

    return provider_label(effective)


from korra_constants import is_termux as _is_termux


def _estop_status_line():
    """One-line pause banner for `hermes status`, or None when not paused.

    Cheap: a single stat on $HERMES_HOME/ESTOP via agent.estop.
    """
    try:
        from agent.estop import get_state
    except ImportError:
        return None
    state = get_state()
    if state is None:
        return None
    reason = state.get("reason")
    suffix = f' — причина: {reason}' if reason else ""
    return f'⏸️  ПАУЗА (экстренная остановка{suffix}; для продолжения — `korra resume`)'


def show_status(args):
    """Show status of all Hermes Agent components."""
    deep = getattr(args, 'deep', False)

    print()
    print(color("┌─────────────────────────────────────────────────────────┐", Colors.CYAN))
    print(color('│                 ⚕ Состояние Корры                      │', Colors.CYAN))
    print(color("└─────────────────────────────────────────────────────────┘", Colors.CYAN))

    _paused_line = _estop_status_line()
    if _paused_line:
        print()
        print(color(_paused_line, Colors.YELLOW, Colors.BOLD))

    # =========================================================================
    # Environment
    # =========================================================================
    print()
    print(color('◆ Окружение', Colors.CYAN, Colors.BOLD))
    print(f'  Проект:       {PROJECT_ROOT}')
    print(f"  Python:       {sys.version.split()[0]}")

    env_path = get_env_path()
    print(f"  Файл .env:    {check_mark(env_path.exists())} {('найден' if env_path.exists() else 'не найден')}")

    try:
        config = load_config()
    except Exception:
        config = {}

    print(f'  Модель:       {_configured_model_label(config)}')
    print(f'  Провайдер:    {_effective_provider_label()}')

    # =========================================================================
    # API Keys
    # =========================================================================
    print()
    print(color('◆ API-ключи', Colors.CYAN, Colors.BOLD))

    # Values may be a single env var name (str) or a tuple of alternates (first found wins).
    keys: dict[str, str | tuple[str, ...]] = {
        "OpenRouter": "OPENROUTER_API_KEY",
        "OpenAI": "OPENAI_API_KEY",
        "Anthropic": ("ANTHROPIC_API_KEY", "ANTHROPIC_TOKEN"),
        "Google / Gemini": ("GOOGLE_API_KEY", "GEMINI_API_KEY"),
        "DeepSeek": "DEEPSEEK_API_KEY",
        "xAI / Grok": "XAI_API_KEY",
        "NVIDIA NIM": "NVIDIA_API_KEY",
        "Z.AI / GLM": "GLM_API_KEY",
        "Kimi": "KIMI_API_KEY",
        "StepFun Step Plan": "STEPFUN_API_KEY",
        "MiniMax": "MINIMAX_API_KEY",
        "MiniMax-CN": "MINIMAX_CN_API_KEY",
        "DeepInfra": "DEEPINFRA_API_KEY",
        "Firecrawl": "FIRECRAWL_API_KEY",
        "Keenable": "KEENABLE_API_KEY",
        "Browser Use": "BROWSER_USE_API_KEY",  # Optional — local browser works without this
        "Browserbase": "BROWSERBASE_API_KEY",  # Optional — direct credentials only
        "FAL": "FAL_KEY",
        "ElevenLabs": "ELEVENLABS_API_KEY",
        "GitHub": "GITHUB_TOKEN",
    }

    def _resolve_env(env_ref) -> str:
        """Return first non-empty env var value from a str or tuple of names."""
        if isinstance(env_ref, tuple):
            for candidate in env_ref:
                v = get_env_value(candidate) or ""
                if v:
                    return v
            return ""
        return get_env_value(env_ref) or ""

    for name, env_ref in keys.items():
        # Anthropic already has a dedicated lookup below; keep that as the
        # single source of truth (it also resolves OAuth tokens), skip here
        # so we don't print two "Anthropic" rows.
        if name == "Anthropic":
            continue
        value = _resolve_env(env_ref)
        has_key = bool(value)
        display = redact_key(value)
        print(f"  {name:<12}  {check_mark(has_key)} {display}")

    from korra_cli.auth import get_anthropic_key
    anthropic_value = get_anthropic_key()
    anthropic_display = redact_key(anthropic_value)
    print(f"  {'Anthropic':<12}  {check_mark(bool(anthropic_value))} {anthropic_display}")

    # =========================================================================
    # Auth Providers (OAuth)
    # =========================================================================
    print()
    print(color('◆ Учётные записи провайдеров', Colors.CYAN, Colors.BOLD))

    try:
        from korra_cli.auth import (
            get_nous_auth_status_local,
            get_codex_auth_status,
            get_qwen_auth_status,
            get_minimax_oauth_auth_status,
        )
        # Read-only display: use the refresh-free snapshot so `hermes status`
        # never performs an OAuth refresh or burns a single-use refresh token.
        nous_status = get_nous_auth_status_local()
        codex_status = get_codex_auth_status()
        qwen_status = get_qwen_auth_status()
        minimax_status = get_minimax_oauth_auth_status()
    except Exception:
        nous_status = {}
        codex_status = {}
        qwen_status = {}
        minimax_status = {}

    nous_account_info = None
    if (
        nous_status.get("logged_in")
        or nous_status.get("access_token")
        or nous_status.get("portal_base_url")
        or nous_status.get("inference_credential_present")
        or nous_status.get("error_code")
    ):
        try:
            nous_account_info = get_nous_portal_account_info()
        except Exception:
            nous_account_info = None

    nous_logged_in = bool(
        nous_status.get("logged_in")
        or (nous_account_info and nous_account_info.logged_in)
    )
    nous_inference_present = bool(
        nous_status.get("inference_credential_present")
        or (nous_account_info and nous_account_info.inference_credential_present)
    )
    nous_error = nous_status.get("error")
    if nous_logged_in:
        nous_label = 'вход выполнен'
    elif nous_inference_present:
        nous_label = 'вход не выполнен (ключ модели Nous настроен)'
    else:
        nous_label = 'вход не выполнен (`korra portal`)'
    print(
        f"  {'Nous Portal':<12}  {check_mark(nous_logged_in)} "
        f"{nous_label}"
    )
    portal_url = nous_status.get("portal_base_url") or "(unknown)"
    inference_url = (
        nous_status.get("inference_base_url")
        or (nous_account_info.inference_base_url if nous_account_info else None)
    )
    access_exp = _format_iso_timestamp(nous_status.get("access_expires_at"))
    key_exp = _format_iso_timestamp(nous_status.get("agent_key_expires_at"))
    refresh_label = 'да' if nous_status.get("has_refresh_token") else 'нет'
    if nous_logged_in or portal_url != "(unknown)" or nous_error:
        print(f'    Адрес Nous: {portal_url}')
    if nous_inference_present and inference_url:
        print(f'    Адрес модели: {inference_url}')
    if nous_logged_in or nous_status.get("access_expires_at"):
        print(f'    Срок доступа: {access_exp}')
    if nous_logged_in or nous_inference_present or nous_status.get("agent_key_expires_at"):
        print(f'    Срок ключа:   {key_exp}')
    if nous_logged_in or nous_status.get("has_refresh_token"):
        print(f'    Обновление входа: {refresh_label}')
    if nous_error:
        print(f'    Ошибка:     {nous_error}')

    codex_logged_in = bool(codex_status.get("logged_in"))
    print(
        f"  {'OpenAI Codex':<12}  {check_mark(codex_logged_in)} {('вход выполнен' if codex_logged_in else 'вход не выполнен (`korra model`)')}"
    )
    codex_auth_file = codex_status.get("auth_store")
    if codex_auth_file:
        print(f'    Файл входа: {codex_auth_file}')
    codex_last_refresh = _format_iso_timestamp(codex_status.get("last_refresh"))
    if codex_status.get("last_refresh"):
        print(f'    Обновлено:  {codex_last_refresh}')
    if codex_status.get("error") and not codex_logged_in:
        print(f"    Ошибка:     {codex_status.get('error')}")

    qwen_logged_in = bool(qwen_status.get("logged_in"))
    print(
        f"  {'Qwen OAuth':<12}  {check_mark(qwen_logged_in)} {('вход выполнен' if qwen_logged_in else 'вход не выполнен (`korra auth add qwen-oauth`)')}"
    )
    qwen_auth_file = qwen_status.get("auth_file")
    if qwen_auth_file:
        print(f'    Файл входа: {qwen_auth_file}')
    qwen_exp = qwen_status.get("expires_at_ms")
    if qwen_exp:
        from datetime import datetime, timezone
        print(f'    Срок доступа: {datetime.fromtimestamp(int(qwen_exp) / 1000, tz=timezone.utc).isoformat()}')
    if qwen_status.get("error") and not qwen_logged_in:
        print(f"    Ошибка:     {qwen_status.get('error')}")

    minimax_logged_in = bool(minimax_status.get("logged_in"))
    print(
        f"  {'MiniMax OAuth':<12}  {check_mark(minimax_logged_in)} {('вход выполнен' if minimax_logged_in else 'вход не выполнен (`korra auth add minimax-oauth`)')}"
    )
    minimax_region = minimax_status.get("region")
    if minimax_logged_in and minimax_region:
        print(f'    Регион:     {minimax_region}')
    minimax_exp = minimax_status.get("expires_at")
    if minimax_exp:
        print(f'    Срок доступа: {minimax_exp}')
    if minimax_status.get("error") and not minimax_logged_in:
        print(f"    Ошибка:     {minimax_status.get('error')}")

    # xAI OAuth — separate try/except so an import failure here cannot
    # disrupt the already-printed Nous/Codex/Qwen/MiniMax rows above.
    try:
        from korra_cli.auth import get_xai_oauth_auth_status
        xai_oauth_status = get_xai_oauth_auth_status() or {}
    except Exception:
        xai_oauth_status = {}

    xai_oauth_logged_in = bool(xai_oauth_status.get("logged_in"))
    print(
        f"  {'xAI OAuth':<12}  {check_mark(xai_oauth_logged_in)} {('вход выполнен' if xai_oauth_logged_in else 'вход не выполнен (`korra auth add xai-oauth`)')}"
    )
    xai_auth_file = xai_oauth_status.get("auth_store")
    if xai_auth_file:
        print(f'    Файл входа: {xai_auth_file}')
    if xai_oauth_status.get("last_refresh"):
        print(f"    Обновлено:  {_format_iso_timestamp(xai_oauth_status.get('last_refresh'))}")
    if xai_oauth_status.get("error") and not xai_oauth_logged_in:
        print(f"    Ошибка:     {xai_oauth_status.get('error')}")

    # =========================================================================
    # Nous Subscription Features
    # =========================================================================
    if managed_nous_tools_enabled():
        features = get_nous_subscription_features(config)
        print()
        print(color('◆ Инструменты по подписке Nous', Colors.CYAN, Colors.BOLD))
        if not features.nous_auth_present:
            print('  Nous Portal   ✗ вход не выполнен')
        else:
            print('  Nous Portal   ✓ инструменты доступны')
        for feature in features.items():
            if feature.managed_by_nous:
                state = 'работает по подписке Nous'
            elif feature.active:
                current = feature.current_provider or 'настроенный провайдер'
                state = f'работает через {current}'
            elif feature.included_by_default and features.nous_auth_present:
                state = 'входит в подписку, сейчас не выбран'
            elif feature.key == "modal" and features.nous_auth_present:
                state = 'доступно по подписке (по выбору)'
            else:
                state = 'не настроено'
            print(f"  {feature.label:<15} {check_mark(feature.available or feature.active or feature.managed_by_nous)} {state}")
    elif nous_logged_in or nous_inference_present:
        # Nous OAuth without entitlement, or an opaque inference key without
        # Portal account information, cannot enable the Tool Gateway.
        print()
        print(color('◆ Инструменты по подписке Nous', Colors.CYAN, Colors.BOLD))
        message = format_nous_portal_entitlement_message(
            nous_account_info,
            capability='поиск, изображения, озвучивание, распознавание речи, браузер и Modal',
        )
        if message:
            for line in message.splitlines():
                print(f"  {line}")

    # =========================================================================
    # API-Key Providers
    # =========================================================================
    print()
    print(color('◆ Провайдеры с API-ключом', Colors.CYAN, Colors.BOLD))

    apikey_providers = {
        "Z.AI / GLM":       ("GLM_API_KEY", "ZAI_API_KEY", "Z_AI_API_KEY"),
        "Kimi / Moonshot":  ("KIMI_API_KEY",),
        "StepFun Step Plan": ("STEPFUN_API_KEY",),
        "MiniMax":          ("MINIMAX_API_KEY",),
        "MiniMax (China)":  ("MINIMAX_CN_API_KEY",),
        "DeepInfra":        ("DEEPINFRA_API_KEY",),
    }
    for pname, env_vars in apikey_providers.items():
        key_val = ""
        for ev in env_vars:
            key_val = get_env_value(ev) or ""
            if key_val:
                break
        configured = bool(key_val)
        label = 'настроено' if configured else 'не настроено (`korra model`)'
        print(f"  {pname:<16} {check_mark(configured)} {label}")

    # LM Studio reachability — only probe when it's the active provider so
    # users with foreign configs don't see noise. Auth rejection vs. silent
    # empty list is the most common LM Studio support case.
    if _effective_provider_label() == "LM Studio":
        from korra_cli.models import probe_lmstudio_models
        model_cfg = config.get("model")
        base = (model_cfg.get("base_url") if isinstance(model_cfg, dict) else None) or get_env_value("LM_BASE_URL") or "http://127.0.0.1:1234/v1"
        try:
            models = probe_lmstudio_models(api_key=get_env_value("LM_API_KEY") or "", base_url=base, timeout=1.5)
            if models is None:
                ok, msg = False, f'недоступно по адресу {base}'
            else:
                ok, msg = True, f'доступно (моделей: {len(models)}), адрес: {base}'
        except AuthError:
            ok, msg = False, 'вход отклонён — укажите LM_API_KEY'
        print(f"  {'LM Studio':<16} {check_mark(ok)} {msg}")

    # =========================================================================
    # Terminal Configuration
    # =========================================================================
    print()
    print(color('◆ Среда терминала', Colors.CYAN, Colors.BOLD))

    terminal_cfg = config.get("terminal", {}) if isinstance(config.get("terminal"), dict) else {}
    terminal_env = os.getenv("TERMINAL_ENV", "")
    if not terminal_env:
        terminal_env = terminal_cfg.get("backend", "local")
    print(f'  Среда:        {terminal_env}')

    if terminal_env == "ssh":
        ssh_host = os.getenv("TERMINAL_SSH_HOST", "")
        ssh_user = os.getenv("TERMINAL_SSH_USER", "")
        print(f"  Сервер SSH:   {ssh_host or '(не задано)'}")
        print(f"  Пользователь SSH: {ssh_user or '(не задано)'}")
    elif terminal_env == "docker":
        docker_image = os.getenv("TERMINAL_DOCKER_IMAGE", "python:3.11-slim")
        print(f'  Образ Docker: {docker_image}')
    elif terminal_env == "daytona":
        daytona_image = os.getenv("TERMINAL_DAYTONA_IMAGE", "nikolaik/python-nodejs:python3.11-nodejs20")
        print(f'  Образ Daytona: {daytona_image}')
    elif terminal_env == "vercel_sandbox":
        runtime = os.getenv("TERMINAL_VERCEL_RUNTIME") or terminal_cfg.get("vercel_runtime") or "node24"
        persist = os.getenv("TERMINAL_CONTAINER_PERSISTENT")
        if persist is None:
            persist_enabled = bool(terminal_cfg.get("container_persistent", True))
        else:
            persist_enabled = persist.lower() in {"1", "true", "yes", "on"}
        auth_status = describe_vercel_auth()
        sdk_ok = importlib.util.find_spec("vercel") is not None
        sdk_label = 'установлено' if sdk_ok else "не установлен (установите: pip install 'hermes-agent[vercel]')"
        print(f'  Среда запуска: {runtime}')
        print(f"  SDK:          {check_mark(sdk_ok)} {sdk_label}")
        print(f'  Вход:         {check_mark(auth_status.ok)} {auth_status.display_label or auth_status.label}')
        for line in auth_status.detail_lines:
            print(f'  Подробности входа: {line}')
        print(f"  Хранение:     {('снимок файлов' if persist_enabled else 'временные файлы')}")
        print('  Процессы:     после очистки, снимка или пересоздания среды процессы не сохраняются')
    else:
        # Plugin-registered terminal backends: show availability via the
        # provider's doctor rows (fail-soft — never break `hermes status`).
        try:
            from korra_cli.plugins import discover_plugins

            discover_plugins()
            from agent.terminal_env_registry import get_provider

            _provider = get_provider(terminal_env)
            if _provider is not None:
                for _ok, _label, _detail in _provider.doctor_checks():
                    print(f"  {_label}: {check_mark(bool(_ok))} {_detail}")
        except Exception:
            pass

    sudo_password = os.getenv("SUDO_PASSWORD", "")
    print(f"  Права sudo:   {check_mark(bool(sudo_password))} {('включено' if sudo_password else 'выключено')}")

    # =========================================================================
    # Messaging Platforms
    # =========================================================================
    print()
    print(color('◆ Мессенджеры', Colors.CYAN, Colors.BOLD))

    platforms = {
        "Telegram": ("TELEGRAM_BOT_TOKEN", "TELEGRAM_HOME_CHANNEL"),
        "Discord": ("DISCORD_BOT_TOKEN", "DISCORD_HOME_CHANNEL"),
        "WhatsApp": ("WHATSAPP_ENABLED", None),
        "Signal": ("SIGNAL_HTTP_URL", "SIGNAL_HOME_CHANNEL"),
        "Slack": ("SLACK_BOT_TOKEN", None),
        "Email": ("EMAIL_ADDRESS", "EMAIL_HOME_ADDRESS"),
        "SMS": ("TWILIO_ACCOUNT_SID", "SMS_HOME_CHANNEL"),
        "DingTalk": ("DINGTALK_CLIENT_ID", None),
        "Feishu": ("FEISHU_APP_ID", "FEISHU_HOME_CHANNEL"),
        "WeCom": ("WECOM_BOT_ID", "WECOM_HOME_CHANNEL"),
        "WeCom Callback": ("WECOM_CALLBACK_CORP_ID", None),
        "Weixin": ("WEIXIN_ACCOUNT_ID", "WEIXIN_HOME_CHANNEL"),
        "BlueBubbles": ("BLUEBUBBLES_SERVER_URL", "BLUEBUBBLES_HOME_CHANNEL"),
        "QQBot": ("QQ_APP_ID", "QQ_HOME_CHANNEL"),
        "Yuanbao": ("YUANBAO_APP_ID", "YUANBAO_HOME_CHANNEL"),
    }

    for name, (token_var, home_var) in platforms.items():
        token = os.getenv(token_var, "")
        has_token = bool(token)
        
        home_channel = ""
        if home_var:
            home_channel = os.getenv(home_var, "")
        # Back-compat: QQBot home channel was renamed from QQ_HOME_CHANNEL to QQBOT_HOME_CHANNEL
        if not home_channel and home_var == "QQBOT_HOME_CHANNEL":
            home_channel = os.getenv("QQ_HOME_CHANNEL", "")
        
        status = 'настроено' if has_token else 'не настроено'
        if home_channel:
            status += f' (основной канал: {home_channel})'
        
        print(f"  {name:<12}  {check_mark(has_token)} {status}")

    # Plugin-registered platforms
    try:
        from gateway.platform_registry import platform_registry
        for entry in platform_registry.plugin_entries():
            # Per-entry guard: one raising probe must not abort the listing
            # of every remaining plugin platform (matches the other three
            # check_fn call sites).
            try:
                configured = bool(entry.check_fn())
            except Exception:
                configured = False
            status_str = 'настроено' if configured else 'не настроено'
            label = entry.label
            print(f'  {label:<12}  {check_mark(configured)} {status_str} (плагин)')
    except Exception:
        pass

    # =========================================================================
    # Gateway Status
    # =========================================================================
    print()
    print(color('◆ Шлюз мессенджеров', Colors.CYAN, Colors.BOLD))

    try:
        from korra_cli.gateway import get_gateway_runtime_snapshot, _format_gateway_pids

        snapshot = get_gateway_runtime_snapshot()
        is_running = snapshot.running
        print(f"  Состояние:    {check_mark(is_running)} {('работает' if is_running else 'остановлен')}")
        manager_label = {
            "Termux / manual process": "Termux / ручной запуск",
            "manual process": "ручной запуск",
            "systemd (user)": "systemd (пользователь)",
            "systemd (system)": "systemd (система)",
        }.get(snapshot.manager, snapshot.manager)
        print(f"  Управление:   {manager_label}")
        if snapshot.gateway_pids:
            print(f'  PID:          {_format_gateway_pids(snapshot.gateway_pids)}')
        if snapshot.has_process_service_mismatch:
            print('  Служба:       установлена, но текущий шлюз запущен отдельно')
        elif _is_termux() and not snapshot.gateway_pids:
            print('  Запуск:       korra gateway')
            print('  Примечание:   Android может остановить фоновые задачи при приостановке Termux')
        elif snapshot.service_installed and not snapshot.service_running:
            print('  Служба:       установлена, но остановлена')
    except Exception:
        if _is_termux():
            print(f"  Состояние:    {color('unknown', Colors.DIM)}")
            print('  Управление:   Termux / ручной запуск')
        elif sys.platform.startswith('linux'):
            print(f"  Состояние:    {color('unknown', Colors.DIM)}")
            print('  Управление:   systemd/manual')
        elif sys.platform == 'darwin':
            print(f"  Состояние:    {color('unknown', Colors.DIM)}")
            print('  Управление:   launchd')
        else:
            print(f"  Состояние:    {color('N/A', Colors.DIM)}")
            print('  Управление:   (не поддерживается в этой системе)')

    # =========================================================================
    # Cron Jobs
    # =========================================================================
    print()
    print(color('◆ Задачи по расписанию', Colors.CYAN, Colors.BOLD))

    jobs_file = get_hermes_home() / "cron" / "jobs.json"
    if jobs_file.exists():
        import json
        try:
            # utf-8-sig: same dialect as cron/jobs.load_jobs — Windows editors
            # may leave a UTF-8 BOM that plain utf-8 json.load rejects.
            with open(jobs_file, encoding="utf-8-sig") as f:
                data = json.load(f)
                jobs = data.get("jobs", [])
                enabled_jobs = [j for j in jobs if j.get("enabled", True)]
                print(f'  Задачи:       {len(enabled_jobs)} активных, {len(jobs)} всего')
        except Exception:
            print('  Задачи:       (не удалось прочитать файл задач)')
    else:
        print('  Задачи:       0')

    # =========================================================================
    # Sessions
    # =========================================================================
    print()
    print(color('◆ Беседы', Colors.CYAN, Colors.BOLD))

    # Gateway session count: state.db is the source of truth (#9006);
    # fall back to sessions.json for pre-migration installs.
    _session_count = None
    _gateway_rows = []
    try:
        from korra_state import SessionDB
        _db = SessionDB()
        try:
            _lister = getattr(_db, "list_gateway_sessions", None)
            if callable(_lister):
                _gateway_rows = _lister(active_only=True) or []
                _session_count = len(_gateway_rows)
        finally:
            _db.close()
    except Exception:
        _session_count = None
        _gateway_rows = []

    if _session_count is not None and _session_count > 0:
        print(f'  Активных:     {_session_count} бесед')
        freshest = max(
            (float(r.get("last_active") or 0) for r in _gateway_rows),
            default=0.0,
        )
        if freshest > 0:
            print(f'  Последняя активность: {_format_relative_ts(freshest):>13}')
    else:
        sessions_file = get_hermes_home() / "sessions" / "sessions.json"
        if sessions_file.exists():
            import json
            try:
                with open(sessions_file, encoding="utf-8") as f:
                    data = json.load(f)
                    _entries = {
                        k: v for k, v in data.items()
                        if not str(k).startswith("_")
                    } if isinstance(data, dict) else {}
                    print(f'  Активных:     {len(_entries)} бесед')
            except Exception:
                print('  Активных:     (не удалось прочитать файл бесед)')
        else:
            print(f'  Активных:     {(_session_count if _session_count is not None else 0)}')

    # Slot usage, only when max_concurrent_sessions is set. The cap is shared
    # across CLI, desktop/TUI and the messaging gateway, so the surface that
    # gets rejected is rarely the one holding the slots — without this the only
    # way to find out is reading runtime/active_sessions.json by hand.
    try:
        from korra_cli.active_sessions import (
            active_session_registry_snapshot,
            format_age,
            resolve_max_concurrent_sessions,
        )

        _cap = resolve_max_concurrent_sessions(config)
    except Exception:
        _cap = None
    if _cap:
        try:
            _held = active_session_registry_snapshot()
        except Exception:
            _held = []
        _full = len(_held) >= _cap
        print(
            '  Места:        '
            + color(
                f'{len(_held)}/{_cap} занято', Colors.YELLOW if _full else Colors.GREEN
            )
        )
        _now = time.time()
        for _entry in sorted(_held, key=lambda e: e.get("started_at") or 0):
            _age = format_age(_now - float(_entry.get("started_at") or _now))
            print(
                f"                {_entry.get('surface') or 'unknown':<17} "
                f"{_entry.get('session_id') or '?':<24} {_age}"
            )

    # =========================================================================
    # Deep checks
    # =========================================================================
    if deep:
        print()
        print(color('◆ Подробная проверка', Colors.CYAN, Colors.BOLD))
        
        # Check OpenRouter connectivity
        openrouter_key = os.getenv("OPENROUTER_API_KEY", "")
        if openrouter_key:
            try:
                import httpx
                response = httpx.get(
                    OPENROUTER_MODELS_URL,
                    headers={"Authorization": f"Bearer {openrouter_key}"},
                    timeout=10
                )
                ok = response.status_code == 200
                print(f"  OpenRouter:   {check_mark(ok)} {('доступно' if ok else f'ошибка ({response.status_code})')}")
            except Exception as e:
                print(f'  OpenRouter:   {check_mark(False)} ошибка: {e}')
        
        # Check gateway port
        try:
            import socket
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(1)
            result = sock.connect_ex(('127.0.0.1', 18789))
            sock.close()
            # Port in use = gateway likely running
            port_in_use = result == 0
            # This is informational, not necessarily bad
            print(f"  Порт 18789:   {('занят' if port_in_use else 'свободен')}")
        except OSError:
            pass

    print()
    print(color("─" * 60, Colors.DIM))
    print(color('  Подробная диагностика: korra doctor', Colors.DIM))
    print(color('  Настройка: korra setup', Colors.DIM))
    print()

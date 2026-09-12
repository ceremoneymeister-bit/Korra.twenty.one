"""
Doctor command for hermes CLI.

Diagnoses issues with Hermes Agent setup.
"""

import os
import sys
import subprocess
import shutil
import importlib.util
from pathlib import Path

from korra_cli.config import (
    detect_install_method,
    get_env_path,
    get_hermes_home,
    get_project_root,
    is_nix_install_method,
    recommended_update_command_for_method,
)
from korra_cli.env_loader import load_hermes_dotenv
from korra_constants import display_hermes_home, korra_env, korra_env_setdefault
from korra_constants import agent_browser_runnable

PROJECT_ROOT = get_project_root()
HERMES_HOME = get_hermes_home()
_DHH = display_hermes_home()  # user-facing display path (e.g. ~/.hermes or ~/.hermes/profiles/coder)

# Load environment variables from ~/.hermes/.env so API key checks work
_env_path = get_env_path()
load_hermes_dotenv(hermes_home=_env_path.parent, project_env=PROJECT_ROOT / ".env")

from korra_cli.colors import Colors, color
from korra_cli.models import _HERMES_USER_AGENT
from korra_cli.vercel_auth import describe_vercel_auth
from korra_constants import OPENROUTER_MODELS_URL
from utils import base_url_host_matches


_PROVIDER_ENV_HINTS = (
    "DEEPINFRA_API_KEY",
    "OPENROUTER_API_KEY",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_TOKEN",
    "OPENAI_BASE_URL",
    "NOUS_API_KEY",
    "GLM_API_KEY",
    "ZAI_API_KEY",
    "Z_AI_API_KEY",
    "KIMI_API_KEY",
    "KIMI_CN_API_KEY",
    "GMI_API_KEY",
    "FIREWORKS_API_KEY",
    "ACTUAL_API_KEY",
    "ACTUAL_BASE_URL",
    "MINIMAX_API_KEY",
    "MINIMAX_CN_API_KEY",
    "KILOCODE_API_KEY",
    "DEEPSEEK_API_KEY",
    "DASHSCOPE_API_KEY",
    "HF_TOKEN",
    "AI_GATEWAY_API_KEY",
    "OPENCODE_ZEN_API_KEY",
    "OPENCODE_GO_API_KEY",
    "COMMANDCODE_API_KEY",
    "XIAOMI_API_KEY",
    "TOKENHUB_API_KEY",
    "TOKENPLAN_API_KEY",
)


from korra_constants import is_termux as _is_termux


def _python_install_cmd() -> str:
    return "python -m pip install" if _is_termux() else "uv pip install"


def _system_package_install_cmd(pkg: str) -> str:
    if _is_termux():
        return f"pkg install {pkg}"
    if sys.platform == "darwin":
        return f"brew install {pkg}"
    return f"sudo apt install {pkg}"


def _sqlite_upgrade_hint(install_method: str | None = None) -> str:
    """Return an actionable SQLite upgrade hint for this install layout."""
    method = install_method or detect_install_method(PROJECT_ROOT)
    if method == "docker":
        command = recommended_update_command_for_method(method)
        action = f'выполните `{command}`, затем пересоздайте все контейнеры Корры'
    elif is_nix_install_method(method):
        # The Nix helper is prose guidance, not a literal shell command.
        action = recommended_update_command_for_method(method)
    elif method == "apt":
        action = f'выполните `{recommended_update_command_for_method(method)}`'
    else:
        action = 'выполните korra update'
    return (
        f'({action}; исправленные версии: 3.51.3+ / 3.50.7 / 3.44.6; см. https://sqlite.org/wal.html#walresetbug)'
    )


def _hermes_database_paths(hermes_home: Path) -> list[tuple[str, Path]]:
    """Return (display name, path) pairs for Hermes-managed SQLite databases."""
    # backup.py owns the canonical list of per-profile stores; reuse it.
    from korra_cli.backup import _QUICK_STATE_FILES

    entries = [
        (name, hermes_home / name)
        for name in _QUICK_STATE_FILES
        if name.endswith(".db")
    ]
    # Non-default kanban boards each keep their own kanban.db.
    for board_db in sorted((hermes_home / "kanban" / "boards").glob("*/kanban.db")):
        entries.append((str(board_db.relative_to(hermes_home)), board_db))
    return entries


_SQLITE_HEADER_MAGIC = b"SQLite format 3\x00"


def _unreadable_reason(db_path: Path) -> str:
    """Explain why a database file could not be read, without opening it.

    ``read_header_bytes_preopen`` collapses every ``OSError`` into ``None``,
    but doctor's job is to say *which* problem it hit. ``stat()`` and
    ``access()`` answer that from directory metadata alone — neither takes a
    file descriptor, so neither can cancel the file's POSIX advisory locks.
    """
    try:
        db_path.stat()
    except OSError as exc:
        return str(exc)
    if not os.access(db_path, os.R_OK):
        return f'Нет доступа: {db_path}'
    return 'Не удалось прочитать файл'


def _read_journal_mode(db_path: Path) -> tuple[str | None, str | None]:
    """Return (journal mode, error) from the file header without opening the database.

    Header byte 18 is 2 for WAL and 1 for a rollback journal. Opening the
    database through the SQLite engine — even read-only — creates -wal/-shm
    sidecar files, which a diagnostic must not do.

    The byte read is routed through ``read_header_bytes_preopen`` rather than
    a bare ``open()``: closing *any* descriptor for a database file cancels
    this process's POSIX advisory locks on it, so a raw read would drop the
    locks a live connection is holding (see ``korra_cli.sqlite_safe_read``).
    ``run_doctor`` is also called in-process by the dashboard console, which
    holds live ``SessionDB`` connections. The helper refuses in that case and
    the mode is reported as unreadable instead.
    """
    from korra_cli.sqlite_safe_read import (
        has_live_connection,
        read_header_bytes_preopen,
    )

    header = read_header_bytes_preopen(db_path, length=20)
    if header is None:
        if has_live_connection(db_path):
            return None, 'База открыта в этом процессе'
        return None, _unreadable_reason(db_path)
    if len(header) == 0:
        return None, 'Файл пуст'
    if len(header) < 20 or not header.startswith(_SQLITE_HEADER_MAGIC):
        return None, 'Файл не является базой данных'
    if header[18] == 2:
        return "wal", None
    if header[18] == 1:
        return "rollback", None
    return None, f'Неизвестная версия формата файла: {header[18]}'


def _format_db_size(db_path: Path) -> str:
    # backup.py owns human-readable size formatting; reuse it (as with
    # _QUICK_STATE_FILES above) and keep only the stat-failure wrap here.
    from korra_cli.backup import _format_size

    try:
        nbytes = db_path.stat().st_size
    except OSError:
        return 'Размер неизвестен'
    return _format_size(nbytes)


def _report_database_journal_modes(
    hermes_home: Path | None = None,
    version_info: tuple[int, ...] | None = None,
) -> None:
    """List each database's journal mode; warn on WAL under a vulnerable SQLite."""
    from korra_state import _wal_reset_repair_hint, is_sqlite_wal_reset_vulnerable

    vulnerable = is_sqlite_wal_reset_vulnerable(version_info)
    home = hermes_home if hermes_home is not None else HERMES_HOME
    try:
        databases = _hermes_database_paths(home)
    except Exception as exc:
        check_warn(f'Не удалось получить список баз Корры: {exc}')
        return
    exposed = []
    for name, path in databases:
        if not path.is_file():
            continue
        mode, error = _read_journal_mode(path)
        size = _format_db_size(path)
        if error is not None:
            if vulnerable:
                check_warn(
                    f'{name}: не удалось прочитать режим журнала',
                    f'({error}; риск ошибки WAL не исключён)',
                )
            else:
                check_info(f'{name}: не удалось прочитать режим журнала ({error})')
        elif mode == "wal":
            if vulnerable:
                exposed.append(name)
                check_warn(
                    f'{name} использует режим WAL ({size})',
                    '(подвержен ошибке сброса WAL до обновления SQLite)',
                )
            else:
                check_info(f'{name}: журнал WAL ({size})')
        elif vulnerable:
            check_info(f'{name}: журнал отката ({size}, без этого риска)')
        else:
            check_info(f'{name}: журнал отката ({size})')
    if exposed:
        check_info(f'Чтобы устранить проблему: {_wal_reset_repair_hint()}')


def _safe_which(cmd: str) -> str | None:
    """shutil.which wrapper resilient to platform monkeypatching in tests."""
    try:
        return shutil.which(cmd)
    except Exception:
        return None


def _termux_browser_setup_steps(node_installed: bool) -> list[str]:
    steps: list[str] = []
    step = 1
    if not node_installed:
        steps.append(f"{step}) pkg install nodejs")
        step += 1
    steps.append(f"{step}) npm install -g agent-browser")
    steps.append(f"{step + 1}) agent-browser install")
    return steps


def _termux_install_all_fallback_notes() -> list[str]:
    return [
        'Для Termux используйте .[termux-all] — совместимый набор по умолчанию.',
        'Сквозное шифрование Matrix исключено в Termux: python-olm не собирается.',
        'Локальный faster-whisper исключён в Termux: недоступна сборка ctranslate2/av.',
        'Для распознавания речи используйте Groq Whisper с GROQ_API_KEY или OpenAI Whisper с VOICE_TOOLS_OPENAI_KEY.',
    ]


def _has_provider_env_config(content: str) -> bool:
    """Return True when ~/.hermes/.env contains provider auth/base URL settings."""
    return any(key in content for key in _PROVIDER_ENV_HINTS)


def _honcho_is_configured_for_doctor() -> bool:
    """Return True when Honcho is configured, even if this process has no active session."""
    try:
        from plugins.memory.honcho.client import HonchoClientConfig

        cfg = HonchoClientConfig.from_global_config()
        return bool(cfg.enabled and (cfg.api_key or cfg.base_url))
    except Exception:
        return False


def _is_kanban_worker_env_gate(item: dict) -> bool:
    """Return True when Kanban is unavailable only because this is not a worker process."""
    if item.get("name") != "kanban":
        return False
    if korra_env("KORRA_KANBAN_TASK"):
        return False

    tools = item.get("tools") or []
    return bool(tools) and all(str(tool).startswith("kanban_") for tool in tools)


def _doctor_tool_availability_detail(toolset: str) -> str:
    """Optional explanatory suffix for toolsets whose doctor status needs context."""
    if toolset == "kanban" and not korra_env("KORRA_KANBAN_TASK"):
        return '(загружается только для исполнителей, запущенных диспетчером)'
    return ""


def _doctor_web_capability_rows() -> list[tuple[str, str, str]]:
    """Return doctor rows for web search/extract provider readiness (#78412).

    Each row is ``(status, label, detail)`` where *status* is ``ok`` or ``warn``.
    Uses the same active-provider resolvers as the tools, but reports readiness
    from ``is_available()`` so an explicitly selected but unconfigured backend
    does not look healthy.
    """
    rows: list[tuple[str, str, str]] = []
    try:
        from agent.web_search_registry import (
            get_active_extract_provider,
            get_active_search_provider,
        )
        from tools.web_tools import _ensure_web_plugins_loaded, _provider_is_ready

        # Doctor runs in a fresh process — bundled web providers register
        # during plugin discovery, which nothing has triggered yet here.
        # Without this the registry is empty and every row reads
        # "no provider selected or registered" (idempotent, cheap on rerun).
        _ensure_web_plugins_loaded()
    except Exception:
        return rows

    for capability, getter in (
        ('Поиск в интернете', get_active_search_provider),
        ('Чтение веб-страниц', get_active_extract_provider),
    ):
        try:
            provider = getter()
        except Exception:
            provider = None
        if provider is None:
            rows.append(
                (
                    "warn",
                    capability,
                    '(провайдер не выбран или не зарегистрирован)',
                )
            )
            continue
        name = getattr(provider, "name", None) or type(provider).__name__
        if _provider_is_ready(provider):
            rows.append(("ok", capability, f"({name})"))
        else:
            rows.append(
                (
                    "warn",
                    capability,
                    f'(выбран {name}; провайдер не настроен)',
                )
            )
    return rows

def _apply_doctor_tool_availability_overrides(available: list[str], unavailable: list[dict]) -> tuple[list[str], list[dict]]:
    """Adjust runtime-gated tool availability for doctor diagnostics."""
    updated_available = list(available)
    updated_unavailable = []
    for item in unavailable:
        name = item.get("name")
        if _is_kanban_worker_env_gate(item):
            if "kanban" not in updated_available:
                updated_available.append("kanban")
            continue
        if name == "honcho" and _honcho_is_configured_for_doctor():
            if "honcho" not in updated_available:
                updated_available.append("honcho")
            continue
        updated_unavailable.append(item)
    return updated_available, updated_unavailable


def _has_healthy_oauth_fallback_for_apikey_provider(provider_label: str) -> bool:
    """Return True when a direct API-key probe failure is non-blocking.

    Some provider families support both a direct API-key path and a separate
    OAuth runtime path. When the OAuth path is already healthy, doctor should
    still show a failed API-key connectivity row, but it should not promote
    that direct-key problem into the final blocking summary.
    """
    normalized = (provider_label or "").strip().lower()
    if normalized == "minimax":
        try:
            from korra_cli.auth import get_minimax_oauth_auth_status
            return bool((get_minimax_oauth_auth_status() or {}).get("logged_in"))
        except Exception:
            return False
    if normalized == "xai":
        try:
            from korra_cli.auth import get_xai_oauth_auth_status
            return bool((get_xai_oauth_auth_status() or {}).get("logged_in"))
        except Exception:
            return False
    return False


def check_ok(text: str, detail: str = ""):
    print(f"  {color('✓', Colors.GREEN)} {text}" + (f" {color(detail, Colors.DIM)}" if detail else ""))

def check_warn(text: str, detail: str = ""):
    print(f"  {color('⚠', Colors.YELLOW)} {text}" + (f" {color(detail, Colors.DIM)}" if detail else ""))

def check_fail(text: str, detail: str = ""):
    print(f"  {color('✗', Colors.RED)} {text}" + (f" {color(detail, Colors.DIM)}" if detail else ""))

def check_info(text: str):
    print(f"    {color('→', Colors.CYAN)} {text}")


# ── state.db health/stats thresholds (advisory only — module constants,
# deliberately NOT config: doctor warnings are guidance, not policy) ──
STATE_DB_SIZE_WARN_BYTES = 1 * 1024 * 1024 * 1024   # 1 GiB logical size


# Shared byte formatter, aliased to the name this module's three rendering
# call sites already use.
from korra_cli.sizefmt import format_bytes as _human_bytes


def _render_state_db_stats(stats: dict, holders=None) -> list:
    """Turn a collect_state_db_stats() dict into doctor output lines.

    Returns a list of ``(kind, text, detail)`` tuples where kind is one of
    'info' / 'warn'. Pure formatting — no I/O — so it is unit-testable
    without spawning the doctor CLI. Tolerates None in every field.
    """
    lines: list = []
    stats = stats or {}

    logical = stats.get("logical_size_bytes")
    wal = stats.get("wal_size_bytes")
    freelist = stats.get("freelist_count")

    size_bits = []
    if logical is not None:
        size_bits.append(f'размер данных {_human_bytes(logical)}')
    if stats.get("page_count") is not None:
        size_bits.append(f"страниц: {stats['page_count']:,}")
    if freelist is not None:
        size_bits.append(f'свободно: {freelist:,}')
    if wal is not None:
        size_bits.append(f"WAL {_human_bytes(wal)}")
    if size_bits:
        lines.append(("info", "state.db " + ", ".join(size_bits), ""))

    row_bits = []
    if stats.get("messages") is not None:
        row_bits.append(f"сообщений: {stats['messages']:,}")
    if stats.get("sessions") is not None:
        row_bits.append(f"бесед: {stats['sessions']:,}")
    if stats.get("journal_mode"):
        row_bits.append(f"режим журнала: {stats['journal_mode']}")
    if holders is not None:
        row_bits.append(f'процессов с открытой базой: {holders}')
    if row_bits:
        lines.append(("info", ", ".join(row_bits), ""))

    fts = stats.get("fts_tables")
    if fts:
        present = [t for t, ok in fts.items() if ok]
        lines.append((
            "info",
            'Таблицы FTS: ' + (", ".join(present) if present else "none"),
            "",
        ))

    deferral = stats.get("fts_rebuild_deferral")
    if isinstance(deferral, dict):
        attempts = deferral.get("attempts")
        pids = deferral.get("holder_pids") or []
        lines.append((
            "warn",
            f"Восстановление FTS в state.db отложено {attempts or '?'} раз из-за процессов PID {pids or 'unknown'}",
            '(остановите указанные процессы и шлюз, затем выполните korra sessions optimize-storage)',
        ))

    # Advisory: oversized database. Suggest auto_prune, and — when the v23
    # FTS rebuild is pending OR the DB still carries the legacy inline
    # trigram layout (fts_storage_version marker absent) — the offline
    # optimize-storage pass that migrates/compacts the FTS indexes.
    if logical is not None and logical > STATE_DB_SIZE_WARN_BYTES:
        detail = (
            'включите sessions.auto_prune в config.yaml, чтобы ограничить рост базы'
        )
        legacy_trigram = (
            fts is not None
            and fts.get("messages_fts_trigram")
            and stats.get("fts_storage_version") is None
        )
        if stats.get("fts_rebuild_pending") or legacy_trigram:
            detail += (
                '; для уменьшения индекса FTS выполните korra sessions optimize-storage при остановленном шлюзе'
            )
        lines.append((
            "warn",
            f'База state.db большая ({_human_bytes(logical)})',
            f"({detail})",
        ))

    # WAL runaway is deliberately NOT warned here: the pre-existing WAL
    # check later in the state.db section already warns above 50 MB and
    # offers a checkpoint via --fix; a second warning at a higher threshold
    # would only duplicate it.

    return lines


def _section(title: str) -> None:
    """Print a doctor section banner: blank line + bold cyan ◆ title."""
    print()
    print(color(f"◆ {title}", Colors.CYAN, Colors.BOLD))


def _fail_and_issue(text: str, detail: str, fix: str, issues: list[str]) -> None:
    """Emit a check_fail and append the corresponding fix instruction."""
    check_fail(text, detail)
    issues.append(fix)


# Deprecated / legacy config keys still read for back-compat. Doctor surfaces
# them as non-failing warnings with the modern replacement — it does not
# auto-migrate or delete (migrations live in config.py version steps).
_DEPRECATED_CONFIG_KEYS: tuple[tuple[str, str, str], ...] = (
    # (section, key, replacement)
    ("display", "tool_progress_overrides", "display.platforms"),
    ("delegation", "max_async_children", "delegation.max_concurrent_children"),
)

# compression.summary_* → auxiliary.compression (model/provider/base_url)
_DEPRECATED_COMPRESSION_SUMMARY_KEYS: tuple[str, ...] = (
    "summary_model",
    "summary_provider",
    "summary_base_url",
)

# Deprecated env vars (checked in the .env file, not process env, so config→env
# bridges like terminal.cwd → TERMINAL_CWD do not false-positive).
_DEPRECATED_ENV_VARS: tuple[tuple[str, str], ...] = (
    # HERMES_TOOL_PROGRESS is fully unsupported since the v12 config support
    # floor removed its only consumer (the v3→4 migration) — it is silently
    # ignored. HERMES_TOOL_PROGRESS_MODE is still read by the gateway as a
    # back-compat fallback but remains deprecated.
    ("HERMES_TOOL_PROGRESS", 'display.tool_progress в config.yaml; не поддерживается и игнорируется начиная с версии настроек v12'),
    ("HERMES_TOOL_PROGRESS_MODE", 'display.tool_progress в config.yaml'),
    ("TERMINAL_CWD", 'terminal.cwd в config.yaml'),
    ("MESSAGING_CWD", 'terminal.cwd в config.yaml'),
    ("QQ_HOME_CHANNEL", "QQBOT_HOME_CHANNEL"),
    ("QQ_HOME_CHANNEL_NAME", "QQBOT_HOME_CHANNEL_NAME"),
)


def collect_deprecated_config_keys(raw_config: dict | None) -> list[tuple[str, str]]:
    """Return ``(legacy_path, replacement)`` for deprecated keys present in *raw_config*.

    Only keys that appear in the on-disk YAML are reported (raw file load, not
    merged defaults). Empty containers still count — presence of the legacy
    key is the signal that the user should migrate.
    """
    findings: list[tuple[str, str]] = []
    if not isinstance(raw_config, dict):
        return findings

    for section, key, replacement in _DEPRECATED_CONFIG_KEYS:
        section_val = raw_config.get(section)
        if isinstance(section_val, dict) and key in section_val:
            findings.append((f"{section}.{key}", replacement))

    compression = raw_config.get("compression")
    if isinstance(compression, dict):
        for key in _DEPRECATED_COMPRESSION_SUMMARY_KEYS:
            if key in compression:
                findings.append((f"compression.{key}", "auxiliary.compression"))

    return findings


def collect_deprecated_env_vars(env_map: dict | None) -> list[tuple[str, str]]:
    """Return ``(legacy_env, replacement)`` for deprecated vars present in *env_map*.

    *env_map* should come from the on-disk ``.env`` (e.g. ``load_env()``), not
    ``os.environ``, so bridged runtime vars do not trigger false positives.
    """
    findings: list[tuple[str, str]] = []
    if not isinstance(env_map, dict):
        return findings
    for name, replacement in _DEPRECATED_ENV_VARS:
        val = env_map.get(name)
        if val is not None and str(val).strip() != "":
            findings.append((name, replacement))
    return findings


def collect_relay_plugin_cutover_findings(
    raw_config: dict | None,
    env_map: dict | None,
) -> list[tuple[str, str]]:
    """Return actionable findings for the removed Hermes Relay plugin."""
    from korra_cli.relay_plugin_cutover import (
        LEGACY_RELAY_EXPORT_ENV_VARS,
        RELAY_PLUGINS_CONFIG_ENV,
        configured_legacy_relay_env_vars,
        legacy_relay_plugin_keys,
    )

    findings: list[tuple[str, str]] = []
    if isinstance(raw_config, dict):
        plugins = raw_config.get("plugins")
        if isinstance(plugins, dict):
            for key in legacy_relay_plugin_keys(plugins.get("enabled")):
                findings.append(
                    (
                        f"plugins.enabled: {key}",
                        f'удалите и настройте {RELAY_PLUGINS_CONFIG_ENV}',
                    )
                )

    effective_env = dict(env_map or {})
    # Fall through to process-level env ONLY when no explicit env_map was
    # given: run_doctor passes None and wants live-process vars included, but
    # callers (and tests) that hand in an explicit map are describing a
    # complete environment — merging os.environ on top breaks hermeticity on
    # any box that exports legacy relay vars (10-vs-2 findings, Aug 2026).
    if env_map is None:
        for name in (*LEGACY_RELAY_EXPORT_ENV_VARS, RELAY_PLUGINS_CONFIG_ENV):
            if name not in effective_env and korra_env(name) is not None:
                effective_env[name] = korra_env(name)
    if not str(effective_env.get(RELAY_PLUGINS_CONFIG_ENV, "")).strip():
        for name in configured_legacy_relay_env_vars(effective_env):
            findings.append(
                (
                    name,
                    f'перенесите настройки выгрузки в {RELAY_PLUGINS_CONFIG_ENV}; эта переменная больше не используется',
                )
            )
    return findings


def report_deprecated_config_and_env(
    raw_config: dict | None = None,
    env_map: dict | None = None,
) -> list[tuple[str, str]]:
    """Emit non-failing doctor warnings for deprecated config keys and env vars.

    Returns the list of ``(legacy, replacement)`` findings that were reported
    (empty when nothing deprecated is present). Does not mutate config/env and
    does not append to the blocking ``issues`` list.
    """
    deprecated = collect_deprecated_config_keys(raw_config)
    deprecated.extend(collect_deprecated_env_vars(env_map))
    relay_cutover = collect_relay_plugin_cutover_findings(raw_config, env_map)
    findings = deprecated + relay_cutover
    if not findings:
        check_ok('Устаревших ключей настроек и переменных среды нет')
        return findings

    for legacy, replacement in deprecated:
        check_warn(
            f'Устарело: {legacy}',
            f'(используйте {replacement})',
        )
        check_info(f'Замените {legacy} → {replacement}; здесь только предупреждение, без автоматической замены')
    for legacy, replacement in relay_cutover:
        check_warn(
            f'Требуется перенос подключения ретранслятора: {legacy}',
            f"({replacement})",
        )
        check_info(f'Перенесите {legacy}: {replacement}')
    return findings


def _enabled_cli_toolsets_for_doctor() -> set[str] | None:
    """Return toolsets enabled for the CLI, or None if config resolution fails."""
    try:
        from korra_cli.config import load_config
        from korra_cli.tools_config import _get_platform_tools

        return {str(toolset) for toolset in _get_platform_tools(load_config() or {}, "cli")}
    except Exception:
        return None


def _missing_api_key_toolsets_for_summary(unavailable: list[dict]) -> list[dict]:
    """Filter unavailable API-key toolsets to those enabled for the CLI."""
    api_key_unavailable = [
        item for item in unavailable
        if item.get("missing_vars") or item.get("env_vars")
    ]
    enabled_toolsets = _enabled_cli_toolsets_for_doctor()
    if enabled_toolsets is None:
        return api_key_unavailable
    return [
        item for item in api_key_unavailable
        if str(item.get("name") or "") in enabled_toolsets
    ]


def _read_pyproject_version() -> str | None:
    """Read the ``version = "..."`` from ``pyproject.toml`` at the project root.

    Returns None when running from an installed wheel (no pyproject.toml ships
    with the package) or when the file can't be parsed. Reads only the
    ``[project]`` version, ignoring any version strings that appear in other
    tables.
    """
    pyproject = PROJECT_ROOT / "pyproject.toml"
    try:
        text = pyproject.read_text(encoding="utf-8")
    except OSError:
        return None
    in_project = False
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("[") and line.endswith("]"):
            in_project = line == "[project]"
            continue
        if in_project and line.startswith("version") and "=" in line:
            value = line.split("=", 1)[1]
            value = value.split("#", 1)[0].strip().strip("\"'")
            return value or None
    return None


def _check_version_consistency(issues: list[str]) -> None:
    """Verify pyproject.toml version matches korra_cli.__version__.

    A git conflict resolution (reset/merge) can revert one file without the
    other, leaving ``hermes --version`` reporting a stale version while
    ``pyproject.toml`` is current. Detect that drift so users can re-sync.
    Silent no-op for installed wheels where pyproject.toml isn't present.
    """
    try:
        from korra_cli import __version__ as init_version
    except Exception:
        return
    pyproject_version = _read_pyproject_version()
    if pyproject_version is None:
        # Installed wheel or unreadable pyproject — nothing to cross-check.
        return
    if pyproject_version == init_version:
        check_ok('Версии в исходных файлах совпадают', f"({init_version})")
    else:
        _fail_and_issue(
            'Версии в исходных файлах не совпадают',
            f"(pyproject.toml {pyproject_version} != korra_cli/__init__.py {init_version})",
            'Синхронизируйте версии: выполните korra update или задайте __version__ в korra_cli/__init__.py по версии из pyproject.toml',
            issues,
        )


def check_local_bot_api(issues: list[str]) -> None:
    """Свой сервер Telegram Bot API: виден ли контуру его каталог с файлами.

    Сервер, запущенный с ``--local``, файлы по HTTP не отдаёт — он называет
    абсолютный путь на своём диске. Если контур этого каталога не видит, всё
    входящее медиа приходит к агенту как 404, а владелец читает «InvalidToken»
    и повторяет отправку без всякого толку. Симптом ничем не выдаёт причину и
    прожил девять дней на трёх установках, поэтому здесь он назван прямо.
    """
    try:
        from korra_cli.config import load_config

        extra = ((load_config() or {}).get("telegram") or {}).get("extra") or {}
    except Exception:
        return
    base_url = str(extra.get("base_url") or "").strip()
    if not base_url:
        return
    if not any(host in base_url for host in ("127.0.0.1", "localhost", "[::1]")):
        # Сервер на другой машине: файлы туда и обратно ходят по сети штатно.
        return
    if extra.get("local_mode") is False:
        check_info('Свой Telegram Bot API: чтение с диска выключено в конфиге')
        return

    root = os.environ.get("KORRA_TELEGRAM_LOCAL_ROOT", "").strip()
    fix = (
        'Пересоздайте контур через host-kit (update.sh) — он примонтирует каталог '
        'своего Telegram Bot API только на чтение; либо задайте BOT_API_DIR вручную'
    )
    if not root:
        _fail_and_issue(
            'Свой Telegram Bot API: каталог с файлами не примонтирован',
            f'({base_url} — входящие фото и голосовые не дойдут до агента)',
            fix,
            issues,
        )
        return
    if not os.path.isdir(root):
        _fail_and_issue(
            'Свой Telegram Bot API: каталог объявлен, но недоступен',
            f'({root})',
            fix,
            issues,
        )
        return
    if not os.access(root, os.R_OK | os.X_OK):
        _fail_and_issue(
            'Свой Telegram Bot API: каталог не читается',
            f'({root})',
            'Проверьте владельца каталога: файлы сервера бота должен читать пользователь контура',
            issues,
        )
        return
    check_ok('Свой Telegram Bot API: медиа читается с диска', f'({root})')


# Человеческие имена провайдеров речи: «local» в отчёте ничего не объясняет,
# а разница между локальным whisper и облаком — это 11 секунд против двух.
_STT_PROVIDER_LABELS = {
    "local": 'локальный whisper из образа',
    "local_command": 'локальная команда распознавания',
}


def _stt_effective_provider(stt_config: dict) -> str:
    """Чем контур распознает речь прямо сейчас — без установки пакетов.

    ``_get_provider`` — это и есть рантайм-решение, поэтому спрашиваем именно
    его. Но по дороге он может попытаться доставить faster-whisper: отчёт о
    состоянии не имеет права тянуть 390 МБ колёс, поэтому эта попытка на время
    проверки выключается.
    """
    import tools.transcription_tools as stt_tools

    original = stt_tools._try_lazy_install_stt
    stt_tools._try_lazy_install_stt = lambda: False
    try:
        return stt_tools._get_provider(stt_config)
    finally:
        stt_tools._try_lazy_install_stt = original


def _stt_declares_env(stt_config: dict, provider: str) -> bool:
    """Объявляет ли провайдер речи обязательные переменные окружения.

    Только тогда «ключ есть» — утверждение, а не догадка: у local и у
    автоопределения проверять нечего.
    """
    import tools.transcription_tools as stt_tools

    section = stt_tools._get_named_stt_provider_config(stt_config, provider)
    return bool(section.get("requires_env"))


def check_speech_recognition(issues: list[str]) -> None:
    """Фактический провайдер речи и причина, по которой выбран именно он.

    Шаблон контура просит deepgram и разрешает откат на local. Без ключа откат
    срабатывает молча: голосовые обрабатываются, просто в пять раз дольше, и
    владелец об этом не узнаёт. Свип 12.09.2026 нашёл восемь таких установок из
    десяти — ни doctor, ни панель фактический выбор не называли.
    """
    import tools.transcription_tools as stt_tools

    stt_config = stt_tools._load_stt_config()
    if not stt_tools.is_stt_enabled(stt_config):
        check_info('Распознавание речи выключено в настройках (stt.enabled: false)')
        return

    selected = stt_config.get("provider") or ""
    selected = str(selected).strip().lower()
    actual = _stt_effective_provider(stt_config)
    label = _STT_PROVIDER_LABELS.get(actual, actual)

    missing = stt_tools._stt_missing_required_env(selected, stt_config) if selected else []

    # ``_get_provider`` возвращает исходный выбор и тогда, когда ни один
    # запасной не готов: его собственная ошибка точнее общей. Для отчёта это
    # не «работает», а «речи нет вообще» — единственный случай здесь, который
    # обязан попасть в список проблем.
    if actual == "none" or (actual == selected and missing):
        detail = (
            f'(выбран {selected}: не задан {", ".join(missing)})' if missing
            else f'(выбран {selected}: провайдер не готов)' if selected
            else '(нет ни локального whisper, ни облачного провайдера)'
        )
        _fail_and_issue(
            'Распознавание речи не работает: ни один провайдер не готов',
            detail,
            'Задайте ключ выбранного провайдера речи (stt.provider) или включите локальный whisper: stt.fallback: local',
            issues,
        )
        return

    if not selected or selected == actual:
        check_ok(f'Распознавание речи: {label}',
                 '(ключ есть)' if missing == [] and selected and _stt_declares_env(stt_config, selected)
                 else '(выбран в настройках)' if selected else '(выбран автоматически)')
        return

    # Работает запасной провайдер: назвать и его, и причину отката — иначе
    # недонастройка выглядит как норма.
    reason = f'не задан {", ".join(missing)}' if missing else 'провайдер не готов'
    check_warn(f'Распознавание речи: {label}',
               f'(вместо {selected}: {reason})')


def check_channels_and_providers(issues: list[str]) -> None:
    """Одна секция про то, чем контур слышит, ищет и получает медиа.

    Всё это до сих пор узнавалось только из жалобы владельца: провал фото
    пролежал девять дней, бесключевой поиск падал сотнями раз за разговор, а
    речь молча ехала на запасном провайдере. Проверки сведены в одно место,
    потому что вопрос у владельца один — «чем сейчас работает контур».
    """
    _section('Каналы и провайдеры')
    try:
        check_speech_recognition(issues)
    except Exception as exc:
        check_warn('Не удалось определить провайдер распознавания речи', f'({exc})')


def _check_s6_supervision(issues: list[str]) -> None:
    """Inside a container under our s6 /init, surface what s6 sees.

    Runs as a counterpart to :func:`_check_gateway_service_linger` for
    the systemd-on-host case. No-op everywhere except in the s6
    container so host runs aren't cluttered with irrelevant output.

    Reports:
      - Whether the main-hermes and dashboard static services are up
      - How many per-profile gateway slots are registered (via
        ``S6ServiceManager.list_profile_gateways()``) and how many are
        currently supervised as ``up``
    """
    try:
        from korra_cli.service_manager import (
            S6ServiceManager,
            detect_service_manager,
        )
    except Exception:
        return

    if detect_service_manager() != "s6":
        return

    _section('Контроль служб s6')

    mgr = S6ServiceManager()

    # Static services. They live under /run/service/ via s6-rc symlinks,
    # so the same s6-svstat probe works.
    for static in ("main-hermes", "dashboard"):
        if mgr.is_running(static):
            check_ok(f'{static}: работает')
        else:
            check_info(f'{static}: остановлено; это нормально, если не включено в среде')

    profiles = mgr.list_profile_gateways()
    if not profiles:
        check_info('Шлюзы профилей ещё не настроены. Создайте профиль: korra profile create <name>')
        return

    up_count = sum(1 for p in profiles if mgr.is_running(f"gateway-{p}"))
    check_ok(
        f'Шлюзы профилей: {up_count}/{len(profiles)} работают под контролем службы'
        + (f" ({', '.join(sorted(profiles))})" if len(profiles) <= 8 else "")
    )


def check_certificates(should_fix: bool = False, issues: "list | None" = None) -> None:
    """Verify the certifi CA bundle is loadable.

    Surfaces the SSLConfigurationError user-friendly path before they hit
    a wall of tracebacks on the first outbound HTTPS call.

    With ``--fix``, a broken bundle (missing/corrupt ``cacert.pem`` — e.g.
    after a brew Python upgrade rebuilt the venv, #29866) is repaired by
    force-reinstalling certifi into THIS interpreter's environment and
    re-verifying.
    """
    try:
        from agent.ssl_guard import verify_ca_bundle_with_fallback
        from agent.errors import SSLConfigurationError
    except Exception as e:
        check_warn('Проверка сертификатов SSL пропущена', str(e))
        return

    try:
        verify_ca_bundle_with_fallback()
        check_ok('Набор корневых сертификатов SSL исправен')
        return
    except SSLConfigurationError as e:
        first_error = str(e)
    except Exception as e:
        check_warn('Проверка сертификатов SSL пропущена', str(e))
        return

    if not should_fix:
        check_fail('Набор корневых сертификатов SSL повреждён', first_error)
        if issues is not None:
            issues.append(
                f'Восстановите сертификаты: korra doctor --fix или `{sys.executable} -m pip install --force-reinstall certifi`'
            )
        return

    # --fix: force-reinstall certifi into the running interpreter's env and
    # re-verify. importlib caches are invalidated so certifi.where() resolves
    # the fresh install without a process restart.
    check_fail('Набор корневых сертификатов SSL повреждён', first_error)
    print('    → Восстановление: переустановка certifi…')
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", "--force-reinstall", "certifi"],
            capture_output=True,
            text=True,
            timeout=300,
        )
    except Exception as exc:
        check_fail('Не удалось запустить pip для восстановления certifi', str(exc))
        if issues is not None:
            issues.append(
                f'Переустановите certifi вручную: {sys.executable} -m pip install --force-reinstall certifi'
            )
        return
    if result.returncode != 0:
        tail = (result.stderr or result.stdout or "")[-500:]
        check_fail('Не удалось переустановить certifi', tail)
        if issues is not None:
            issues.append(
                f'Переустановите certifi вручную: {sys.executable} -m pip install --force-reinstall certifi'
            )
        return

    # Drop any cached certifi module so where() re-resolves the new bundle.
    import importlib
    for mod_name in [m for m in sys.modules if m == "certifi" or m.startswith("certifi.")]:
        sys.modules.pop(mod_name, None)
    importlib.invalidate_caches()

    try:
        verify_ca_bundle_with_fallback()
        check_ok('Набор корневых сертификатов SSL восстановлен переустановкой certifi')
    except SSLConfigurationError as e:
        check_fail('После переустановки набор корневых сертификатов SSL всё ещё повреждён', str(e))
        if issues is not None:
            issues.append(
                'Переустановка certifi не восстановила сертификаты. Проверьте, не указывают ли SSL_CERT_FILE или REQUESTS_CA_BUNDLE на отсутствующий файл, либо пересоздайте виртуальное окружение.'
            )


def check_multiplex_profiles(
    should_fix: bool = False,
    issues: "list[str] | None" = None,
    hermes_home: "Path | None" = None,
) -> None:
    """Секция «Вкладки агентов»: три условия мультиплекса по каждому профилю.

    Вкладка агента в панели отвечает только когда корневой шлюз
    мультиплексирует профили, у профиля есть корневой ``API_SERVER_KEY`` и
    его собственный api-сервер выключен. Любой пропуск даёт одинаковый
    симптом «Корра не смогла ответить» при живом Telegram (K21-058).
    ``--fix`` чинит все три пункта на месте.
    """
    from korra_cli.multiplex_reconcile import inspect_multiplex, reconcile_multiplex

    if hermes_home is None:
        try:
            from korra_constants import get_default_hermes_root

            hermes_home = Path(get_default_hermes_root())
        except Exception:
            hermes_home = Path(HERMES_HOME)
    issues = issues if issues is not None else []

    report = inspect_multiplex(hermes_home)
    if report.named_profiles == 0 and report.flag is not None:
        return  # нечего мультиплексировать и владелец уже определился

    _section('Вкладки агентов (мультиплекс профилей)')

    if should_fix and not report.tabs_work and report.flag is not False:
        fixed = reconcile_multiplex(hermes_home)
        for line in fixed.actions:
            check_ok(f'Исправлено: {line}')
        for line in fixed.errors:
            check_warn(f'Не удалось исправить: {line}')
        report = fixed
        if fixed.actions and report.tabs_work:
            check_info('Перезапустите шлюз, чтобы общий шлюз подхватил профили: korra gateway restart')

    if report.flag is None:
        _fail_and_issue(
            'gateway.multiplex_profiles не задан в корневом config.yaml',
            f'профилей: {report.named_profiles}; вкладки агентов в панели отвечают 404',
            'Включите общий шлюз: korra doctor --fix (или gateway.multiplex_profiles: true в config.yaml)',
            issues,
        )
    elif report.flag is False:
        if report.named_profiles:
            check_warn(
                'gateway.multiplex_profiles: false — вкладки агентов в панели работать не будут',
                'это явное решение владельца; агенты доступны только в своих каналах',
            )
        else:
            check_ok('Мультиплекс выключен, именованных профилей нет')
        return
    else:
        check_ok('gateway.multiplex_profiles: true')

    if not report.root_key_present:
        _fail_and_issue(
            'В корневом .env нет API_SERVER_KEY',
            'без него общий шлюз не поднимет api-сервер и не раздаст ключ профилям',
            'Задайте API_SERVER_KEY в корневом .env (openssl rand -hex 32) и перезапустите шлюз',
            issues,
        )

    if report.named_profiles == 0:
        check_info('Именованных профилей пока нет')
        return

    for finding in report.profiles:
        if finding.ok:
            check_ok(f'  {finding.name}: ключ, пин api_server и собственный шлюз в порядке')
        else:
            _fail_and_issue(
                f'  {finding.name}: {finding.describe()}',
                '',
                f'Профиль {finding.name}: korra doctor --fix (ключ в .env, platforms.api_server.enabled: false, собственный шлюз stopped)',
                issues,
            )


def _check_gateway_service_linger(issues: list[str]) -> None:
    """Warn when a systemd user gateway service will stop after logout.

    Skipped inside a container running under s6 — the linger concept
    (user-systemd surviving SSH logout) doesn't apply there, and the
    s6 supervision state is surfaced separately by
    ``_check_s6_supervision``.
    """
    try:
        from korra_cli.gateway import (
            get_systemd_linger_status,
            get_systemd_unit_path,
            is_linux,
        )
        from korra_cli.service_manager import detect_service_manager
    except Exception as e:
        check_warn('Работа шлюза после выхода из системы', f'(не удалось загрузить компоненты шлюза: {e})')
        return

    if not is_linux():
        return

    # Inside a container under our s6 /init, _check_s6_supervision
    # reports the live supervision state; the linger warning would be
    # confusing here (no systemd, no logout, no "lingering" concept).
    if detect_service_manager() == "s6":
        return

    unit_path = get_systemd_unit_path()
    if not unit_path.exists():
        return

    _section('Служба шлюза')
    linger_enabled, linger_detail = get_systemd_linger_status()
    if linger_enabled is True:
        check_ok('Systemd linger включён', '(шлюз продолжает работать после выхода из системы)')
    elif linger_enabled is False:
        check_warn('Systemd linger отключён', '(шлюз может остановиться после выхода из системы)')
        check_info('Выполните: sudo loginctl enable-linger $USER')
        issues.append('Разрешите службе шлюза работать после выхода: sudo loginctl enable-linger $USER')
    else:
        check_warn('Не удалось проверить systemd linger', f"({linger_detail})")


_APIKEY_PROVIDERS_CACHE: list | None = None


def _build_apikey_providers_list() -> list:
    """Build the API-key provider health-check list once and cache it.

    Tuple format: (name, env_vars, default_url, base_env, supports_models_endpoint)
    Base list augmented with any ProviderProfile with auth_type="api_key" not
    already present — adding plugins/model-providers/<name>/ is sufficient to get into doctor.
    """
    _static = [
        ("Z.AI / GLM",      ("GLM_API_KEY", "ZAI_API_KEY", "Z_AI_API_KEY"), "https://api.z.ai/api/paas/v4/models", "GLM_BASE_URL", True),
        ("Kimi / Moonshot",  ("KIMI_API_KEY",),                              "https://api.moonshot.ai/v1/models",   "KIMI_BASE_URL", True),
        ("StepFun Step Plan", ("STEPFUN_API_KEY",),                          "https://api.stepfun.ai/step_plan/v1/models", "STEPFUN_BASE_URL", True),
        ("Kimi / Moonshot (China)", ("KIMI_CN_API_KEY",),                    "https://api.moonshot.cn/v1/models",   None, True),
        ("Arcee AI",         ("ARCEEAI_API_KEY",),                           "https://api.arcee.ai/api/v1/models",  "ARCEE_BASE_URL", True),
        ("GMI Cloud",        ("GMI_API_KEY",),                               "https://api.gmi-serving.com/v1/models", "GMI_BASE_URL", True),
        ("DeepSeek",         ("DEEPSEEK_API_KEY",),                          "https://api.deepseek.com/v1/models",  "DEEPSEEK_BASE_URL", True),
        ("Hugging Face",     ("HF_TOKEN",),                                  "https://router.huggingface.co/v1/models", "HF_BASE_URL", True),
        ("NVIDIA NIM",       ("NVIDIA_API_KEY",),                            "https://integrate.api.nvidia.com/v1/models", "NVIDIA_BASE_URL", True),
        ("Alibaba/DashScope", ("DASHSCOPE_API_KEY",),                        "https://dashscope-intl.aliyuncs.com/compatible-mode/v1/models", "DASHSCOPE_BASE_URL", True),
        # MiniMax global: /v1 endpoint supports /models.
        ("MiniMax",          ("MINIMAX_API_KEY",),                           "https://api.minimax.io/v1/models",    "MINIMAX_BASE_URL", True),
        # MiniMax CN: /v1 endpoint does NOT support /models (returns 404).
        ("MiniMax (China)",  ("MINIMAX_CN_API_KEY",),                        "https://api.minimaxi.com/v1/models",  "MINIMAX_CN_BASE_URL", False),
        ("Vercel AI Gateway", ("AI_GATEWAY_API_KEY",),                       "https://ai-gateway.vercel.sh/v1/models", "AI_GATEWAY_BASE_URL", True),
        ("Kilo Code",        ("KILOCODE_API_KEY",),                          "https://api.kilo.ai/api/gateway/models", "KILOCODE_BASE_URL", True),
        ("OpenCode Zen",     ("OPENCODE_ZEN_API_KEY",),                      "https://opencode.ai/zen/v1/models",  "OPENCODE_ZEN_BASE_URL", True),
        # OpenCode Go has no shared /models endpoint; skip the health check.
        ("OpenCode Go",      ("OPENCODE_GO_API_KEY",),                       None,                                  "OPENCODE_GO_BASE_URL", False),
    ]
    _known_names = {t[0] for t in _static}
    # Also index by profile canonical name so profiles without display_name
    # don't create duplicate entries for providers already in the static list.
    _known_canonical: set[str] = set()
    _name_to_canonical = {
        "Z.AI / GLM": "zai", "Kimi / Moonshot": "kimi-coding",
        "StepFun Step Plan": "stepfun", "Kimi / Moonshot (China)": "kimi-coding-cn",
        "Arcee AI": "arcee", "GMI Cloud": "gmi", "DeepSeek": "deepseek",
        "Hugging Face": "huggingface", "NVIDIA NIM": "nvidia",
        "Alibaba/DashScope": "alibaba", "MiniMax": "minimax",
        "MiniMax (China)": "minimax-cn", "Vercel AI Gateway": "ai-gateway",
        "Kilo Code": "kilocode", "OpenCode Zen": "opencode-zen",
        "OpenCode Go": "opencode-go",
    }
    for _label, _canonical in _name_to_canonical.items():
        _known_canonical.add(_canonical)
    # Providers that already have a dedicated health check above the generic
    # API-key loop (with custom headers/auth). Skip their pluggable profiles
    # here so the generic Bearer-auth loop doesn't run a duplicate, broken
    # check (e.g. Anthropic native API requires x-api-key, not Bearer).
    _dedicated_canonical = {"anthropic", "openrouter", "bedrock"}
    _known_canonical.update(_dedicated_canonical)
    try:
        from providers import list_providers
        from providers.base import ProviderProfile as _PP
        try:
            from korra_cli.providers import normalize_provider as _normalize_provider
        except Exception:  # pragma: no cover - normalization is best-effort
            def _normalize_provider(_name: str) -> str:
                return (_name or "").strip().lower()
        for _pp in list_providers():
            if not isinstance(_pp, _PP) or _pp.auth_type != "api_key" or not _pp.env_vars:
                continue
            _label = _pp.display_name or _pp.name
            if _label in _known_names or _pp.name in _known_canonical:
                continue
            _candidates = {_normalize_provider(_pp.name)}
            for _alias in (_pp.aliases or ()):
                _candidates.add(_normalize_provider(_alias))
            if _candidates & _dedicated_canonical:
                continue
            # Separate API-key vars from base-URL override vars — the health-check
            # loop sends the first found value as Authorization: Bearer, so a URL
            # string must never be picked.
            _key_vars = tuple(
                v for v in _pp.env_vars
                if not v.endswith("_BASE_URL") and not v.endswith("_URL")
            )
            _base_var = next(
                (v for v in _pp.env_vars if v.endswith("_BASE_URL") or v.endswith("_URL")),
                None,
            )
            if not _key_vars:
                continue
            _models_url = (
                (_pp.models_url or (_pp.base_url.rstrip("/") + "/models"))
                if _pp.base_url else None
            )
            _hc = getattr(_pp, "supports_health_check", True)
            _static.append((_label, _key_vars, _models_url, _base_var, _hc))
    except Exception:
        pass
    return _static


def managed_scope_check() -> None:
    """Report the active managed scope (resolved dir + pinned key counts).

    Silent when no managed scope is present. When the managed directory was
    resolved from the HERMES_MANAGED_DIR override (rather than the system
    default), that is surfaced too — a redirected scope is the documented
    foot-gun (see docs/design/managed-scope.md §7) and an operator should see it.
    """
    try:
        from korra_cli import managed_scope
        managed_dir = managed_scope.get_managed_dir()
    except Exception:  # noqa: BLE001 — diagnostics must never crash
        return
    if managed_dir is None:
        return
    n_cfg = len(managed_scope.managed_config_keys())
    n_env = len(managed_scope.load_managed_env())
    check_ok(
        f'Управляемые настройки: {n_cfg} ключей настроек и {n_env} переменных среды закреплены в {managed_dir}'
    )
    if korra_env("KORRA_MANAGED_DIR", "").strip():
        check_info(f'Папка управляемых настроек: HERMES_MANAGED_DIR={managed_dir}')


def check_macos_tcc_grants() -> None:
    """Check macOS TCC grant persistence for a locally-built desktop bundle.

    TCC keys permission grants (Screen Recording, Full Disk Access,
    Accessibility, ...) to the app's code-signing requirement. A bundle
    signed with the pre-#73681 cdhash-pinned ad-hoc identity gets a new DR on
    every rebuild, so all grants silently stop matching — and the stale row
    keeps the System Settings toggle ON while macOS re-prompts on every
    capture (issue #86385).

    Post-#73681 builds pin ``designated => identifier "com.nousresearch.hermes"``
    (no cdhash), so new grants survive rebuilds — but grants made to older
    binaries remain stale until re-granted once. The stale state is not
    directly readable (TCC.db needs Full Disk Access), so this check reports
    the DR class and, when the DR is stable, prints the exact one-time repair.
    Silent on non-macOS and when no desktop bundle is installed.
    """
    if sys.platform != "darwin":
        return
    app = _desktop_app_bundle()
    if app is None:
        return
    dr = _macos_desktop_dr(app)
    if not dr:
        check_warn(
            'Проверка разрешений macOS TCC',
            '(не удалось прочитать требования подписи приложения)',
        )
        return
    # The DR string is the only readable signal — TCC.db itself needs Full
    # Disk Access. A cdhash anchor marks the pre-#73681 ad-hoc identity
    # (rebuild ⇒ new cdhash ⇒ stale grants); its absence marks identifier-
    # pinned. Treat the match as a proxy for the signing class, not a
    # contract on DR wording.
    if "cdhash" in dr.lower():
        check_warn(
            'Разрешения macOS TCC будут сбрасываться при каждом обновлении',
            'Старая сборка привязывает разрешения к хешу приложения, поэтому пересборка их сбрасывает. Выполните korra update для постоянной подписи, затем выдайте разрешения ещё раз.',
        )
        return
    if "certificate" in dr.lower():
        # Certificate-anchored DR (hermes desktop --setup-tcc-identity, or a
        # notarized release build): the strongest anchor TCC can key on.
        check_ok(
            'Подпись для разрешений macOS TCC стабильна',
            '(привязка к сертификату сохраняет разрешения после пересборки)',
        )
    else:
        check_ok(
            'Подпись для разрешений macOS TCC стабильна',
            '(привязка к ID сохраняет разрешения после пересборки; для привязки к сертификату: korra desktop --setup-tcc-identity)',
        )
    check_info(
        'Если macOS снова запрашивает уже включённое разрешение, сохранённая запись устарела. Выполните `tccutil reset ScreenCapture com.nousresearch.hermes` для нужного разрешения, включите его в настройках системы, затем полностью закройте и заново откройте Корру.'
    )


def _desktop_app_bundle() -> Path | None:
    """Locate the locally-built desktop app bundle, if any.

    Mirrors the install layout the self-updater produces
    (``apps/desktop/release/mac-<arch>/Hermes.app``) — the only layout whose
    ad-hoc re-signed bundle can invalidate TCC grants. When multiple arch
    trees coexist (stale cross-build), the newest wins, matching
    ``_desktop_packaged_executable``'s selection. ``/Applications/Hermes.app``
    is deliberately not probed: it is the separately-signed Hermes-Setup
    launcher (``com.nousresearch.hermes.setup``, certificate-anchored), whose
    grants are stable by construction and unaffected by rebuilds.
    """
    root = Path(__file__).resolve().parents[1]
    release_dir = root / "apps" / "desktop" / "release"
    candidates = [p for p in release_dir.glob("mac*/Hermes.app") if p.is_dir()]
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


def _macos_desktop_dr(app: Path) -> str | None:
    """Return the bundle's designated requirement string, or None on failure."""
    codesign = shutil.which("codesign")
    if not codesign:
        return None
    try:
        proc = subprocess.run(
            [codesign, "-d", "--requirements", "-", str(app)],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        # Never let a hanging codesign abort the whole doctor run — the
        # caller falls through to its "could not read" warning.
        return None
    if proc.returncode != 0:
        return None
    return (proc.stdout or "") + (proc.stderr or "")


def check_macos_tcc_anchor(should_fix: bool = False) -> None:
    """Report (and optionally install) the dylib-complete TCC anchor (#95596).

    Silent on non-macOS and for interpreters that are not uv-managed.  Never
    raises — a failed check must not crash doctor.  Install is gated by the
    module's pre-install boot probe, so ``--fix`` cannot brick the CLI.
    """
    try:
        from korra_cli import macos_tcc_anchor as tcc

        status, detail = tcc.tcc_anchor_state()
        if status == "skip":
            return
        if status == "active":
            check_ok('Постоянная подпись macOS TCC активна', f"({detail})")
            return
        if should_fix:
            anchored = tcc.ensure_tcc_anchor()
            if anchored is not None:
                check_ok('Постоянная подпись macOS TCC установлена', f"({anchored})")
                return
        check_warn(
            'Постоянная подпись macOS TCC отсутствует' if status == 'missing' else 'Постоянная подпись macOS TCC устарела',
            f"({detail})",
        )
    except Exception as e:  # diagnostics must never crash
        check_warn('Не удалось проверить постоянную подпись macOS TCC', f"({e})")


def check_macos_full_disk_access() -> None:
    """One-grant guidance: Full Disk Access silences every folder prompt.

    macOS TCC prompts per-category (Desktop, then Downloads, then Documents,
    ...), so first-run agents drip-feed permission dialogs as they touch each
    folder. ONE Full Disk Access grant covers all of them, permanently — and
    with the stable signing identities now in place (#73681/#95091/#95131),
    it survives updates too. This check probes whether the terminal context
    already has FDA and, when it doesn't, prints the exact one-switch setup
    with the System Settings deep link.

    Probe: readability of ``~/Library/Application Support/com.apple.TCC`` —
    the TCC database directory itself is FDA-gated, readable ONLY with the
    grant, and (critically) probing it with os.access/listdir does NOT
    trigger a prompt: TCC prompts fire for protected-CATEGORY paths (Desktop
    etc.), while the TCC dir simply returns EPERM without one. Silent on
    non-macOS.
    """
    if sys.platform != "darwin":
        return
    tcc_dir = Path.home() / "Library" / "Application Support" / "com.apple.TCC"
    try:
        os.listdir(tcc_dir)
        has_fda = True
    except PermissionError:
        has_fda = False
    except OSError:
        # Missing dir / other error: can't tell — stay silent rather than
        # nag on an indeterminate probe.
        return
    if has_fda:
        check_ok(
            'Полный доступ к диску macOS разрешён',
            '(отдельные запросы доступа к папкам не появятся)',
        )
        return
    check_info(
        'Чтобы macOS не спрашивала доступ к каждой папке, разрешите терминалу полный доступ к диску: Системные настройки → Конфиденциальность и безопасность → Полный доступ к диску. Открыть раздел командой: open "x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles". Включите терминал и приложение Корры, затем перезапустите их. Постоянная подпись сохраняет разрешения при обновлениях.'
    )


def run_doctor(args):
    """Run diagnostic checks."""
    should_fix = getattr(args, 'fix', False)
    ack_target = getattr(args, 'ack', None)

    # Doctor runs from the interactive CLI, so CLI-gated tool availability
    # checks (like cronjob management) should see the same context as `hermes`.
    korra_env_setdefault(os.environ, "KORRA_INTERACTIVE", "1")

    # Handle `hermes doctor --ack <id>` as a fast path. Persist the ack and
    # return without running the rest of the diagnostics — the user has
    # already seen the advisory and just wants to silence it.
    if ack_target:
        from korra_cli.security_advisories import (
            ADVISORIES,
            ack_advisory,
        )
        valid_ids = {a.id for a in ADVISORIES}
        if ack_target not in valid_ids:
            print(color(
                f"Неизвестный ID предупреждения: {ack_target!r}. Известные ID: {', '.join(sorted(valid_ids)) or '(нет)'}",
                Colors.RED,
            ))
            sys.exit(2)
        if ack_advisory(ack_target):
            print(color(
                f'  ✓ Предупреждение {ack_target} отмечено просмотренным и больше не появится при запуске.',
                Colors.GREEN,
            ))
        else:
            print(color(
                f'  ✗ Не удалось сохранить отметку для {ack_target}. Проверьте доступ на запись в config.yaml профиля.',
                Colors.RED,
            ))
            sys.exit(1)
        return

    issues = []
    manual_issues = []  # issues that can't be auto-fixed
    fixed_count = 0

    print()
    print(color("┌─────────────────────────────────────────────────────────┐", Colors.CYAN))
    print(color('│                 🩺 Диагностика Корры                    │', Colors.CYAN))
    print(color("└─────────────────────────────────────────────────────────┘", Colors.CYAN))

    _section('Предупреждения безопасности')
    try:
        from korra_cli.security_advisories import (
            detect_compromised,
            filter_unacked,
            full_remediation_text,
            get_acked_ids,
        )
        all_hits = detect_compromised()
        fresh_hits = filter_unacked(all_hits)
        if fresh_hits:
            for hit in fresh_hits:
                check_fail(
                    f"{hit.advisory.title}",
                    f"({hit.package}=={hit.installed_version})",
                )
                # Print the full remediation block, indented under the
                # check_fail header so it reads as a single section.
                for line in full_remediation_text(hit):
                    if line:
                        print(f"    {color(line, Colors.YELLOW)}")
                    else:
                        print()
                # Funnel into the action list so the summary block surfaces it
                # for users who scroll past the section.
                manual_issues.append(
                    f'Устраните предупреждение безопасности {hit.advisory.id}: удалите {hit.package}=={hit.installed_version}, замените ключи доступа, затем выполните korra doctor --ack {hit.advisory.id}.'
                )
            # Acked-but-still-installed: show as informational so the user
            # knows the package is still on disk after the ack.
            acked_ids = get_acked_ids()
            for h in all_hits:
                if h.advisory.id in acked_ids:
                    check_warn(
                        f'{h.package}=={h.installed_version} всё ещё установлен; предупреждение {h.advisory.id} отмечено просмотренным',
                    )
        else:
            check_ok('Действующих предупреждений безопасности нет')
    except Exception as e:
        # Never let a bug in the advisory check block the rest of doctor.
        check_warn(f'Не удалось проверить предупреждения безопасности: {e}')

    _section('Безопасность серверов MCP')
    try:
        from korra_cli.config import load_config
        from korra_cli.mcp_security import validate_mcp_server_entry

        servers = load_config().get("mcp_servers") or {}
        suspicious = 0
        if isinstance(servers, dict):
            for name, entry in sorted(servers.items()):
                if not isinstance(entry, dict):
                    continue
                issues_found = validate_mcp_server_entry(name, entry)
                if not issues_found:
                    continue
                suspicious += 1
                check_warn(f'У сервера MCP «{name}» подозрительная команда stdio', "; ".join(issues_found))
                manual_issues.append(
                    f'Проверьте или удалите mcp_servers.{name} в config.yaml. Замените ключи доступа, которые могли быть раскрыты.'
                )
        if suspicious == 0:
            check_ok('Подозрительных команд stdio для MCP нет')
    except Exception as e:
        check_warn(f'Не удалось проверить безопасность MCP: {e}')
    
    _section('Окружение Python')
    py_version = sys.version_info
    if py_version >= (3, 11):
        check_ok(f"Python {py_version.major}.{py_version.minor}.{py_version.micro}")
    elif py_version >= (3, 10):
        check_ok(f"Python {py_version.major}.{py_version.minor}.{py_version.micro}")
        check_warn('Для инструментов обучения RL рекомендуется Python 3.11 или новее; tinker требует минимум 3.11')
    elif py_version >= (3, 8):
        check_warn(f"Python {py_version.major}.{py_version.minor}.{py_version.micro}", '(рекомендуется 3.10 или новее)')
    else:
        _fail_and_issue(
            f"Python {py_version.major}.{py_version.minor}.{py_version.micro}",
            '(требуется 3.10 или новее)',
            'Обновите Python до 3.10 или новее',
            issues,
        )

    # Linked SQLite library (issue #69784): version + source id matter independently
    # of the Python minor — uv's python-build-standalone can keep a vulnerable
    # SQLite across Python upgrades.
    try:
        import sqlite3
        from korra_state import is_sqlite_wal_reset_vulnerable, sqlite_source_id

        _sqlite_ver = sqlite3.sqlite_version
        _sqlite_src = sqlite_source_id()
        _sqlite_src_short = (
            (_sqlite_src[:48] + "…") if len(_sqlite_src) > 48 else _sqlite_src
        )
        if is_sqlite_wal_reset_vulnerable():
            # Warn-only: Hermes already refuses to enable WAL on fresh DBs.
            # Do not append to ``issues`` because runtime repair remains
            # best-effort and unsupported installs may need manual action.
            check_warn(
                f'SQLite {_sqlite_ver}: ошибка сброса WAL',
                _sqlite_upgrade_hint(),
            )
        else:
            check_ok(f"SQLite {_sqlite_ver}")
        if _sqlite_src_short:
            check_info(f'ID исходников SQLite: {_sqlite_src_short}')
        _report_database_journal_modes()
    except Exception as e:
        check_warn(f'Не удалось проверить версию SQLite: {e}')
    # Check if in virtual environment
    in_venv = sys.prefix != sys.base_prefix
    if in_venv:
        check_ok('Виртуальное окружение активно')
    else:
        check_warn('Виртуальное окружение не используется', '(рекомендуется)')

    # macOS TCC interpreter anchor (#95596): dylib-complete re-land of the
    # mechanism reverted in #95563. Silent on non-macOS.
    check_macos_tcc_anchor(should_fix=should_fix)

    # macOS Full Disk Access (issue #52010 follow-up): one grant silences
    # every per-folder prompt permanently. Silent on non-macOS.
    check_macos_full_disk_access()

    # Detect drift between pyproject.toml and korra_cli/__init__.py versions
    # (a git conflict resolution can silently revert one but not the other).
    _check_version_consistency(issues)

    # macOS TCC grant persistence (issue #86385): a locally-built desktop
    # bundle whose DR is cdhash-pinned loses every permission grant on each
    # rebuild; a post-#73681 identifier-pinned DR survives, but grants made
    # to older binaries stay stale (toggle shows ON while macOS re-prompts).
    check_macos_tcc_grants()

    _section('Сертификаты SSL / CA')
    check_certificates(should_fix=should_fix, issues=manual_issues)

    _section('Обязательные пакеты')
    required_packages = [
        ("openai", "OpenAI SDK"),
        ("rich", 'Rich (интерфейс терминала)'),
        ("dotenv", "python-dotenv"),
        ("yaml", "PyYAML"),
        ("httpx", "HTTPX"),
    ]
    
    optional_packages = [
        ("croniter", 'Croniter (выражения расписания)'),
        ("telegram", "python-telegram-bot"),
        ("discord", "discord.py"),
    ]
    
    for module, name in required_packages:
        try:
            __import__(module)
            check_ok(name)
        except ImportError:
            _fail_and_issue(name, '(отсутствует)', f'Установите {name}: {_python_install_cmd()} {module}', issues)
    
    for module, name in optional_packages:
        try:
            __import__(module)
            check_ok(name, '(необязательно)')
        except ImportError:
            check_warn(name, '(необязательно, не установлен)')
    
    _section('Файлы настроек')
    # Managed scope (administrator-pinned config/env), when present.
    managed_scope_check()
    # Check ~/.hermes/.env (primary location for user config)
    env_path = HERMES_HOME / '.env'
    if env_path.exists():
        check_ok(f'Файл {_DHH}/.env существует')
        
        # Prefer UTF-8 (.env is written as UTF-8 elsewhere). Fall back to
        # latin-1 for Windows Notepad/cp1252 files that are not valid UTF-8 —
        # matches korra_cli.env_loader._load_dotenv_with_fallback.
        try:
            content = env_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            content = env_path.read_text(encoding="latin-1")
        if _has_provider_env_config(content):
            check_ok('Ключ API или собственный адрес сервера настроен')
        else:
            check_warn(f'В {_DHH}/.env не найден ключ API')
            issues.append('Настройте ключи API командой korra setup')
    else:
        # Also check project root as fallback
        fallback_env = PROJECT_ROOT / '.env'
        if fallback_env.exists():
            check_ok('Файл .env существует в папке проекта')
        else:
            check_fail(f'Файл {_DHH}/.env отсутствует')
            if should_fix:
                env_path.parent.mkdir(parents=True, exist_ok=True)
                env_path.touch()
                # .env holds API keys — restrict to owner-only access from
                # creation. touch() obeys umask which is commonly 0o022,
                # leaving the file world-readable; tighten explicitly.
                try:
                    os.chmod(str(env_path), 0o600)
                except OSError:
                    pass
                check_ok(f'Создан пустой файл {_DHH}/.env')
                check_info('Настройте ключи API командой korra setup')
                fixed_count += 1
            else:
                check_info('Создайте файл командой korra setup')
                issues.append('Создайте .env командой korra setup')
    
    # Check ~/.hermes/config.yaml (primary) or project cli-config.yaml (fallback)
    config_path = HERMES_HOME / 'config.yaml'
    if config_path.exists():
        check_ok(f'Файл {_DHH}/config.yaml существует')

        # Validate model.provider and model.default values
        try:
            # Raw-file diagnostic: inspects what the user actually wrote.
            from korra_cli.config import read_user_config_raw
            cfg = read_user_config_raw(config_path)
            model_section = cfg.get("model") or {}
            provider_raw = (model_section.get("provider") or "").strip()
            provider = provider_raw.lower()
            default_model = (model_section.get("default") or model_section.get("model") or "").strip()

            known_providers: set = set()
            try:
                from korra_cli.auth import (
                    PROVIDER_REGISTRY,
                    resolve_provider as _resolve_auth_provider,
                )
                known_providers = set(PROVIDER_REGISTRY.keys()) | {"openrouter", "custom", "auto", "moa"}
            except Exception:
                _resolve_auth_provider = None
                pass
            try:
                from korra_cli.config import get_compatible_custom_providers as _compatible_custom_providers
                from korra_cli.providers import (
                    custom_provider_aliases as _custom_provider_aliases,
                    normalize_provider as _normalize_catalog_provider,
                    resolve_provider_full as _resolve_provider_full,
                )
            except Exception:
                _compatible_custom_providers = None
                _custom_provider_aliases = None
                _normalize_catalog_provider = None
                _resolve_provider_full = None

            custom_providers = []
            if _compatible_custom_providers is not None:
                try:
                    custom_providers = _compatible_custom_providers(cfg)
                except Exception:
                    custom_providers = []

            user_providers = cfg.get("providers")
            if isinstance(user_providers, dict):
                from korra_cli.config import is_provider_enabled
                known_providers.update(
                    str(name).strip().lower()
                    for name, prov_cfg in user_providers.items()
                    if str(name).strip() and is_provider_enabled(prov_cfg)
                )
            for entry in custom_providers:
                if not isinstance(entry, dict):
                    continue
                name = str(entry.get("name") or "").strip()
                provider_key = str(entry.get("provider_key") or "").strip()
                if name and _custom_provider_aliases is not None:
                    known_providers.update(
                        _custom_provider_aliases(name, provider_key)
                    )

            valid_provider_ids = set(known_providers)
            provider_ids_to_accept = {provider} if provider else set()
            if _normalize_catalog_provider is not None:
                for known_provider in known_providers:
                    try:
                        valid_provider_ids.add(_normalize_catalog_provider(known_provider))
                    except Exception:
                        continue

            runtime_provider = provider
            if (
                provider
                and _resolve_auth_provider is not None
                and provider not in {"auto", "custom"}
            ):
                try:
                    runtime_provider = _resolve_auth_provider(provider)
                    provider_ids_to_accept.add(runtime_provider)
                except Exception:
                    runtime_provider = provider

            catalog_provider = provider
            if (
                provider
                and _resolve_provider_full is not None
                and provider not in {"auto", "custom"}
            ):
                provider_def = _resolve_provider_full(provider, user_providers, custom_providers)
                catalog_provider = provider_def.id if provider_def is not None else None
                if catalog_provider is not None:
                    provider_ids_to_accept.add(catalog_provider)

            if provider and provider != "auto":
                if catalog_provider is None or (
                    known_providers
                    and not (provider_ids_to_accept & valid_provider_ids)
                ):
                    known_list = ", ".join(sorted(known_providers)) if known_providers else "(unavailable)"
                    _fail_and_issue(
                        f'Неизвестный провайдер model.provider: «{provider_raw}»',
                        f'(известные: {known_list})',
                        (
                            f'Неизвестный model.provider «{provider_raw}». Допустимые провайдеры: {known_list}. Исправление: korra config set model.provider <valid_provider>'
                        ),
                        issues,
                    )

            # Warn if model is set to a provider-prefixed name on a provider that doesn't use them.
            # Vendor/model slugs are valid on aggregator-style providers and on any custom
            # provider — bare "custom" or a named "custom:<name>" that fronts an OpenAI-compatible
            # aggregator (e.g. custom:hpc-ai serving deepseek/deepseek-v4-flash) requires the prefix.
            provider_for_policy = runtime_provider or catalog_provider
            provider_policy_id = str(provider_for_policy or "").strip().lower()
            providers_accepting_vendor_slugs = {
                "openrouter",
                "auto",
                "ai-gateway",
                "kilocode",
                "opencode-zen",
                "huggingface",
                "lmstudio",
                "nous",
                "nvidia",
                # Fireworks' native model IDs are slash-form
                # (accounts/fireworks/models/... and .../routers/...), so a "/"
                # is expected, not an aggregator vendor prefix.
                "fireworks",
                # DeepInfra is an aggregator-style gateway: its catalog
                # is exclusively ``vendor/model`` slugs (Qwen/Qwen3.5-…,
                # meta-llama/Llama-3-…, anthropic/claude-opus-4-7, …).
                "deepinfra",
            }
            provider_accepts_vendor_slug = (
                provider_policy_id in providers_accepting_vendor_slugs
                or provider_policy_id == "custom"
                or provider_policy_id.startswith("custom:")
            )
            if (
                default_model
                and "/" in default_model
                and provider_policy_id
                and not provider_accepts_vendor_slug
            ):
                check_warn(
                    f'model.default «{default_model}» содержит префикс поставщика, но выбран провайдер «{provider_raw}»',
                    '(имена с префиксом поставщика используются агрегаторами, например openrouter)',
                )
                issues.append(
                    f'У model.default «{default_model}» есть префикс поставщика, а model.provider — «{provider_raw}». Выберите openrouter в model.provider или уберите префикс.'
                )

            # Check credentials for the configured provider.
            # Limit to API-key providers in PROVIDER_REGISTRY — other provider
            # types (OAuth, SDK, anthropic/custom/auto) have their own env-var
            # checks elsewhere in doctor, and get_auth_status() returns a bare
            # {logged_in: False} for anything it doesn't explicitly dispatch,
            # which would produce false positives.
            if runtime_provider and runtime_provider not in ("auto", "custom"):
                try:
                    if runtime_provider == "openrouter":
                        from korra_cli.config import get_env_value

                        configured = bool(
                            str(get_env_value("OPENROUTER_API_KEY") or "").strip()
                            or str(get_env_value("OPENAI_API_KEY") or "").strip()
                        )
                    else:
                        from korra_cli.auth import PROVIDER_REGISTRY, get_auth_status

                        pconfig = PROVIDER_REGISTRY.get(runtime_provider)
                        configured = True
                        if pconfig and getattr(pconfig, "auth_type", "") == "api_key":
                            status = get_auth_status(runtime_provider) or {}
                            configured = bool(
                                status.get("configured")
                                or status.get("logged_in")
                                or status.get("api_key")
                            )
                    if not configured:
                        _fail_and_issue(
                            f'Выбран model.provider «{runtime_provider}», но ключ API не настроен',
                            '(проверьте .env профиля или выполните korra setup)',
                            (
                                f'Нет данных входа для провайдера «{runtime_provider}». Выполните korra setup, задайте его ключ API в {_DHH}/.env либо смените провайдера: korra config set model.provider <name>.'
                            ),
                            issues,
                        )
                except Exception:
                    pass

        except Exception as e:
            check_warn('Не удалось проверить настройки модели и провайдера', f"({e})")
    else:
        fallback_config = PROJECT_ROOT / 'cli-config.yaml'
        if fallback_config.exists():
            check_ok('Файл cli-config.yaml существует в папке проекта')
        else:
            if should_fix:
                config_path.parent.mkdir(parents=True, exist_ok=True)
                example_config = PROJECT_ROOT / 'cli-config.yaml.example'
                if example_config.exists():
                    shutil.copy2(str(example_config), str(config_path))
                    check_ok(f'Создан {_DHH}/config.yaml по шаблону cli-config.yaml.example')
                else:
                    from korra_cli.config import DEFAULT_CONFIG, save_config
                    save_config(DEFAULT_CONFIG)
                    check_ok(f'Создан {_DHH}/config.yaml с исходными настройками')
                fixed_count += 1
            else:
                check_warn('Файл config.yaml не найден', '(используются исходные настройки)')

    # Check config version and stale keys
    config_path = HERMES_HOME / 'config.yaml'
    if config_path.exists():
        try:
            from korra_cli.config import check_config_version, migrate_config
            current_ver, latest_ver = check_config_version()
            if current_ver < latest_ver:
                check_warn(
                    f'Версия настроек устарела: v{current_ver} → v{latest_ver}',
                    '(доступны новые параметры)'
                )
                if should_fix:
                    try:
                        migrate_config(interactive=False, quiet=False)
                        check_ok('Формат настроек обновлён до последней версии')
                        fixed_count += 1
                    except Exception as mig_err:
                        check_warn(f'Не удалось автоматически обновить формат: {mig_err}')
                        issues.append('Обновите формат настроек командой korra setup')
                else:
                    issues.append('Обновите формат настроек: korra doctor --fix или korra setup')
            else:
                check_ok(f'Версия настроек актуальна: v{current_ver}')
        except Exception:
            pass

        # Detect stale root-level model keys (known bug source — PR #4329)
        try:
            # Raw-file diagnostic: stale-key detection must see the raw file.
            from korra_cli.config import read_user_config_raw
            raw_config = read_user_config_raw(config_path)
            stale_root_keys = [k for k in ("provider", "base_url") if k in raw_config and isinstance(raw_config[k], str)]
            if stale_root_keys:
                check_warn(
                    f"Устаревшие ключи в корне настроек: {', '.join(stale_root_keys)}",
                    '(должны находиться в разделе model:)'
                )
                if should_fix:
                    # Coerce scalar/None ``model:`` into a dict before mutation —
                    # ``setdefault("model", {})`` would return an existing scalar
                    # and then ``model_section[k] = ...`` would raise TypeError.
                    raw_model = raw_config.get("model")
                    if isinstance(raw_model, dict):
                        model_section = raw_model
                    elif isinstance(raw_model, str) and raw_model.strip():
                        model_section = {"default": raw_model.strip()}
                        raw_config["model"] = model_section
                    else:
                        model_section = {}
                        raw_config["model"] = model_section
                    for k in stale_root_keys:
                        if not model_section.get(k):
                            model_section[k] = raw_config.pop(k)
                        else:
                            raw_config.pop(k)
                    from korra_cli.config import atomic_config_write
                    atomic_config_write(config_path, raw_config)
                    check_ok('Устаревшие ключи перенесены из корня в раздел model')
                    fixed_count += 1
                else:
                    issues.append('Устаревшие provider/base_url в корне config.yaml; выполните korra doctor --fix')
        except Exception:
            pass

        # Detect stale HERMES_MAX_ITERATIONS ghost in .env shadowing
        # agent.max_turns in config.yaml (issue #17534). The setup wizard
        # used to dual-write the iteration budget to both stores; users who
        # later edit only config.yaml are left with a .env ghost. The gateway
        # bridge normally derives HERMES_MAX_ITERATIONS from agent.max_turns
        # at startup, but if that bridge bails (any earlier config-parse
        # error), the stale .env value silently wins and the agent runs at the
        # wrong budget — e.g. config says 400 but the activity line reads N/90.
        # Read the .env FILE directly (load_env), not get_env_value/os.environ,
        # which the startup bridge may already have overridden.
        try:
            from korra_cli.config import load_env, read_user_config_raw, remove_env_value
            # Raw-file diagnostic: drift check against the raw file.
            raw_config = read_user_config_raw(config_path)
            agent_cfg = raw_config.get("agent")
            cfg_max_turns = (
                agent_cfg.get("max_turns")
                if isinstance(agent_cfg, dict)
                else None
            )
            # Legacy root-level key counts too.
            if cfg_max_turns is None:
                cfg_max_turns = raw_config.get("max_turns")
            env_ghost = korra_env("KORRA_MAX_ITERATIONS", env=load_env())
            drift = (
                cfg_max_turns is not None
                and env_ghost is not None
                and str(cfg_max_turns).strip() != str(env_ghost).strip()
            )
            if drift:
                check_warn(
                    f'HERMES_MAX_ITERATIONS={env_ghost} в .env заменяет agent.max_turns={cfg_max_turns} из config.yaml',
                    '(устаревшая запись от прежнего запуска korra setup)',
                )
                if should_fix:
                    if remove_env_value("HERMES_MAX_ITERATIONS"):
                        check_ok(
                            f'Устаревший HERMES_MAX_ITERATIONS удалён из .env; теперь действует agent.max_turns={cfg_max_turns} из config.yaml'
                        )
                        fixed_count += 1
                    else:
                        check_warn('Не удалось удалить HERMES_MAX_ITERATIONS из .env')
                        manual_issues.append(
                            f'Удалите строку HERMES_MAX_ITERATIONS из {_DHH}/.env вручную. Используется agent.max_turns из config.yaml.'
                        )
                else:
                    issues.append(
                        'Устаревший HERMES_MAX_ITERATIONS в .env перекрывает config.yaml; выполните korra doctor --fix'
                    )
        except Exception:
            pass

        # Surface deprecated/legacy config keys and env vars (warn-only).
        # Migrations may still live in config.py version steps; doctor does
        # not auto-delete here — only tells the user the modern replacement.
        try:
            from korra_cli.config import load_env as _load_env_depr
            from korra_cli.config import read_user_config_raw as _read_raw_depr

            # Raw-file diagnostic: deprecation sweep inspects the raw file.
            _raw_for_depr = _read_raw_depr(config_path)
            # Prefer the on-disk .env so bridged process env (e.g. TERMINAL_CWD
            # from terminal.cwd) does not false-positive.
            try:
                _env_for_depr = _load_env_depr()
            except Exception:
                _env_for_depr = {}
            report_deprecated_config_and_env(_raw_for_depr, _env_for_depr)
        except Exception:
            pass

        # Validate config structure (catches malformed custom_providers, etc.)
        try:
            from korra_cli.config import validate_config_structure
            config_issues = validate_config_structure()
            if config_issues:
                _section('Структура настроек')
                for ci in config_issues:
                    if ci.severity == "error":
                        check_fail(ci.message)
                    else:
                        check_warn(ci.message)
                    # Show the hint indented
                    for hint_line in ci.hint.splitlines():
                        check_info(hint_line)
                    issues.append(ci.message)
        except Exception:
            pass

    if not config_path.exists():
        # No config.yaml — still surface deprecated env vars from .env.
        try:
            from korra_cli.config import load_env as _load_env_depr

            try:
                _env_for_depr = _load_env_depr()
            except Exception:
                _env_for_depr = {}
            report_deprecated_config_and_env({}, _env_for_depr)
        except Exception:
            pass

    _section('Модели xAI, снятые с поддержки 15 мая 2026 года')

    try:
        from korra_cli.config import load_config
        from korra_cli.xai_retirement import (
            MIGRATION_GUIDE_URL,
            find_retired_xai_refs,
            format_issue,
        )

        _xai_cfg = load_config()
        retired_refs = find_retired_xai_refs(_xai_cfg)
        if not retired_refs:
            check_ok('В настройках нет снятых с поддержки моделей xAI')
        else:
            for ref in retired_refs:
                check_warn(format_issue(ref))
            check_info(f'Инструкция по переходу: {MIGRATION_GUIDE_URL}')
            manual_issues.append(
                f'Замените устаревшие модели xAI в config.yaml ({len(retired_refs)} ссылок); см. {MIGRATION_GUIDE_URL}'
            )
    except Exception as _xai_check_err:
        check_warn('Проверка устаревших моделей xAI пропущена', f"({_xai_check_err})")

    _section('Вход у провайдеров')

    try:
        from korra_cli.auth import (
            get_nous_auth_status_local,
            get_codex_auth_status,
            get_minimax_oauth_auth_status,
        )

        # Read-only display: refresh-free snapshot — doctor must never
        # trigger an OAuth refresh as a side effect of a health check.
        nous_status = get_nous_auth_status_local()
        if nous_status.get("logged_in"):
            check_ok('Вход Nous Portal', '(вход выполнен)')
        else:
            check_warn('Вход Nous Portal', '(вход не выполнен)')

        codex_status = get_codex_auth_status()
        if codex_status.get("logged_in"):
            check_ok('Вход OpenAI Codex', '(вход выполнен)')
        else:
            check_warn('Вход OpenAI Codex', '(вход не выполнен)')
            if codex_status.get("error"):
                check_info(codex_status["error"])
            # Native OAuth uses Hermes' own device-code flow — the Codex CLI is
            # only needed to import existing tokens from ~/.codex/auth.json.
            # Attach the hint to the Codex auth row so it doesn't read as
            # remediation for whichever provider happens to print next (#27975).
            if not _safe_which("codex"):
                check_info(
                    'Codex CLI не установлен; он необязателен и нужен только для импорта токенов уже выполненного входа'
                )

        minimax_status = get_minimax_oauth_auth_status()
        if minimax_status.get("logged_in"):
            region = minimax_status.get("region", "global")
            check_ok("MiniMax OAuth", f'(вход выполнен, регион {region})')
        else:
            check_warn("MiniMax OAuth", '(вход не выполнен)')
    except Exception as e:
        check_warn('Состояние входа у провайдера', f'(не удалось проверить: {e})')

    # xAI OAuth — separate try/except so an import failure here cannot
    # disrupt the already-printed Nous/Codex/Gemini/MiniMax rows above.
    try:
        from korra_cli.auth import get_xai_oauth_auth_status
        xai_oauth_status = get_xai_oauth_auth_status() or {}
        if xai_oauth_status.get("logged_in"):
            check_ok("xAI OAuth", '(вход выполнен)')
        else:
            check_warn("xAI OAuth", '(вход не выполнен)')
            if xai_oauth_status.get("error"):
                check_info(xai_oauth_status["error"])
    except Exception:
        pass

    _section('Папки данных')
    hermes_home = HERMES_HOME
    if hermes_home.exists():
        check_ok(f'Папка {_DHH} существует')
    elif should_fix:
        hermes_home.mkdir(parents=True, exist_ok=True)
        check_ok(f'Создана папка {_DHH}')
        fixed_count += 1
    else:
        check_warn(f'{_DHH} не найден', '(создастся при первом использовании)')
    
    # Check expected subdirectories
    expected_subdirs = ["cron", "sessions", "logs", "skills", "memories"]
    for subdir_name in expected_subdirs:
        subdir_path = hermes_home / subdir_name
        if subdir_path.exists():
            check_ok(f'Папка {_DHH}/{subdir_name}/ существует')
        elif should_fix:
            subdir_path.mkdir(parents=True, exist_ok=True)
            check_ok(f'Создана папка {_DHH}/{subdir_name}/')
            fixed_count += 1
        else:
            check_warn(f'Папка {_DHH}/{subdir_name}/ не найдена', '(создастся при первом использовании)')
    
    # Check for SOUL.md persona file
    soul_path = hermes_home / "SOUL.md"
    if soul_path.exists():
        content = soul_path.read_text(encoding="utf-8").strip()
        # Check if it's just the template comments (no real content)
        lines = [l for l in content.splitlines() if l.strip() and not l.strip().startswith(("<!--", "-->", "#"))]
        if lines:
            check_ok(f'Файл {_DHH}/SOUL.md существует; характер общения настроен')
        else:
            check_info(f'Файл {_DHH}/SOUL.md пуст. Заполните его, чтобы настроить характер общения.')
    else:
        check_warn(f'Файл {_DHH}/SOUL.md не найден', '(создайте его, чтобы задать характер общения Корры)')
        if should_fix:
            soul_path.parent.mkdir(parents=True, exist_ok=True)
            # Korra: сеем тот же канонический текст, что и первый запуск
            # (_ensure_default_soul_md), а не отдельную персону «Hermes».
            from korra_cli.default_soul import DEFAULT_SOUL_MD

            soul_path.write_text(DEFAULT_SOUL_MD + "\n", encoding="utf-8")
            check_ok(f'Создан файл {_DHH}/SOUL.md с базовым шаблоном')
            fixed_count += 1
    
    # Check memory directory
    memories_dir = hermes_home / "memories"
    if memories_dir.exists():
        check_ok(f'Папка {_DHH}/memories/ существует')
        memory_file = memories_dir / "MEMORY.md"
        user_file = memories_dir / "USER.md"
        if memory_file.exists():
            size = len(memory_file.read_text(encoding="utf-8").strip())
            check_ok(f'MEMORY.md существует: {size} символов')
        else:
            check_info('MEMORY.md ещё не создан; появится при первой записи агента в память')
        if user_file.exists():
            size = len(user_file.read_text(encoding="utf-8").strip())
            check_ok(f'USER.md существует: {size} символов')
        else:
            check_info('USER.md ещё не создан; появится при первой записи агента в память')
    else:
        check_warn(f'Папка {_DHH}/memories/ не найдена', '(создастся при первом использовании)')
        if should_fix:
            memories_dir.mkdir(parents=True, exist_ok=True)
            check_ok(f'Создана папка {_DHH}/memories/')
            fixed_count += 1
    
    # Check SQLite session store
    state_db_path = hermes_home / "state.db"
    if state_db_path.exists():
        try:
            import sqlite3
            conn = sqlite3.connect(str(state_db_path))
            cursor = conn.execute("SELECT COUNT(*) FROM sessions")
            count = cursor.fetchone()[0]
            conn.close()
            check_ok(f'База {_DHH}/state.db существует: {count} бесед')

            # FTS write-health probe (#50502): `SELECT COUNT(*)` above succeeds
            # even when the FTS index is corrupt and every message write fails
            # through the triggers. `_db_opens_cleanly` now drives a rolled-back
            # write so this otherwise-silent corruption class is surfaced (and
            # repaired in place with --fix).
            from korra_state import _db_opens_cleanly, repair_state_db_schema

            _write_reason = _db_opens_cleanly(state_db_path)
            if _write_reason is not None:
                check_warn(
                    f'Проверка записи в {_DHH}/state.db не прошла; возможно, повреждён поисковый индекс FTS',
                    f"({_write_reason})",
                )
                if should_fix:
                    report = repair_state_db_schema(state_db_path)
                    if report.get("repaired"):
                        backup_name = (
                            Path(report["backup_path"]).name
                            if report.get("backup_path") else "n/a"
                        )
                        check_ok(
                            'Запись в поисковый индекс FTS базы state.db восстановлена',
                            f"(способ: {report.get('strategy')}; копия: {backup_name})",
                        )
                        fixed_count += 1
                    else:
                        check_warn(
                            'Не удалось автоматически восстановить запись в FTS базы state.db',
                            f"({report.get('error')}; копия: {report.get('backup_path')})",
                        )
                        issues.append(
                            'Повреждён FTS в state.db, автоматическое восстановление не помогло. Восстановите резервную копию рядом с state.db.'
                        )
                else:
                    issues.append(
                        'Повреждён поисковый индекс FTS в state.db. Восстановите его: korra doctor --fix или korra sessions repair.'
                    )
        except Exception as e:
            from korra_state import is_malformed_db_error, repair_state_db_schema

            if is_malformed_db_error(e):
                # sqlite_master itself is malformed (e.g. duplicate
                # messages_fts) — every statement fails before it runs, so
                # this is NOT a plain FTS-index rebuild. Repair sqlite_master
                # in place (backup first; sessions/messages preserved).
                check_warn(
                    f'Повреждена структура {_DHH}/state.db; беседы скрыты до восстановления',
                    f"({e})",
                )
                if should_fix:
                    report = repair_state_db_schema(state_db_path)
                    if report.get("repaired"):
                        try:
                            conn = sqlite3.connect(str(state_db_path))
                            count = conn.execute(
                                "SELECT COUNT(*) FROM sessions"
                            ).fetchone()[0]
                            conn.close()
                        except Exception:
                            count = "?"
                        backup_name = (
                            Path(report["backup_path"]).name
                            if report.get("backup_path") else "n/a"
                        )
                        check_ok(
                            f'Структура state.db восстановлена; возвращено бесед: {count}',
                            f"(способ: {report.get('strategy')}; копия: {backup_name})",
                        )
                        fixed_count += 1
                    else:
                        check_warn(
                            'Не удалось автоматически восстановить структуру state.db',
                            f"({report.get('error')}; копия: {report.get('backup_path')})",
                        )
                        issues.append(
                            'Повреждена структура state.db, автоматическое восстановление не помогло. Восстановите резервную копию рядом с state.db.'
                        )
                else:
                    issues.append(
                        'Повреждена структура state.db. Для возврата бесед выполните korra doctor --fix или korra sessions repair.'
                    )
            else:
                check_warn(f'Файл {_DHH}/state.db существует, но обнаружены проблемы: {e}')

        # Health/stats snapshot (#statedb-visibility): a multi-GB state.db
        # with a runaway WAL was previously invisible to every Hermes
        # surface. Strictly read-only (mode=ro) so it is safe against a
        # live DB held by the gateway; any failure degrades to one info
        # line rather than failing doctor.
        try:
            from korra_state import collect_state_db_stats, count_db_holders

            _db_stats = collect_state_db_stats(state_db_path)
            _db_holders = count_db_holders(state_db_path)
            for _kind, _text, _detail in _render_state_db_stats(
                _db_stats, holders=_db_holders
            ):
                if _kind == "warn":
                    check_warn(_text, _detail)
                    if "auto_prune" in _detail:
                        issues.append(
                            'База state.db большая; включите sessions.auto_prune в config.yaml'
                            + (
                                ' и выполните korra sessions optimize-storage при остановленном шлюзе'
                                if "optimize-storage" in _detail else ""
                            )
                        )
                else:
                    check_info(_text + (f" {_detail}" if _detail else ""))
        except Exception as _stats_exc:
            check_info(f'Статистика state.db недоступна: {_stats_exc}')
    else:
        check_info(f'Файл {_DHH}/state.db ещё не создан; появится при первой беседе')

    # Check WAL file size (unbounded growth indicates missed checkpoints)
    wal_path = hermes_home / "state.db-wal"
    if wal_path.exists():
        try:
            wal_size = wal_path.stat().st_size
            if wal_size > 50 * 1024 * 1024:  # 50 MB
                check_warn(
                    f'Файл WAL большой: {wal_size // (1024 * 1024)} МБ',
                    '(возможно, давно не выполнялось сохранение журнала)'
                )
                if should_fix:
                    import sqlite3
                    conn = sqlite3.connect(str(state_db_path))
                    conn.execute("PRAGMA wal_checkpoint(PASSIVE)")
                    conn.close()
                    new_size = wal_path.stat().st_size if wal_path.exists() else 0
                    check_ok(f'Журнал WAL сохранён: {wal_size // 1024} КБ → {new_size // 1024} КБ')
                    fixed_count += 1
                else:
                    issues.append('Файл WAL большой; выполните korra doctor --fix для сохранения журнала')
            elif wal_size > 10 * 1024 * 1024:  # 10 MB
                check_info(f'Размер WAL — {wal_size // (1024 * 1024)} МБ; для активных бесед это нормально')
        except Exception:
            pass

    _check_gateway_service_linger(issues)
    _check_s6_supervision(issues)

    if sys.platform != "win32":
        _section('Установка команды')
        # Determine the venv entry point location
        _venv_bin = None
        for _venv_name in ("venv", ".venv"):
            _candidate = PROJECT_ROOT / _venv_name / "bin" / "hermes"
            if _candidate.exists():
                _venv_bin = _candidate
                break

        # Determine the expected command link directory (mirrors install.sh logic)
        _prefix = os.environ.get("PREFIX", "")
        _is_termux_env = bool(os.environ.get("TERMUX_VERSION")) or "com.termux/files/usr" in _prefix
        if _is_termux_env and _prefix:
            _cmd_link_dir = Path(_prefix) / "bin"
            _cmd_link_display = "$PREFIX/bin"
        else:
            _cmd_link_dir = Path.home() / ".local" / "bin"
            _cmd_link_display = "~/.local/bin"
        _cmd_link = _cmd_link_dir / "hermes"

        if _venv_bin is None:
            check_warn(
                'Команда в виртуальном окружении не найдена',
                "(команды нет в venv/bin/ или .venv/bin/; переустановите через pip install -e '.[all]')"
            )
            manual_issues.append(
                f"Переустановите команду: cd {PROJECT_ROOT} && source venv/bin/activate && pip install -e '.[all]'"
            )
        else:
            check_ok(f'Команда виртуального окружения существует: {_venv_bin.relative_to(PROJECT_ROOT)}')

            # Check the symlink at the command link location
            if _cmd_link.is_symlink():
                _target = _cmd_link.resolve()
                _expected = _venv_bin.resolve()
                if _target == _expected:
                    check_ok(f'{_cmd_link_display}/hermes → верная цель ссылки')
                else:
                    check_warn(
                        f'{_cmd_link_display}/hermes указывает не туда',
                        f'(→ {_target}, ожидается → {_expected})'
                    )
                    if should_fix:
                        _cmd_link.unlink()
                        _cmd_link.symlink_to(_venv_bin)
                        check_ok(f'Ссылка исправлена: {_cmd_link_display}/hermes → {_venv_bin}')
                        fixed_count += 1
                    else:
                        issues.append(f'Повреждена ссылка {_cmd_link_display}/hermes; выполните korra doctor --fix')
            elif _cmd_link.exists():
                # It's a regular file, not a symlink — possibly a wrapper script
                check_ok(f'{_cmd_link_display}/hermes существует и не является ссылкой')
            else:
                check_fail(
                    f'{_cmd_link_display}/hermes не найден',
                    '(команда совместимости может не работать вне виртуального окружения)'
                )
                if should_fix:
                    _cmd_link_dir.mkdir(parents=True, exist_ok=True)
                    _cmd_link.symlink_to(_venv_bin)
                    check_ok(f'Создана ссылка: {_cmd_link_display}/hermes → {_venv_bin}')
                    fixed_count += 1

                    # Check if the link dir is on PATH
                    _path_dirs = os.environ.get("PATH", "").split(os.pathsep)
                    if str(_cmd_link_dir) not in _path_dirs:
                        check_warn(
                            f'Папка {_cmd_link_display} не входит в PATH',
                            '(добавьте в настройки оболочки: export PATH="$HOME/.local/bin:$PATH")'
                        )
                        manual_issues.append(f'Добавьте {_cmd_link_display} в PATH')
                else:
                    issues.append(f'Нет ссылки {_cmd_link_display}/hermes; выполните korra doctor --fix')

    _section('Внешние инструменты')
    # Git
    if _safe_which("git"):
        check_ok("git")
    else:
        check_warn('git не найден', '(необязательно)')
    
    # ripgrep (optional, for faster file search)
    if _safe_which("rg"):
        check_ok("ripgrep (rg)", '(ускоренный поиск файлов)')
    else:
        check_warn('ripgrep (rg) не найден', '(для поиска используется grep)')
        check_info(f"Для ускорения поиска установите: {_system_package_install_cmd('ripgrep')}")
    
    # Docker (optional)
    terminal_env = os.getenv("TERMINAL_ENV", "local")
    try:
        from korra_constants import is_container as _is_container
        running_in_container = _is_container()
    except Exception:
        running_in_container = False

    if running_in_container:
        # Inside our container the Docker terminal backend is not
        # configured by default (Docker-in-Docker isn't set up); the
        # local backend is the intended one. Skip the noisy "docker
        # not found" warning. If the user has explicitly chosen
        # TERMINAL_ENV=docker inside the container they likely mounted
        # /var/run/docker.sock, so fall through to the normal check.
        if terminal_env != "docker":
            check_info(
                'Запуск внутри контейнера: используется локальный терминал; Docker внутри Docker по умолчанию не настроен'
            )
            # Skip to next section; Docker isn't relevant here.
            terminal_env = "local"
    if terminal_env == "docker":
        if _safe_which("docker"):
            # Check if docker daemon is running
            try:
                result = subprocess.run(["docker", "info"], capture_output=True, timeout=10)
            except subprocess.TimeoutExpired:
                result = None
            if result is not None and result.returncode == 0:
                check_ok("docker", '(служба работает)')
            else:
                _fail_and_issue('Служба Docker не работает', "", 'Запустите службу Docker', issues)
        else:
            _fail_and_issue(
                'docker не найден',
                '(нужен для terminal.backend: docker)',
                'Установите Docker или смените terminal.backend в config.yaml',
                issues,
            )
    elif _safe_which("docker"):
        check_ok("docker", '(необязательно)')
    elif _is_termux():
        check_info('Docker недоступен внутри Termux; для Android это ожидаемо')
    elif running_in_container:
        pass  # already explained above
    else:
        check_warn('docker не найден', '(необязательно)')
    
    # SSH (if using ssh backend)
    if terminal_env == "ssh":
        ssh_host = os.getenv("TERMINAL_SSH_HOST")
        if ssh_host:
            ssh_user = os.getenv("TERMINAL_SSH_USER")
            ssh_port = os.getenv("TERMINAL_SSH_PORT")
            ssh_key = os.getenv("TERMINAL_SSH_KEY")
            target = f"{ssh_user}@{ssh_host}" if ssh_user else ssh_host
            cmd = ["ssh", "-o", "ConnectTimeout=5", "-o", "BatchMode=yes"]
            if ssh_port:
                cmd += ["-p", ssh_port]
            if ssh_key:
                cmd += ["-i", os.path.expanduser(ssh_key)]
            cmd += [target, "echo ok"]
            # Try to connect
            try:
                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True, encoding='utf-8', errors='replace',
                    timeout=15
                )
            except subprocess.TimeoutExpired:
                result = None
            if result is not None and result.returncode == 0:
                check_ok(f'Подключение SSH к {ssh_host}')
            else:
                _fail_and_issue(f'Подключение SSH к {ssh_host}', "", f'Проверьте настройки SSH для {ssh_host}', issues)
        else:
            _fail_and_issue(
                'TERMINAL_SSH_HOST не задан',
                '(нужен для terminal.backend: ssh)',
                'Укажите SSH-сервер в настройках терминала: korra setup terminal',
                issues,
            )
    
    # Daytona (if using daytona backend)
    if terminal_env == "daytona":
        daytona_key = os.getenv("DAYTONA_API_KEY")
        if daytona_key:
            check_ok('Ключ API Daytona', '(настроен)')
        else:
            _fail_and_issue(
                'DAYTONA_API_KEY не задан',
                '(нужен для terminal.backend: daytona)',
                'Задайте ключ DAYTONA_API_KEY',
                issues,
            )
        try:
            from daytona import Daytona  # noqa: F401 — SDK presence check
            check_ok('SDK Daytona', '(установлен)')
        except ImportError:
            _fail_and_issue(
                'SDK Daytona не установлен',
                "(pip install daytona)",
                'Установите SDK Daytona: pip install daytona',
                issues,
            )

    # Vercel Sandbox (if using vercel_sandbox backend)
    if terminal_env == "vercel_sandbox":
        runtime = os.getenv("TERMINAL_VERCEL_RUNTIME", "node24").strip() or "node24"
        from tools.terminal_tool import _SUPPORTED_VERCEL_RUNTIMES
        if runtime in _SUPPORTED_VERCEL_RUNTIMES:
            check_ok('Среда выполнения Vercel', f"({runtime})")
        else:
            supported = ", ".join(_SUPPORTED_VERCEL_RUNTIMES)
            _fail_and_issue(
                'Среда выполнения Vercel не поддерживается',
                f'({runtime}; используйте {supported})',
                f'Выберите среду Vercel в настройках терминала: {supported}',
                issues,
            )

        disk = os.getenv("TERMINAL_CONTAINER_DISK", "51200").strip()
        if disk in {"", "0", "51200"}:
            check_ok('Размер диска Vercel', '(используется значение платформы по умолчанию)')
        else:
            _fail_and_issue(
                'Свой размер диска Vercel не поддерживается',
                '(верните terminal.container_disk: 51200)',
                'Vercel Sandbox не поддерживает свой container_disk; используйте общее значение 51200',
                issues,
            )

        if importlib.util.find_spec("vercel") is not None:
            check_ok('SDK Vercel', '(установлен)')
        else:
            _fail_and_issue(
                'SDK Vercel не установлен',
                "(pip install 'hermes-agent[vercel]')",
                "Установите дополнительный пакет Vercel: pip install 'hermes-agent[vercel]'",
                issues,
            )

        auth_status = describe_vercel_auth()
        if auth_status.ok:
            check_ok('Вход Vercel', f"({auth_status.display_label or auth_status.label})")
        elif auth_status.label.startswith("partial"):
            _fail_and_issue(
                'Вход Vercel настроен не полностью',
                f"({auth_status.display_label or auth_status.label})",
                'Задайте вместе VERCEL_TOKEN, VERCEL_PROJECT_ID и VERCEL_TEAM_ID',
                issues,
            )
        else:
            _fail_and_issue(
                'Вход Vercel не настроен',
                f"({auth_status.display_label or auth_status.label})",
                'Настройте вход Vercel Sandbox: VERCEL_TOKEN, VERCEL_PROJECT_ID и VERCEL_TEAM_ID',
                issues,
            )
        for line in auth_status.detail_lines:
            check_info(f'Вход Vercel: {line}')

        persistent = os.getenv("TERMINAL_CONTAINER_PERSISTENT", "true").lower() in {"1", "true", "yes", "on"}
        if persistent:
            check_info('Хранение Vercel: сохраняется снимок файлов; работающие процессы не переживают пересоздание среды')
        else:
            check_info('Хранение Vercel: временная файловая система')

    # Plugin-registered terminal backends (if one is the active backend)
    if terminal_env not in {
        "local", "docker", "singularity", "modal", "managed_modal",
        "daytona", "vercel_sandbox", "ssh",
    }:
        try:
            from korra_cli.plugins import discover_plugins

            discover_plugins()
            from agent.terminal_env_registry import get_provider

            _provider = get_provider(terminal_env)
        except Exception:
            _provider = None
        if _provider is None:
            _fail_and_issue(
                f'Неизвестная среда терминала «{terminal_env}»',
                '(встроенной среды или плагина с таким именем нет)',
                'Исправьте terminal.backend в config.yaml либо установите и включите нужный плагин',
                issues,
            )
        else:
            for _ok, _label, _detail in _provider.doctor_checks():
                if _ok:
                    check_ok(_label, _detail)
                else:
                    _fail_and_issue(_label, _detail, _detail.strip("()"), issues)

    # Node.js + agent-browser (for browser automation tools)
    if _safe_which("node"):
        check_ok("Node.js")
        # agent-browser is no longer a root package.json dependency (#43564)
        # — it resolves lazily via npx (or a global/Hermes-managed install)
        # at first use. Mirror tools.browser_tool._find_agent_browser's own
        # resolution cascade here so doctor can't diverge from what browser
        # tools will actually find; validate=False keeps this a cheap
        # existence check with no subprocess spawn or install side effects.
        agent_browser_ok = False
        try:
            from tools.browser_tool import _find_agent_browser, _is_npx_agent_browser_sentinel
            _resolved_ab = _find_agent_browser(validate=False)
        except Exception:
            _resolved_ab = None

        if _resolved_ab and _is_npx_agent_browser_sentinel(_resolved_ab):
            check_ok("agent-browser", '(будет получен через npx при первом использовании)')
            agent_browser_ok = True
            if should_fix:
                # Doctor can't tell from here whether npx's cache already
                # has agent-browser warm — just fire the same warm-up
                # `hermes update` does, so a session's first browser call
                # doesn't pay the registry fetch either way.
                from tools.browser_tool import warm_agent_browser_npx_cache
                if warm_agent_browser_npx_cache():
                    check_info('  Кеш npx для agent-browser подготовлен')
                else:
                    check_info('  Не удалось подготовить кеш npx: нет сети или npx недоступен')
        elif _resolved_ab and agent_browser_runnable(_resolved_ab):
            check_ok("agent-browser", '(управление браузером)')
            agent_browser_ok = True
        elif _resolved_ab:
            # Found on PATH but won't run — almost always a dangling global
            # symlink left behind by agent-browser's npm postinstall after a
            # `hermes update` wiped node_modules (issue #48521).
            check_warn(
                'agent-browser найден, но не запускается',
                f'(возможно, повреждена ссылка {_resolved_ab}; проверьте: npx agent-browser --version)',
            )
        elif _is_termux():
            check_info('agent-browser не установлен; это ожидаемо в проверенной конфигурации Termux')
            check_info('Позже можно установить вручную: npm install -g agent-browser && agent-browser install')
            check_info('Настройка браузера в Termux:')
            for step in _termux_browser_setup_steps(node_installed=True):
                check_info(step)
        else:
            check_warn('agent-browser не установлен', '(нужен npm или npx в PATH)')

        # Chromium presence — the browser tools silently fail to register when
        # agent-browser is found but no Playwright-managed Chromium is on disk
        # (tools/browser_tool.py::check_browser_requirements filters them out
        # before the agent ever sees them).  Reuse the exact predicate it uses
        # so the two checks cannot diverge.  Skip on Termux (not a tested
        # path).
        if agent_browser_ok and not _is_termux():
            try:
                # Lazy import: browser_tool is a ~150KB module we don't want
                # to eagerly load in every `hermes doctor` invocation.
                from tools.browser_tool import (
                    _chromium_installed,
                    _is_camofox_mode,
                    _get_cloud_provider,
                    _get_cdp_override_raw,
                    _using_lightpanda_engine,
                )
            except Exception:
                # If browser_tool can't even import, that's a separate bug
                # surfaced elsewhere; don't crash doctor.
                pass
            else:
                # Only warn about Chromium if the installed engine actually
                # requires it: Camofox, CDP override, a cloud provider, or
                # Lightpanda all bypass the local Chromium requirement.
                skip_chromium_check = (
                    _is_camofox_mode()
                    or bool(_get_cdp_override_raw())
                    or _get_cloud_provider() is not None
                    or _using_lightpanda_engine()
                )
                if not skip_chromium_check:
                    if _chromium_installed():
                        check_ok("Playwright Chromium", '(движок браузера)')
                    else:
                        check_warn(
                            'Playwright Chromium не установлен',
                            '(инструменты browser_* будут скрыты от агента)',
                        )
                        if sys.platform == "win32":
                            check_info(
                                f'Установка: cd {PROJECT_ROOT} && npx playwright install chromium'
                            )
                        else:
                            check_info(
                                f'Установка: cd {PROJECT_ROOT} && npx playwright install --with-deps chromium'
                            )
    elif _is_termux():
        check_info('Node.js не найден; браузерные инструменты необязательны в проверенной конфигурации Termux')
        check_info('Установите Node.js в Termux: pkg install nodejs')
        check_info('Настройка браузера в Termux:')
        for step in _termux_browser_setup_steps(node_installed=False):
            check_info(step)
    else:
        check_warn('Node.js не найден', '(необязателен, нужен для браузерных инструментов)')

    # Lightpanda engine (browser.engine / AGENT_BROWSER_ENGINE). Independent
    # of Node: Browser Use mode spawns ``lightpanda serve`` itself.
    try:
        from tools.browser_tool import _using_lightpanda_engine, lightpanda_engine_status
        from tools.browser_lightpanda import LIGHTPANDA_INSTALL_HINT, find_lightpanda_binary
    except Exception:
        pass
    else:
        # _using_lightpanda_engine() is a cached config read — a failure
        # there would be exceptional, not something to silently hide.
        if _using_lightpanda_engine():
            try:
                _lp_used, _lp_reason = lightpanda_engine_status()
            except Exception as e:
                _lp_used, _lp_reason = False, f'Не удалось проверить состояние: {e}'
            if not _lp_used:
                check_warn('Настройка browser.engine=lightpanda перекрыта другой настройкой', f"({_lp_reason})")
                check_info(
                    'Выберите Lightpanda в korra tools → Управление браузером либо задайте browser.engine: auto'
                )
            elif find_lightpanda_binary():
                check_ok("Lightpanda", f"({_lp_reason})")
            else:
                check_warn(
                    'Выбран Lightpanda, но программа не найдена',
                    '(браузерные инструменты не заработают до установки)',
                )
                check_info(LIGHTPANDA_INSTALL_HINT)

    # npm audit for all Node.js packages
    _npm_bin = _safe_which("npm")
    if _npm_bin:
        # Each entry: (cwd, label, extra_audit_args)
        # PROJECT_ROOT is audited with --workspaces=false so that the apps/*
        # glob (which pulls in Electron, node-pty, etc.) is never resolved
        # for a routine security check. The web and ui-tui workspaces are
        # audited separately via --workspace flags. See #38772.
        # The WhatsApp bridge may live under a writable HERMES_HOME mirror
        # instead of the (possibly read-only) install tree in Docker — resolve
        # it through the shared helper so we audit the dir that actually holds
        # node_modules. See #49561.
        try:
            from gateway.platforms.whatsapp_common import resolve_whatsapp_bridge_dir
            _whatsapp_bridge_dir = resolve_whatsapp_bridge_dir()
        except Exception:
            _whatsapp_bridge_dir = PROJECT_ROOT / "scripts" / "whatsapp-bridge"
        npm_audit_targets = [
            (PROJECT_ROOT, 'Браузерные инструменты (agent-browser)', ["--workspaces=false"]),
            (PROJECT_ROOT, 'Рабочая область web', ["--workspace", "web"]),
            (PROJECT_ROOT, 'Рабочая область ui-tui', ["--workspace", "ui-tui"]),
            (_whatsapp_bridge_dir, 'Мост WhatsApp', []),
        ]
        for npm_dir, label, audit_extra in npm_audit_targets:
            # For workspace-scoped audits run from PROJECT_ROOT the
            # node_modules check must use the workspace root; standalone dirs
            # (whatsapp-bridge) check their own node_modules.
            check_dir = PROJECT_ROOT if audit_extra else npm_dir
            if not (check_dir / "node_modules").exists():
                continue
            try:
                # Use resolved absolute path so Windows can execute
                # npm.cmd (CreateProcessW can't run bare .cmd names).
                audit_result = subprocess.run(
                    [_npm_bin, "audit", "--json", *audit_extra],
                    cwd=str(npm_dir),
                    capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=30,
                )
                import json as _json
                audit_data = _json.loads(audit_result.stdout) if audit_result.stdout.strip() else {}
                vuln_count = audit_data.get("metadata", {}).get("vulnerabilities", {})
                critical = vuln_count.get("critical", 0)
                high = vuln_count.get("high", 0)
                moderate = vuln_count.get("moderate", 0)
                total = critical + high + moderate
                # Determine a scoped fix command for the remediation hint.
                if audit_extra and audit_extra[0] == "--workspace":
                    # Detection (`npm audit --workspace <name>`) is read-only and
                    # safe, but `npm audit fix --workspace <name>` crashes on
                    # current npm with "Cannot read properties of null (reading
                    # 'edgesOut')" — an arborist bug with workspace-filtered
                    # audit fix. The root-level `npm audit fix` can crash on the
                    # same tree with "isDescendantOf", so do not hand the user a
                    # manual fix command for these build-tool advisories.
                    fix_cmd = None
                elif audit_extra == ["--workspaces=false"]:
                    fix_cmd = f"cd {npm_dir} && npm audit fix --workspaces=false"
                else:
                    fix_cmd = f"cd {npm_dir} && npm audit fix"
                if total == 0:
                    check_ok(f'Зависимости {label}', '(известных уязвимостей нет)')
                elif critical > 0 or high > 0:
                    if fix_cmd:
                        vuln_detail = (
                            f'критических: {critical}, серьёзных: {high}, умеренных: {moderate}; выполните: {fix_cmd}'
                        )
                    else:
                        vuln_detail = (
                            f'критических: {critical}, серьёзных: {high}, умеренных: {moderate}; замечание к сборке, исправляется обновлением файла зависимостей lock'
                        )
                    check_warn(
                        f'Зависимости {label}',
                        f"({vuln_detail})"
                    )
                    if audit_extra and audit_extra[0] == "--workspace":
                        # The web/ui-tui workspace advisories are in build-time
                        # tooling (esbuild/vite, etc.), not runtime code that ships
                        # to users. Manual npm remediation may error with a known
                        # arborist crash (edgesOut / isDescendantOf) on this monorepo
                        # tree — in that case it is an npm bug, not a Hermes one.
                        check_info(
                            '  ^ Инструменты сборки, не выполнения. Если ручное исправление npm падает с arborist, это известная ошибка npm; помогает обновление файла зависимостей lock.'
                        )
                    issues.append(
                        f"{label}: {total} {('уязвимость' if total == 1 else 'уязвимостей')} npm"
                    )
                else:
                    check_ok(
                        f'Зависимости {label}',
                        f"({moderate} умеренных {('уязвимость' if moderate == 1 else 'уязвимостей')})",
                    )
            except Exception:
                pass

    if _is_termux():
        check_info('Варианты совместимости Termux:')
        for note in _termux_install_all_fallback_notes():
            check_info(note)

    _section('Подключение к API')
    # Refactor: every connectivity probe below is HTTP-bound and fully
    # independent. Running them in series spent ~5s wall on a typical
    # workstation (2s of that was boto3's IMDS lookup for AWS credentials,
    # which times out unless you're actually on EC2). Threading them with
    # a small executor pool collapses the section to roughly the slowest
    # single probe — about 2s — without changing the output format.
    #
    # Each ``_probe_*`` helper is a pure function: takes its inputs,
    # makes one HTTP/SDK call, returns a ``_ConnectivityResult`` carrying
    # the line(s) to print and any issue strings to append. No globals,
    # no shared mutable state, no printing inside the workers.
    import concurrent.futures as _futures
    from collections import namedtuple as _namedtuple

    _ConnectivityResult = _namedtuple(
        "_ConnectivityResult", ["label", "lines", "issues"]
    )
    _probes: list = []  # list of (label, callable) submitted in display order

    def _probe_openrouter() -> _ConnectivityResult:
        key = os.getenv("OPENROUTER_API_KEY")
        if not key:
            return _ConnectivityResult(
                "OpenRouter API",
                [(color("⚠", Colors.YELLOW), "OpenRouter API",
                  color('(не настроен)', Colors.DIM))],
                [],
            )
        try:
            import httpx
            r = httpx.get(
                OPENROUTER_MODELS_URL,
                headers={"Authorization": f"Bearer {key}"},
                timeout=10,
            )
            if r.status_code == 200:
                return _ConnectivityResult(
                    "OpenRouter API",
                    [(color("✓", Colors.GREEN), "OpenRouter API", "")],
                    [],
                )
            if r.status_code == 401:
                return _ConnectivityResult(
                    "OpenRouter API",
                    [(color("✗", Colors.RED), "OpenRouter API",
                      color('(неверный ключ API)', Colors.DIM))],
                    ['Проверьте OPENROUTER_API_KEY в .env'],
                )
            if r.status_code == 402:
                return _ConnectivityResult(
                    "OpenRouter API",
                    [(color("✗", Colors.RED), "OpenRouter API",
                      color('(баланс исчерпан; нужно пополнение)', Colors.DIM))],
                    ['Недостаточно средств OpenRouter. Смените провайдера: korra config set model.provider <provider>, либо пополните баланс на https://openrouter.ai/settings/credits'],
                )
            if r.status_code == 429:
                return _ConnectivityResult(
                    "OpenRouter API",
                    [(color("✗", Colors.RED), "OpenRouter API",
                      color('(достигнут лимит запросов)', Colors.DIM))],
                    ['Достигнут лимит запросов OpenRouter. Подождите или выберите другого провайдера.'],
                )
            return _ConnectivityResult(
                "OpenRouter API",
                [(color("✗", Colors.RED), "OpenRouter API",
                  color(f"(HTTP {r.status_code})", Colors.DIM))],
                [],
            )
        except Exception as e:
            return _ConnectivityResult(
                "OpenRouter API",
                [(color("✗", Colors.RED), "OpenRouter API",
                  color(f"({e})", Colors.DIM))],
                ['Проверьте подключение к сети'],
            )

    def _probe_anthropic() -> _ConnectivityResult:
        from korra_cli.auth import get_anthropic_key
        key = get_anthropic_key()
        if not key:
            return _ConnectivityResult("Anthropic API", [], [])
        try:
            import httpx
            from agent.anthropic_adapter import (
                _is_oauth_token,
                _COMMON_BETAS,
                _OAUTH_ONLY_BETAS,
                _CONTEXT_1M_BETA,
            )
            headers = {"anthropic-version": "2023-06-01"}
            is_oauth = _is_oauth_token(key)
            if is_oauth:
                headers["Authorization"] = f"Bearer {key}"
                headers["anthropic-beta"] = ",".join(_COMMON_BETAS + _OAUTH_ONLY_BETAS)
            else:
                headers["x-api-key"] = key
            r = httpx.get(
                "https://api.anthropic.com/v1/models",
                headers=headers, timeout=10,
            )
            # Reactive recovery: OAuth subscriptions without 1M context reject the
            # request with 400 "long context beta is not yet available for this
            # subscription". Retry once with that beta stripped so the doctor
            # check doesn't falsely report Anthropic as unreachable.
            if (
                is_oauth
                and r.status_code == 400
                and "long context beta" in r.text.lower()
                and "not yet available" in r.text.lower()
            ):
                headers["anthropic-beta"] = ",".join(
                    [b for b in _COMMON_BETAS if b != _CONTEXT_1M_BETA]
                    + list(_OAUTH_ONLY_BETAS)
                )
                r = httpx.get(
                    "https://api.anthropic.com/v1/models",
                    headers=headers, timeout=10,
                )
            if r.status_code == 200:
                return _ConnectivityResult(
                    "Anthropic API",
                    [(color("✓", Colors.GREEN), "Anthropic API", "")],
                    [],
                )
            if r.status_code == 401:
                return _ConnectivityResult(
                    "Anthropic API",
                    [(color("✗", Colors.RED), "Anthropic API",
                      color('(неверный ключ API)', Colors.DIM))],
                    [],
                )
            return _ConnectivityResult(
                "Anthropic API",
                [(color("⚠", Colors.YELLOW), "Anthropic API",
                  color('(не удалось проверить)', Colors.DIM))],
                [],
            )
        except Exception as e:
            return _ConnectivityResult(
                "Anthropic API",
                [(color("⚠", Colors.YELLOW), "Anthropic API",
                  color(f"({e})", Colors.DIM))],
                [],
            )

    def _probe_apikey_provider(pname, env_vars, default_url, base_env,
                               supports_health_check) -> _ConnectivityResult:
        key = ""
        for ev in env_vars:
            key = os.getenv(ev, "")
            if key:
                break
        if not key:
            return _ConnectivityResult(pname, [], [])
        label = pname.ljust(20)
        if not supports_health_check:
            return _ConnectivityResult(
                pname,
                [(color("✓", Colors.GREEN), label,
                  color('(ключ настроен)', Colors.DIM))],
                [],
            )
        try:
            import httpx
            base = os.getenv(base_env, "") if base_env else ""
            # Auto-detect Kimi Code keys (sk-kimi-) → api.kimi.com/coding/v1
            # (OpenAI-compat surface, which exposes /models for health check).
            if not base and key.startswith("sk-kimi-"):
                base = "https://api.kimi.com/coding/v1"
            # Anthropic-compat endpoints (/anthropic, api.kimi.com/coding
            # with no /v1) don't support /models. Rewrite to OpenAI-compat
            # /v1 surface for health checks.
            if base and base.rstrip("/").endswith("/anthropic"):
                from agent.auxiliary_client import _to_openai_base_url
                base = _to_openai_base_url(base)
            if base_url_host_matches(base, "api.kimi.com") and base.rstrip("/").endswith("/coding"):
                base = base.rstrip("/") + "/v1"
            url = (base.rstrip("/") + "/models") if base else default_url
            headers = {
                "Authorization": f"Bearer {key}",
                "User-Agent": _HERMES_USER_AGENT,
            }
            if base_url_host_matches(base, "api.kimi.com"):
                headers["User-Agent"] = "claude-code/0.1.0"
            # Google's Generative Language API (generativelanguage.googleapis.com)
            # rejects ``Authorization: Bearer <api-key>`` with 401
            # ``ACCESS_TOKEN_TYPE_UNSUPPORTED`` — that header is reserved for
            # OAuth 2 access tokens, not plain API keys. Plain keys use
            # ``x-goog-api-key`` (or ``?key=``). Without this, a perfectly valid
            # GOOGLE_API_KEY/GEMINI_API_KEY always shows red in ``hermes doctor``.
            if url and base_url_host_matches(url, "generativelanguage.googleapis.com"):
                headers.pop("Authorization", None)
                headers["x-goog-api-key"] = key
            r = httpx.get(url, headers=headers, timeout=10)
            if (
                pname == "Alibaba/DashScope"
                and not base
                and r.status_code == 401
            ):
                r = httpx.get(
                    "https://dashscope.aliyuncs.com/compatible-mode/v1/models",
                    headers=headers, timeout=10,
                )
            if r.status_code == 200:
                return _ConnectivityResult(
                    pname,
                    [(color("✓", Colors.GREEN), label, "")],
                    [],
                )
            if r.status_code == 401:
                return _ConnectivityResult(
                    pname,
                    [(color("✗", Colors.RED), label,
                      color('(неверный ключ API)', Colors.DIM))],
                    [f'Проверьте {env_vars[0]} в .env'],
                )
            return _ConnectivityResult(
                pname,
                [(color("⚠", Colors.YELLOW), label,
                  color(f"(HTTP {r.status_code})", Colors.DIM))],
                [],
            )
        except Exception as e:
            return _ConnectivityResult(
                pname,
                [(color("⚠", Colors.YELLOW), label,
                  color(f"({e})", Colors.DIM))],
                [],
            )

    def _probe_bedrock() -> _ConnectivityResult:
        try:
            from agent.bedrock_adapter import (
                has_aws_credentials,
                resolve_aws_auth_env_var,
                resolve_bedrock_region,
            )
        except ImportError:
            return _ConnectivityResult("AWS Bedrock", [], [])
        if not has_aws_credentials():
            return _ConnectivityResult("AWS Bedrock", [], [])
        auth_var = resolve_aws_auth_env_var()
        region = resolve_bedrock_region()
        label = "AWS Bedrock".ljust(20)
        try:
            import boto3
            from botocore.config import Config as _BotoConfig
            # Trim retries on the actual Bedrock API call so a transient
            # failure doesn't pad the doctor run by 30+ seconds.
            cfg = _BotoConfig(
                connect_timeout=5,
                read_timeout=10,
                retries={"max_attempts": 1},
            )
            client = boto3.client("bedrock", region_name=region, config=cfg)
            resp = client.list_foundation_models()
            n = len(resp.get("modelSummaries", []))
            return _ConnectivityResult(
                "AWS Bedrock",
                [(color("✓", Colors.GREEN), label,
                  color(f'({auth_var}, {region}, моделей: {n})', Colors.DIM))],
                [],
            )
        except ImportError:
            return _ConnectivityResult(
                "AWS Bedrock",
                [(color("⚠", Colors.YELLOW), label,
                  color(f'(boto3 не установлен; выполните {sys.executable} -m pip install boto3)',
                        Colors.DIM))],
                [f'Установите boto3 для Bedrock: {sys.executable} -m pip install boto3'],
            )
        except Exception as e:
            err_name = type(e).__name__
            return _ConnectivityResult(
                "AWS Bedrock",
                [(color("⚠", Colors.YELLOW), label,
                  color(f"({err_name}: {e})", Colors.DIM))],
                [f'AWS Bedrock: {err_name}; проверьте права IAM для bedrock:ListFoundationModels'],
            )

    def _probe_azure_entra() -> _ConnectivityResult:
        """Probe Azure Foundry Entra ID auth, parallel to ``_probe_bedrock``.

        Skipped unless the active config has ``model.provider:
        azure-foundry`` AND ``model.auth_mode: entra_id`` — we don't probe
        the token-service / CLI chain for users on plain API-key Azure.

        Bounded by a 10s timeout (via
        :func:`agent.azure_identity_adapter.describe_active_credential`)
        so a slow token service can't pad the doctor run.
        """
        label = "Azure Foundry (Entra ID)".ljust(28)
        try:
            from korra_cli.config import load_config
            cfg = load_config()
            model_cfg = cfg.get("model") if isinstance(cfg, dict) else {}
            if not isinstance(model_cfg, dict):
                return _ConnectivityResult("Azure Foundry (Entra ID)", [], [])
            cfg_provider = str(model_cfg.get("provider") or "").strip().lower()
            auth_mode = str(model_cfg.get("auth_mode") or "").strip().lower()
            if cfg_provider != "azure-foundry" or auth_mode != "entra_id":
                return _ConnectivityResult("Azure Foundry (Entra ID)", [], [])
        except Exception:
            return _ConnectivityResult("Azure Foundry (Entra ID)", [], [])

        try:
            from agent.azure_identity_adapter import (
                EntraIdentityConfig,
                SCOPE_AI_AZURE_DEFAULT,
                describe_active_credential,
                has_azure_identity_installed,
            )
        except Exception as exc:
            return _ConnectivityResult(
                "Azure Foundry (Entra ID)",
                [(color("⚠", Colors.YELLOW), label,
                  color(f'(не удалось загрузить адаптер: {exc})', Colors.DIM))],
                [f'Не удалось загрузить адаптер Azure Foundry: {exc}'],
            )

        if not has_azure_identity_installed():
            return _ConnectivityResult(
                "Azure Foundry (Entra ID)",
                [(color("⚠", Colors.YELLOW), label,
                  color('(azure-identity не установлен)', Colors.DIM))],
                [f'Установите azure-identity: {sys.executable} -m pip install azure-identity'],
            )

        entra_cfg = model_cfg.get("entra") or {}
        if not isinstance(entra_cfg, dict):
            entra_cfg = {}
        scope = (
            str(entra_cfg.get("scope") or "").strip()
            or SCOPE_AI_AZURE_DEFAULT
        )
        config = EntraIdentityConfig(
            scope=scope,
        )
        info = describe_active_credential(config=config, timeout_seconds=10.0)
        if info.get("ok"):
            env_sources = info.get("env_sources") or []
            tag = ", ".join(env_sources) if env_sources else 'стандартная цепочка входа'
            return _ConnectivityResult(
                "Azure Foundry (Entra ID)",
                [(color("✓", Colors.GREEN), label,
                  color(f"({tag}, scope={scope})", Colors.DIM))],
                [],
            )
        err = info.get("error") or 'подходящих данных входа не найдено'
        hint = info.get("hint") or (
            'Выполните az login, задайте AZURE_TENANT_ID, AZURE_CLIENT_ID и AZURE_CLIENT_SECRET либо подключите управляемую учётную запись к этой виртуальной машине.'
        )
        return _ConnectivityResult(
            "Azure Foundry (Entra ID)",
            [(color("⚠", Colors.YELLOW), label,
              color(f"({err})", Colors.DIM))],
            [f"Azure Foundry Entra: {err}. {hint}"],
        )

    # Build the probe submission list in display order
    _probes.append(("OpenRouter API", _probe_openrouter))
    _probes.append(("Anthropic API", _probe_anthropic))

    global _APIKEY_PROVIDERS_CACHE
    if _APIKEY_PROVIDERS_CACHE is None:
        _APIKEY_PROVIDERS_CACHE = _build_apikey_providers_list()
    for _entry in _APIKEY_PROVIDERS_CACHE:
        _pname, _env_vars, _default_url, _base_env, _supports = _entry
        # Capture loop vars by binding default args — without this, all closures
        # would share the final iteration's values and every probe would hit
        # the last provider's URL.
        _probes.append((_pname, lambda p=_pname, e=_env_vars, u=_default_url,
                                       b=_base_env, s=_supports:
                                _probe_apikey_provider(p, e, u, b, s)))

    _probes.append(("AWS Bedrock", _probe_bedrock))
    _probes.append(("Azure Foundry (Entra ID)", _probe_azure_entra))

    # Print a single status line so users see something happening, then
    # fan out. ``\r`` clears it once the first real result line lands.
    print(f"  {color(f'Проверяем {len(_probes)} подключений параллельно…', Colors.DIM)}",
          end="", flush=True)

    # Disable boto3's EC2 instance-metadata-service probe for the duration
    # of the parallel block. boto's default credential chain tries
    # 169.254.169.254 with a multi-second timeout when we're not on EC2,
    # which dominated the section's wall time before this fix
    # (~2s on a developer laptop, even with the rest parallelized).
    # Set on the parent thread before submitting work so the env-var
    # mutation never races with another worker. has_aws_credentials() in
    # the bedrock probe already gates on real env-var creds, so IMDS is
    # never the legitimate source for `hermes doctor`.
    _imds_prev = os.environ.get("AWS_EC2_METADATA_DISABLED")
    os.environ["AWS_EC2_METADATA_DISABLED"] = "true"
    try:
        # 8 workers is plenty — each probe is a single HTTP call plus a TLS
        # handshake. More than that wastes thread-startup cost and risks
        # noisy output if anything ever printed from inside a worker.
        with _futures.ThreadPoolExecutor(max_workers=8,
                                         thread_name_prefix="doctor-probe") as _ex:
            _futures_in_order = [_ex.submit(_fn) for _, _fn in _probes]
            _results = [_f.result() for _f in _futures_in_order]
    finally:
        if _imds_prev is None:
            os.environ.pop("AWS_EC2_METADATA_DISABLED", None)
        else:
            os.environ["AWS_EC2_METADATA_DISABLED"] = _imds_prev

    # Clear the "Running …" line and print all results in submission order.
    print("\r" + " " * 70 + "\r", end="")
    for _r in _results:
        for _glyph, _label, _detail in _r.lines:
            if _detail:
                print(f"  {_glyph} {_label} {_detail}")
            else:
                print(f"  {_glyph} {_label}")
        _issues_to_add = list(_r.issues)
        if _issues_to_add and _has_healthy_oauth_fallback_for_apikey_provider(_r.label):
            _issues_to_add = []
        for _issue in _issues_to_add:
            issues.append(_issue)

    _section('Доступность инструментов')
    try:
        # Add project root to path for imports
        sys.path.insert(0, str(PROJECT_ROOT))
        from model_tools import check_tool_availability, TOOLSET_REQUIREMENTS
        
        available, unavailable = check_tool_availability()
        available, unavailable = _apply_doctor_tool_availability_overrides(available, unavailable)

        # Web is split into search/extract readiness rows so an explicitly
        # selected but unconfigured backend cannot look healthy (#78412).
        web_rows = []
        if "web" in available or any(item.get("name") == "web" for item in unavailable):
            web_rows = _doctor_web_capability_rows()
            if web_rows:
                available = [tid for tid in available if tid != "web"]
                unavailable = [item for item in unavailable if item.get("name") != "web"]

        for tid in available:
            info = TOOLSET_REQUIREMENTS.get(tid, {})
            check_ok(info.get("name", tid), _doctor_tool_availability_detail(tid))

        for status, label, detail in web_rows:
            if status == "ok":
                check_ok(label, detail)
            else:
                check_warn(label, detail)

        for item in unavailable:
            env_vars = item.get("missing_vars") or item.get("env_vars") or []
            if env_vars:
                vars_str = ", ".join(env_vars)
                check_warn(item["name"], f'(не хватает {vars_str})')
            else:
                check_warn(item["name"], '(отсутствует системная зависимость)')

        # Count missing API-key requirements only for toolsets enabled in the
        # current CLI platform. Default-off or explicitly disabled toolsets may
        # still show warnings above, but should not pollute the final summary.
        api_disabled = _missing_api_key_toolsets_for_summary(unavailable)
        web_not_ready = any(status != "ok" for status, _, _ in web_rows)
        if api_disabled or web_not_ready:
            issues.append('Для доступа ко всем инструментам настройте недостающие ключи API: korra setup')
    except Exception as e:
        check_warn('Не удалось проверить доступность инструментов', f"({e})")
    
    _section('Каталог навыков')
    hub_dir = HERMES_HOME / "skills" / ".hub"
    if hub_dir.exists():
        check_ok('Папка каталога навыков существует')
        lock_file = hub_dir / "lock.json"
        if lock_file.exists():
            try:
                import json
                lock_data = json.loads(lock_file.read_text(encoding="utf-8"))
                count = len(lock_data.get("installed", {}))
                check_ok(f'Файл учёта исправен: навыков из каталога — {count}')
            except Exception:
                check_warn('Файл учёта навыков', '(повреждён или недоступен для чтения)')
        quarantine = hub_dir / "quarantine"
        q_count = sum(1 for d in quarantine.iterdir() if d.is_dir()) if quarantine.exists() else 0
        if q_count > 0:
            check_warn(f'Навыков в карантине: {q_count}', '(ожидают проверки)')
    else:
        check_warn('Папка каталога навыков ещё не создана', '(выполните korra skills list)')

    from korra_cli.config import get_env_value

    def _gh_authenticated() -> bool:
        """Check if gh CLI is authenticated via token file or device flow."""
        try:
            result = subprocess.run(
                ["gh", "auth", "status", "--json", "authenticated"],
                capture_output=True, timeout=10,
            )
            return result.returncode == 0
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False

    github_token = get_env_value("GITHUB_TOKEN") or get_env_value("GH_TOKEN")
    if github_token:
        check_ok('Токен GitHub настроен; API доступен с авторизацией')
    elif _gh_authenticated():
        check_ok('Вход GitHub выполнен через gh CLI', '(полный доступ к API; GITHUB_TOKEN не нужен)')
    else:
        check_warn('GITHUB_TOKEN отсутствует', f'(лимит 60 запросов в час; добавьте токен в {_DHH}/.env для увеличения лимита)')

    _section('Провайдер памяти')
    _active_memory_provider = ""
    try:
        from korra_cli.config import read_user_config_raw as _read_raw_mem
        _mem_cfg_path = HERMES_HOME / "config.yaml"
        if _mem_cfg_path.exists():
            # Raw-file diagnostic (+ managed overlay below, unchanged).
            _raw_cfg = _read_raw_mem(_mem_cfg_path)
            try:
                from korra_cli import managed_scope
                _raw_cfg = managed_scope.apply_managed_overlay(_raw_cfg)
            except Exception:
                pass
            _active_memory_provider = (_raw_cfg.get("memory") or {}).get("provider", "")
    except Exception:
        pass

    if not _active_memory_provider:
        check_ok('Встроенная память работает', '(внешний провайдер не настроен — это нормально)')
    elif _active_memory_provider == "honcho":
        try:
            from plugins.memory.honcho.client import HonchoClientConfig, resolve_config_path
            hcfg = HonchoClientConfig.from_global_config()
            _honcho_cfg_path = resolve_config_path()

            if not _honcho_cfg_path.exists():
                # Config file missing — but env var fallback may have resolved it.
                # Only warn if the config didn't actually resolve from env vars.
                if hcfg.api_key or hcfg.base_url:
                    check_ok(
                        'Honcho настроен через переменные среды',
                        f'Файл настроек {_honcho_cfg_path} не найден; используется переменная HONCHO_API_KEY',
                    )
                else:
                    check_warn('Настройки Honcho не найдены', 'Выполните: korra memory setup')
            elif not hcfg.enabled:
                check_info(f'Honcho отключён; для включения задайте enabled: true в {_honcho_cfg_path}')
            elif not (hcfg.api_key or hcfg.base_url):
                _fail_and_issue(
                    'Ключ API или основной адрес Honcho не задан',
                    'Выполните: korra memory setup',
                    'Нет ключа API Honcho; выполните korra memory setup',
                    issues,
                )
            else:
                from plugins.memory.honcho.client import get_honcho_client, reset_honcho_client
                reset_honcho_client()
                try:
                    get_honcho_client(hcfg)
                    check_ok(
                        'Honcho подключён',
                        f'Проект: {hcfg.workspace_id}; режим: {hcfg.recall_mode}; частота: {hcfg.write_frequency}',
                    )
                except Exception as _e:
                    _fail_and_issue('Подключение Honcho не удалось', str(_e), f'Honcho недоступен: {_e}', issues)
        except ImportError:
            _fail_and_issue(
                'honcho-ai не установлен',
                "pip install honcho-ai",
                'Выбрана память Honcho, но пакет honcho-ai не установлен',
                issues,
            )
        except Exception as _e:
            check_warn('Не удалось проверить Honcho', str(_e))
    elif _active_memory_provider == "mem0":
        try:
            from plugins.memory.mem0 import _load_config as _load_mem0_config
            mem0_cfg = _load_mem0_config()
            mem0_key = mem0_cfg.get("api_key", "")
            if mem0_key:
                check_ok('Ключ API Mem0 настроен')
                check_info(f"user_id={mem0_cfg.get('user_id', '?')}  agent_id={mem0_cfg.get('agent_id', '?')}")
            else:
                _fail_and_issue(
                    'Ключ API Mem0 не задан',
                    '(задайте MEM0_API_KEY в .env или выполните korra memory setup)',
                    'Выбрана память Mem0, но ключ API отсутствует',
                    issues,
                )
        except ImportError:
            _fail_and_issue(
                'Не удалось загрузить плагин Mem0',
                "pip install mem0ai",
                'Выбрана память Mem0, но пакет mem0ai не установлен',
                issues,
            )
        except Exception as _e:
            check_warn('Не удалось проверить Mem0', str(_e))
    else:
        # Generic check for other memory providers (openviking, hindsight, etc.)
        try:
            from plugins.memory import load_memory_provider
            _provider = load_memory_provider(_active_memory_provider)
            if _provider and _provider.is_available():
                check_ok(f'Провайдер {_active_memory_provider} активен')
            elif _provider:
                check_warn(f'{_active_memory_provider} настроен, но недоступен', 'Выполните: korra memory status')
            else:
                check_warn(f'Плагин {_active_memory_provider} не найден', 'Выполните: korra memory setup')
        except Exception as _e:
            check_warn(f'Не удалось проверить {_active_memory_provider}', str(_e))

    try:
        check_multiplex_profiles(should_fix=should_fix, issues=issues)
    except Exception:
        pass

    try:
        check_local_bot_api(issues)
    except Exception:
        pass

    try:
        check_channels_and_providers(issues)
    except Exception:
        pass

    try:
        from korra_cli.profiles import list_profiles, _get_wrapper_dir, profile_exists
        import re as _re

        named_profiles = [p for p in list_profiles() if not p.is_default]
        if named_profiles:
            _section('Профили')
            check_ok(f'Найдено профилей: {len(named_profiles)}')
            wrapper_dir = _get_wrapper_dir()
            for p in named_profiles:
                parts = []
                if p.gateway_running:
                    parts.append('шлюз работает')
                if p.model:
                    parts.append(p.model[:30])
                if not (p.path / "config.yaml").exists():
                    parts.append('⚠ нет настроек')
                if not (p.path / ".env").exists():
                    parts.append("no .env")
                wrapper = wrapper_dir / p.name
                if not wrapper.exists():
                    parts.append('нет команды-обёртки')
                status = ", ".join(parts) if parts else "configured"
                check_ok(f"  {p.name}: {status}")

            # Check for orphan wrappers
            if wrapper_dir.is_dir():
                for wrapper in wrapper_dir.iterdir():
                    if not wrapper.is_file():
                        continue
                    try:
                        content = wrapper.read_text(encoding="utf-8")
                        if "hermes -p" in content:
                            _m = _re.search(r"hermes -p (\S+)", content)
                            if _m and not profile_exists(_m.group(1)):
                                check_warn(f'Команда {wrapper.name} ссылается на несуществующий профиль «{_m.group(1)}»')
                    except Exception:
                        pass
    except ImportError:
        pass
    except Exception:
        pass

    # Opt-in live backend probes run AFTER all static checks, only with
    # `hermes doctor --live` (real network calls; bounded + read-only).
    try:
        from korra_cli.doctor_live import maybe_run_live_checks
        maybe_run_live_checks(args, manual_issues)
    except Exception:
        pass

    print()
    remaining_issues = issues + manual_issues
    if should_fix and fixed_count > 0:
        print(color("─" * 60, Colors.GREEN))
        print(color(f'  Исправлено проблем: {fixed_count}.', Colors.GREEN, Colors.BOLD), end="")
        if remaining_issues:
            print(color(f' Требуют ручного исправления: {len(remaining_issues)}.', Colors.YELLOW, Colors.BOLD))
        else:
            print()
        print()
        if remaining_issues:
            for i, issue in enumerate(remaining_issues, 1):
                print(f"  {i}. {issue}")
            print()
    elif remaining_issues:
        print(color("─" * 60, Colors.YELLOW))
        print(color(f'  Найдено проблем для исправления: {len(remaining_issues)}:', Colors.YELLOW, Colors.BOLD))
        print()
        for i, issue in enumerate(remaining_issues, 1):
            print(f"  {i}. {issue}")
        print()
        if not should_fix:
            print(color('  Автоматическое исправление доступных проблем: korra doctor --fix.', Colors.DIM))
    else:
        print(color("─" * 60, Colors.GREEN))
        print(color('  Все проверки пройдены! 🎉', Colors.GREEN, Colors.BOLD))
    
    print()

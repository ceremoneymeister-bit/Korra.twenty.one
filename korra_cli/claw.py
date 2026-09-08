"""hermes claw — OpenClaw migration commands.

Usage:
    hermes claw migrate              # Preview then migrate (always shows preview first)
    hermes claw migrate --dry-run    # Preview only, no changes
    hermes claw migrate --yes        # Skip confirmation prompt
    hermes claw migrate --preset full --overwrite --migrate-secrets  # Full run w/ secrets
    hermes claw migrate --no-backup  # Skip pre-migration snapshot
    hermes claw cleanup              # Archive leftover OpenClaw directories
    hermes claw cleanup --dry-run    # Preview what would be archived
"""

import importlib.util
import logging
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

from korra_cli.config import get_hermes_home, get_config_path, load_config, save_config
from korra_constants import get_optional_skills_dir
from korra_cli.setup import (
    Colors,
    color,
    print_header,
    print_info,
    print_success,
    print_error,
    prompt_yes_no,
)

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).parent.parent.resolve()

_OPENCLAW_SCRIPT = (
    get_optional_skills_dir(PROJECT_ROOT / "optional-skills")
    / "migration"
    / "openclaw-migration"
    / "scripts"
    / "openclaw_to_hermes.py"
)

# Fallback: user may have installed the skill from the Hub
_OPENCLAW_SCRIPT_INSTALLED = (
    get_hermes_home()
    / "skills"
    / "migration"
    / "openclaw-migration"
    / "scripts"
    / "openclaw_to_hermes.py"
)

# Known OpenClaw directory names (current + legacy)
_OPENCLAW_DIR_NAMES = (".openclaw", ".clawdbot", ".moltbot")

def _detect_openclaw_processes() -> list[str]:
    """Detect running OpenClaw processes and services.

    Returns a list of human-readable descriptions of what was found.
    An empty list means nothing was detected.
    """
    found: list[str] = []

    # -- systemd service (Linux) ------------------------------------------
    if sys.platform != "win32":
        try:
            result = subprocess.run(
                ["systemctl", "--user", "is-active", "openclaw-gateway.service"],
                capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=5,
            )
            if result.stdout.strip() == "active":
                found.append("systemd service: openclaw-gateway.service")
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

    # -- process scan ------------------------------------------------------
    if sys.platform == "win32":
        # bounded_probe_run: a plain subprocess.run(timeout=...) can hang
        # forever on Windows in post-timeout cleanup when a conhost.exe
        # descendant holds duplicated pipe handles (#87134) — and a hang is
        # not an exception, so the try/except here can't save the caller.
        from korra_cli._subprocess_compat import bounded_probe_run

        try:
            for exe in ("openclaw.exe", "clawd.exe"):
                result = bounded_probe_run(
                    ["tasklist", "/FI", f"IMAGENAME eq {exe}"],
                    timeout=5,
                )
                if result is not None and exe in (result.stdout or "").lower():
                    found.append(f"process: {exe}")

            # Node.js-hosted OpenClaw — tasklist doesn't show command lines,
            # so fall back to PowerShell.
            ps_cmd = (
                'Get-CimInstance Win32_Process -Filter "Name = \'node.exe\'" | '
                'Where-Object { $_.CommandLine -match "openclaw|clawd" } | '
                'Select-Object -First 1 ProcessId'
            )
            result = bounded_probe_run(
                ["powershell", "-NoProfile", "-Command", ps_cmd],
                timeout=5,
            )
            if result is not None and (result.stdout or "").strip():
                found.append(f"node.exe process with openclaw in command line (PID {result.stdout.strip()})")
        except Exception:
            pass
    else:
        try:
            result = subprocess.run(
                ["pgrep", "-f", "openclaw"],
                capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=3,
            )
            if result.returncode == 0:
                pids = result.stdout.strip().split()
                found.append(f"openclaw process(es) (PIDs: {', '.join(pids)})")
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

    return found


def _warn_if_openclaw_running(auto_yes: bool) -> None:
    """Warn if OpenClaw is still running before migration.

    Telegram, Discord, and Slack only allow one active connection per bot
    token. Migrating while OpenClaw is running causes both to fight for the
    same token.
    """
    running = _detect_openclaw_processes()
    if not running:
        return

    print()
    print_error("OpenClaw сейчас работает:")
    for detail in running:
        print_info(f"  * {detail}")
    print_info(
        "Telegram, Discord и Slack разрешают только одно активное подключение "
        "для токена бота. Если продолжить, OpenClaw и Korra будут одновременно "
        "использовать токен, что приведёт к разрывам связи."
    )
    print_info("Перед переносом остановите OpenClaw.")
    print()
    if auto_yes:
        return
    if not sys.stdin.isatty():
        print_info("Запуск без участия пользователя: доступен только предварительный просмотр.")
        return
    if not prompt_yes_no("Всё равно продолжить?", default=False):
        print_info("Перенос отменён. Остановите OpenClaw и повторите попытку.")
        sys.exit(0)


def _warn_if_gateway_running(auto_yes: bool) -> None:
    """Check if a Hermes gateway is running with connected platforms.

    Migrating bot tokens while the gateway is polling will cause conflicts
    (e.g. Telegram 409 "terminated by other getUpdates request"). Warn the
    user and let them decide whether to continue.
    """
    from gateway.status import get_running_pid, read_runtime_status

    if not get_running_pid():
        return

    data = read_runtime_status() or {}
    platforms = data.get("platforms") or {}
    connected = [name for name, info in platforms.items()
                 if isinstance(info, dict) and info.get("state") == "connected"]
    if not connected:
        return

    print()
    print_error(
        "Шлюз Korra работает с активными подключениями: "
        + ", ".join(connected)
    )
    print_info(
        "Перенос токенов при работающем шлюзе вызовет конфликт: Telegram, "
        "Discord и Slack разрешают одно активное подключение на токен."
    )
    print_info("Сначала остановите шлюз: `korra gateway stop`.")
    print()
    if not auto_yes and not prompt_yes_no("Всё равно продолжить?", default=False):
        print_info("Перенос отменён. Остановите шлюз и повторите попытку.")
        sys.exit(0)

# State files commonly found in OpenClaw workspace directories — listed
# during cleanup to help the user decide whether to archive
_WORKSPACE_STATE_GLOBS = (
    "*/todo.json",
    "*/sessions/*",
    "*/memory/*.json",
    "*/logs/*",
)


def _find_migration_script() -> Path | None:
    """Find the openclaw_to_hermes.py script in known locations."""
    for candidate in [_OPENCLAW_SCRIPT, _OPENCLAW_SCRIPT_INSTALLED]:
        if candidate.exists():
            return candidate
    return None


def _load_migration_module(script_path: Path):
    """Dynamically load the migration script as a module."""
    spec = importlib.util.spec_from_file_location("openclaw_to_hermes", script_path)
    if spec is None or spec.loader is None:
        return None
    mod = importlib.util.module_from_spec(spec)
    # Register in sys.modules so @dataclass can resolve the module
    # (Python 3.11+ requires this for dynamically loaded modules)
    sys.modules[spec.name] = mod
    try:
        spec.loader.exec_module(mod)
    except Exception:
        sys.modules.pop(spec.name, None)
        raise
    return mod


def _find_openclaw_dirs() -> list[Path]:
    """Find all OpenClaw directories on disk."""
    found = []
    for name in _OPENCLAW_DIR_NAMES:
        candidate = Path.home() / name
        if candidate.is_dir():
            found.append(candidate)
    return found


def _scan_workspace_state(source_dir: Path) -> list[tuple[Path, str]]:
    """Scan an OpenClaw directory for workspace state files.

    Returns a list of (path, description) tuples.
    """
    findings: list[tuple[Path, str]] = []

    if not source_dir.exists():
        return findings

    # Direct state files in the root
    for name in ("todo.json", "sessions", "logs"):
        candidate = source_dir / name
        if candidate.exists():
            kind = "directory" if candidate.is_dir() else "file"
            findings.append((candidate, f"Root {kind}: {name}"))

    # State files inside workspace directories
    try:
        children = sorted(source_dir.iterdir())
    except OSError:
        return findings

    for child in children:
        if not child.is_dir() or child.name.startswith("."):
            continue
        # Check for workspace-like subdirectories
        for state_name in ("todo.json", "sessions", "logs", "memory"):
            state_path = child / state_name
            if state_path.exists():
                kind = "directory" if state_path.is_dir() else "file"
                rel = state_path.relative_to(source_dir).as_posix()
                findings.append((state_path, f"Workspace {kind}: {rel}"))

    return findings


def _archive_directory(source_dir: Path, dry_run: bool = False) -> Path:
    """Rename an OpenClaw directory to .pre-migration.

    Returns the archive path.
    """
    timestamp = datetime.now().strftime("%Y%m%d")
    archive_name = f"{source_dir.name}.pre-migration"
    archive_path = source_dir.parent / archive_name

    # If archive already exists, add timestamp
    if archive_path.exists():
        archive_name = f"{source_dir.name}.pre-migration-{timestamp}"
        archive_path = source_dir.parent / archive_name

    # If still exists (multiple runs same day), add counter
    counter = 2
    while archive_path.exists():
        archive_name = f"{source_dir.name}.pre-migration-{timestamp}-{counter}"
        archive_path = source_dir.parent / archive_name
        counter += 1

    if not dry_run:
        source_dir.rename(archive_path)

    return archive_path


def claw_command(args):
    """Route hermes claw subcommands."""
    action = getattr(args, "claw_action", None)

    if action == "migrate":
        _cmd_migrate(args)
    elif action in {"cleanup", "clean"}:
        _cmd_cleanup(args)
    else:
        print("Использование: korra claw <команда> [параметры]")
        print()
        print("Команды:")
        print("  migrate          перенести настройки из OpenClaw в Korra")
        print("  cleanup          архивировать оставшиеся после переноса папки OpenClaw")
        print()
        print("Параметры: `korra claw <команда> --help`.")


def _cmd_migrate(args):
    """Run the OpenClaw → Hermes migration."""
    # Check current and legacy OpenClaw directories
    explicit_source = getattr(args, "source", None)
    if explicit_source:
        source_dir = Path(explicit_source)
    else:
        source_dir = Path.home() / ".openclaw"
        if not source_dir.is_dir():
            # Try legacy directory names
            for legacy in (".clawdbot", ".moltbot"):
                candidate = Path.home() / legacy
                if candidate.is_dir():
                    source_dir = candidate
                    break
    dry_run = getattr(args, "dry_run", False)
    preset = getattr(args, "preset", "full")
    overwrite = getattr(args, "overwrite", False)
    migrate_secrets = getattr(args, "migrate_secrets", False)
    workspace_target = getattr(args, "workspace_target", None)
    skill_conflict = getattr(args, "skill_conflict", "skip")
    no_backup = getattr(args, "no_backup", False)

    # Secrets are never included implicitly — they must be explicitly requested
    # via --migrate-secrets, even under --preset full.  This mirrors OpenClaw's
    # migrate-hermes posture (two-phase: run once without secrets, rerun with
    # --include-secrets) and prevents a --preset full invocation from silently
    # importing API keys that the user may not have intended to copy.

    print()
    print(
        color(
            "┌─────────────────────────────────────────────────────────┐",
            Colors.MAGENTA,
        )
    )
    print(
        color(
            "│          ⚕ Korra — перенос из OpenClaw                 │",
            Colors.MAGENTA,
        )
    )
    print(
        color(
            "└─────────────────────────────────────────────────────────┘",
            Colors.MAGENTA,
        )
    )

    # Check source directory
    if not source_dir.is_dir():
        print()
        print_error(f"Папка OpenClaw не найдена: {source_dir}")
        print_info("Проверьте путь установки OpenClaw.")
        print_info("Другой путь можно указать так: korra claw migrate --source /путь/к/.openclaw")
        return

    # Find the migration script
    script_path = _find_migration_script()
    if not script_path:
        print()
        print_error("Сценарий переноса не найден.")
        print_info("Ожидается в одном из расположений:")
        print_info(f"  {_OPENCLAW_SCRIPT}")
        print_info(f"  {_OPENCLAW_SCRIPT_INSTALLED}")
        print_info("Убедитесь, что навык openclaw-migration установлен.")
        return

    # Show what we're doing
    hermes_home = get_hermes_home()
    auto_yes = getattr(args, "yes", False)
    print()
    print_header("Настройки переноса")
    print_info(f"Источник:       {source_dir}")
    print_info(f"Назначение:     {hermes_home}")
    print_info(f"Набор:          {preset}")
    print_info(f"Перезапись:     {'да' if overwrite else 'нет; конфликты пропускаются'}")
    print_info(f"Секреты:        {'да; только разрешённые' if migrate_secrets else 'нет'}")
    if skill_conflict != "skip":
        print_info(f"Конфликты навыков: {skill_conflict}")
    if workspace_target:
        print_info(f"Рабочая папка: {workspace_target}")
    print()

    # Check if OpenClaw is still running — migrating tokens while both are
    # active will cause conflicts (e.g. Telegram 409).
    _warn_if_openclaw_running(auto_yes)

    # Check if a Hermes gateway is running with connected platforms.
    _warn_if_gateway_running(auto_yes)

    # Ensure config.yaml exists before migration tries to read it
    config_path = get_config_path()
    if not config_path.exists():
        save_config(load_config())

    # Load the migration module
    try:
        mod = _load_migration_module(script_path)
        if mod is None:
            print_error("Не удалось загрузить сценарий переноса.")
            return
    except Exception as e:
        print()
        print_error(f"Не удалось загрузить сценарий переноса: {e}")
        logger.debug("OpenClaw migration error", exc_info=True)
        return

    selected = mod.resolve_selected_options(None, None, preset=preset)
    ws_target = Path(workspace_target).resolve() if workspace_target else None

    # ── Phase 1: Always preview first ──────────────────────────
    try:
        preview = mod.Migrator(
            source_root=source_dir.resolve(),
            target_root=hermes_home.resolve(),
            execute=False,
            workspace_target=ws_target,
            overwrite=overwrite,
            migrate_secrets=migrate_secrets,
            output_dir=None,
            selected_options=selected,
            preset_name=preset,
            skill_conflict_mode=skill_conflict,
        )
        preview_report = preview.migrate()
    except Exception as e:
        print()
        print_error(f"Не удалось подготовить план переноса: {e}")
        logger.debug("OpenClaw migration preview error", exc_info=True)
        return

    preview_summary = preview_report.get("summary", {})
    preview_count = preview_summary.get("migrated", 0)
    preview_conflicts = preview_summary.get("conflict", 0)

    # "Nothing to migrate" means nothing migrated AND nothing blocked by
    # conflicts.  If there are conflicts, we still want to show the plan and
    # surface the refusal/--overwrite guidance instead of silently bailing.
    if preview_count == 0 and preview_conflicts == 0:
        print()
        print_info("В OpenClaw нет данных для переноса.")
        _print_migration_report(preview_report, dry_run=True)
        return

    print()
    if preview_count > 0:
        print_header(f"План переноса — будет импортировано объектов: {preview_count}")
    else:
        print_header(
            f"План переноса — конфликтов: {preview_conflicts}; импортировать нечего"
        )
    print_info("Изменения ещё не внесены. Проверьте список:")
    _print_migration_report(preview_report, dry_run=True)

    # If --dry-run, stop here
    if dry_run:
        return

    # ── Phase 1b: Refuse if the plan has conflicts and --overwrite is not set ─
    # Modelled on OpenClaw's assertConflictFreePlan() — apply is a safe no-op
    # on conflicts unless the user explicitly opts in to overwriting.  Without
    # this guard, the user would answer "yes, proceed" and silently end up
    # with a migration that skipped every conflicting item.
    if preview_conflicts > 0 and not overwrite:
        print()
        print_error(
            f"В плане конфликтов: {preview_conflicts}. Применение остановлено."
        )
        print_info(
            "Конфликт означает, что целевой объект уже есть в папке данных Korra. "
            "Чтобы заменить такие объекты, повторите с --overwrite. Их резервные "
            "копии будут сохранены рядом с отчётом переноса."
        )
        print_info("Для повторного просмотра полного плана используйте --dry-run.")
        return

    # ── Phase 2: Confirm and execute ───────────────────────────
    print()
    if not auto_yes:
        if not sys.stdin.isatty():
            print_info("Запуск без участия пользователя: доступен только просмотр.")
            print_info("Для выполнения повторите: korra claw migrate --yes")
            return
        if not prompt_yes_no("Выполнить перенос?", default=True):
            print_info("Перенос отменён.")
            return

    # ── Phase 2b: Pre-apply backup of the Hermes home ─────────
    # Delegates to korra_cli.backup.create_pre_migration_backup(), which
    # shares implementation with the pre-update backup (same exclusion
    # rules, same SQLite safe-copy, zip format) so the archive is
    # restorable with `hermes import`.  Mirrors OpenClaw's
    # createPreMigrationBackup posture — one atomic restore point before
    # any mutation, auto-pruned to the last 5 pre-migration zips.
    backup_archive: Optional[Path] = None
    if not no_backup:
        try:
            from korra_cli.backup import create_pre_migration_backup, _format_size
            backup_archive = create_pre_migration_backup(hermes_home=hermes_home)
            if backup_archive:
                size_str = _format_size(backup_archive.stat().st_size)
                print()
                print_success(f"Резервная копия перед переносом: {backup_archive} ({size_str})")
                print_info(f"Восстановить: korra import {backup_archive.name}")
        except Exception as e:
            print()
            print_error(f"Не удалось создать резервную копию перед переносом: {e}")
            print_info(
                "Освободите место в папке данных Korra или повторите с --no-backup, чтобы пропустить копию."
            )
            logger.debug("Pre-migration backup error", exc_info=True)
            return

    try:
        migrator = mod.Migrator(
            source_root=source_dir.resolve(),
            target_root=hermes_home.resolve(),
            execute=True,
            workspace_target=ws_target,
            overwrite=overwrite,
            migrate_secrets=migrate_secrets,
            output_dir=None,
            selected_options=selected,
            preset_name=preset,
            skill_conflict_mode=skill_conflict,
        )
        report = migrator.migrate()
    except Exception as e:
        print()
        print_error(f"Ошибка переноса: {e}")
        logger.debug("OpenClaw migration error", exc_info=True)
        if backup_archive:
            print_info(f"Резервная копия перед переносом: {backup_archive}")
            print_info(f"Восстановить: korra import {backup_archive.name}")
        return

    # Print results
    _print_migration_report(report, dry_run=False)

    # Source directory is left untouched — archiving is not the migration
    # tool's responsibility.  Users who want to clean up can run
    # 'hermes claw cleanup' separately.


def _cmd_cleanup(args):
    """Archive leftover OpenClaw directories after migration.

    Scans for OpenClaw directories that still exist after migration and offers
    to rename them to .pre-migration to free disk space.
    """
    dry_run = getattr(args, "dry_run", False)
    auto_yes = getattr(args, "yes", False)
    explicit_source = getattr(args, "source", None)

    print()
    print(
        color(
            "┌─────────────────────────────────────────────────────────┐",
            Colors.MAGENTA,
        )
    )
    print(
        color(
            "│          ⚕ Korra — очистка после OpenClaw              │",
            Colors.MAGENTA,
        )
    )
    print(
        color(
            "└─────────────────────────────────────────────────────────┘",
            Colors.MAGENTA,
        )
    )

    # Find OpenClaw directories
    if explicit_source:
        dirs_to_check = [Path(explicit_source)]
    else:
        dirs_to_check = _find_openclaw_dirs()

    if not dirs_to_check:
        print()
        print_success("Папки OpenClaw не найдены. Очищать нечего.")
        return

    # Warn if OpenClaw is still running — archiving while the service is
    # active causes it to recreate an empty skeleton directory (#8502).
    running = _detect_openclaw_processes()
    if running:
        print()
        print_error("OpenClaw всё ещё работает:")
        for detail in running:
            print_info(f"  * {detail}")
        print_info(
            "Если архивировать .openclaw/ при работающей службе, она может сразу "
            "создать пустую папку заново и повредить настройки."
        )
        print_info("Сначала остановите OpenClaw: systemctl --user stop openclaw-gateway.service")
        print()
        if not auto_yes:
            if not sys.stdin.isatty():
                print_info("Запуск без участия пользователя остановлен. Остановите OpenClaw и повторите.")
                return
            if not prompt_yes_no("Всё равно продолжить?", default=False):
                print_info("Отменено. Остановите OpenClaw и выполните: korra claw cleanup")
                return

    total_archived = 0

    for source_dir in dirs_to_check:
        print()
        print_header(f"Найдена папка: {source_dir}")

        # Scan for state files
        state_files = _scan_workspace_state(source_dir)

        # Show directory stats
        try:
            workspace_dirs = [
                d for d in source_dir.iterdir()
                if d.is_dir() and not d.name.startswith(".")
                and any((d / name).exists() for name in ("todo.json", "SOUL.md", "MEMORY.md", "USER.md"))
            ]
        except OSError:
            workspace_dirs = []

        if workspace_dirs:
            print_info(f"Рабочих папок: {len(workspace_dirs)}")
            for ws in workspace_dirs[:5]:
                items = []
                if (ws / "todo.json").exists():
                    items.append("todo.json")
                if (ws / "sessions").is_dir():
                    items.append("sessions/")
                if (ws / "SOUL.md").exists():
                    items.append("SOUL.md")
                if (ws / "MEMORY.md").exists():
                    items.append("MEMORY.md")
                detail = ", ".join(items) if items else "пусто"
                print(f"      {ws.name}/  ({detail})")
            if len(workspace_dirs) > 5:
                print(f"      … и ещё {len(workspace_dirs) - 5}")

        if state_files:
            print()
            print(color(f"  Найдено файлов состояния: {len(state_files)}:", Colors.YELLOW))
            for path, desc in state_files[:8]:
                print(f"      {desc}")
            if len(state_files) > 8:
                print(f"      … и ещё {len(state_files) - 8}")

        print()

        if dry_run:
            archive_path = _archive_directory(source_dir, dry_run=True)
            print_info(f"Будет архивировано: {source_dir} → {archive_path}")
        elif not auto_yes and not sys.stdin.isatty():
            print_info(f"Запуск без участия пользователя; будет архивировано: {source_dir}")
            print_info("Для выполнения повторите: korra claw cleanup --yes")
        elif auto_yes or prompt_yes_no(f"Архивировать {source_dir}?", default=True):
            try:
                archive_path = _archive_directory(source_dir)
                print_success(f"Архивировано: {source_dir} → {archive_path}")
                total_archived += 1
            except OSError as e:
                print_error(f"Не удалось архивировать: {e}")
                print_info(f"Попробуйте вручную: mv {source_dir} {source_dir}.pre-migration")
        else:
            print_info("Пропущено.")

    # Summary
    print()
    if dry_run:
        _n_dirs = len(dirs_to_check)
        print_info(
            f"Проверка завершена. Будет архивировано папок: {_n_dirs}."
        )
        print_info("Для архивации запустите без --dry-run.")
    elif total_archived:
        print_success(
            f"Архивировано папок OpenClaw: {total_archived}."
        )
        print_info("Папки переименованы, а не удалены. Для отмены верните прежние имена.")
    else:
        print_info("Ни одна папка не архивирована.")


def _print_migration_report(report: dict, dry_run: bool):
    """Print a formatted migration report."""
    summary = report.get("summary", {})
    migrated = summary.get("migrated", 0)
    skipped = summary.get("skipped", 0)
    conflicts = summary.get("conflict", 0)
    errors = summary.get("error", 0)

    print()
    if dry_run:
        print_header("Результат проверки")
        print_info("Файлы не изменены. Показан предварительный результат.")
    else:
        print_header("Результат переноса")

    print()

    # Detailed items
    items = report.get("items", [])
    if items:
        # Group by status
        migrated_items = [i for i in items if i.get("status") == "migrated"]
        skipped_items = [i for i in items if i.get("status") == "skipped"]
        conflict_items = [i for i in items if i.get("status") == "conflict"]
        error_items = [i for i in items if i.get("status") == "error"]

        if migrated_items:
            label = "Будет перенесено" if dry_run else "Перенесено"
            print(color(f"  ✓ {label}:", Colors.GREEN))
            for item in migrated_items:
                kind = item.get("kind", "неизвестно")
                dest = item.get("destination", "")
                if dest:
                    dest_short = str(dest).replace(str(Path.home()), "~")
                    print(f"      {kind:<22s} → {dest_short}")
                else:
                    print(f"      {kind}")
            print()

        if conflict_items:
            print(color("  ⚠ Конфликты; пропущены, для замены используйте --overwrite:", Colors.YELLOW))
            for item in conflict_items:
                kind = item.get("kind", "неизвестно")
                reason = item.get("reason", "уже существует")
                print(f"      {kind:<22s}  {reason}")
            print()

        if skipped_items:
            print(color("  ─ Пропущено:", Colors.DIM))
            for item in skipped_items:
                kind = item.get("kind", "неизвестно")
                reason = item.get("reason", "")
                print(f"      {kind:<22s}  {reason}")
            print()

        if error_items:
            print(color("  ✗ Ошибки:", Colors.RED))
            for item in error_items:
                kind = item.get("kind", "неизвестно")
                reason = item.get("reason", "неизвестная ошибка")
                print(f"      {kind:<22s}  {reason}")
            print()

    # Summary line
    parts = []
    if migrated:
        action = "будет перенесено" if dry_run else "перенесено"
        parts.append(f"{migrated} {action}")
    if conflicts:
        parts.append(f"конфликтов: {conflicts}")
    if skipped:
        parts.append(f"пропущено: {skipped}")
    if errors:
        parts.append(f"ошибок: {errors}")

    if parts:
        print_info(f"Итого: {', '.join(parts)}")
    else:
        print_info("Переносить нечего.")

    # Output directory
    output_dir = report.get("output_dir")
    if output_dir:
        print_info(f"Полный отчёт сохранён: {output_dir}")

    if dry_run:
        print()
        print_info("Чтобы выполнить перенос, запустите без --dry-run:")
        print_info(f"  korra claw migrate --preset {report.get('preset', 'full')}")
    elif migrated:
        print()
        print_success("Перенос завершён!")
        # Warn if API keys were skipped (migrate_secrets not enabled)
        skipped_keys = [
            i for i in report.get("items", [])
            if i.get("kind") == "provider-keys" and i.get("status") == "skipped"
        ]
        if skipped_keys:
            print()
            print(color("  ⚠ Ключи API не перенесены: перенос секретов по умолчанию выключен.", Colors.YELLOW))
            print(color("  Добавьте OPENROUTER_API_KEY и другие ключи провайдеров вручную.", Colors.YELLOW))
            print()
            print_info("Чтобы перенести ключи API, повторите:")
            print_info("  korra claw migrate --migrate-secrets")
            print()
            print_info("Или добавьте ключ вручную:")
            print_info("  korra config set OPENROUTER_API_KEY sk-or-v1-...")

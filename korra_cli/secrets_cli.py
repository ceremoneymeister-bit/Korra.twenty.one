"""CLI handlers for ``hermes secrets bitwarden ...``.

Subcommands:
    setup    — interactive wizard: install bws, prompt for token + project, test fetch
    status   — show current config + binary version + token validation status
    sync     — run a fetch right now and show what would be applied (dry-run friendly)
    disable  — flip ``secrets.bitwarden.enabled`` to False
    install  — just download the bws binary (no token / project required)
"""

from __future__ import annotations

import argparse
import io
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import List, Optional

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

# NOTE: the Bitwarden backend (``agent.secret_sources.bitwarden``) pulls in
# ``cryptography`` at module-import time.  On Windows the resulting
# ``cryptography._rust.pyd`` is mapped into the running process — and when
# that process is ``hermes update``, the self-lock preflight detects the
# loaded native module and defers (#86781).  Keep the backend import lazy:
# this module is registered parse-time from ``korra_cli.main`` and must not
# touch ``bw`` until a handler actually runs.
#
# ``_BWS_VERSION`` is duplicated here (as a plain string) so ``register_cli``
# can render the ``install --help`` text without importing the backend.
# ``agent.secret_sources.bitwarden._BWS_VERSION`` is the source of truth;
# bump both together when pinning a new bws release.
_BWS_VERSION = "2.0.0"

from korra_cli.config import (
    get_env_path,
    load_config,
    save_config,
    save_env_value,
)
from korra_cli.secret_prompt import masked_secret_prompt


def _load_bw():
    """Import ``agent.secret_sources.bitwarden`` on first use (crypto payload)."""
    from agent.secret_sources import bitwarden as _bw

    return _bw


def __getattr__(name: str):
    """PEP 562 module-level lazy resolver.

    Existing callers (and upstream tests) monkeypatch attributes on
    ``korra_cli.secrets_cli.bw`` directly.  Resolving that attribute at
    module-import time would re-import ``cryptography`` eagerly — the very
    self-lock we are preventing (#86781).  Defer the backend import until
    the first actual attribute access, so ``import korra_cli.secrets_cli``
    stays crypto-free while ``secrets_cli.bw.find_bws`` still resolves.
    """
    if name == "bw":
        return _load_bw()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


# ---------------------------------------------------------------------------
# Argparse wiring — called from korra_cli.main
# ---------------------------------------------------------------------------


def register_cli(parent_parser: argparse.ArgumentParser) -> None:
    """Attach the ``bitwarden`` subcommand tree to a parent parser.

    Called from ``korra_cli.main`` as part of building the top-level
    ``hermes secrets`` parser.
    """
    sub = parent_parser.add_subparsers(dest="secrets_bw_command")

    setup = sub.add_parser(
        "setup",
        help='Мастер: установить bws, сохранить токен доступа и выбрать проект',
    )
    setup.add_argument(
        "--project-id",
        help='Выбрать проект по UUID без запроса',
    )
    setup.add_argument(
        "--access-token",
        help='Передать токен доступа без интерактивного ввода; сохранится в .env',
    )
    setup.add_argument(
        "--server-url",
        help=(
            'Регион Bitwarden или свой сервер: https://vault.bitwarden.com — США (по умолчанию), https://vault.bitwarden.eu — ЕС, либо ваш адрес. Пропускает выбор региона в меню.'
        ),
    )
    setup.set_defaults(func=cmd_setup)

    status = sub.add_parser(
        "status",
        help='Показать настройки, программу и результат проверки токена',
    )
    status.set_defaults(func=cmd_status)

    token = sub.add_parser(
        "token",
        help='Заменить токен доступа: проверить новый и сохранить в .env',
    )
    token.add_argument(
        "--access-token",
        help='Передать новый токен без интерактивного ввода; по умолчанию скрытый запрос',
    )
    token.add_argument(
        "--no-verify",
        action="store_true",
        help='Сохранить без предварительной проверки Bitwarden; не рекомендуется',
    )
    token.set_defaults(func=cmd_token)

    sync = sub.add_parser("sync", help='Получить секреты сейчас и показать изменения')
    sync.add_argument(
        "--apply",
        action="store_true",
        help='Экспортировать секреты в переменные текущей оболочки; по умолчанию только просмотр',
    )
    sync.set_defaults(func=cmd_sync)

    disable = sub.add_parser("disable", help='Отключить подключение Bitwarden')
    disable.set_defaults(func=cmd_disable)

    install = sub.add_parser(
        "install",
        help=f"Download and verify the pinned bws binary (v{_BWS_VERSION})",
    )
    install.add_argument(
        "--force",
        action="store_true",
        help='Загрузить заново, даже если управляемая копия уже существует',
    )
    install.set_defaults(func=cmd_install)


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------


def cmd_setup(args: argparse.Namespace) -> int:
    bw = _load_bw()
    console = Console()
    console.print(
        Panel.fit(
            "[bold]Настройка Bitwarden Secrets Manager[/bold]\n\n"
            "Токен доступа создаётся в приложении Bitwarden:\n"
            "  Secrets Manager → Machine accounts → [your account] →\n"
            "  Access tokens → Create access token\n\n"
            "Скопируйте токен, начинающийся с [cyan]0.[/cyan]… Позже его нельзя будет посмотреть.",
            border_style="cyan",
        )
    )

    # ------------------------------------------------------------------ binary
    console.print()
    console.print("[bold]Шаг 1[/bold]  Установка bws CLI")
    try:
        binary = bw.find_bws(install_if_missing=False)
        if binary is None:
            console.print("  bws не найден в PATH; загружаю…")
            binary = bw.install_bws()
        version = _bws_version(binary)
        console.print(f"  [green]✓[/green] {binary}  ({version})")
    except Exception as exc:  # noqa: BLE001
        console.print(f"  [red]✗ Не удалось установить bws: {exc}[/red]")
        console.print(
            "  Установить вручную: "
            "https://github.com/bitwarden/sdk-sm/releases"
        )
        return 1

    # -- non-interactive guard --
    if not sys.stdin.isatty():
        missing = []
        if not (args.access_token and args.access_token.strip()):
            missing.append("--access-token")
        if not (args.server_url and args.server_url.strip()):
            # Also accept BWS_SERVER_URL env var as non-interactive substitute
            if not os.environ.get("BWS_SERVER_URL", "").strip():
                missing.append("--server-url")
        if not (args.project_id and args.project_id.strip()):
            missing.append("--project-id")
        if missing:
            console.print(
                f"  [red]Для запуска без TTY укажите все параметры настройки.[/red]\n"
                f"  Не указаны: {', '.join(missing)}\n\n"
                "  Использование:\n"
                "    korra secrets bitwarden setup \\\n"
                "      --access-token '0.xxx' \\\n"
                "      --server-url 'https://vault.bitwarden.com' \\\n"
                "      --project-id 'xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx'"
            )
            return 1

    # ------------------------------------------------------------------- token
    console.print()
    console.print("[bold]Шаг 2[/bold]  Токен доступа")
    cfg = load_config()
    secrets_cfg = (cfg.setdefault("secrets", {})
                     .setdefault("bitwarden", {}))
    token_env = secrets_cfg.get("access_token_env", "BWS_ACCESS_TOKEN")

    token = (args.access_token or "").strip()
    if not token:
        token = masked_secret_prompt(f"  Вставьте токен доступа ({token_env}): ").strip()
    if not token:
        console.print("  [red]Токен пуст. Настройка отменена.[/red]")
        return 1
    if not token.startswith("0."):
        console.print(
            "  [yellow]Внимание: токен не начинается с '0.'. Возможно, это не токен BSM; "
            "продолжаю по вашему выбору.[/yellow]"
        )

    save_env_value(token_env, token)
    os.environ[token_env] = token  # so the test fetch below sees it
    console.print(f"  [green]✓[/green] сохранён в {get_env_path()} как {token_env}")

    # ------------------------------------------------------------------ region
    console.print()
    console.print("[bold]Шаг 3[/bold]  Регион Bitwarden")
    server_url = _resolve_server_url(args, secrets_cfg, console)
    if server_url is None:
        return 1
    if server_url:
        console.print(f"  [green]✓[/green] используется {server_url}")
    else:
        console.print(
            "  [green]✓[/green] используются настройки bws по умолчанию "
            "(US Cloud, https://vault.bitwarden.com)"
        )

    # ------------------------------------------------------------------- project
    if args.project_id and args.project_id.strip():
        project_id = args.project_id.strip()
    else:
        console.print()
        console.print("[bold]Шаг 4[/bold]  Выбор проекта")
        project_id = ""
        projects = _list_projects(binary, token, console, server_url=server_url)
        if projects is None:
            return 1
        if not projects:
            console.print("  [yellow]Этому машинному аккаунту не доступны проекты.[/yellow]")
            console.print(
                "  В приложении Bitwarden откройте машинный аккаунт → Projects и "
                "дайте ему доступ хотя бы к одному проекту."
            )
            return 1

        table = Table(show_header=True, header_style="bold")
        table.add_column("#", style="cyan", width=4)
        table.add_column("Название")
        table.add_column("ID", style="dim")
        for i, p in enumerate(projects, 1):
            table.add_row(str(i), p.get("name", "?"), p.get("id", "?"))
        console.print(table)

        while True:
            choice = console.input(f"  Выберите проект [1-{len(projects)}]: ").strip()
            if not choice:
                continue
            try:
                idx = int(choice)
            except ValueError:
                console.print("  [red]Введите номер.[/red]")
                continue
            if 1 <= idx <= len(projects):
                project_id = projects[idx - 1]["id"]
                break
            console.print(f"  [red]Выберите номер от 1 до {len(projects)}.[/red]")

    # ------------------------------------------------------------------- test
    console.print()
    step_num = 5 if not (args.project_id and args.project_id.strip()) else 4
    console.print(f"[bold]Шаг {step_num}[/bold]  Проверка загрузки")
    try:
        secrets, warnings = bw.fetch_bitwarden_secrets(
            access_token=token,
            project_id=project_id,
            binary=binary,
            use_cache=False,
            server_url=server_url,
        )
    except Exception as exc:  # noqa: BLE001
        console.print(f"  [red]✗ Не удалось загрузить секреты: {exc}[/red]")
        return 1

    if not secrets:
        console.print("  [yellow]Загрузка прошла успешно, но в проекте нет секретов.[/yellow]")
    else:
        table = Table(show_header=True, header_style="bold")
        table.add_column("Название", style="cyan")
        table.add_column("Состояние")
        for key in sorted(secrets):
            if key == token_env:
                status = "[dim]служебный токен; сам себя не заменяет[/dim]"
            elif os.environ.get(key):
                status = "[yellow]уже задан в окружении; будет заменён[/yellow]"
            else:
                status = "[green]новый[/green]"
            table.add_row(key, status)
        console.print(table)
    for w in warnings:
        console.print(f"  [yellow]предупреждение:[/yellow] {w}")

    # ------------------------------------------------------------------- save
    secrets_cfg["enabled"] = True
    secrets_cfg["project_id"] = project_id
    secrets_cfg["server_url"] = server_url
    secrets_cfg.setdefault("access_token_env", token_env)
    secrets_cfg.setdefault("cache_ttl_seconds", 300)
    secrets_cfg.setdefault("override_existing", True)
    secrets_cfg.setdefault("auto_install", True)
    save_config(cfg)

    console.print()
    console.print(
        "[green]✓ Bitwarden Secrets Manager включён.[/green] "
        "Секреты будут загружаться при запуске каждого процесса Korra."
    )
    console.print(
        "  Состояние: [cyan]korra secrets bitwarden status[/cyan]\n"
        "  Обновить:   [cyan]korra secrets bitwarden sync[/cyan]\n"
        "  Выключить:  [cyan]korra secrets bitwarden disable[/cyan]"
    )
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    bw = _load_bw()
    console = Console()
    cfg = load_config()
    bw_cfg = (cfg.get("secrets") or {}).get("bitwarden") or {}

    enabled = bool(bw_cfg.get("enabled"))
    token_env = bw_cfg.get("access_token_env", "BWS_ACCESS_TOKEN")
    project_id = bw_cfg.get("project_id", "")
    server_url = str(bw_cfg.get("server_url", "") or "").strip()
    token = os.environ.get(token_env, "").strip()
    token_set = bool(token)
    binary = bw.find_bws(install_if_missing=False)
    token_validation, validation_messages = _token_validation_status(
        enabled=enabled,
        binary=binary,
        token=token,
        server_url=server_url,
    )

    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column("", style="bold")
    table.add_column("")
    table.add_row("Включено",          _yn(enabled))
    table.add_row("Переменная токена", token_env)
    table.add_row("Токен в окружении", _yn(token_set))
    table.add_row("Проверка токена",    token_validation)
    table.add_row("ID проекта",         project_id or "[dim](не задан)[/dim]")
    table.add_row(
        "Адрес сервера",
        server_url or "[dim]по умолчанию: US Cloud, https://vault.bitwarden.com[/dim]",
    )
    table.add_row("Заменять существующие", _yn(bool(bw_cfg.get("override_existing", False))))
    table.add_row("Время кеша, с", str(bw_cfg.get("cache_ttl_seconds", 300)))
    table.add_row("Автоустановка", _yn(bool(bw_cfg.get("auto_install", True))))

    if binary:
        table.add_row("bws binary",  f"{binary} ({_bws_version(binary)})")
    else:
        table.add_row("Программа bws", "[yellow]не установлена[/yellow]")

    console.print(Panel(table, title="Bitwarden Secrets Manager", border_style="cyan"))
    for message in validation_messages:
        console.print(message)

    if not enabled:
        console.print("\n  Для включения выполните [cyan]korra secrets bitwarden setup[/cyan].")
        return 0
    if not token_set:
        console.print(
            f"\n  [yellow]Интеграция включена, но {token_env} не задана. "
            "Korra пропустит BSM и предупредит при следующем запуске.[/yellow]"
        )
    if not project_id:
        console.print(
            "\n  [yellow]Интеграция включена, но project_id не задан; загружать нечего.[/yellow]"
        )
    return 0


def cmd_token(args: argparse.Namespace) -> int:
    """Rotate the BSM access token without re-running the whole setup wizard.

    Prompts for (or accepts via ``--access-token``) a new machine-account
    token, probes Bitwarden with it (unless ``--no-verify``), and only then
    persists it to .env — so a bad paste never bricks the working token.
    """
    bw = _load_bw()
    console = Console()
    cfg = load_config()
    bw_cfg = (cfg.get("secrets") or {}).get("bitwarden") or {}
    token_env = bw_cfg.get("access_token_env", "BWS_ACCESS_TOKEN")
    server_url = str(bw_cfg.get("server_url", "") or "").strip()

    token = (args.access_token or "").strip()
    if not token:
        if not sys.stdin.isatty():
            console.print(
                "[red]Нет интерактивного терминала; передайте токен через --access-token.[/red]"
            )
            return 1
        console.print(
            "Создайте новый токен в приложении Bitwarden:\n"
            "  Secrets Manager → Machine accounts → [your account] → "
            "Access tokens → Create access token\n"
        )
        token = masked_secret_prompt(f"Вставьте новый токен доступа ({token_env}): ").strip()
    if not token:
        console.print("[red]Токен пуст; отменено.[/red]")
        return 1
    if not token.startswith("0."):
        console.print(
            "[yellow]Внимание: токен не начинается с '0.'. Возможно, это не токен доступа BSM.[/yellow]"
        )

    if not args.no_verify:
        binary = bw.find_bws(install_if_missing=True)
        if binary is None:
            console.print(
                "[red]Программа bws недоступна; проверить токен нельзя. "
                "Чтобы всё равно сохранить его, повторите с --no-verify.[/red]"
            )
            return 1
        console.print("Проверяю токен в Bitwarden…")
        projects = _list_projects(binary, token, console, server_url=server_url)
        if projects is None:
            console.print(
                "[red]✗ Новый токен отклонён; изменения не внесены.[/red]"
            )
            return 1
        console.print(
            f"[green]✓ Токен принят[/green]; доступно проектов: {len(projects)}."
        )
        project_id = str(bw_cfg.get("project_id", "") or "")
        if project_id and projects and project_id not in {p["id"] for p in projects}:
            console.print(
                f"[yellow]Внимание: проект {project_id} недоступен этому машинному аккаунту. "
                "Выдайте доступ в Bitwarden или повторите `korra secrets bitwarden setup` "
                "и выберите другой проект.[/yellow]"
            )

    save_env_value(token_env, token)
    os.environ[token_env] = token
    # Old cached pulls are keyed on the previous token's fingerprint; drop
    # them so the next startup fetches fresh with the new credential.
    bw.clear_caches()
    console.print(
        f"[green]✓[/green] Сохранено в {get_env_path()} как {token_env}. "
        "Изменение применится при следующем запуске Korra."
    )
    if not bw_cfg.get("enabled"):
        console.print(
            "[yellow]Bitwarden сейчас выключен. Выполните `korra secrets bitwarden setup` "
            "или задайте secrets.bitwarden.enabled: true.[/yellow]"
        )
    return 0


def cmd_sync(args: argparse.Namespace) -> int:
    bw = _load_bw()
    console = Console()
    cfg = load_config()
    bw_cfg = (cfg.get("secrets") or {}).get("bitwarden") or {}
    if not bw_cfg.get("enabled"):
        console.print(
            "[yellow]Интеграция Bitwarden выключена. Сначала выполните "
            "`korra secrets bitwarden setup`.[/yellow]"
        )
        return 1

    token_env = bw_cfg.get("access_token_env", "BWS_ACCESS_TOKEN")
    token = os.environ.get(token_env, "").strip()
    if not token:
        console.print(f"[red]Переменная {token_env} не задана.[/red]")
        return 1

    project_id = bw_cfg.get("project_id", "")
    if not project_id:
        console.print("[red]project_id не настроен.[/red]")
        return 1

    server_url = str(bw_cfg.get("server_url", "") or "").strip()

    try:
        secrets, warnings = bw.fetch_bitwarden_secrets(
            access_token=token,
            project_id=project_id,
            use_cache=False,
            server_url=server_url,
        )
    except Exception as exc:  # noqa: BLE001
        console.print(f"[red]Не удалось загрузить секреты: {exc}[/red]")
        return 1

    if not secrets:
        console.print("[yellow]В проекте нет секретов.[/yellow]")
        return 0

    override = bool(bw_cfg.get("override_existing", False)) or args.apply
    table = Table(show_header=True, header_style="bold")
    table.add_column("Название", style="cyan")
    table.add_column("Действие")
    applied = 0
    for key in sorted(secrets):
        if key == token_env:
            table.add_row(key, "[dim]пропустить: служебный токен[/dim]")
            continue
        already = bool(os.environ.get(key))
        if already and not override:
            table.add_row(key, "[dim]пропустить: уже задан[/dim]")
            continue
        if args.apply:
            os.environ[key] = secrets[key]
            applied += 1
            table.add_row(key, "[green]добавлен[/green]" + (" (заменён)" if already else ""))
        else:
            table.add_row(key, "[green]будет добавлен[/green]" + (" (заменит существующий)" if already else ""))

    console.print(table)
    for w in warnings:
        console.print(f"[yellow]предупреждение:[/yellow] {w}")

    if not args.apply:
        console.print(
            "\n  Это была проверка. Секреты автоматически загрузятся при следующем "
            "запуске [cyan]korra[/cyan]. Для текущего процесса повторите с [cyan]--apply[/cyan]."
        )
    else:
        console.print(f"\n  [green]В текущий процесс добавлено секретов: {applied}.[/green]")
    return 0


def cmd_disable(args: argparse.Namespace) -> int:
    console = Console()
    cfg = load_config()
    bw_cfg = (cfg.setdefault("secrets", {})
                .setdefault("bitwarden", {}))
    bw_cfg["enabled"] = False
    save_config(cfg)
    console.print(
        "[green]Выключено.[/green] При следующем запуске Korra секреты Bitwarden "
        "загружаться не будут.\n  Токен доступа оставлен в .env; удалите его вручную, "
        "если хотите также отозвать учётные данные."
    )
    return 0


def cmd_install(args: argparse.Namespace) -> int:
    bw = _load_bw()
    console = Console()
    try:
        path = bw.install_bws(force=bool(args.force))
        console.print(f"[green]✓[/green] {path}  ({_bws_version(path)})")
        return 0
    except Exception as exc:  # noqa: BLE001
        console.print(f"[red]Ошибка установки: {exc}[/red]")
        return 1


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _yn(b: bool) -> str:
    return "[green]да[/green]" if b else "[dim]нет[/dim]"


def _bws_version(binary: Path) -> str:
    try:
        res = subprocess.run(
            [str(binary), "--version"],
            capture_output=True,
            text=True, encoding='utf-8', errors='replace',
            timeout=5,
        )
        if res.returncode == 0:
            return (res.stdout or res.stderr).strip().splitlines()[0]
    except (OSError, subprocess.TimeoutExpired):
        pass
    return "версия неизвестна"


def _token_validation_status(
    *,
    enabled: bool,
    binary: Optional[Path],
    token: str,
    server_url: str = "",
) -> tuple[str, list[str]]:
    if not enabled:
        return "[dim]не проверен[/dim] (интеграция выключена)", []
    if not token:
        return "[dim]не проверен[/dim] (токен отсутствует)", []
    if binary is None:
        return "[dim]не проверен[/dim] (bws не установлен)", []

    messages: list[str] = []
    if not token.startswith("0."):
        messages.append(
            "  [yellow]Внимание: токен не начинается с '0.'. Возможно, это не токен BSM.[/yellow]"
        )

    capture = io.StringIO()
    probe_console = Console(file=capture, record=True, width=200)
    projects = _list_projects(binary, token, probe_console, server_url=server_url)
    if projects is None:
        details = probe_console.export_text(styles=False).strip()
        if details:
            messages.extend(line.rstrip() for line in details.splitlines())
        return "[red]ошибка[/red]", messages
    return "[green]пройдено[/green]", messages


def _list_projects(
    binary: Path, token: str, console: Console, *, server_url: str = ""
) -> Optional[List[dict]]:
    """Call ``bws project list`` and return the parsed list, or None on failure."""
    # Secret-manager CLI child: intentionally receives tokens — no scrub,
    # no HOME rewrite (bws stores state under the real user home).
    from tools.environments.local import build_subprocess_env
    env = build_subprocess_env(scrub_secrets=False, inherit_profile_home=False)
    env["BWS_ACCESS_TOKEN"] = token
    env.setdefault("NO_COLOR", "1")
    if server_url:
        env["BWS_SERVER_URL"] = server_url
    try:
        res = subprocess.run(
            [str(binary), "project", "list", "--output", "json"],
            env=env,
            capture_output=True,
            text=True, encoding='utf-8', errors='replace',
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        console.print(f"  [red]Не удалось получить список проектов: {exc}[/red]")
        return None

    if res.returncode != 0:
        err = (res.stderr or res.stdout).strip()[:300]
        console.print(f"  [red]Команда bws project list завершилась с ошибкой: {err}[/red]")
        lowered = err.lower()
        if "invalid_client" in lowered or "400 bad request" in lowered:
            console.print(
                "  [yellow]Ответ 'invalid_client' от американского сервера обычно означает, "
                "что токен выпущен для другого региона Bitwarden. Повторите "
                "[cyan]korra secrets bitwarden setup[/cyan] и выберите EU или свой сервер, "
                "либо задайте [cyan]secrets.bitwarden.server_url[/cyan] в config.yaml.[/yellow]"
            )
        elif "authorization" in lowered or "invalid" in lowered:
            console.print(
                "  [yellow]Возможно, токен доступа неверен или отозван. "
                "Проверьте его в приложении Bitwarden.[/yellow]"
            )
        return None

    try:
        data = json.loads(res.stdout or "[]")
    except json.JSONDecodeError as exc:
        console.print(f"  [red]bws вернул данные не в формате JSON: {exc}[/red]")
        return None
    if not isinstance(data, list):
        return []
    return [p for p in data if isinstance(p, dict) and p.get("id")]


# Canonical Bitwarden region endpoints.  Keep in sync with what Bitwarden
# publishes — these are stable but if a third region appears, add it here
# and to the prompt below.
_REGION_PRESETS = [
    ("US Cloud  (https://vault.bitwarden.com — bws default)", ""),
    ("EU Cloud  (https://vault.bitwarden.eu)", "https://vault.bitwarden.eu"),
]


def _resolve_server_url(
    args: argparse.Namespace,
    secrets_cfg: dict,
    console: Console,
) -> Optional[str]:
    """Pick a Bitwarden server URL for setup.

    Resolution order:
      1. ``--server-url`` CLI flag (non-interactive)
      2. ``BWS_SERVER_URL`` env var (so users running with that already set
         in their shell don't have to re-enter it)
      3. Existing ``secrets.bitwarden.server_url`` value (for re-runs)
      4. Interactive menu: US / EU / self-hosted

    Returns the chosen URL as a string (empty string = bws default,
    i.e. US Cloud).  Returns None if the user aborted with an empty
    custom URL.
    """
    if args.server_url and args.server_url.strip():
        return args.server_url.strip()

    env_url = os.environ.get("BWS_SERVER_URL", "").strip()
    if env_url:
        console.print(
            f"  В оболочке задано [cyan]BWS_SERVER_URL[/cyan]={env_url}; использую это значение."
        )
        return env_url

    existing = str(secrets_cfg.get("server_url", "") or "").strip()
    if existing:
        console.print(
            f"  Текущая настройка: [cyan]{existing}[/cyan]. "
            "Нажмите Enter, чтобы сохранить её, или выберите другой вариант."
        )

    table = Table(show_header=True, header_style="bold", box=None, padding=(0, 2))
    table.add_column("#", style="cyan", width=4)
    table.add_column("Регион или адрес")
    for i, (label, _url) in enumerate(_REGION_PRESETS, 1):
        table.add_row(str(i), label)
    table.add_row(str(len(_REGION_PRESETS) + 1), "Собственный сервер или адрес")
    console.print(table)

    custom_idx = len(_REGION_PRESETS) + 1
    while True:
        prompt = f"  Выберите регион [1-{custom_idx}]"
        if existing:
            prompt += " (Enter — сохранить текущий)"
        prompt += ": "
        choice = console.input(prompt).strip()
        if not choice:
            if existing:
                return existing
            console.print("  [red]Введите номер.[/red]")
            continue
        try:
            idx = int(choice)
        except ValueError:
            console.print("  [red]Введите номер.[/red]")
            continue
        if 1 <= idx <= len(_REGION_PRESETS):
            return _REGION_PRESETS[idx - 1][1]
        if idx == custom_idx:
            custom = console.input(
                "  Введите адрес сервера Bitwarden, например "
                "https://vault.example.com: "
            ).strip()
            if not custom:
                console.print("  [red]Адрес пуст; отменено.[/red]")
                return None
            if not custom.startswith(("http://", "https://")):
                console.print(
                    "  [yellow]Внимание: адрес не начинается с http:// или https://; "
                    "bws может его отклонить.[/yellow]"
                )
            return custom
        console.print(f"  [red]Выберите номер от 1 до {custom_idx}.[/red]")

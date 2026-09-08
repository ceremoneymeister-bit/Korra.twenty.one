"""CLI handlers for ``hermes secrets onepassword ...``.

Subcommands:
    setup    — verify the op CLI, set account / token env var, enable
    status   — show config + op binary + auth + configured references
    set      — map an env var to an ``op://…`` reference
    remove   — drop a mapping
    sync     — resolve references now and show what would be applied (dry-run)
    disable  — flip ``secrets.onepassword.enabled`` to False

Unlike Bitwarden, the ``op`` binary is NOT auto-installed: 1Password publishes
the CLI through OS package managers and signed installers, so Hermes expects
an already-installed, already-authenticated ``op`` and never downloads one.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path
from typing import Optional

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from agent.secret_sources import onepassword as op_src
from korra_cli.config import (
    get_env_path,
    load_config,
    save_config,
    save_env_value,
)
from korra_cli.secret_prompt import masked_secret_prompt

_DEFAULT_TOKEN_ENV = "OP_SERVICE_ACCOUNT_TOKEN"
_DOCS_URL = "https://developer.1password.com/docs/cli/get-started/"


# ---------------------------------------------------------------------------
# Argparse wiring — called from korra_cli.main
# ---------------------------------------------------------------------------


def register_cli(parent_parser: argparse.ArgumentParser) -> None:
    """Attach the ``onepassword`` subcommand tree to a parent parser."""
    sub = parent_parser.add_subparsers(dest="secrets_op_command")

    setup = sub.add_parser(
        "setup",
        help='Проверить программу op, задать учётную запись и токен, включить подключение',
    )
    setup.add_argument(
        "--account",
        help='Краткое имя учётной записи 1Password или адрес входа для op --account',
    )
    setup.add_argument(
        "--token-env",
        help=f"Env var holding a service-account token (default {_DEFAULT_TOKEN_ENV})",
    )
    setup.add_argument(
        "--token",
        help='Токен служебной учётной записи для сохранения в .env без интерактивного ввода',
    )
    setup.add_argument(
        "--binary-path",
        help='Абсолютный путь к op без поиска в PATH',
    )
    setup.set_defaults(func=cmd_setup)

    status = sub.add_parser("status", help='Показать настройки, программу op и ссылки')
    status.set_defaults(func=cmd_status)

    token = sub.add_parser(
        "token",
        help='Заменить токен служебной учётной записи: проверить и сохранить в .env',
    )
    token.add_argument(
        "--token",
        help='Передать новый токен без интерактивного ввода; по умолчанию скрытый запрос',
    )
    token.add_argument(
        "--no-verify",
        action="store_true",
        help='Сохранить без предварительной проверки 1Password; не рекомендуется',
    )
    token.set_defaults(func=cmd_token)

    set_p = sub.add_parser("set", help='Связать переменную среды со ссылкой op://')
    set_p.add_argument("env_var", help='Имя переменной среды, например OPENAI_API_KEY')
    set_p.add_argument("reference", help='Ссылка 1Password, например op://Private/OpenAI/api key')
    set_p.set_defaults(func=cmd_set)

    remove = sub.add_parser("remove", help='Удалить связь переменной среды со ссылкой')
    remove.add_argument("env_var", help='Имя переменной для удаления связи')
    remove.set_defaults(func=cmd_remove)

    sync = sub.add_parser("sync", help='Получить значения по ссылкам сейчас и показать изменения')
    sync.add_argument(
        "--apply",
        action="store_true",
        help='Экспортировать полученные значения в текущую оболочку; по умолчанию только просмотр',
    )
    sync.set_defaults(func=cmd_sync)

    disable = sub.add_parser("disable", help='Отключить подключение 1Password')
    disable.set_defaults(func=cmd_disable)


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------


def cmd_setup(args: argparse.Namespace) -> int:
    console = Console()
    console.print(
        Panel.fit(
            "[bold]Настройка секретов 1Password[/bold]\n\n"
            "Korra получает ссылки [cyan]op://vault/item/field[/cyan] через установленную\n"
            "и авторизованную программу 1Password CLI (`op`).\n\n"
            f"Если её нет, установите и войдите: [cyan]{_DOCS_URL}[/cyan]",
            border_style="cyan",
        )
    )

    cfg = load_config()
    op_cfg = cfg.setdefault("secrets", {}).setdefault("onepassword", {})

    # ------------------------------------------------------------------ binary
    console.print()
    console.print("[bold]Шаг 1[/bold]  Поиск программы op")
    binary_path = (args.binary_path or op_cfg.get("binary_path", "") or "").strip()
    binary = op_src.find_op(binary_path)
    if binary is None:
        if binary_path:
            console.print(f"  [red]✗ {binary_path} не является исполняемой программой op.[/red]")
        else:
            console.print("  [red]✗ op не найдена в PATH.[/red]")
        console.print(f"  Установить 1Password CLI: {_DOCS_URL}")
        return 1
    console.print(f"  [green]✓[/green] {binary}  ({_op_version(binary)})")
    if binary_path:
        op_cfg["binary_path"] = binary_path

    # ----------------------------------------------------------------- account
    if args.account and args.account.strip():
        op_cfg["account"] = args.account.strip()
        console.print(f"  Аккаунт: [cyan]{op_cfg['account']}[/cyan]")

    # ------------------------------------------------------------------- token
    console.print()
    console.print("[bold]Шаг 2[/bold]  Авторизация")
    token_env = (args.token_env or op_cfg.get("service_account_token_env")
                 or _DEFAULT_TOKEN_ENV).strip()
    op_cfg["service_account_token_env"] = token_env

    token = (args.token or "").strip()
    if token:
        save_env_value(token_env, token)
        os.environ[token_env] = token
        console.print(f"  [green]✓[/green] Токен сервисного аккаунта сохранён в "
                      f"{get_env_path()} как {token_env}")
    elif os.environ.get(token_env):
        console.print(f"  [green]✓[/green] Используется токен сервисного аккаунта из {token_env}")
    else:
        who = _op_whoami(binary, op_cfg.get("account", ""))
        if who:
            console.print(f"  [green]✓[/green] Используется активный сеанс op ({who})")
        else:
            console.print(
                "  [yellow]Нет токена сервисного аккаунта или активного сеанса op.[/yellow]\n"
                "  Выполните [cyan]op signin[/cyan] в интерактивном терминале или задайте "
                f"токен в {token_env}, затем снова проверьте состояние."
            )

    # ----------------------------------------------------------------- enable
    op_cfg["enabled"] = True
    op_cfg.setdefault("env", {})
    op_cfg.setdefault("cache_ttl_seconds", 300)
    op_cfg.setdefault("override_existing", True)
    save_config(cfg)

    console.print()
    console.print("[green]✓ Источник секретов 1Password включён.[/green]")
    console.print(
        "  Добавить секрет: [cyan]korra secrets onepassword set OPENAI_API_KEY "
        "\"op://Private/OpenAI/api key\"[/cyan]\n"
        "  Проверить:       [cyan]korra secrets onepassword sync[/cyan]\n"
        "  Состояние:       [cyan]korra secrets onepassword status[/cyan]"
    )
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    console = Console()
    cfg = load_config()
    op_cfg = (cfg.get("secrets") or {}).get("onepassword") or {}

    enabled = bool(op_cfg.get("enabled"))
    account = str(op_cfg.get("account", "") or "").strip()
    token_env = op_cfg.get("service_account_token_env", _DEFAULT_TOKEN_ENV)
    binary_path = str(op_cfg.get("binary_path", "") or "").strip()
    references = op_cfg.get("env") if isinstance(op_cfg.get("env"), dict) else {}
    token_set = bool(os.environ.get(token_env))

    binary = op_src.find_op(binary_path)

    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column("", style="bold")
    table.add_column("")
    table.add_row("Включено", _yn(enabled))
    table.add_row("Аккаунт", account or "[dim]по умолчанию[/dim]")
    table.add_row("Переменная токена", token_env)
    table.add_row("Токен в окружении", _yn(token_set))
    table.add_row("Заменять существующие", _yn(bool(op_cfg.get("override_existing", True))))
    table.add_row("Время кеша, с", str(op_cfg.get("cache_ttl_seconds", 300)))
    if binary:
        table.add_row("op binary", f"{binary} ({_op_version(binary)})")
    else:
        table.add_row("Программа op", "[yellow]не найдена[/yellow]")
    table.add_row("Ссылки", str(len(references)))

    console.print(Panel(table, title="Источник секретов 1Password", border_style="cyan"))

    if references:
        ref_table = Table(show_header=True, header_style="bold")
        ref_table.add_column("Переменная", style="cyan")
        ref_table.add_column("Ссылка")
        for name in sorted(references):
            ref_table.add_row(name, str(references[name]))
        console.print(ref_table)

    if not enabled:
        console.print("\n  Для включения выполните [cyan]korra secrets onepassword setup[/cyan].")
        return 0
    if binary and not token_set:
        who = _op_whoami(binary, account)
        if who:
            console.print(f"\n  [green]Активный сеанс op:[/green] {who}")
        else:
            console.print(
                f"\n  [yellow]Нет активного сеанса op, а {token_env} не задана. "
                "При следующем запуске Korra пропустит 1Password с предупреждением.[/yellow]"
            )
    if not references:
        console.print(
            "\n  [yellow]Ссылки ещё не настроены.[/yellow] Добавить: "
            "[cyan]korra secrets onepassword set ENV_VAR \"op://…\"[/cyan]"
        )
    return 0


def cmd_set(args: argparse.Namespace) -> int:
    console = Console()
    # Reuse the backend validator so the CLI and startup paths agree on what a
    # valid reference is — and store the *validated/stripped* value, not the
    # raw arg (so trailing whitespace never lands in config.yaml).
    valid, warnings = op_src._validate_references({args.env_var: args.reference})
    if args.env_var not in valid:
        for w in warnings:
            console.print(f"[red]{w}[/red]")
        return 1

    cfg = load_config()
    op_cfg = cfg.setdefault("secrets", {}).setdefault("onepassword", {})
    env_map = op_cfg.get("env")
    if not isinstance(env_map, dict):
        env_map = {}
        op_cfg["env"] = env_map
    env_map[args.env_var] = valid[args.env_var]
    save_config(cfg)
    console.print(
        f"[green]✓[/green] Добавлено сопоставление [cyan]{args.env_var}[/cyan] → "
        f"{valid[args.env_var]}"
    )
    if not op_cfg.get("enabled"):
        console.print(
            "  [yellow]Интеграция выключена. Для включения выполните "
            "[cyan]korra secrets onepassword setup[/cyan].[/yellow]"
        )
    return 0


def cmd_remove(args: argparse.Namespace) -> int:
    console = Console()
    cfg = load_config()
    op_cfg = cfg.setdefault("secrets", {}).setdefault("onepassword", {})
    env_map = op_cfg.get("env")
    if not isinstance(env_map, dict) or args.env_var not in env_map:
        console.print(f"[yellow]Для {args.env_var} нет сопоставления.[/yellow]")
        return 1
    del env_map[args.env_var]
    save_config(cfg)
    console.print(f"[green]✓[/green] Сопоставление для [cyan]{args.env_var}[/cyan] удалено")
    return 0


def cmd_token(args: argparse.Namespace) -> int:
    """Rotate the 1Password service-account token without the full setup flow.

    Prompts for (or accepts via ``--token``) a new service-account token,
    verifies it with ``op whoami`` (unless ``--no-verify``), and only then
    persists it to .env — so a bad paste never bricks the working token.
    """
    console = Console()
    cfg = load_config()
    op_cfg = (cfg.get("secrets") or {}).get("onepassword") or {}
    token_env = op_cfg.get("service_account_token_env", _DEFAULT_TOKEN_ENV)
    account = str(op_cfg.get("account", "") or "").strip()
    binary_path = str(op_cfg.get("binary_path", "") or "").strip()

    token = (args.token or "").strip()
    if not token:
        if not sys.stdin.isatty():
            console.print("[red]Нет интерактивного терминала; передайте токен через --token.[/red]")
            return 1
        console.print(
            "Создайте новый токен сервисного аккаунта: "
            "https://my.1password.com → Developer → Service Accounts.\n"
        )
        token = masked_secret_prompt(f"Вставьте новый токен ({token_env}): ").strip()
    if not token:
        console.print("[red]Токен пуст; отменено.[/red]")
        return 1

    if not args.no_verify:
        binary = op_src.find_op(binary_path)
        if binary is None:
            console.print(
                f"[red]Программа op не найдена. Установите её ({_DOCS_URL}) или "
                "повторите с --no-verify, чтобы сохранить без проверки.[/red]"
            )
            return 1
        console.print("Проверяю через `op whoami`…")
        who = _op_whoami(binary, account, token_value=token)
        if who is None:
            console.print(
                "[red]✗ Новый токен отклонён программой op; изменения не внесены.[/red]"
            )
            return 1
        console.print(f"[green]✓ Токен принят[/green] ({who}).")

    save_env_value(token_env, token)
    os.environ[token_env] = token
    # Cached resolutions are keyed on the previous token's fingerprint;
    # drop them so the next startup resolves fresh with the new credential.
    op_src.clear_caches()
    console.print(
        f"[green]✓[/green] Сохранено в {get_env_path()} как {token_env}. "
        "Изменение применится при следующем запуске Korra."
    )
    if not op_cfg.get("enabled"):
        console.print(
            "[yellow]Интеграция 1Password выключена. Для включения выполните "
            "`korra secrets onepassword setup`.[/yellow]"
        )
    return 0


def cmd_sync(args: argparse.Namespace) -> int:
    console = Console()
    cfg = load_config()
    op_cfg = (cfg.get("secrets") or {}).get("onepassword") or {}
    if not op_cfg.get("enabled"):
        console.print(
            "[yellow]Интеграция 1Password выключена. Сначала выполните "
            "`korra secrets onepassword setup`.[/yellow]"
        )
        return 1

    references = op_cfg.get("env") if isinstance(op_cfg.get("env"), dict) else {}
    if not references:
        console.print(
            "[yellow]Ссылки op:// не настроены. Добавьте командой "
            "`korra secrets onepassword set ENV_VAR \"op://…\"`.[/yellow]"
        )
        return 0

    account = str(op_cfg.get("account", "") or "").strip()
    token_env = op_cfg.get("service_account_token_env", _DEFAULT_TOKEN_ENV)
    binary_path = str(op_cfg.get("binary_path", "") or "").strip()

    # --apply delegates to the same code path startup uses, so the skip /
    # override / token-guard policy lives in exactly one place.
    if args.apply:
        result = op_src.apply_onepassword_secrets(
            enabled=True,
            env=references,
            account=account,
            service_account_token_env=token_env,
            binary_path=binary_path,
            override_existing=bool(op_cfg.get("override_existing", True)),
            cache_ttl_seconds=0,  # an explicit sync always resolves fresh
        )
        if result.error:
            console.print(f"[red]{result.error}[/red]")
            return 1
        table = Table(show_header=True, header_style="bold")
        table.add_column("Переменная", style="cyan")
        table.add_column("Действие")
        for name in sorted(result.applied):
            table.add_row(name, "[green]добавлен[/green]")
        for name in sorted(result.skipped):
            table.add_row(name, "[dim]пропущен: уже задан или переменная токена[/dim]")
        console.print(table)
        for w in result.warnings:
            console.print(f"[yellow]предупреждение:[/yellow] {w}")
        console.print(
            f"\n  [green]В текущий процесс добавлено секретов: {len(result.applied)}.[/green]"
        )
        return 0

    # Dry-run: resolve fresh (no cache) and preview, mutating nothing.
    try:
        secrets, warnings = op_src.fetch_onepassword_secrets(
            references=references,
            account=account,
            token_env=token_env,
            binary_path=binary_path,
            use_cache=False,
        )
    except RuntimeError as exc:
        console.print(f"[red]{exc}[/red]")
        return 1

    override = bool(op_cfg.get("override_existing", True))
    table = Table(show_header=True, header_style="bold")
    table.add_column("Переменная", style="cyan")
    table.add_column("Действие")
    for name in sorted(references):
        if name == token_env:
            table.add_row(name, "[dim]пропустить: переменная токена[/dim]")
        elif name not in secrets:
            table.add_row(name, "[red]не получен; см. предупреждения[/red]")
        elif os.environ.get(name) and not override:
            table.add_row(name, "[dim]пропустить: уже задан[/dim]")
        else:
            already = bool(os.environ.get(name))
            table.add_row(
                name,
                "[green]будет добавлен[/green]" + (" (заменит существующий)" if already else ""),
            )
    console.print(table)
    for w in warnings:
        console.print(f"[yellow]предупреждение:[/yellow] {w}")
    console.print(
        "\n  Это была проверка. Ссылки будут получены при следующем запуске "
        "[cyan]korra[/cyan]. Для текущего процесса повторите с [cyan]--apply[/cyan]."
    )
    return 0


def cmd_disable(args: argparse.Namespace) -> int:
    console = Console()
    cfg = load_config()
    op_cfg = cfg.setdefault("secrets", {}).setdefault("onepassword", {})
    op_cfg["enabled"] = False
    save_config(cfg)
    console.print(
        "[green]Выключено.[/green] При следующем запуске Korra ссылки 1Password "
        "получены не будут.\n  Сопоставления оставлены в config.yaml; удалить их можно "
        "командой [cyan]korra secrets onepassword remove ENV_VAR[/cyan]."
    )
    return 0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _yn(b: bool) -> str:
    return "[green]да[/green]" if b else "[dim]нет[/dim]"


def _op_version(binary: Path) -> str:
    try:
        res = subprocess.run(
            [str(binary), "--version"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5,
        )
        if res.returncode == 0:
            return (res.stdout or res.stderr).strip().splitlines()[0]
    except (OSError, subprocess.TimeoutExpired):
        pass
    return "version unknown"


def _op_whoami(
    binary: Path, account: str, *, token_value: str = ""
) -> Optional[str]:
    """Return a short identity string if op is authenticated, else None.

    ``token_value``, when given, is passed to the child as
    ``OP_SERVICE_ACCOUNT_TOKEN`` so a candidate token can be probed
    without touching the caller's environment.
    """
    cmd = [str(binary), "whoami"]
    if account:
        cmd += ["--account", account]
    # 1Password CLI child: intentionally receives the service-account token —
    # no scrub, no HOME rewrite (op stores auth state under the real home).
    from tools.environments.local import build_subprocess_env
    env = build_subprocess_env(scrub_secrets=False, inherit_profile_home=False)
    env.setdefault("NO_COLOR", "1")
    if token_value:
        env["OP_SERVICE_ACCOUNT_TOKEN"] = token_value
    try:
        res = subprocess.run(
            cmd, env=env, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=10
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if res.returncode != 0:
        return None
    out = (res.stdout or "").strip()
    return out.replace("\n", " ")[:120] or "authenticated"

"""Implementation of the ``hermes bundles`` CLI subcommand.

Mirrors the structure of ``korra_cli/skills_hub.py`` but for skill
bundles. Bundles are tiny YAML files that name a set of skills to load
together via a single ``/<bundle>`` slash command.

Subcommands:
- list: show all bundles
- show: dump one bundle's contents
- create: build a new bundle from arguments or interactively
- delete: remove a bundle
- reload: re-scan the bundles directory
"""

from __future__ import annotations
from korra_cli.cli_output import line_input

import sys
from typing import List

from rich.console import Console
from rich.table import Table

from agent.skill_bundles import (
    _bundles_dir,
    delete_bundle,
    get_bundle,
    list_bundles,
    reload_bundles,
    save_bundle,
    scan_bundles,
)


def _console() -> Console:
    # Bind to stderr so piping `hermes bundles list | grep …` doesn't
    # garble rich markup with table styling. Tables and headings still
    # render to a terminal; pure text columns survive piping.
    return Console()


def _cmd_list(args) -> None:
    c = _console()
    bundles = list_bundles()
    if not bundles:
        c.print(
            f'[dim]Наборы навыков пока не установлены. Создать:\n  korra bundles create <имя> --skill skill1 --skill skill2[/]\nПапка наборов: [bold]{_bundles_dir()}[/]'
        )
        return

    table = Table(title=f'Наборы навыков ({len(bundles)})', show_lines=False)
    table.add_column('Команда', style="bold cyan")
    table.add_column('Название', style="bold")
    table.add_column('Навыки', justify="right")
    table.add_column('Описание')

    for info in bundles:
        skill_count = len(info.get("skills", []))
        table.add_row(
            f"/{info['slug']}",
            info["name"],
            str(skill_count),
            info.get("description") or "",
        )
    c.print(table)
    c.print(f'\n[dim]Папка наборов: {_bundles_dir()}[/]')


def _cmd_show(args) -> None:
    c = _console()
    info = get_bundle(args.name)
    if not info:
        c.print(f'[bold red]Набор {args.name!r} не найден.[/]')
        sys.exit(1)
    c.print(f"[bold cyan]/{info['slug']}[/]  [bold]{info['name']}[/]")
    if info.get("description"):
        c.print(f"  {info['description']}")
    c.print(f"  [dim]Файл: {info['path']}[/]")
    c.print(f"  [bold]Навыки ({len(info['skills'])}):[/]")
    for s in info["skills"]:
        c.print(f"    - {s}")
    if info.get("instruction"):
        c.print(f"  [bold]Инструкция:[/]\n    {info['instruction']}")


def _cmd_create(args) -> None:
    c = _console()
    name = args.name
    skills: List[str] = list(args.skill or [])
    description = args.description or ""
    instruction = args.instruction or ""
    overwrite = bool(args.force)

    if not skills:
        # Interactive prompt for skills if none were passed on the CLI.
        c.print(
            '[dim]Навыки через --skill не указаны. Введите по одному имени на строку.\nПустая строка завершит ввод.[/]'
        )
        try:
            while True:
                line = line_input('навык> ').strip()
                if not line:
                    break
                skills.append(line)
        except (EOFError, KeyboardInterrupt):
            c.print('\n[yellow]Отменено.[/]')
            sys.exit(1)

    if not skills:
        c.print('[bold red]В наборе должен быть хотя бы один навык.[/]')
        sys.exit(1)

    try:
        path = save_bundle(
            name,
            skills,
            description=description,
            instruction=instruction,
            overwrite=overwrite,
        )
    except FileExistsError as exc:
        c.print(f'[bold red]{exc}[/]\n[dim]Для перезаписи добавьте --force.[/]')
        sys.exit(1)
    except ValueError as exc:
        c.print(f"[bold red]{exc}[/]")
        sys.exit(1)

    c.print(f'[bold green]Набор создан:[/] {path}')
    info = get_bundle(name)
    if info:
        c.print(
            f"  Запустить: [bold cyan]/{info['slug']}[/]  (загружается навыков: {len(info['skills'])})"
        )


def _cmd_delete(args) -> None:
    c = _console()
    try:
        path = delete_bundle(args.name)
    except FileNotFoundError as exc:
        c.print(f"[bold red]{exc}[/]")
        sys.exit(1)
    c.print(f'[bold green]Набор удалён:[/] {path}')


def _cmd_reload(args) -> None:
    c = _console()
    diff = reload_bundles()
    if diff["added"]:
        c.print(f"[bold green]Добавлено ({len(diff['added'])}):[/]")
        for entry in diff["added"]:
            c.print(f"  + {entry['name']} — {entry.get('description', '')}")
    if diff["removed"]:
        c.print(f"[bold red]Удалено ({len(diff['removed'])}):[/]")
        for entry in diff["removed"]:
            c.print(f"  - {entry['name']}")
    if not diff["added"] and not diff["removed"]:
        c.print(f"[dim]Изменений нет. Загружено наборов: {diff['total']}.[/]")
    else:
        c.print(f"[dim]Всего наборов: {diff['total']}[/]")


def register_cli(subparser) -> None:
    """Build the ``hermes bundles`` argparse tree.

    Called from ``korra_cli/main.py`` where it owns the top-level
    ``bundles`` subparser. Keeping registration here means the bundles
    subcommand's argparse tree lives next to its handlers.
    """
    subs = subparser.add_subparsers(dest="bundles_action")

    p_list = subs.add_parser("list", help='Показать установленные комплекты навыков')
    p_list.set_defaults(_bundles_handler=_cmd_list)

    p_show = subs.add_parser("show", help='Показать содержимое комплекта')
    p_show.add_argument("name", help='Имя комплекта')
    p_show.set_defaults(_bundles_handler=_cmd_show)

    p_create = subs.add_parser(
        "create",
        help='Создать комплект навыков',
        description=(
            'Создать комплект. Передайте навыки через повторяемый --skill или выберите их в меню.'
        ),
    )
    p_create.add_argument("name", help='Имя комплекта; станет командой /<имя>')
    p_create.add_argument(
        "--skill", "-s", action="append", default=[],
        help='Имя включаемого навыка; повторите для нескольких',
    )
    p_create.add_argument(
        "--description", "-d", default="",
        help='Описание для /help и korra bundles list',
    )
    p_create.add_argument(
        "--instruction", "-i", default="",
        help='Дополнительные инструкции перед содержимым навыков',
    )
    p_create.add_argument(
        "--force", "-f", action="store_true",
        help='Заменить существующий комплект с таким же именем',
    )
    p_create.set_defaults(_bundles_handler=_cmd_create)

    p_delete = subs.add_parser("delete", help='Удалить комплект навыков')
    p_delete.add_argument("name", help='Имя комплекта')
    p_delete.set_defaults(_bundles_handler=_cmd_delete)

    p_reload = subs.add_parser(
        "reload", help='Обновить список комплектов и показать изменения'
    )
    p_reload.set_defaults(_bundles_handler=_cmd_reload)

    # Ensure a fresh scan when any bundles subcommand runs.
    scan_bundles()


def bundles_command(args) -> None:
    """Dispatch ``hermes bundles <subcommand>`` to the right handler."""
    handler = getattr(args, "_bundles_handler", None)
    if handler is None:
        # No subcommand given — default to list.
        _cmd_list(args)
        return
    handler(args)

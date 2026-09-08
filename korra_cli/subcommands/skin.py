"""``hermes skin`` subcommand parser."""

from __future__ import annotations

from typing import Callable


def build_skin_parser(subparsers, *, cmd_skin: Callable) -> None:
    """Attach the ``skin`` subcommand to ``subparsers``."""
    skin_parser = subparsers.add_parser(
        "skin",
        help='Просмотр, выбор и настройка тем оформления',
        description='Управление темами Корры. set изменяет один цвет текущей темы.',
    )
    skin_subparsers = skin_parser.add_subparsers(dest="skin_command")

    skin_subparsers.add_parser("list", help='Показать доступные темы')

    skin_use = skin_subparsers.add_parser("use", help='Сменить текущую тему')
    skin_use.add_argument("name", help='Название темы')

    # skin set — change ONE color of the active skin in place (bg untouched).
    skin_set = skin_subparsers.add_parser(
        "set", help="Изменить один цвет текущей темы, например skin set ui_tool '#00FFFF'"
    )
    skin_set.add_argument("key", help='Ключ цвета, например ui_tool, ui_accent или background')
    skin_set.add_argument("value", help='Цвет в формате #rrggbb')
    skin_set.add_argument("--skin", help='Изменить указанную тему вместо текущей')

    skin_parser.set_defaults(func=cmd_skin)

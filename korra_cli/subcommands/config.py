"""``hermes config`` subcommand parser.

Extracted verbatim from ``korra_cli/main.py:main()`` (god-file Phase 2).
Handler injected to avoid importing ``main``.
"""

from __future__ import annotations

from typing import Callable


def build_config_parser(subparsers, *, cmd_config: Callable) -> None:
    """Attach the ``config`` subcommand to ``subparsers``."""
    # =========================================================================
    # config command
    # =========================================================================
    config_parser = subparsers.add_parser(
        "config",
        help='Просмотр и изменение настроек',
        description='Управление настройками Корры',
    )
    config_subparsers = config_parser.add_subparsers(dest="config_command")

    # config show (default)
    config_subparsers.add_parser("show", help='Показать текущие настройки')

    # config edit
    config_subparsers.add_parser("edit", help='Открыть файл настроек в редакторе')

    # config get
    config_get = config_subparsers.add_parser(
        "get", help='Показать действующее значение настройки'
    )
    config_get.add_argument("key", nargs="?", help='Ключ настройки, например model')
    config_get.add_argument("--json", action="store_true", help='Вывести значение в JSON')

    # config set
    config_set = config_subparsers.add_parser("set", help='Изменить значение настройки')
    config_set.add_argument(
        "key", nargs="?", help='Ключ настройки, например model или terminal.backend'
    )
    config_set.add_argument("value", nargs="?", help='Новое значение')
    config_set.add_argument(
        "--force",
        action="store_true",
        help='Скрыть уведомление о неизвестном ключе после сохранения. Значение сохраняется в любом случае.',
    )

    # config unset
    config_unset = config_subparsers.add_parser(
        "unset", help='Удалить значение настройки'
    )
    config_unset.add_argument("key", nargs="?", help='Ключ удаляемой настройки')

    # config path
    config_subparsers.add_parser("path", help='Показать путь к файлу настроек')

    # config env-path
    config_subparsers.add_parser("env-path", help='Показать путь к файлу .env')

    # config check
    config_subparsers.add_parser("check", help='Проверить недостающие и устаревшие настройки')

    # config migrate
    config_subparsers.add_parser("migrate", help='Добавить новые параметры в настройки')

    config_parser.set_defaults(func=cmd_config)

"""``hermes mcp`` subcommand parser.

Extracted from ``korra_cli/main.py:main()`` (god-file Phase 2 follow-up).
Handler injected to avoid importing ``main``.
"""

from __future__ import annotations

import argparse
from typing import Callable

from korra_cli.subcommands._shared import add_accept_hooks_flag


def build_mcp_parser(subparsers, *, cmd_mcp: Callable) -> None:
    """Attach the ``mcp`` subcommand to ``subparsers``."""
    mcp_parser = subparsers.add_parser(
        "mcp",
        help='Подключение серверов MCP и запуск Корры как сервера MCP',
        description=(
            'Серверы MCP добавляют инструменты через Model Context Protocol. Подключить сервер: korra mcp add. Открыть доступ к беседам Корры через MCP: korra mcp serve.'
        ),
    )
    mcp_sub = mcp_parser.add_subparsers(dest="mcp_action")

    mcp_serve_p = mcp_sub.add_parser(
        "serve",
        help='Запустить Корру как сервер MCP и открыть беседы для других агентов',
    )
    mcp_serve_p.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help='Включить подробные журналы в stderr',
    )
    add_accept_hooks_flag(mcp_serve_p)

    mcp_add_p = mcp_sub.add_parser(
        "add", help='Добавить сервер MCP с предварительной проверкой инструментов'
    )
    mcp_add_p.add_argument("name", help='Имя сервера; используется как ключ настроек')
    mcp_add_p.add_argument("--url", help='Адрес HTTP/SSE')
    # dest="mcp_command" so this flag does not clobber the top-level
    # subparser's args.command attribute, which the dispatcher reads to
    # route to cmd_mcp.  Without an explicit dest, argparse derives
    # dest="command" from the flag name and sets it to None when the
    # flag is omitted, causing `hermes mcp add ...` to fall through to
    # interactive chat.
    mcp_add_p.add_argument(
        "--command", dest="mcp_command", help='Команда для stdio, например npx'
    )
    mcp_add_p.add_argument(
        "--args",
        nargs=argparse.REMAINDER,
        default=[],
        help='Аргументы команды stdio; должны идти последним параметром',
    )
    mcp_add_p.add_argument("--auth", choices=["oauth", "header"], help='Способ входа')
    mcp_add_p.add_argument("--preset", help='Имя готовой настройки MCP')
    mcp_add_p.add_argument(
        "--connect-timeout",
        type=float,
        help='Время ожидания первого подключения и поиска инструментов в секундах',
    )
    mcp_add_p.add_argument(
        "--env",
        nargs="*",
        default=[],
        help='Переменные среды для серверов stdio в формате KEY=VALUE',
    )

    mcp_rm_p = mcp_sub.add_parser("remove", aliases=["rm"], help='Удалить сервер MCP')
    mcp_rm_p.add_argument("name", help='Имя удаляемого сервера')

    mcp_sub.add_parser("list", aliases=["ls"], help='Показать настроенные серверы MCP')

    mcp_test_p = mcp_sub.add_parser("test", help='Проверить подключение к серверу MCP')
    mcp_test_p.add_argument("name", help='Имя проверяемого сервера')

    mcp_cfg_p = mcp_sub.add_parser(
        "configure", aliases=["config"], help='Изменить выбор инструментов'
    )
    mcp_cfg_p.add_argument("name", help='Имя настраиваемого сервера')

    mcp_login_p = mcp_sub.add_parser(
        "login",
        help='Повторно войти на сервер MCP через OAuth',
    )
    mcp_login_p.add_argument("name", help='Имя сервера для повторного входа')

    mcp_reauth_p = mcp_sub.add_parser(
        "reauth",
        help='Повторно войти на один сервер MCP через OAuth или на все с --all',
    )
    mcp_reauth_p.add_argument(
        "name", nargs="?", help='Имя сервера; не указывается с --all'
    )
    mcp_reauth_p.add_argument(
        "--all",
        action="store_true",
        help='Последовательно повторить вход на все серверы OAuth из настроек',
    )

    # ── Catalog (Nous-approved MCPs shipped with the repo) ─────────────────
    mcp_sub.add_parser(
        "picker",
        help='Открыть каталог с выбором; также действие по умолчанию для korra mcp',
    )
    mcp_sub.add_parser(
        "catalog",
        help='Показать одобренные Nous серверы MCP для простой установки',
    )
    mcp_install_p = mcp_sub.add_parser(
        "install",
        help='Установить MCP из каталога по имени, например korra mcp install n8n',
    )
    mcp_install_p.add_argument(
        "identifier",
        help='Имя записи каталога или official/<name>',
    )

    add_accept_hooks_flag(mcp_parser)
    mcp_parser.set_defaults(func=cmd_mcp)

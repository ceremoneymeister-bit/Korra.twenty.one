"""``hermes acp`` subcommand parser.

Extracted from ``korra_cli/main.py:main()`` (god-file Phase 2 follow-up).
Handler injected to avoid importing ``main``.
"""

from __future__ import annotations

from typing import Callable

from korra_cli.subcommands._shared import add_accept_hooks_flag


def build_acp_parser(subparsers, *, cmd_acp: Callable) -> None:
    """Attach the ``acp`` subcommand to ``subparsers``."""
    acp_parser = subparsers.add_parser(
        "acp",
        help='Запустить Корру как сервер ACP (Agent Client Protocol)',
        description='Запустить Корру в режиме ACP для подключения к VS Code, Zed или JetBrains',
    )
    add_accept_hooks_flag(acp_parser)
    acp_parser.add_argument(
        "--version",
        action="store_true",
        dest="acp_version",
        help='Показать версию ACP Корры и выйти',
    )
    acp_parser.add_argument(
        "--check",
        action="store_true",
        help='Проверить зависимости и загрузку адаптера ACP, затем выйти',
    )
    acp_parser.add_argument(
        "--setup",
        action="store_true",
        help='Открыть настройку провайдера и модели для входа через терминал ACP',
    )
    acp_parser.add_argument(
        "--setup-browser",
        action="store_true",
        help='Установить agent-browser и Playwright Chromium в папку node профиля для браузерных инструментов; повторный запуск безопасен.',
    )
    acp_parser.add_argument(
        "--yes",
        "-y",
        action="store_true",
        dest="assume_yes",
        help='Подтвердить все запросы; с --setup-browser пропустить подтверждение загрузки Chromium размером около 400 МБ.',
    )
    acp_parser.set_defaults(func=cmd_acp)

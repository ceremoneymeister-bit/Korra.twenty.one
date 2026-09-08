"""``hermes logs`` subcommand parser.

Extracted verbatim from ``korra_cli/main.py:main()`` (god-file Phase 2).
Handler injected to avoid importing ``main``.
"""

from __future__ import annotations

import argparse
from typing import Callable


def build_logs_parser(subparsers, *, cmd_logs: Callable) -> None:
    """Attach the ``logs`` subcommand to ``subparsers``."""
    # =========================================================================
    # logs command
    # =========================================================================
    logs_parser = subparsers.add_parser(
        "logs",
        help='Просмотр и фильтрация журналов Корры',
        description='Читать и фильтровать agent.log, errors.log, gateway.log, gui.log и desktop.log',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='Примеры: korra logs — последние 50 строк; korra logs -f — новые записи в реальном времени; korra logs errors — ошибки; korra logs gateway -n 100 — 100 строк шлюза; korra logs gui -f — журнал панели; korra logs desktop -f — журнал приложения; --level WARNING — предупреждения и ошибки; --session abc123 — фильтр беседы; --component tools — инструменты; --since 1h — за час; korra logs list — файлы и размеры.',
    )
    logs_parser.add_argument(
        "log_name",
        nargs="?",
        default="agent",
        help='Журнал: agent (по умолчанию), errors, gateway, gui; list — список файлов',
    )
    logs_parser.add_argument(
        "-n",
        "--lines",
        type=int,
        default=50,
        help='Число строк (по умолчанию 50)',
    )
    logs_parser.add_argument(
        "-f",
        "--follow",
        action="store_true",
        help='Читать новые записи в реальном времени, как tail -f',
    )
    logs_parser.add_argument(
        "--level",
        metavar="LEVEL",
        help='Минимальный уровень журнала: DEBUG, INFO, WARNING или ERROR',
    )
    logs_parser.add_argument(
        "--session",
        metavar="ID",
        help='Строки, содержащие указанную часть ID беседы',
    )
    logs_parser.add_argument(
        "--since",
        metavar="TIME",
        help='Записи за период, например 1h, 30m или 2d',
    )
    logs_parser.add_argument(
        "--component",
        metavar="NAME",
        help='Фильтр по компоненту: gateway, agent, tools, cli, cron или gui',
    )
    logs_parser.set_defaults(func=cmd_logs)

"""``hermes doctor`` subcommand parser.

Extracted verbatim from ``korra_cli/main.py:main()`` (god-file Phase 2).
Handler injected to avoid importing ``main``.
"""

from __future__ import annotations

from typing import Callable


def build_doctor_parser(subparsers, *, cmd_doctor: Callable) -> None:
    """Attach the ``doctor`` subcommand to ``subparsers``."""
    # =========================================================================
    # doctor command
    # =========================================================================
    doctor_parser = subparsers.add_parser(
        "doctor",
        help='Проверить настройки и зависимости',
        description='Найти проблемы в настройке Корры',
    )
    doctor_parser.add_argument(
        "--fix", action="store_true", help='Попробовать исправить проблемы автоматически'
    )
    doctor_parser.add_argument(
        "--live",
        action="store_true",
        help=(
            'После обычных проверок выполнить по одному ограниченному пробному запросу к настроенным инструментам: Firecrawl, FAL, браузеру, MCP, синтезу и распознаванию речи. Использует настоящие сетевые запросы без изменения данных.'
        ),
    )
    doctor_parser.add_argument(
        "--ack",
        metavar="ADVISORY_ID",
        default=None,
        help=(
            'Отметить предупреждение безопасности просмотренным по ID и выйти. Оно перестанет появляться при запуске. Список предупреждений и ID: korra doctor.'
        ),
    )
    doctor_parser.set_defaults(func=cmd_doctor)

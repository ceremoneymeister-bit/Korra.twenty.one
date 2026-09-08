"""``hermes backup`` subcommand parser.

Extracted verbatim from ``korra_cli/main.py:main()`` (god-file Phase 2).
Handler injected to avoid importing ``main``.
"""

from __future__ import annotations

from typing import Callable


def build_backup_parser(subparsers, *, cmd_backup: Callable) -> None:
    """Attach the ``backup`` subcommand to ``subparsers``."""
    # =========================================================================
    # backup command
    # =========================================================================
    backup_parser = subparsers.add_parser(
        "backup",
        help='Сохранить папку данных Корры в ZIP-архив',
        description='Создать ZIP-архив настроек, навыков, бесед и данных Корры без исходного кода. --quick — быстрый снимок только важных файлов состояния.',
    )
    backup_parser.add_argument(
        "-o",
        "--output",
        help='Путь к ZIP-архиву; по умолчанию — домашняя папка, имя с датой и временем',
    )
    backup_parser.add_argument(
        "-q",
        "--quick",
        action="store_true",
        help='Быстрый снимок: настройки, state.db, .env, учётные записи и расписание',
    )
    backup_parser.add_argument(
        "-l", "--label", help='Метка снимка, только с --quick'
    )
    backup_parser.set_defaults(func=cmd_backup)

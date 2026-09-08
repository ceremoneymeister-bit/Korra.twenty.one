"""``hermes uninstall`` subcommand parser.

Extracted verbatim from ``korra_cli/main.py:main()`` (god-file Phase 2).
Handler injected to avoid importing ``main``.
"""

from __future__ import annotations

from typing import Callable


def build_uninstall_parser(subparsers, *, cmd_uninstall: Callable) -> None:
    """Attach the ``uninstall`` subcommand to ``subparsers``."""
    # =========================================================================
    # uninstall command
    # =========================================================================
    uninstall_parser = subparsers.add_parser(
        "uninstall",
        help='Удалить Корру',
        description='Удалить Корру с компьютера. Настройки и данные можно сохранить для повторной установки.',
    )
    uninstall_parser.add_argument(
        "--full",
        action="store_true",
        help='Полностью удалить приложение, настройки и данные',
    )
    uninstall_parser.add_argument(
        "--gui",
        action="store_true",
        help='Удалить только приложение для компьютера, сохранив агент',
    )
    uninstall_parser.add_argument(
        "--gui-summary",
        action="store_true",
        help='Показать в JSON установленные компоненты приложения и агента, затем выйти; приложение использует это для выбора вариантов удаления',
    )
    uninstall_parser.add_argument(
        "--yes", "-y", action="store_true", help='Пропустить запросы подтверждения'
    )
    uninstall_parser.add_argument(
        "--dry-run",
        action="store_true",
        help='Показать план удаления без изменений',
    )
    uninstall_parser.set_defaults(func=cmd_uninstall)

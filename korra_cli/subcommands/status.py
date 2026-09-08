"""``hermes status`` subcommand parser.

Extracted verbatim from ``korra_cli/main.py:main()`` (god-file Phase 2).
Handler injected to avoid importing ``main``.
"""

from __future__ import annotations

from typing import Callable


def build_status_parser(subparsers, *, cmd_status: Callable) -> None:
    """Attach the ``status`` subcommand to ``subparsers``."""
    # =========================================================================
    # status command
    # =========================================================================
    status_parser = subparsers.add_parser(
        "status",
        help='Показать состояние всех компонентов',
        description='Показать состояние компонентов Корры',
    )
    status_parser.add_argument(
        "--all", action="store_true", help='Показать все подробности со скрытыми секретами для передачи другим'
    )
    status_parser.add_argument(
        "--deep", action="store_true", help='Выполнить подробную проверку; может занять больше времени'
    )
    status_parser.set_defaults(func=cmd_status)

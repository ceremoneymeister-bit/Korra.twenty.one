"""``hermes dump`` subcommand parser.

Extracted verbatim from ``korra_cli/main.py:main()`` (god-file Phase 2).
Handler injected to avoid importing ``main``.
"""

from __future__ import annotations

from typing import Callable


def build_dump_parser(subparsers, *, cmd_dump: Callable) -> None:
    """Attach the ``dump`` subcommand to ``subparsers``."""
    # =========================================================================
    # dump command
    # =========================================================================
    dump_parser = subparsers.add_parser(
        "dump",
        help='Краткая сводка настроек для поддержки',
        description='Вывести краткую текстовую сводку настроек Корры, которую можно передать в поддержку через Discord или GitHub',
    )
    dump_parser.add_argument(
        "--show-keys",
        action="store_true",
        help='Показать первые и последние 4 символа ключей API вместо отметки о наличии',
    )
    dump_parser.set_defaults(func=cmd_dump)

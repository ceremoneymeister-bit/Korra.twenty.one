"""``hermes import`` subcommand parser.

Extracted verbatim from ``korra_cli/main.py:main()`` (god-file Phase 2).
Handler injected to avoid importing ``main``.
"""

from __future__ import annotations

from typing import Callable


def build_import_cmd_parser(subparsers, *, cmd_import: Callable) -> None:
    """Attach the ``import`` subcommand to ``subparsers``."""
    # =========================================================================
    # import command
    # =========================================================================
    import_parser = subparsers.add_parser(
        "import",
        help='Восстановить резервную копию Корры из ZIP',
        description='Восстановить настройки, навыки, беседы и данные из резервного ZIP-архива в папку данных Корры',
    )
    import_parser.add_argument("zipfile", help='Путь к резервному ZIP-архиву')
    import_parser.add_argument(
        "--force",
        "-f",
        action="store_true",
        help='Перезаписать существующие файлы без подтверждения',
    )
    import_parser.set_defaults(func=cmd_import)

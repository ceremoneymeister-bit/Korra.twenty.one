"""``hermes prompt-size`` subcommand parser.

Extracted verbatim from ``korra_cli/main.py:main()`` (god-file Phase 2).
Handler injected to avoid importing ``main``.
"""

from __future__ import annotations

from typing import Callable


def build_prompt_size_parser(subparsers, *, cmd_prompt_size: Callable) -> None:
    """Attach the ``prompt-size`` subcommand to ``subparsers``."""
    # =========================================================================
    # prompt-size command
    # =========================================================================
    prompt_size_parser = subparsers.add_parser(
        "prompt-size",
        help='Показать размер системной инструкции и схем инструментов по частям, в байтах',
        description=(
            'Показать размер начального контекста: системная инструкция, список навыков, память, профиль пользователя и схемы инструментов JSON. Работает без вызова API.'
        ),
    )
    prompt_size_parser.add_argument(
        "--platform",
        default="cli",
        help='Платформа для проверки: cli, telegram, discord и другие; по умолчанию cli',
    )
    prompt_size_parser.add_argument(
        "--json",
        action="store_true",
        help='Вывести размеры по частям в JSON',
    )
    prompt_size_parser.set_defaults(func=cmd_prompt_size)

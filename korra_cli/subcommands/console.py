"""``hermes console`` subcommand parser."""

from __future__ import annotations

from typing import Callable


def build_console_parser(subparsers, *, cmd_console: Callable) -> None:
    """Attach the safe Hermes Console REPL subcommand."""
    console_parser = subparsers.add_parser(
        "console",
        help='Открыть безопасную консоль команд Корры',
        description=(
            'Открыть консоль с выбранными командами Корры. Это ограниченный набор команд, а не системная оболочка.'
        ),
    )
    console_parser.set_defaults(func=cmd_console)

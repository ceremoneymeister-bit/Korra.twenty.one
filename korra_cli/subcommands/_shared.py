"""Shared parser helpers used across multiple CLI subcommand builders.

These were module-level helpers in ``korra_cli/main.py``. They are pulled
into a neutral module so both ``main.py`` and every
``korra_cli/subcommands/<group>.py`` builder can import them without an
import cycle. ``main.py`` re-exports them for backwards compatibility, so
existing references keep working.
"""

from __future__ import annotations

import argparse


def add_accept_hooks_flag(parser: argparse.ArgumentParser) -> None:
    """Attach the ``--accept-hooks`` flag.

    Shared across every agent subparser so the flag works regardless of CLI
    position.
    """
    parser.add_argument(
        "--accept-hooks",
        action="store_true",
        default=argparse.SUPPRESS,
        help=(
            'Автоматически одобрять новые обработчики shell без запроса в терминале. Аналог HERMES_ACCEPT_HOOKS=1 или hooks_auto_accept: true.'
        ),
    )

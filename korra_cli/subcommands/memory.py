"""``hermes memory`` subcommand parser.

Extracted from ``korra_cli/main.py:main()`` (god-file Phase 2 follow-up).
Handler injected to avoid importing ``main``.
"""

from __future__ import annotations

from typing import Callable


def build_memory_parser(subparsers, *, cmd_memory: Callable) -> None:
    """Attach the ``memory`` subcommand to ``subparsers``."""
    memory_parser = subparsers.add_parser(
        "memory",
        help='Настроить внешнего провайдера памяти',
        description=(
            'Настройка плагинов внешней памяти: honcho, openviking, mem0, hindsight, holographic, retaindb, byterover. Одновременно работает один внешний провайдер. Встроенная память MEMORY.md/USER.md работает всегда.'
        ),
    )
    memory_sub = memory_parser.add_subparsers(dest="memory_command")
    _setup_parser = memory_sub.add_parser(
        "setup", help='Выбрать и настроить провайдера в меню'
    )
    _setup_parser.add_argument(
        "provider",
        nargs="?",
        default=None,
        help='Настроить указанного провайдера, например honcho, без меню выбора',
    )
    memory_sub.add_parser("status", help='Показать настройки текущего провайдера памяти')
    memory_sub.add_parser("off", help='Отключить внешнего провайдера, оставить встроенную память')
    _reset_parser = memory_sub.add_parser(
        "reset",
        help='Очистить всю встроенную память: MEMORY.md и USER.md',
    )
    _reset_parser.add_argument(
        "--yes",
        "-y",
        action="store_true",
        help='Пропустить запрос подтверждения',
    )
    _reset_parser.add_argument(
        "--target",
        choices=["all", "memory", "user"],
        default="all",
        help='Что очистить: all — всё (по умолчанию), memory — память, user — сведения о пользователе',
    )
    memory_parser.set_defaults(func=cmd_memory)

"""``hermes hooks`` subcommand parser.

Extracted verbatim from ``korra_cli/main.py:main()`` (god-file Phase 2).
Handler injected to avoid importing ``main``.
"""

from __future__ import annotations

from typing import Callable


def build_hooks_parser(subparsers, *, cmd_hooks: Callable) -> None:
    """Attach the ``hooks`` subcommand to ``subparsers``."""
    # =========================================================================
    hooks_parser = subparsers.add_parser(
        "hooks",
        help='Просмотр и настройка обработчиков shell',
        description=(
            'Просмотреть обработчики shell из config.yaml профиля, проверить их на тестовых данных и настроить разрешения первого запуска в shell-hooks-allowlist.json.'
        ),
    )
    hooks_subparsers = hooks_parser.add_subparsers(dest="hooks_action")

    hooks_subparsers.add_parser(
        "list",
        aliases=["ls"],
        help='Показать обработчики, условия, время ожидания и состояние разрешения',
    )

    _hk_test = hooks_subparsers.add_parser(
        "test",
        help='Выполнить все обработчики события <event> на тестовых данных',
    )
    _hk_test.add_argument(
        "event",
        help='Имя события, например pre_tool_call, pre_llm_call или subagent_stop',
    )
    _hk_test.add_argument(
        "--for-tool",
        dest="for_tool",
        default=None,
        help=(
            'Запускать только обработчики, подходящие к этому инструменту; для pre_tool_call и post_tool_call'
        ),
    )
    _hk_test.add_argument(
        "--payload-file",
        dest="payload_file",
        default=None,
        help=(
            'Путь к JSON-файлу с дополнениями к тестовым данным перед запуском'
        ),
    )

    _hk_revoke = hooks_subparsers.add_parser(
        "revoke",
        aliases=["remove", "rm"],
        help='Удалить разрешения команды; вступит в силу после перезапуска',
    )
    _hk_revoke.add_argument(
        "command",
        help='Точная строка отзываемой команды из config.yaml',
    )

    hooks_subparsers.add_parser(
        "doctor",
        help=(
            'Проверить обработчики: право выполнения, разрешение, изменение файла, корректность JSON и время пробного запуска'
        ),
    )

    hooks_parser.set_defaults(func=cmd_hooks)

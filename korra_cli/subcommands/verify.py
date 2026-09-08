"""``hermes verify`` subcommand parser.

Follows the pattern of ``korra_cli/subcommands/doctor.py``: parser built
here, handler injected to avoid importing ``main``.
"""

from __future__ import annotations

from typing import Callable

# Keep in sync with agent/verify/runner.py defaults; not imported here to
# avoid paying an extra module import on every `hermes` invocation.
DEFAULT_PHASE_TIMEOUT = 600.0
DEFAULT_READY_TIMEOUT = 60.0


def build_verify_parser(subparsers, *, cmd_verify: Callable) -> None:
    """Attach the ``verify`` subcommand to ``subparsers``."""
    verify_parser = subparsers.add_parser(
        "verify",
        help='Определить способ запуска проекта и проверить его работу',
        description=(
            'Определить команды сборки, тестирования и запуска проекта либо прочитать .hermes/environment.json. Выполнить подготовку, сборку, тесты, фоновый запуск, проверку готовности и завершение.'
        ),
    )
    verify_parser.add_argument(
        "path",
        nargs="?",
        default=None,
        help='Корень проверяемого проекта (по умолчанию текущая папка)',
    )
    verify_parser.add_argument(
        "--detect-only",
        action="store_true",
        help='Только определить и вывести команды в JSON, ничего не запускать',
    )
    verify_parser.add_argument(
        "--save",
        action="store_true",
        help='Сохранить команды в .hermes/environment.json проекта',
    )
    verify_parser.add_argument(
        "--skip-start",
        action="store_true",
        help='Выполнить команды без запуска приложения и проверки готовности',
    )
    verify_parser.add_argument(
        "--phase",
        action="append",
        choices=["bootstrap", "build", "test", "start"],
        default=None,
        help='Выполнить только указанные этапы; параметр можно повторять',
    )
    verify_parser.add_argument(
        "--port",
        type=int,
        default=None,
        help='Задать порт для проверки готовности',
    )
    verify_parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_PHASE_TIMEOUT,
        help=f"Per-phase timeout in seconds (default: {DEFAULT_PHASE_TIMEOUT:.0f})",
    )
    verify_parser.add_argument(
        "--ready-timeout",
        type=float,
        default=DEFAULT_READY_TIMEOUT,
        help=f"Readiness poll timeout in seconds (default: {DEFAULT_READY_TIMEOUT:.0f})",
    )
    verify_parser.add_argument(
        "--json",
        action="store_true",
        help='Вывести машиночитаемый результат JSON',
    )
    verify_parser.set_defaults(func=cmd_verify)

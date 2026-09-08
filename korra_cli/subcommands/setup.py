"""``hermes setup`` subcommand parser.

Extracted verbatim from ``korra_cli/main.py:main()`` (god-file Phase 2).
Handler injected to avoid importing ``main``.
"""

from __future__ import annotations

from typing import Callable


def build_setup_parser(subparsers, *, cmd_setup: Callable) -> None:
    """Attach the ``setup`` subcommand to ``subparsers``."""
    # =========================================================================
    # setup command
    # =========================================================================
    setup_parser = subparsers.add_parser(
        "setup",
        help='Мастер настройки',
        description='Настроить Корру с помощью мастера. Отдельный раздел: korra setup model|tts|terminal|gateway|tools|telemetry|agent',
    )
    setup_parser.add_argument(
        "section",
        nargs="?",
        choices=[
            "model",
            "tts",
            "terminal",
            "gateway",
            "tools",
            "telemetry",
            "agent",
        ],
        default=None,
        help='Открыть выбранный раздел вместо полного мастера',
    )
    setup_parser.add_argument(
        "--non-interactive",
        action="store_true",
        help='Без интерактивного ввода: использовать исходные значения и переменные среды',
    )
    setup_parser.add_argument(
        "--reset", action="store_true", help='Сбросить настройки к исходным значениям'
    )
    setup_parser.add_argument(
        "--reconfigure",
        action="store_true",
        help='Повторить полный мастер с текущими значениями по умолчанию. Это обычное поведение korra setup на настроенной установке; параметр сохранён для совместимости.',
    )
    setup_parser.add_argument(
        "--quick",
        action="store_true",
        help='На настроенной установке запрашивать только отсутствующие значения вместо полного мастера',
    )
    setup_parser.add_argument(
        "--portal",
        action="store_true",
        help='Быстрая настройка Nous: войти через OAuth, выбрать модель Nous, назначить провайдера Nous и подключить шлюз инструментов. Остальные разделы мастера пропускаются.',
    )
    setup_parser.set_defaults(func=cmd_setup)

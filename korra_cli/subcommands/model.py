"""``hermes model`` subcommand parser.

Extracted verbatim from ``korra_cli/main.py:main()`` (god-file Phase 2).
Handler injected to avoid importing ``main``.
"""

from __future__ import annotations

from typing import Callable


def build_model_parser(subparsers, *, cmd_model: Callable) -> None:
    """Attach the ``model`` subcommand to ``subparsers``."""
    # =========================================================================
    # model command
    # =========================================================================
    model_parser = subparsers.add_parser(
        "model",
        help='Выбрать основную модель и провайдера',
        description='Выбрать провайдера и основную модель в меню',
    )
    model_parser.add_argument(
        "--refresh",
        action="store_true",
        help='Очистить кеш списка моделей на диске и заново получить /v1/models у всех провайдеров.',
    )
    model_parser.add_argument(
        "--portal-url",
        help='Адрес портала для входа Nous; по умолчанию рабочий портал',
    )
    model_parser.add_argument(
        "--inference-url",
        help='Адрес API моделей для входа Nous; по умолчанию рабочий API',
    )
    model_parser.add_argument(
        "--client-id",
        default=None,
        help='ID клиента OAuth для входа Nous; по умолчанию штатный ID клиента CLI',
    )
    model_parser.add_argument(
        "--scope", default=None, help='Права OAuth, запрашиваемые при входе Nous'
    )
    model_parser.add_argument(
        "--no-browser",
        action="store_true",
        help='Не открывать браузер автоматически при входе Nous',
    )
    model_parser.add_argument(
        "--timeout",
        type=float,
        default=15.0,
        help='Время ожидания HTTP при входе Nous в секундах (по умолчанию 15)',
    )
    model_parser.add_argument(
        "--ca-bundle", help='Путь к PEM-файлу сертификатов CA для проверки TLS Nous'
    )
    model_parser.add_argument(
        "--insecure",
        action="store_true",
        help='Отключить проверку TLS при входе Nous; только для тестирования',
    )
    model_parser.set_defaults(func=cmd_model)

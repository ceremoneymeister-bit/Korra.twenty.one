"""``hermes webhook`` subcommand parser.

Extracted verbatim from ``korra_cli/main.py:main()`` (god-file Phase 2).
Handler injected to avoid importing ``main``.
"""

from __future__ import annotations

from typing import Callable


def build_webhook_parser(subparsers, *, cmd_webhook: Callable) -> None:
    """Attach the ``webhook`` subcommand to ``subparsers``."""
    # =========================================================================
    # webhook command
    # =========================================================================
    webhook_parser = subparsers.add_parser(
        "webhook",
        help='Управление подписками на вебхуки',
        description='Создать, показать или удалить подписки вебхуков для запуска агента по событиям',
    )
    webhook_subparsers = webhook_parser.add_subparsers(dest="webhook_action")

    wh_sub = webhook_subparsers.add_parser(
        "subscribe", aliases=["add"], help='Создать подписку вебхука'
    )
    wh_sub.add_argument("name", help='Имя маршрута для адреса /webhooks/<name>')
    wh_sub.add_argument(
        "--prompt", default="", help='Шаблон запроса со ссылками на поля данных через {dot.notation}'
    )
    wh_sub.add_argument(
        "--events", default="", help='Принимаемые типы событий через запятую'
    )
    wh_sub.add_argument("--description", default="", help='Назначение подписки')
    wh_sub.add_argument(
        "--skills", default="", help='Имена загружаемых навыков через запятую'
    )
    wh_sub.add_argument(
        "--deliver",
        default="log",
        help='Куда отправлять: log, telegram, discord, slack и другие',
    )
    wh_sub.add_argument(
        "--deliver-chat-id",
        default="",
        help='ID целевого чата для отправки в другую платформу',
    )
    wh_sub.add_argument(
        "--secret", default="", help='Секрет HMAC; без параметра создаётся автоматически'
    )
    wh_sub.add_argument(
        "--deliver-only",
        action="store_true",
        help='Отправить готовый текст напрямую без агента и затрат модели. --deliver должен указывать реальный канал, а не log.',
    )
    wh_sub.add_argument(
        "--script",
        default="",
        help='Скрипт фильтрации или преобразования из scripts/ профиля. Получает JSON через stdin. Пустой stdout, [SILENT] или ненулевой код выхода отменяют обработку вебхука.',
    )

    webhook_subparsers.add_parser(
        "list", aliases=["ls"], help='Показать все динамические подписки'
    )

    wh_rm = webhook_subparsers.add_parser(
        "remove", aliases=["rm"], help='Удалить подписку'
    )
    wh_rm.add_argument("name", help='Имя удаляемой подписки')

    wh_test = webhook_subparsers.add_parser(
        "test", help='Отправить пробный POST на маршрут вебхука'
    )
    wh_test.add_argument("name", help='Имя проверяемой подписки')
    wh_test.add_argument(
        "--payload", default="", help='Отправляемые данные JSON; по умолчанию тестовые'
    )

    webhook_parser.set_defaults(func=cmd_webhook)

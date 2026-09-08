"""``hermes pairing`` subcommand parser.

Extracted from ``korra_cli/main.py:main()`` (god-file Phase 2 follow-up).
Handler injected to avoid importing ``main``.
"""

from __future__ import annotations

from typing import Callable


def build_pairing_parser(subparsers, *, cmd_pairing: Callable) -> None:
    """Attach the ``pairing`` subcommand to ``subparsers``."""
    pairing_parser = subparsers.add_parser(
        "pairing",
        help='Управление кодами подключения пользователей в личных сообщениях',
        description='Разрешить или отозвать доступ пользователя по коду подключения',
    )
    pairing_sub = pairing_parser.add_subparsers(dest="pairing_action")

    pairing_sub.add_parser("list", help='Показать ожидающих и допущенных пользователей')

    pairing_approve_parser = pairing_sub.add_parser(
        "approve", help='Одобрить запрос на подключение'
    )
    pairing_approve_parser.add_argument(
        "platform", help='Платформа: telegram, discord, slack или whatsapp'
    )
    pairing_approve_parser.add_argument(
        "code",
        metavar="request-id|code",
        help='ID запроса из pairing list или код, который бот отправил пользователю',
    )

    pairing_revoke_parser = pairing_sub.add_parser("revoke", help='Отозвать доступ пользователя')
    pairing_revoke_parser.add_argument("platform", help='Название платформы')
    pairing_revoke_parser.add_argument("user_id", help='ID пользователя для отзыва доступа')

    pairing_sub.add_parser("clear-pending", help='Удалить все ожидающие коды')
    pairing_parser.set_defaults(func=cmd_pairing)

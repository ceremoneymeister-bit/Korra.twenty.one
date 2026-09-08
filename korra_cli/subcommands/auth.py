"""``hermes auth`` subcommand parser.

Extracted verbatim from ``korra_cli/main.py:main()`` (god-file Phase 2).
Handler injected to avoid importing ``main``.
"""

from __future__ import annotations

from typing import Callable


def build_auth_parser(subparsers, *, cmd_auth: Callable) -> None:
    """Attach the ``auth`` subcommand to ``subparsers``."""
    auth_parser = subparsers.add_parser(
        "auth",
        help='Управление ключами и учётными записями провайдеров',
    )
    auth_subparsers = auth_parser.add_subparsers(dest="auth_action")
    auth_add = auth_subparsers.add_parser("add", help='Добавить ключ или учётную запись')
    auth_add.add_argument(
        "provider",
        help='ID провайдера, например anthropic, openai-codex или openrouter',
    )
    auth_add.add_argument(
        "--type",
        dest="auth_type",
        choices=["oauth", "api-key", "api_key"],
        help='Тип добавляемого ключа или учётной записи',
    )
    auth_add.add_argument("--label", help='Необязательная метка для отображения')
    auth_add.add_argument(
        "--api-key", help='Ключ API; если не указан, будет запрошен скрытым вводом'
    )
    auth_add.add_argument("--portal-url", help='Основной адрес портала Nous')
    auth_add.add_argument("--inference-url", help='Основной адрес API моделей Nous')
    auth_add.add_argument("--client-id", help='ID клиента OAuth')
    auth_add.add_argument("--scope", help='Заменить запрашиваемые права OAuth')
    auth_add.add_argument(
        "--no-browser",
        action="store_true",
        help='Не открывать браузер автоматически для входа OAuth',
    )
    auth_add.add_argument(
        "--timeout", type=float, help='Время ожидания сети и OAuth в секундах'
    )
    auth_add.add_argument(
        "--insecure",
        action="store_true",
        help='Отключить проверку TLS при входе OAuth',
    )
    auth_add.add_argument("--ca-bundle", help='Свой набор сертификатов CA для входа OAuth')
    auth_list = auth_subparsers.add_parser("list", help='Показать сохранённые ключи и учётные записи')
    auth_list.add_argument("provider", nargs="?", help='Необязательный фильтр по провайдеру')
    auth_remove = auth_subparsers.add_parser(
        "remove", help='Удалить ключ или учётную запись по номеру, ID или точной метке'
    )
    auth_remove.add_argument("provider", help='ID провайдера')
    auth_remove.add_argument(
        "target", help='Номер, ID или точная метка ключа или учётной записи'
    )
    auth_reset = auth_subparsers.add_parser(
        "reset", help='Снять отметку об исчерпании лимита со всех ключей провайдера'
    )
    auth_reset.add_argument("provider", help='ID провайдера')
    auth_status = auth_subparsers.add_parser(
        "status", help='Показать состояние входа у провайдера'
    )
    auth_status.add_argument("provider", help='ID провайдера')
    auth_logout = auth_subparsers.add_parser(
        "logout", help='Выйти из учётной записи провайдера и удалить сохранённые данные входа'
    )
    auth_logout.add_argument("provider", help='ID провайдера')
    auth_spotify = auth_subparsers.add_parser(
        "spotify", help='Подключить Spotify к Корре через PKCE'
    )
    auth_spotify.add_argument(
        "spotify_action",
        nargs="?",
        choices=["login", "status", "logout"],
        default="login",
    )
    auth_spotify.add_argument(
        "--client-id", help='client_id приложения Spotify; также задаётся через HERMES_SPOTIFY_CLIENT_ID'
    )
    auth_spotify.add_argument(
        "--redirect-uri",
        help='Разрешённый адрес перенаправления localhost для приложения Spotify',
    )
    auth_spotify.add_argument("--scope", help='Заменить запрашиваемые права Spotify')
    auth_spotify.add_argument(
        "--no-browser",
        action="store_true",
        help='Не открывать браузер автоматически',
    )
    auth_spotify.add_argument(
        "--timeout", type=float, help='Время ожидания обратного вызова и обмена токенами в секундах'
    )
    auth_parser.set_defaults(func=cmd_auth)

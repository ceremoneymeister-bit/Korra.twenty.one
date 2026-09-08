"""``hermes gateway`` and ``hermes proxy`` subcommand parsers.

Extracted verbatim from ``korra_cli/main.py:main()`` (god-file Phase 2).
Both parsers are built together because they shared one inline block (the
``gateway`` section also defined ``proxy``). Handlers injected to avoid
importing ``main``.
"""

from __future__ import annotations

import argparse
from typing import Callable

from korra_cli.subcommands._shared import add_accept_hooks_flag


def _add_compat_platform_flag(parser: argparse.ArgumentParser) -> None:
    """Accept stale `gateway <verb> --platform X` docs without advertising it.

    Gateway service lifecycle commands operate on the gateway process, not a
    single messaging adapter.  Photon briefly printed a per-platform start
    command during setup; keep that command parseable so users following the
    old hint don't get blocked by argparse before the gateway can start.
    """
    parser.add_argument(
        "--platform",
        dest="platform",
        help=argparse.SUPPRESS,
    )


def build_gateway_parser(
    subparsers, *, cmd_gateway: Callable, cmd_proxy: Callable, cmd_gateway_enroll: Callable
) -> None:
    """Attach the ``gateway`` and ``proxy`` subcommands to ``subparsers``."""
    # =========================================================================
    # gateway command
    # =========================================================================
    gateway_parser = subparsers.add_parser(
        "gateway",
        help='Управление шлюзом мессенджеров',
        description='Управление подключениями Telegram, Discord, WhatsApp, Weixin и других мессенджеров',
    )
    gateway_subparsers = gateway_parser.add_subparsers(dest="gateway_command")

    # gateway run (default)
    gateway_run = gateway_subparsers.add_parser(
        "run", help='Запустить шлюз в текущем терминале; рекомендуется для WSL, Docker и Termux'
    )
    gateway_run.add_argument(
        "-v",
        "--verbose",
        action="count",
        default=0,
        help='Подробность журналов в stderr: -v — INFO, -vv — DEBUG',
    )
    gateway_run.add_argument(
        "-q", "--quiet", action="store_true", help='Не выводить журналы в stderr'
    )
    gateway_run.add_argument(
        "--replace",
        action="store_true",
        help='Заменить уже работающий шлюз; удобно для systemd',
    )
    gateway_run.add_argument(
        "--force",
        action="store_true",
        help=(
            'Запустить шлюз в терминале, даже если профиль уже обслуживает systemd, launchd или s6. Без --force такой запуск запрещён: два диспетчера могут повредить общее состояние шлюза.'
        ),
    )
    gateway_run.add_argument(
        "--no-supervise",
        action="store_true",
        help=(
            'В Docker с s6-overlay команда gateway run обычно запускает службу s6 с автоматическим перезапуском и, при HERMES_DASHBOARD, веб-панелью. --no-supervise запускает шлюз основным процессом контейнера, который завершится вместе с ним. Вне s6-контейнера не действует.'
        ),
    )
    gateway_run.add_argument(
        "--external-supervisor",
        action="store_true",
        help=(
            'Указать, что шлюзом управляет внешний менеджер процессов. Перезапуск и обновление из чата завершат процесс, чтобы менеджер поднял его снова. Для обёрток launchd/systemd, скрывающих штатные метки среды.'
        ),
    )
    add_accept_hooks_flag(gateway_run)
    add_accept_hooks_flag(gateway_parser)

    # gateway start
    gateway_start = gateway_subparsers.add_parser(
        "start", help='Запустить установленную фоновую службу systemd/launchd'
    )
    gateway_start.add_argument(
        "--system",
        action="store_true",
        help='Работать с общесистемной службой шлюза Linux',
    )
    gateway_start.add_argument(
        "--all",
        action="store_true",
        help='Перед запуском остановить все зависшие процессы шлюза во всех профилях',
    )
    _add_compat_platform_flag(gateway_start)

    # gateway stop
    gateway_stop = gateway_subparsers.add_parser("stop", help='Остановить службу шлюза')
    gateway_stop.add_argument(
        "--system",
        action="store_true",
        help='Работать с общесистемной службой шлюза Linux',
    )
    gateway_stop.add_argument(
        "--all",
        action="store_true",
        help='Остановить все процессы шлюза во всех профилях',
    )

    # gateway restart
    gateway_restart = gateway_subparsers.add_parser(
        "restart", help='Перезапустить службу шлюза'
    )
    gateway_restart.add_argument(
        "--system",
        action="store_true",
        help='Работать с общесистемной службой шлюза Linux',
    )
    gateway_restart.add_argument(
        "--all",
        action="store_true",
        help='Перед перезапуском остановить все процессы шлюза во всех профилях',
    )
    _add_compat_platform_flag(gateway_restart)

    # gateway status
    gateway_status = gateway_subparsers.add_parser("status", help='Показать состояние шлюза')
    gateway_status.add_argument("--deep", action="store_true", help='Подробная проверка состояния')
    gateway_status.add_argument(
        "-l",
        "--full",
        action="store_true",
        help='Показать полный вывод службы и журналов без сокращения, где это поддерживается',
    )
    gateway_status.add_argument(
        "--system",
        action="store_true",
        help='Работать с общесистемной службой шлюза Linux',
    )
    _add_compat_platform_flag(gateway_status)

    # gateway install
    gateway_install = gateway_subparsers.add_parser(
        "install", help='Установить шлюз как фоновую службу systemd/launchd'
    )
    gateway_install.add_argument("--force", action="store_true", help='Переустановить принудительно')
    gateway_install.add_argument(
        "--system",
        action="store_true",
        help='Установить общесистемную службу Linux с запуском при загрузке',
    )
    gateway_install.add_argument(
        "--run-as-user",
        dest="run_as_user",
        help='Учётная запись для запуска системной службы Linux',
    )
    gateway_install.add_argument(
        "--start-now",
        dest="start_now",
        action="store_true",
        default=None,
        help='Запустить службу шлюза сразу после установки',
    )
    gateway_install.add_argument(
        "--no-start-now",
        dest="start_now",
        action="store_false",
        help='Не запускать службу шлюза после установки',
    )
    gateway_install.add_argument(
        "--start-on-login",
        dest="start_on_login",
        action="store_true",
        default=None,
        help='Включить автозапуск службы при входе или загрузке системы',
    )
    gateway_install.add_argument(
        "--no-start-on-login",
        dest="start_on_login",
        action="store_false",
        help='Не включать автозапуск службы',
    )
    gateway_install.add_argument(
        "--elevated-handoff",
        dest="elevated_handoff",
        action="store_true",
        help=argparse.SUPPRESS,
    )

    # gateway uninstall
    gateway_uninstall = gateway_subparsers.add_parser(
        "uninstall", help='Удалить службу шлюза'
    )
    gateway_uninstall.add_argument(
        "--system",
        action="store_true",
        help='Работать с общесистемной службой шлюза Linux',
    )

    # gateway list
    gateway_subparsers.add_parser("list", help='Показать все профили и состояние их шлюзов')

    # gateway setup
    gateway_subparsers.add_parser("setup", help='Настроить мессенджеры')

    # gateway migrate-legacy
    gateway_migrate_legacy = gateway_subparsers.add_parser(
        "migrate-legacy",
        help='Удалить устаревшие службы hermes.service от прежних установок',
        description=(
            'Остановить, отключить и удалить устаревшие файлы служб шлюза, например hermes.service. Службы профилей hermes-gateway-<profile>.service и сторонние службы не затрагиваются.'
        ),
    )
    gateway_migrate_legacy.add_argument(
        "--dry-run",
        dest="dry_run",
        action="store_true",
        help='Показать план удаления без изменений',
    )
    gateway_migrate_legacy.add_argument(
        "-y",
        "--yes",
        dest="yes",
        action="store_true",
        help='Пропустить запрос подтверждения',
    )

    # gateway enroll — enroll a self-hosted gateway with a relay connector
    # (connector⇄gateway auth). Redeems a single-use enrollment token for the
    # per-gateway secret + per-tenant delivery key and writes them to .env.
    # See docs/relay-connector-contract.md (and the connector repo's
    # docs/connector-gateway-auth-design.md). EXPERIMENTAL.
    gateway_enroll = gateway_subparsers.add_parser(
        "enroll",
        help='Подключить шлюз к ретранслятору и сохранить ключи доступа в .env',
        description=(
            'Использовать одноразовый токен подключения ретранслятора и вашу учётную запись Nous. Создать секрет шлюза и ключ доставки, сохранить GATEWAY_RELAY_ID, GATEWAY_RELAY_SECRET и GATEWAY_RELAY_DELIVERY_KEY в .env профиля. Требуется вход через korra setup. Недоступно в управляемых установках.'
        ),
    )
    gateway_enroll.add_argument(
        "--token",
        default=None,
        help=(
            'Одноразовый токен подключения из настроек шлюза; также GATEWAY_RELAY_ENROLL_TOKEN'
        ),
    )
    gateway_enroll.add_argument(
        "--connector-url",
        dest="connector_url",
        default=None,
        help=(
            'Основной адрес ретранслятора, например wss://connector.example.com/relay или https://connector.example.com. Также GATEWAY_RELAY_URL или gateway.relay_url в config.yaml.'
        ),
    )
    gateway_enroll.add_argument(
        "--gateway-id",
        dest="gateway_id",
        default=None,
        help=(
            'Постоянный ID экземпляра шлюза для отдельного отключения; по умолчанию gw-<hostname>'
        ),
    )
    gateway_enroll.add_argument(
        "--wake-url",
        dest="wake_url",
        default=None,
        help=(
            'Необязательный адрес пробуждения: ретранслятор отправляет GET без данных, когда есть работа, а шлюз спит. Сохраняется как GATEWAY_RELAY_WAKE_URL в .env. Без адреса работа будет получена при следующем самостоятельном подключении шлюза.'
        ),
    )
    gateway_enroll.set_defaults(func=cmd_gateway_enroll)

    # =========================================================================
    # proxy command — local OpenAI-compatible proxy that attaches the user's
    # OAuth-authenticated provider credentials to outbound requests. Lets
    # external apps (OpenViking, Karakeep, Open WebUI, ...) ride a logged-in
    # subscription without copy-pasting static API keys.
    # =========================================================================
    proxy_parser = subparsers.add_parser(
        "proxy",
        help='Локальный прокси с API OpenAI для провайдеров OAuth',
        description=(
            'Запустить локальный HTTP-сервер, передающий совместимые с OpenAI запросы провайдеру со входом OAuth, например Nous. Внешние приложения могут передать любой bearer-токен; прокси подставит ваши настоящие данные входа.'
        ),
    )
    proxy_subparsers = proxy_parser.add_subparsers(dest="proxy_command")

    proxy_start = proxy_subparsers.add_parser(
        "start", help='Запустить прокси в текущем терминале'
    )
    proxy_start.add_argument(
        "--provider",
        default="nous",
        help='Провайдер: nous или xai (по умолчанию nous). См. korra proxy providers.',
    )
    proxy_start.add_argument(
        "--host",
        default=None,
        help='Адрес сервера (по умолчанию 127.0.0.1); 0.0.0.0 — доступ из локальной сети',
    )
    proxy_start.add_argument(
        "--port",
        type=int,
        default=None,
        help='Порт сервера (по умолчанию 8645)',
    )

    proxy_subparsers.add_parser(
        "status", help='Показать готовые подключения провайдеров прокси'
    )
    proxy_subparsers.add_parser(
        "providers", help='Показать доступных провайдеров прокси'
    )
    proxy_parser.set_defaults(func=cmd_proxy)
    gateway_parser.set_defaults(func=cmd_gateway)

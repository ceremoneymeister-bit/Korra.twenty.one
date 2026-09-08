"""``hermes dashboard`` / ``hermes serve`` subcommand parsers.

``dashboard`` is the browser web UI; ``serve`` is the same gateway, headless —
what the desktop app and remote backends run. ``serve`` also skips the web UI
build (``headless_backend=True``): pure JSON-RPC/WS clients never load the SPA.
Both share one handler (``cmd_dashboard`` → ``start_server``). Extracted from
``korra_cli/main.py:main()`` (god-file Phase 2); handler injected to avoid
importing ``main``.
"""

from __future__ import annotations

import argparse
from typing import Callable


def _add_server_runtime_args(parser) -> None:
    """Attach the runtime flags shared by ``dashboard`` and ``serve``.

    Both subcommands boot the *same* ``web_server.start_server`` (the
    JSON-RPC/WebSocket gateway). ``dashboard`` opens a browser UI on top of
    it; ``serve`` is the headless backend the desktop app and remote clients
    connect to. The shared server logic lives in one place — only the
    browser-opening behavior and help framing differ.
    """
    parser.add_argument(
        "--port", type=int, default=9119, help='Порт: по умолчанию 9119, 0 — выбрать автоматически'
    )
    parser.add_argument(
        "--host", default="127.0.0.1", help='Адрес сервера (по умолчанию 127.0.0.1)'
    )
    parser.add_argument(
        "--insecure",
        action="store_true",
        help=(
            'Устаревший параметр, ничего не делает. Ранее отключал вход при публичном адресе. После усиления защиты в июне 2026 года публичный адрес всегда требует пароль или OAuth. Для локального доступа используйте 127.0.0.1 и туннель.'
        ),
    )
    parser.add_argument(
        "--skip-build",
        action="store_true",
        help=(
            'Использовать готовую сборку веб-панели без пересборки. Для CI и фоновых задач, где npm может быть недоступен. Собрать заранее: cd web && npm run build'
        ),
    )
    parser.add_argument(
        "--isolated",
        action="store_true",
        help=(
            'Для именованного профиля запустить отдельный сервер этого профиля. По умолчанию все профили подключаются к одному серверу на компьютере с выбором нужного профиля.'
        ),
    )
    # Internal flag set by the unified-launch re-exec (cmd_dashboard) to
    # preselect the launching profile in the SPA switcher. Hidden from --help.
    parser.add_argument(
        "--open-profile",
        dest="open_profile",
        default="",
        help=argparse.SUPPRESS,
    )
    # Lifecycle flags — mutually exclusive with each other and with the
    # start-a-server flags above (if both are passed, --stop / --status win
    # because they exit before the server is started).  The server has no
    # service manager and no PID file, so these scan the process table for
    # `hermes dashboard` / `hermes serve` cmdlines and SIGTERM them directly —
    # the same path `hermes update` uses to clean up stale servers.
    parser.add_argument(
        "--stop",
        action="store_true",
        help='Остановить все процессы веб-сервера Корры и выйти',
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help='Показать процессы веб-сервера Корры и выйти',
    )


def build_dashboard_parser(
    subparsers, *, cmd_dashboard: Callable, cmd_dashboard_register: Callable
) -> None:
    """Attach the ``dashboard`` and ``serve`` subcommands.

    Both share the same backend (``cmd_dashboard`` → ``start_server``).
    ``dashboard`` is the browser UI; ``serve`` is the headless backend used by
    the desktop app and remote clients. They are independent surfaces — neither
    "launches" the other — so the desktop app spawns ``serve``, never
    ``dashboard``.
    """
    # =========================================================================
    # dashboard command — the browser web UI
    # =========================================================================
    dashboard_parser = subparsers.add_parser(
        "dashboard",
        help='Запустить веб-панель',
        description='Открыть веб-панель Корры для настроек, ключей API и бесед',
    )
    _add_server_runtime_args(dashboard_parser)
    dashboard_parser.add_argument(
        "--no-open", action="store_true", help='Не открывать браузер автоматически'
    )
    # Backward-compat shim: older Hermes desktop app shells (<= 0.15.x) spawn the
    # backend as `hermes dashboard --no-open --tui --host ... --port ...`. The
    # `--tui` flag was removed from this subcommand in cae6b5486 (embedded chat is
    # always on now). When a user's CLI updates past that commit but their desktop
    # app binary has not, argparse used to hard-error with "unrecognized arguments:
    # --tui" and exit(2) — the backend died before becoming ready and the GUI just
    # showed "Hermes couldn't start" with no actionable cause. Accept and silently
    # ignore the flag so an old app + new CLI degrades gracefully instead of
    # bricking. Hidden from --help; safe to delete once the floor app version is
    # well past 0.16.0.
    dashboard_parser.add_argument(
        "--tui",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    dashboard_parser.set_defaults(func=cmd_dashboard)

    # =========================================================================
    # serve command — the headless backend server
    #
    # `serve` boots the exact same gateway as `dashboard` but never opens a
    # browser. It exists so the Hermes Desktop app (and headless remote
    # backends) can launch a backend WITHOUT invoking `dashboard`: the desktop
    # app and the web dashboard are independent surfaces that merely share this
    # server, and neither should appear to launch the other.
    # =========================================================================
    serve_parser = subparsers.add_parser(
        "serve",
        help='Запустить сервер Корры без интерфейса для приложения и удалённых клиентов',
        description=(
            'Запустить сервер Корры: шлюз JSON-RPC/WebSocket для приложения и удалённых клиентов. Браузерный интерфейс не открывается.'
        ),
    )
    _add_server_runtime_args(serve_parser)
    # Accepted but redundant: `serve` is always headless (see set_defaults
    # below). Kept so callers that pass the legacy `--no-open` flag (e.g. the
    # desktop backend spawn) don't trip "unrecognized arguments".
    serve_parser.add_argument(
        "--no-open", action="store_true", help=argparse.SUPPRESS
    )
    serve_parser.add_argument(
        "--ssh-session-token-file",
        dest="ssh_session_token_file",
        metavar="PATH",
        default=None,
        help='Прочитать одноразовый токен SSH-сеанса приложения из PATH',
    )
    serve_parser.add_argument(
        "--ssh-owner-nonce",
        dest="ssh_owner_nonce",
        metavar="NONCE",
        default=None,
        help='Пометить процесс SSH-сервера как запущенный приложением',
    )
    # `headless_backend` marks the lean path: desktop/remote clients speak pure
    # JSON-RPC/WS, so `serve` skips the web UI build AND never serves the SPA
    # (cmd_dashboard exports HERMES_SERVE_HEADLESS=1). `dashboard` leaves it
    # unset and serves the browser UI as before.
    serve_parser.set_defaults(func=cmd_dashboard, no_open=True, headless_backend=True)

    # `hermes dashboard register` — register a self-hosted dashboard OAuth
    # client with Nous Portal and write the client_id into ~/.hermes/.env.
    # Nested subparser so bare `hermes dashboard` keeps launching the server
    # (set_defaults(func=cmd_dashboard) above remains the default).
    dashboard_subparsers = dashboard_parser.add_subparsers(
        dest="dashboard_subcommand"
    )
    dashboard_register_parser = dashboard_subparsers.add_parser(
        "register",
        help='Зарегистрировать свою веб-панель в портале Nous и сохранить ID клиента OAuth в .env',
        description=(
            'Зарегистрировать эту веб-панель в вашей учётной записи Nous. Создать клиента OAuth, сохранить HERMES_DASHBOARD_OAUTH_CLIENT_ID в .env профиля и показать, как включить вход. Требуется предварительный вход через korra setup.'
        ),
    )
    dashboard_register_parser.add_argument(
        "--name",
        default=None,
        help='Понятное название веб-панели; по умолчанию создаётся автоматически',
    )
    dashboard_register_parser.add_argument(
        "--redirect-uri",
        dest="redirect_uri",
        default=None,
        help=(
            'Необязательный публичный HTTPS-адрес возврата OAuth, например https://korra.example.com/auth/callback. Для работы только на localhost не требуется.'
        ),
    )
    dashboard_register_parser.add_argument(
        "--portal-url",
        dest="portal_url",
        default=None,
        help=(
            'Другой адрес портала Nous для регистрации; по умолчанию тот, где вы вошли. Токен должен действовать на этом портале. Также задаётся через HERMES_DASHBOARD_PORTAL_URL; в основном для тестовых порталов.'
        ),
    )
    dashboard_register_parser.set_defaults(func=cmd_dashboard_register)

"""``hermes slack`` subcommand parser.

Extracted verbatim from ``korra_cli/main.py:main()`` (god-file Phase 2).
Handler injected to avoid importing ``main``.
"""

from __future__ import annotations

from typing import Callable


def build_slack_parser(subparsers, *, cmd_slack: Callable) -> None:
    """Attach the ``slack`` subcommand to ``subparsers``."""
    # =========================================================================
    # slack command
    # =========================================================================
    slack_parser = subparsers.add_parser(
        "slack",
        help='Подключение Slack: создание манифеста и другие настройки',
        description='Настройка подключения Slack к Корре',
    )
    slack_sub = slack_parser.add_subparsers(dest="slack_command")
    slack_manifest = slack_sub.add_parser(
        "manifest",
        help='Вывести или сохранить манифест приложения Slack с командами шлюза: /btw, /stop, /model и другими',
        description=(
            'Создать манифест Slack со всеми командами шлюза из COMMAND_REGISTRY. Вставьте результат в настройки приложения Slack: Features → App Manifest → Edit, затем Save. Переустановите приложение, если Slack предложит.'
        ),
    )
    slack_manifest.add_argument(
        "--write",
        nargs="?",
        const=True,
        default=None,
        metavar="PATH",
        help='Сохранить манифест в файл вместо stdout; без PATH — в slack-manifest.json профиля',
    )
    slack_manifest.add_argument(
        "--name",
        default=None,
        help='Имя бота для отображения (по умолчанию Korra)',
    )
    slack_manifest.add_argument(
        "--description",
        default=None,
        help='Описание бота в каталоге приложений Slack',
    )
    slack_long_description = slack_manifest.add_mutually_exclusive_group()
    slack_long_description.add_argument(
        "--long-description",
        default=None,
        metavar="TEXT",
        help='Подробное описание приложения Slack: 175–4000 символов',
    )
    slack_long_description.add_argument(
        "--long-description-file",
        default=None,
        metavar="PATH",
        help=(
            'Прочитать подробное описание Slack из текстового файла UTF-8: 175–4000 символов'
        ),
    )
    slack_manifest.add_argument(
        "--slashes-only",
        action="store_true",
        help='Вывести только массив features.slash_commands для ручного добавления к существующему манифесту',
    )
    slack_messaging = slack_manifest.add_mutually_exclusive_group()
    slack_messaging.add_argument(
        "--no-assistant",
        action="store_true",
        help='Не включать режим Slack AI Assistant: assistant_view, право assistant:write и события assistant_thread_*. Личные сообщения будут обычным чатом, где /help и /new работают прямо в переписке.',
    )
    slack_messaging.add_argument(
        "--agent-view",
        action="store_true",
        help='Включить новый режим общения Slack Agent: agent_view, app_home_opened и message.im вместо assistant_view. После применения манифеста Slack не позволяет вернуть прежний режим.',
    )
    slack_parser.set_defaults(func=cmd_slack)

"""``hermes tools`` subcommand parser.

Extracted from ``korra_cli/main.py:main()`` (god-file Phase 2 follow-up).
Handler injected to avoid importing ``main``.
"""

from __future__ import annotations

from typing import Callable


def build_tools_parser(subparsers, *, cmd_tools: Callable) -> None:
    """Attach the ``tools`` subcommand to ``subparsers``."""
    tools_parser = subparsers.add_parser(
        "tools",
        help='Выбрать доступные инструменты для каждой платформы',
        description=(
            'Включить, отключить или показать инструменты CLI, Telegram, Discord и других платформ. Встроенные наборы указываются по имени, например web или memory. Инструменты MCP — server:tool, например github:create_issue. korra tools без подкоманды открывает меню настройки.'
        ),
    )
    tools_parser.add_argument(
        "--summary",
        action="store_true",
        help='Показать сводку включённых инструментов по платформам и выйти',
    )
    tools_sub = tools_parser.add_subparsers(dest="tools_action")

    # hermes tools list [--platform cli]
    tools_list_p = tools_sub.add_parser(
        "list",
        help='Показать все инструменты и состояние их включения',
    )
    tools_list_p.add_argument(
        "--platform",
        default="cli",
        help='Платформа для просмотра (по умолчанию cli)',
    )

    # hermes tools disable <name...> [--platform cli]
    tools_disable_p = tools_sub.add_parser(
        "disable",
        help='Отключить наборы инструментов или инструменты MCP',
    )
    tools_disable_p.add_argument(
        "names",
        nargs="+",
        metavar="NAME",
        help='Имя набора, например web, или инструмент MCP в формате server:tool',
    )
    tools_disable_p.add_argument(
        "--platform",
        default="cli",
        help='Платформа для изменения (по умолчанию cli)',
    )

    # hermes tools enable <name...> [--platform cli]
    tools_enable_p = tools_sub.add_parser(
        "enable",
        help='Включить наборы инструментов или инструменты MCP',
    )
    tools_enable_p.add_argument(
        "names",
        nargs="+",
        metavar="NAME",
        help='Имя набора или инструмент MCP в формате server:tool',
    )
    tools_enable_p.add_argument(
        "--platform",
        default="cli",
        help='Платформа для изменения (по умолчанию cli)',
    )

    # hermes tools post-setup <key>
    tools_postsetup_p = tools_sub.add_parser(
        "post-setup",
        help='Выполнить установку зависимостей провайдера: npm, pip или программу',
        description=(
            'Выполнить установщик зависимостей инструмента, как после выбора провайдера в korra tools. Для Chromium, Camofox, cua-driver, KittenTTS, Piper, ddgs, Spotify, Langfuse и xAI. Команда работает без меню и используется веб-панелью. Ключи: agent_browser, camofox, cua_driver, kittentts, piper, ddgs, spotify, langfuse, xai_grok.'
        ),
    )
    tools_postsetup_p.add_argument(
        "post_setup_key",
        metavar="KEY",
        help='Ключ установщика, например agent_browser, camofox или kittentts',
    )
    tools_parser.set_defaults(func=cmd_tools)

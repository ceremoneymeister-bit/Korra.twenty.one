"""``hermes security`` subcommand parser.

Extracted verbatim from ``korra_cli/main.py:main()`` (god-file Phase 2).
Handler injected to avoid importing ``main``.
"""

from __future__ import annotations

from typing import Callable


def build_security_parser(subparsers, *, cmd_security: Callable) -> None:
    """Attach the ``security`` subcommand to ``subparsers``."""
    # =========================================================================
    security_parser = subparsers.add_parser(
        "security",
        help='Проверка уязвимостей окружения Python, плагинов и MCP через OSV.dev',
        description=(
            'Проверить через OSV.dev пакеты Python окружения Корры, зависимости установленных плагинов и закреплённые npx/uvx-серверы MCP из config.yaml. Глобальные пакеты и расширения редакторов и браузеров не проверяются.'
        ),
    )
    security_subparsers = security_parser.add_subparsers(
        dest="security_command",
        metavar="<subcommand>",
    )

    audit_parser = security_subparsers.add_parser(
        "audit",
        help='Выполнить разовую проверку зависимостей на уязвимости',
        description='Запросить OSV.dev об известных уязвимостях установленных компонентов',
    )
    audit_parser.add_argument(
        "--json",
        action="store_true",
        help='Вывести машиночитаемый JSON',
    )
    audit_parser.add_argument(
        "--fail-on",
        default="critical",
        choices=["low", "moderate", "high", "critical"],
        help='Ненулевой код выхода при находках указанной тяжести; по умолчанию critical',
    )
    audit_parser.add_argument(
        "--skip-venv",
        action="store_true",
        help='Не проверять окружение Python Корры',
    )
    audit_parser.add_argument(
        "--skip-plugins",
        action="store_true",
        help='Не проверять файлы зависимостей плагинов',
    )
    audit_parser.add_argument(
        "--skip-mcp",
        action="store_true",
        help='Не проверять закреплённые серверы MCP из config.yaml',
    )
    audit_parser.set_defaults(func=cmd_security)
    security_parser.set_defaults(func=cmd_security)

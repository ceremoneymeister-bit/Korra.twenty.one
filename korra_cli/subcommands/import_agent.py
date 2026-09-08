"""``hermes import-agent`` subcommand parser.

Follows the ``hermes claw`` pattern (see ``korra_cli/subcommands/claw.py``):
parser building lives here, the handler is injected to avoid importing
``main``, and the import logic itself lives in ``korra_cli/agent_import.py``.
"""

from __future__ import annotations

from typing import Callable


def build_import_agent_parser(subparsers, *, cmd_import_agent: Callable) -> None:
    """Attach the ``import-agent`` subcommand to ``subparsers``."""
    parser = subparsers.add_parser(
        "import-agent",
        help='Перенести настройки Claude Code или Codex CLI в Корру',
        description=(
            'Перенести инструкции CLAUDE.md/AGENTS.md, разрешения команд, серверы MCP, навыки и память из другого агента. Перед изменениями показывается план. Ключи API и данные входа не переносятся; настройте их через korra setup.'
        ),
    )
    parser.add_argument(
        "agent",
        nargs="?",
        choices=["claude-code", "codex"],
        help='Источник переноса; по умолчанию определяется по ~/.claude или ~/.codex',
    )
    parser.add_argument(
        "--source",
        help='Папка настроек исходного агента (по умолчанию ~/.claude или ~/.codex)',
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help='Только показать план импорта без изменений',
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help='Перезаписать элементы Корры с совпадающими именами; по умолчанию пропустить',
    )
    parser.add_argument(
        "--yes", "-y", action="store_true", help='Пропустить запросы подтверждения'
    )
    parser.set_defaults(func=cmd_import_agent)

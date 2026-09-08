"""``hermes approvals`` subcommand parser.

Follows the cron/security pattern: parser construction lives here, the
handler is injected by ``main.py`` so this module never imports ``main``
(cycle avoidance).
"""

from __future__ import annotations

import argparse
from typing import Callable


def build_approvals_parser(subparsers, *, cmd_approvals: Callable) -> None:
    """Attach the ``approvals`` subcommand to ``subparsers``."""
    approvals_parser = subparsers.add_parser(
        "approvals",
        help='Настройка подтверждений: предложить разрешённые команды по истории решений',
        description=(
            'Управление подтверждением опасных команд. `korra approvals suggest` анализирует прошлые решения и предлагает записи command_allowlist, чтобы не спрашивать повторно о привычных командах.'
        ),
    )
    approvals_subparsers = approvals_parser.add_subparsers(
        dest="approvals_command",
        metavar="<subcommand>",
    )

    suggest_parser = approvals_subparsers.add_parser(
        "suggest",
        help='Предложить записи command_allowlist по истории подтверждений',
        description=(
            'Найти часто разрешаемые опасные команды в истории бесед и показать пронумерованные предложения. Запись настроек — только с --apply. Удаление папок, sudo, запись на диски и изменение ключей доступа никогда не предлагаются.'
        ),
    )
    suggest_parser.add_argument(
        "--apply",
        dest="apply_indices",
        metavar="N[,M...]",
        help='Добавить указанные номера предложений из предыдущего запуска в command_allowlist файла config.yaml',
    )
    suggest_parser.add_argument(
        "--json",
        action="store_true",
        help='Вывести машиночитаемый JSON',
    )
    suggest_parser.add_argument(
        "--days",
        type=int,
        default=90,
        help='За сколько дней проверить историю (по умолчанию 90; 0 — за всё время)',
    )
    suggest_parser.add_argument(
        "--min-count",
        dest="min_count",
        type=int,
        default=2,
        help='Минимум подтверждений для предложения правила (по умолчанию 2)',
    )
    suggest_parser.add_argument(
        "--limit",
        type=int,
        default=20,
        help='Максимум предложений (по умолчанию 20)',
    )
    suggest_parser.add_argument(
        "--db",
        help='Путь к другой базе бесед (по умолчанию state.db в папке профиля)',
    )
    suggest_parser.set_defaults(func=cmd_approvals)

    test_parser = approvals_subparsers.add_parser(
        "test",
        help='Проверить решение системы подтверждений без выполнения команды',
        description=(
            'Проверить команду по действующим правилам: обязательные запреты, approvals.deny, опасные шаблоны, разрешения и режим yolo/off. Показать решение, сработавшее правило и нормализованную команду без выполнения и сохранения. Коды выхода: 0 — разрешено, 2 — нужно подтверждение, 3 — запрещено. Перед командой укажите --, например: korra approvals test -- rm -rf /tmp/x'
        ),
    )
    test_parser.add_argument(
        "--env-type",
        dest="env_type",
        default="local",
        help='Среда терминала для проверки (по умолчанию local; для изолированных контейнеров, например docker, эти проверки пропускаются)',
    )
    test_parser.add_argument(
        "--json",
        action="store_true",
        help='Вывести машиночитаемый JSON',
    )
    test_parser.add_argument(
        "command_words",
        nargs=argparse.REMAINDER,
        metavar="command",
        # NOTE: dest must NOT be "command" — main.py's startup path reads
        # args.command as the top-level subcommand name ("approvals").
        help='Проверяемая команда; добавьте перед ней --, чтобы сохранить её параметры',
    )
    test_parser.set_defaults(func=cmd_approvals)

    approvals_parser.set_defaults(func=cmd_approvals)

"""``hermes sync`` subcommand parser — Skill Sync.

Cloned from ``korra_cli/subcommands/cron.py`` — same injected-handler shape
(``func=cmd_sync``) so this module does not import ``main`` (cycle avoidance).

Skill Sync covers two surfaces, both under this one command for launch:

  Personal — your own skills, across your own devices:
    hermes sync status                 show gate/opt-in/head state
    hermes sync pull                   pull and materialize opted-in skills
    hermes sync push                   push opted-in skills
    hermes sync now                    reconcile: pull then push
    hermes sync enable <skill>         opt a skill into sync
    hermes sync disable <skill>        opt a skill out of sync
    hermes sync device [--name]        show or set this device's label

  Organisation — skills shared with your team:
    hermes sync propose <skill>        share a skill with your organisation

Sync is INERT unless the resolved Nous token carries the access-gate claim
AND a sync base URL is configured. The commands report that state rather than
failing opaquely.
"""

from __future__ import annotations

import argparse
from typing import Callable


def build_sync_parser(subparsers, *, cmd_sync: Callable) -> None:
    """Attach the ``sync`` subcommand (and its sub-actions) to ``subparsers``."""
    sync_parser = subparsers.add_parser(
        "sync",
        help='Синхронизация навыков между вашими устройствами и командой',
        description=(
            'Переносите личные навыки между устройствами. Если ваша учётная запись входит в организацию, вы также получаете общие навыки и можете предлагать команде свои.'
        ),
        epilog=(
            'Примеры: korra sync status — что и откуда синхронизируется; korra sync enable my-skill — включить навык; korra sync now — получить и отправить изменения; korra sync propose my-skill — предложить навык команде.'
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sync_sub = sync_parser.add_subparsers(dest="sync_command")

    sync_sub.add_parser("status", help='Показать, что и откуда синхронизируется')
    sync_sub.add_parser(
        "pull", help='Получить ваши синхронизируемые навыки и навыки организации'
    )
    sync_sub.add_parser("push", help='Отправить навыки, для которых вы включили синхронизацию')
    sync_sub.add_parser("now", help='Синхронизировать сейчас: получить, затем отправить')

    enable = sync_sub.add_parser("enable", help='Включить синхронизацию навыка')
    enable.add_argument("skill", help='Имя навыка из метаданных или имя папки')

    disable = sync_sub.add_parser("disable", help='Исключить навык из синхронизации')
    disable.add_argument("skill", help='Имя навыка из метаданных или имя папки')

    device = sync_sub.add_parser(
        "device",
        help='Показать или задать имя устройства для консоли синхронизации',
    )
    device.add_argument(
        "--name",
        dest="device_name",
        default=None,
        help='Задать понятное имя устройства, например «Рабочий ноутбук». Без аргумента — показать текущее имя.',
    )

    # Org-shared skills. A member's submission becomes a proposal an admin
    # reviews; an admin's merges straight into the shared set. Accounts that
    # aren't in a shared organisation are told so plainly.
    propose = sync_sub.add_parser(
        "propose",
        help='Поделиться навыком с организацией',
        description=(
            'Предложить свой навык организации. У администратора навык добавляется сразу, у остальных — после проверки администратором. Доступно только учётным записям в организации.'
        ),
    )
    propose.add_argument("name", help='Имя навыка для передачи')
    propose.add_argument(
        "-m",
        "--message",
        default=None,
        help='Необязательное сообщение об изменениях',
    )

    sync_parser.set_defaults(func=cmd_sync)

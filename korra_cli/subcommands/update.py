"""``hermes update`` subcommand parser.

Extracted verbatim from ``korra_cli/main.py:main()`` (god-file Phase 2).
Handler injected to avoid importing ``main``.
"""

from __future__ import annotations

from typing import Callable


def build_update_parser(subparsers, *, cmd_update: Callable) -> None:
    """Attach the ``update`` subcommand to ``subparsers``."""
    # =========================================================================
    # update command
    # =========================================================================
    update_parser = subparsers.add_parser(
        "update",
        help='Обновить Корру до последней версии',
        description='Получить последние изменения из Git и переустановить зависимости',
    )
    update_parser.add_argument(
        "--gateway",
        action="store_true",
        default=False,
        help='Режим шлюза: запросы через файловый IPC вместо stdin; используется командой /update',
    )
    update_parser.add_argument(
        "--check",
        action="store_true",
        default=False,
        help='Проверить наличие обновления без установки',
    )
    update_parser.add_argument(
        "--plan",
        action="store_true",
        default=False,
        help=(
            'Показать план обновления без изменений: тип установки (git/docker/nix), службы всех профилей, их менеджеры процессов, версии кода и способы перезапуска. Только чтение, подходит для работающих установок.'
        ),
    )
    update_parser.add_argument(
        "--no-backup",
        action="store_true",
        default=False,
        help='Пропустить все резервные копии перед этим обновлением: быстрый снимок и полный ZIP; заменяет updates.pre_update_backup',
    )
    update_parser.add_argument(
        "--backup",
        action="store_true",
        default=False,
        help='Обязательно создать быстрый снимок состояния и полный ZIP папки данных перед этим обновлением, независимо от updates.pre_update_backup',
    )
    update_parser.add_argument(
        "--yes",
        "-y",
        action="store_true",
        default=False,
        help='Без запросов ввода: подтвердить перенос настроек и восстановление локальных правок; пропустить подключение upstream для форка. Ввод ключей API пропускается; для него выполните korra config migrate отдельно.',
    )
    update_parser.add_argument(
        "--keep-stash",
        action="store_true",
        default=False,
        help=(
            'Не возвращать локальные правки после обновления. Несохранённые изменения помещаются в git stash и остаются там. Используется обновлением приложения, чтобы старые правки исходников не применялись незаметно.'
        ),
    )
    update_parser.add_argument(
        "--branch",
        default=None,
        metavar="NAME",
        help=(
            'Обновить указанную ветку вместо main. Если открыта другая ветка, Корра сначала сохранит несохранённые изменения в stash и переключится на нужную.'
        ),
    )
    update_parser.add_argument(
        "--switch-branch",
        action="store_true",
        default=False,
        help=(
            'При updates.parked_branch_strategy: update_in_place временно переключиться на целевую ветку и обновить её, не добавляя слияние в текущую ветку. При обычной стратегии switch ничего не меняет. Рабочая папка должна быть чистой.'
        ),
    )
    update_parser.add_argument(
        "--force",
        action="store_true",
        default=False,
        help='Windows: продолжить обновление при обнаружении другого процесса команды. Возможны предупреждения WinError 32. Проверку процессов окружения Python не отключает; см. --force-venv.',
    )
    update_parser.add_argument(
        "--force-venv",
        action="store_true",
        default=False,
        help='Windows: изменять окружение Python, даже если им пользуются приложение, шлюз или терминалы. Они могут блокировать .pyd и сорвать обновление зависимостей. Используйте только при ложном обнаружении занятых файлов.',
    )
    update_parser.set_defaults(func=cmd_update)

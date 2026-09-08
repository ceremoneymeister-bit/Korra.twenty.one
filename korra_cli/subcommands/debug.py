"""``hermes debug`` subcommand parser.

Extracted verbatim from ``korra_cli/main.py:main()`` (god-file Phase 2).
Handler injected to avoid importing ``main``.
"""

from __future__ import annotations

import argparse
from typing import Callable


def build_debug_parser(subparsers, *, cmd_debug: Callable) -> None:
    """Attach the ``debug`` subcommand to ``subparsers``."""
    # =========================================================================
    # debug command
    # =========================================================================
    debug_parser = subparsers.add_parser(
        "debug",
        help='Диагностика: журналы и сведения о системе для поддержки',
        description='Средства диагностики Корры. korra debug share загружает сведения о системе и последние записи журналов в сервис публикации текста и выдаёт ссылку для поддержки.',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='Примеры: korra debug share — загрузить отчёт с подтверждением; --yes — без подтверждения; --lines 500 — больше строк; --expire 30 — хранить 30 дней; --local — вывести локально; --no-redact — не скрывать секреты; --nous — закрытое хранилище Nous. Удаление: korra debug delete <url>.',
    )
    debug_sub = debug_parser.add_subparsers(dest="debug_command")
    share_parser = debug_sub.add_parser(
        "share",
        help='Загрузить диагностический отчёт и показать ссылку',
    )
    share_parser.add_argument(
        "--lines",
        type=int,
        default=200,
        help='Число строк из каждого журнала (по умолчанию 200)',
    )
    share_parser.add_argument(
        "--expire",
        type=int,
        default=7,
        help='Срок хранения в днях (по умолчанию 7)',
    )
    share_parser.add_argument(
        "--local",
        action="store_true",
        help='Вывести отчёт локально без загрузки',
    )
    share_parser.add_argument(
        "-y",
        "--yes",
        action="store_true",
        help=(
            'Пропустить подтверждение и сразу загрузить отчёт. Обязательно для скриптов и CI без терминала; без этого параметра загрузка не выполняется.'
        ),
    )
    share_parser.add_argument(
        "--no-redact",
        action="store_true",
        help=(
            'Не скрывать секреты перед загрузкой. По умолчанию ключи и пароли принудительно скрываются, чтобы не попасть в публичную публикацию.'
        ),
    )
    share_parser.add_argument(
        "--nous",
        action="store_true",
        help=(
            'Загрузить отчёт в закрытое хранилище Nous (AWS S3). Доступен только сотрудникам Nous и допущенным модераторам Discord через вход Google; удаляется через 14 дней. Секреты скрываются, кроме случая с --no-redact.'
        ),
    )
    delete_parser = debug_sub.add_parser(
        "delete",
        help='Удалить отчёт, загруженный через korra debug share',
    )
    delete_parser.add_argument(
        "urls",
        nargs="*",
        default=[],
        help='Один или несколько адресов публикаций для удаления, например https://paste.rs/abc123',
    )
    debug_parser.set_defaults(func=cmd_debug)

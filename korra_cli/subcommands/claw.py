"""``hermes claw`` subcommand parser.

Extracted from ``korra_cli/main.py:main()`` (god-file Phase 2 follow-up).
Handler injected to avoid importing ``main``.
"""

from __future__ import annotations

from typing import Callable


def build_claw_parser(subparsers, *, cmd_claw: Callable) -> None:
    """Attach the ``claw`` subcommand to ``subparsers``."""
    claw_parser = subparsers.add_parser(
        "claw",
        help='Перенос данных из OpenClaw',
        description='Перенести настройки, память, навыки и ключи API из OpenClaw в Корру',
    )
    claw_subparsers = claw_parser.add_subparsers(dest="claw_action")

    # claw migrate
    claw_migrate = claw_subparsers.add_parser(
        "migrate",
        help='Перенести данные из OpenClaw в Корру',
        description='Импортировать настройки, память, навыки и ключи API из OpenClaw. Перед изменениями всегда показывается предварительный план.',
    )
    claw_migrate.add_argument(
        "--source", help='Папка OpenClaw (по умолчанию ~/.openclaw)'
    )
    claw_migrate.add_argument(
        "--dry-run",
        action="store_true",
        help='Только показать план переноса, без изменений',
    )
    claw_migrate.add_argument(
        "--preset",
        choices=["user-data", "full"],
        default="full",
        help='Вариант переноса (по умолчанию full). Ключи доступа не переносятся без --migrate-secrets при любом варианте.',
    )
    claw_migrate.add_argument(
        "--overwrite",
        action="store_true",
        help='Перезаписать существующие файлы; по умолчанию перенос при конфликтах запрещён',
    )
    claw_migrate.add_argument(
        "--migrate-secrets",
        action="store_true",
        help='Перенести разрешённые секреты: TELEGRAM_BOT_TOKEN, ключи API и другие. Требуется и при --preset full.',
    )
    claw_migrate.add_argument(
        "--no-backup",
        action="store_true",
        help='Не создавать резервный ZIP-снимок профиля перед переносом. По умолчанию снимок сохраняется в backups/; восстановление — через korra import.',
    )
    claw_migrate.add_argument(
        "--workspace-target", help='Абсолютный путь для копирования инструкций проекта'
    )
    claw_migrate.add_argument(
        "--skill-conflict",
        choices=["skip", "overwrite", "rename"],
        default="skip",
        help='Как поступать при совпадении имён навыков (по умолчанию skip — пропустить)',
    )
    claw_migrate.add_argument(
        "--yes", "-y", action="store_true", help='Пропустить запросы подтверждения'
    )

    # claw cleanup
    claw_cleanup = claw_subparsers.add_parser(
        "cleanup",
        aliases=["clean"],
        help='Архивировать оставшиеся папки OpenClaw после переноса',
        description='Найти и архивировать оставшиеся папки OpenClaw, чтобы данные не хранились в разных местах',
    )
    claw_cleanup.add_argument(
        "--source", help='Путь к конкретной папке OpenClaw для очистки'
    )
    claw_cleanup.add_argument(
        "--dry-run",
        action="store_true",
        help='Показать план архивирования без изменений',
    )
    claw_cleanup.add_argument(
        "--yes", "-y", action="store_true", help='Пропустить запросы подтверждения'
    )
    claw_parser.set_defaults(func=cmd_claw)

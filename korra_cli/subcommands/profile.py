"""``hermes profile`` subcommand parser.

Extracted verbatim from ``korra_cli/main.py:main()`` (god-file Phase 2).
Handler injected to avoid importing ``main``.
"""

from __future__ import annotations

from typing import Callable


def build_profile_parser(subparsers, *, cmd_profile: Callable) -> None:
    """Attach the ``profile`` subcommand to ``subparsers``."""
    # =========================================================================
    # profile command
    # =========================================================================
    profile_parser = subparsers.add_parser(
        "profile",
        help='Управление профилями: отдельные настройки и данные Корры',
    )
    profile_subparsers = profile_parser.add_subparsers(dest="profile_action")

    profile_subparsers.add_parser("list", help='Показать все профили')
    profile_use = profile_subparsers.add_parser(
        "use", help='Назначить постоянный профиль по умолчанию'
    )
    profile_use.add_argument("profile_name", help='Имя профиля или default')

    profile_create = profile_subparsers.add_parser(
        "create", help='Создать профиль'
    )
    profile_create.add_argument(
        "profile_name", help='Имя профиля: строчные латинские буквы и цифры'
    )
    profile_create.add_argument(
        "--clone",
        action="store_true",
        help='Скопировать config.yaml, .env, SOUL.md и навыки из текущего профиля',
    )
    profile_create.add_argument(
        "--clone-all",
        action="store_true",
        help='Полностью скопировать текущий профиль без его истории',
    )
    profile_create.add_argument(
        "--clone-from",
        metavar="SOURCE",
        help='Исходный профиль для копирования; включает --clone, если не указан --clone-all',
    )
    profile_create.add_argument(
        "--no-alias", action="store_true", help='Не создавать команду-обёртку'
    )
    profile_create.add_argument(
        "--no-skills",
        action="store_true",
        help='Создать пустой профиль без встроенных навыков и их синхронизации при korra update',
    )
    profile_create.add_argument(
        "--description",
        default=None,
        help='Опишите назначение профиля в одном-двух предложениях. Диспетчер доски использует описание для распределения задач по ролям. Можно добавить позже через korra profile describe.',
    )

    profile_delete = profile_subparsers.add_parser("delete", help='Удалить профиль')
    profile_delete.add_argument("profile_name", help='Удаляемый профиль')
    profile_delete.add_argument(
        "-y", "--yes", action="store_true", help='Пропустить запрос подтверждения'
    )

    profile_describe = profile_subparsers.add_parser(
        "describe",
        help='Показать или изменить описание профиля для диспетчера доски',
    )
    profile_describe.add_argument(
        "profile_name",
        nargs="?",
        default=None,
        help='Профиль для описания; для всех используйте --all --auto без имени',
    )
    profile_describe.add_argument(
        "--text",
        default=None,
        help='Записать этот текст как описание, заменив прежнее',
    )
    profile_describe.add_argument(
        "--auto",
        action="store_true",
        help='Создать описание вспомогательной моделью из auxiliary.profile_describer',
    )
    profile_describe.add_argument(
        "--overwrite",
        action="store_true",
        help='С --auto заменять и пользовательские описания; по умолчанию заполняются только пустые или ранее созданные автоматически',
    )
    profile_describe.add_argument(
        "--all",
        dest="all_missing",
        action="store_true",
        help='С --auto обработать все профили без описания',
    )

    profile_show = profile_subparsers.add_parser("show", help='Показать сведения о профиле')
    profile_show.add_argument("profile_name", help='Профиль для просмотра')

    profile_alias = profile_subparsers.add_parser(
        "alias", help='Управление командами-обёртками'
    )
    profile_alias.add_argument("profile_name", help='Имя профиля')
    profile_alias.add_argument(
        "--remove", action="store_true", help='Удалить команду-обёртку'
    )
    profile_alias.add_argument(
        "--name",
        dest="alias_name",
        metavar="NAME",
        help='Своё имя команды; по умолчанию имя профиля',
    )

    profile_rename = profile_subparsers.add_parser(
        "rename",
        help='Переименовать профиль; для default меняется только отображаемое имя, ID сохраняется',
    )
    profile_rename.add_argument("old_name", help='Текущее имя профиля')
    profile_rename.add_argument(
        "new_name",
        help='Новое имя; для default — отображаемое имя, внутренний ID остаётся default',
    )

    profile_export = profile_subparsers.add_parser(
        "export", help='Сохранить профиль в архив'
    )
    profile_export.add_argument("profile_name", help='Профиль для экспорта')
    profile_export.add_argument(
        "-o", "--output", default=None, help='Выходной файл (по умолчанию <name>.tar.gz)'
    )

    profile_import = profile_subparsers.add_parser(
        "import", help='Загрузить профиль из архива'
    )
    profile_import.add_argument("archive", help='Путь к архиву .tar.gz')
    profile_import.add_argument(
        "--name",
        dest="import_name",
        metavar="NAME",
        help='Имя профиля; по умолчанию определяется по архиву',
    )

    # ---------- Distribution subcommands (issue #20456) ----------
    profile_install = profile_subparsers.add_parser(
        "install",
        help='Установить готовый профиль по адресу Git или из локальной папки',
        description=(
            'Установить готовый профиль Корры. SOURCE — адрес Git (github.com/user/repo, https://... или git@...) либо локальная папка с distribution.yaml в корне.'
        ),
    )
    profile_install.add_argument(
        "source",
        help='Источник профиля: адрес Git или локальная папка',
    )
    profile_install.add_argument(
        "--name", dest="install_name", metavar="NAME",
        help='Своё имя профиля; по умолчанию из манифеста',
    )
    profile_install.add_argument(
        "--alias", action="store_true",
        help='Создать команду-обёртку для установленного профиля',
    )
    profile_install.add_argument(
        "--force", action="store_true",
        help='Заменить профиль с таким же именем, сохранив пользовательские данные',
    )
    profile_install.add_argument(
        "-y", "--yes", action="store_true",
        help='Пропустить подтверждение после просмотра манифеста',
    )

    profile_update = profile_subparsers.add_parser(
        "update",
        help='Повторно получить готовый профиль и применить обновления, сохранив ваши данные',
        description=(
            'Получить обновление из сохранённого источника и заменить файлы поставки: SOUL.md, skills/, cron/, mcp.json. Память, беседы, данные входа и .env не затрагиваются. config.yaml сохраняется, кроме случая с --force-config.'
        ),
    )
    profile_update.add_argument("profile_name", help='Обновляемый профиль')
    profile_update.add_argument(
        "--force-config", action="store_true",
        help='Также заменить config.yaml; обычно он сохраняется, чтобы не потерять ваши настройки',
    )
    profile_update.add_argument(
        "-y", "--yes", action="store_true",
        help='Пропустить подтверждение',
    )

    profile_info = profile_subparsers.add_parser(
        "info",
        help='Показать манифест профиля: версию, требования и источник',
    )
    profile_info.add_argument("profile_name", help='Профиль для проверки')

    profile_parser.set_defaults(func=cmd_profile)

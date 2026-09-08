"""``hermes skills`` subcommand parser.

Extracted from ``korra_cli/main.py:main()`` (god-file Phase 2 follow-up).
Handler injected to avoid importing ``main``.
"""

from __future__ import annotations

from typing import Callable


def build_skills_parser(subparsers, *, cmd_skills: Callable) -> None:
    """Attach the ``skills`` subcommand to ``subparsers``."""
    skills_parser = subparsers.add_parser(
        "skills",
        help='Поиск, установка и настройка навыков',
        description='Найти, установить, просмотреть, проверить и настроить навыки из skills.sh, стандартных источников навыков агентов, GitHub, ClawHub и других каталогов',
    )
    skills_subparsers = skills_parser.add_subparsers(dest="skills_action")

    skills_trust = skills_subparsers.add_parser(
        "trust",
        help='Разрешить загрузку навыков проекта из ./.hermes/skills и ./.agents/skills',
    )
    skills_trust.add_argument(
        "path",
        nargs="?",
        default=None,
        help='Корень доверенного проекта; по умолчанию текущий репозиторий Git',
    )

    skills_untrust = skills_subparsers.add_parser(
        "untrust", help='Отозвать доверие к навыкам проекта'
    )
    skills_untrust.add_argument(
        "path",
        nargs="?",
        default=None,
        help='Корень проекта для отзыва доверия; по умолчанию текущий репозиторий Git',
    )

    skills_browse = skills_subparsers.add_parser(
        "browse", help='Просмотреть все доступные навыки по страницам'
    )
    skills_browse.add_argument(
        "--page", type=int, default=1, help='Номер страницы (по умолчанию 1)'
    )
    skills_browse.add_argument(
        "--size", type=int, default=20, help='Результатов на странице (по умолчанию 20)'
    )
    skills_browse.add_argument(
        "--source",
        default="all",
        choices=[
            "all",
            "official",
            "skills-sh",
            "well-known",
            "github",
            "clawhub",
            "lobehub",
            "browse-sh",
            # Provider filters (GitHub taps stored under source="github"):
            "nvidia",
            "openai",
            "anthropic",
            "huggingface",
            "voltagent",
            "gstack",
            "minimax",
        ],
        help='Фильтр по источнику или провайдеру, например nvidia или openai; по умолчанию все',
    )

    skills_search = skills_subparsers.add_parser(
        "search", help='Поиск в каталогах навыков'
    )
    skills_search.add_argument("query", help='Поисковый запрос')
    skills_search.add_argument(
        "--source",
        default="all",
        choices=[
            "all",
            "official",
            "skills-sh",
            "well-known",
            "github",
            "clawhub",
            "lobehub",
            "browse-sh",
            # Provider filters (GitHub taps stored under source="github"):
            "nvidia",
            "openai",
            "anthropic",
            "huggingface",
            "voltagent",
            "gstack",
            "minimax",
        ],
        help='Фильтр по источнику или провайдеру, например nvidia или openai',
    )
    skills_search.add_argument("--limit", type=int, default=25, help='Максимум результатов')
    skills_search.add_argument(
        "--json",
        action="store_true",
        help='Вывести JSON вместо таблицы: полные идентификаторы, удобно для скриптов',
    )

    skills_install = skills_subparsers.add_parser("install", help='Установить навык')
    skills_install.add_argument(
        "identifier",
        help='ID навыка, например openai/skills/skill-creator, или прямой HTTP(S)-адрес SKILL.md',
    )
    skills_install.add_argument(
        "--category", default="", help='Папка категории для установки'
    )
    skills_install.add_argument(
        "--name",
        default="",
        help='Задать имя навыка; удобно для SKILL.md по ссылке без поля name:',
    )
    skills_install.add_argument(
        "--force", action="store_true", help='Установить, несмотря на блокирующий результат проверки'
    )
    skills_install.add_argument(
        "--yes",
        "-y",
        action="store_true",
        help='Пропустить подтверждение; требуется в терминальном интерфейсе TUI',
    )

    skills_inspect = skills_subparsers.add_parser(
        "inspect", help='Просмотреть навык без установки'
    )
    skills_inspect.add_argument("identifier", help='ID навыка')

    skills_list = skills_subparsers.add_parser("list", help='Показать установленные навыки')
    skills_list.add_argument(
        "--source", default="all", choices=["all", "hub", "builtin", "local"]
    )
    skills_list.add_argument(
        "--enabled-only",
        action="store_true",
        help='Скрыть отключённые навыки. С -p <profile> покажет только навыки, которые загрузятся для этого профиля.',
    )

    skills_check = skills_subparsers.add_parser(
        "check", help='Проверить обновления навыков из каталога'
    )
    skills_check.add_argument(
        "name", nargs="?", help='Навык для проверки; по умолчанию все'
    )

    skills_update = skills_subparsers.add_parser(
        "update", help='Обновить навыки из каталога'
    )
    skills_update.add_argument(
        "name",
        nargs="?",
        help='Обновляемый навык; по умолчанию все устаревшие',
    )
    skills_update.add_argument(
        "--force",
        action="store_true",
        help='Заменить и навыки, изменённые вами вручную; по умолчанию они пропускаются',
    )

    skills_audit = skills_subparsers.add_parser(
        "audit", help='Повторно проверить установленные навыки из каталога'
    )
    skills_audit.add_argument(
        "name", nargs="?", help='Навык для проверки; по умолчанию все'
    )
    skills_audit.add_argument(
        "--deep",
        action="store_true",
        help='Дополнительно проанализировать структуру Python-файлов (AST)',
    )

    skills_uninstall = skills_subparsers.add_parser(
        "uninstall", help='Удалить навык, установленный из каталога'
    )
    skills_uninstall.add_argument("name", help='Имя удаляемого навыка')
    skills_uninstall.add_argument(
        "--yes",
        "-y",
        action="store_true",
        help='Пропустить запрос подтверждения',
    )

    skills_reset = skills_subparsers.add_parser(
        "reset",
        help='Снять отметку о ручном изменении встроенного навыка и вернуть автоматические обновления',
        description=(
            'Удалить запись навыка из .bundled_manifest, чтобы korra update больше не считал его изменённым пользователем. --restore также заменяет текущую копию версией из поставки.'
        ),
    )
    skills_reset.add_argument(
        "name", help='Имя навыка для сброса, например google-workspace'
    )
    skills_reset.add_argument(
        "--restore",
        action="store_true",
        help='Также удалить текущую копию и восстановить версию из поставки',
    )
    skills_reset.add_argument(
        "--yes",
        "-y",
        action="store_true",
        help='Пропустить подтверждение при --restore',
    )

    skills_list_modified = skills_subparsers.add_parser(
        "list-modified",
        help='Показать изменённые вами встроенные навыки, которые сохраняет korra update',
        description=(
            'Показать встроенные навыки, отличающиеся от последней синхронизированной версии и пропускаемые при korra update. Изменения: korra skills diff <name>. Возврат обновлений: korra skills reset <name>.'
        ),
    )
    skills_list_modified.add_argument(
        "--json",
        action="store_true",
        help='Вывести список в JSON',
    )

    skills_diff = skills_subparsers.add_parser(
        "diff",
        help='Показать отличия вашей копии встроенного навыка от версии из поставки',
        description=(
            'Показать изменения между вашей копией навыка и текущей версией из поставки в формате unified diff перед korra skills reset'
        ),
    )
    skills_diff.add_argument(
        "name", help='Имя навыка для сравнения, например google-workspace'
    )

    skills_opt_out = skills_subparsers.add_parser(
        "opt-out",
        help='Отключить добавление встроенных навыков в этот профиль',
        description=(
            'Создать метку .no-bundled-skills: установщик, korra update и синхронизация больше не добавят встроенные навыки. Существующие файлы сохраняются. --remove также удалит неизменённые встроенные навыки; ваши правки, локальные навыки и навыки из каталогов сохранятся.'
        ),
    )
    skills_opt_out.add_argument(
        "--remove",
        action="store_true",
        help='Также удалить уже установленные неизменённые встроенные навыки',
    )
    skills_opt_out.add_argument(
        "--yes",
        "-y",
        action="store_true",
        help='Пропустить подтверждение при --remove',
    )

    skills_opt_in = skills_subparsers.add_parser(
        "opt-in",
        help='Снова включить добавление встроенных навыков',
        description=(
            'Удалить метку .no-bundled-skills: встроенные навыки появятся при следующем korra update. --sync добавит их сразу.'
        ),
    )
    skills_opt_in.add_argument(
        "--sync",
        action="store_true",
        help='Добавить встроенные навыки сразу, не дожидаясь обновления',
    )

    skills_repair_official = skills_subparsers.add_parser(
        "repair-official",
        help='Восстановить или учесть официальные дополнительные навыки из репозитория',
        description=(
            'Восстановить сведения об источнике дополнительных навыков. По умолчанию заполняются только метаданные точных совпадений. --restore восстанавливает отсутствующие или изменённые копии из optional-skills/, предварительно сохраняя резервные копии. all — все дополнительные навыки.'
        ),
    )
    skills_repair_official.add_argument(
        "name", help='Имя папки или поле name официального дополнительного навыка; all — все'
    )
    skills_repair_official.add_argument(
        "--restore",
        action="store_true",
        help='Восстановить из официального источника с резервной копией существующих файлов',
    )
    skills_repair_official.add_argument(
        "--yes",
        "-y",
        action="store_true",
        help='Пропустить подтверждение при --restore',
    )

    skills_publish = skills_subparsers.add_parser(
        "publish", help='Опубликовать навык в каталоге'
    )
    skills_publish.add_argument("skill_path", help='Путь к папке навыка')
    skills_publish.add_argument(
        "--to", default="github", choices=["github", "clawhub"], help='Целевой каталог'
    )
    skills_publish.add_argument(
        "--repo", default="", help='Репозиторий GitHub, например openai/skills'
    )

    skills_snapshot = skills_subparsers.add_parser(
        "snapshot", help='Экспорт и импорт настроек навыков'
    )
    snapshot_subparsers = skills_snapshot.add_subparsers(dest="snapshot_action")
    snap_export = snapshot_subparsers.add_parser(
        "export", help='Сохранить список установленных навыков в файл'
    )
    snap_export.add_argument("output", help='Путь к выходному JSON-файлу; - — stdout')
    snap_import = snapshot_subparsers.add_parser(
        "import", help='Загрузить и установить навыки из файла'
    )
    snap_import.add_argument("input", help='Путь к входному JSON-файлу')
    snap_import.add_argument(
        "--force", action="store_true", help='Установить, несмотря на предупреждение проверки'
    )

    skills_tap = skills_subparsers.add_parser("tap", help='Управление источниками навыков')
    tap_subparsers = skills_tap.add_subparsers(dest="tap_action")
    tap_subparsers.add_parser("list", help='Показать подключённые источники')
    tap_add = tap_subparsers.add_parser("add", help='Добавить репозиторий GitHub как источник навыков')
    tap_add.add_argument("repo", help='Репозиторий GitHub, например owner/repo')
    tap_rm = tap_subparsers.add_parser("remove", help='Удалить источник')
    tap_rm.add_argument("name", help='Имя удаляемого источника')

    # config sub-action: interactive enable/disable
    skills_subparsers.add_parser(
        "config",
        help='Настроить навыки в меню: включить или отключить отдельные навыки',
    )

    skills_parser.set_defaults(func=cmd_skills)

"""``hermes plugins`` subcommand parser.

Extracted from ``korra_cli/main.py:main()`` (god-file Phase 2 follow-up).
Handler injected to avoid importing ``main``.
"""

from __future__ import annotations

from typing import Callable


def build_plugins_parser(subparsers, *, cmd_plugins: Callable) -> None:
    """Attach the ``plugins`` subcommand to ``subparsers``."""
    plugins_parser = subparsers.add_parser(
        "plugins",
        help='Управление и проверка плагинов',
        description=(
            'Установить, обновить, удалить, показать или проверить плагины Корры и переносимые пакеты Agent Plugins v1. Переносимые пакеты устанавливаются выключенными.'
        ),
    )
    plugins_subparsers = plugins_parser.add_subparsers(dest="plugins_action")

    plugins_install = plugins_subparsers.add_parser(
        "install", help='Установить плагин по адресу Git, owner/repo или имени в каталоге'
    )
    plugins_install.add_argument(
        "identifier",
        help=(
            'Адрес Git, сокращение owner/repo или имя плагина в каталоге сообщества; поиск: korra plugins search'
        ),
    )
    plugins_install.add_argument(
        "--force",
        "-f",
        action="store_true",
        help='Удалить существующий плагин и установить заново',
    )
    plugins_install.add_argument(
        "--ref",
        metavar="COMMIT_SHA",
        help='Установить конкретный неизменяемый коммит Git по SHA из 40 символов',
    )
    _install_enable_group = plugins_install.add_mutually_exclusive_group()
    _install_enable_group.add_argument(
        "--enable",
        action="store_true",
        help='Автоматически включить плагин после установки без подтверждения',
    )
    _install_enable_group.add_argument(
        "--no-enable",
        action="store_true",
        help='Установить выключенным без подтверждения; включить позже: korra plugins enable <name>',
    )

    plugins_search = plugins_subparsers.add_parser(
        "search", help='Поиск в каталоге плагинов сообщества'
    )
    plugins_search.add_argument(
        "term",
        nargs="?",
        default="",
        help='Запрос для поиска по имени, описанию и меткам; без запроса — весь каталог',
    )
    plugins_search.add_argument(
        "--json",
        action="store_true",
        help='Вывести машиночитаемый JSON',
    )
    plugins_search.add_argument(
        "--capability",
        metavar="CAP",
        help='Фильтр по заявленной возможности, например tools, platform или commands',
    )
    plugins_search.add_argument(
        "--refresh",
        action="store_true",
        help='Заново загрузить каталог без использования локального кеша',
    )

    plugins_update = plugins_subparsers.add_parser(
        "update", help='Получить последние изменения установленного плагина'
    )
    plugins_update.add_argument("name", help='Имя обновляемого плагина')

    plugins_remove = plugins_subparsers.add_parser(
        "remove", aliases=["rm", "uninstall"], help='Удалить установленный плагин'
    )
    plugins_remove.add_argument("name", help='Имя папки удаляемого плагина')

    plugins_list = plugins_subparsers.add_parser(
        "list", aliases=["ls"], help='Показать установленные плагины'
    )
    plugins_list.add_argument(
        "--enabled",
        action="store_true",
        help='Показать только включённые плагины',
    )
    plugins_list.add_argument(
        "--user",
        action="store_true",
        help='Показать только установленные пользователем плагины, включая Git',
    )
    plugins_list.add_argument(
        "--no-bundled",
        action="store_true",
        help='Скрыть встроенные плагины',
    )
    plugins_list.add_argument(
        "--plain",
        action="store_true",
        help='Краткий текстовый вывод вместо таблицы Rich',
    )
    plugins_list.add_argument(
        "--json",
        action="store_true",
        help='Вывести машиночитаемый JSON',
    )

    plugins_enable = plugins_subparsers.add_parser(
        "enable", help='Включить отключённый плагин'
    )
    plugins_enable.add_argument("name", help='Имя включаемого плагина')
    _enable_override_group = plugins_enable.add_mutually_exclusive_group()
    _enable_override_group.add_argument(
        "--allow-tool-override",
        action="store_true",
        help='Разрешить плагину заменять встроенные инструменты, например shell_exec и write_file, без запроса подтверждения',
    )
    _enable_override_group.add_argument(
        "--no-allow-tool-override",
        action="store_true",
        help='Включить без права заменять встроенные инструменты и без запроса подтверждения',
    )

    plugins_disable = plugins_subparsers.add_parser(
        "disable", help='Отключить плагин, не удаляя его'
    )
    plugins_disable.add_argument("name", help='Имя отключаемого плагина')

    plugins_capabilities = plugins_subparsers.add_parser(
        "capabilities",
        help='Показать запрошенные и предоставленные возможности плагинов',
        description=(
            'Сравнить возможности из plugin.yaml с разрешениями пользователя. Эти разрешения нужны для согласия и проверки; они не изолируют код плагина.'
        ),
    )
    plugins_capabilities.add_argument(
        "name",
        nargs="?",
        default=None,
        help='ID проверяемого плагина; без параметра — все плагины с заявленными возможностями',
    )

    plugins_doctor = plugins_subparsers.add_parser(
        "doctor", help='Проверить плагин по действующим требованиям среды выполнения'
    )
    plugins_doctor.add_argument(
        "target",
        nargs="?",
        default=".",
        help='Путь к плагину или ID установленного плагина; по умолчанию текущая папка',
    )
    plugins_doctor.add_argument(
        "--ci",
        action="store_true",
        help='Завершиться с ненулевым кодом, если проверка нашла ошибку',
    )

    plugins_pack = plugins_subparsers.add_parser(
        "pack",
        help='Переносимые наборы плагинов в файле hermes-pack.yaml',
        description=(
            'Установить, сохранить или посмотреть набор плагинов: YAML-файл с точными SHA коммитов и необязательными настройками без секретов. Каждый плагин устанавливается отдельно; разрешения также выдаются отдельно.'
        ),
    )
    pack_subparsers = plugins_pack.add_subparsers(dest="pack_action")

    pack_install = pack_subparsers.add_parser(
        "install", help='Просмотреть и установить набор из файла или по HTTPS-адресу'
    )
    pack_install.add_argument(
        "source", help='Путь к hermes-pack.yaml или адрес https://'
    )
    pack_install.add_argument(
        "--force",
        "-f",
        action="store_true",
        help='Переустановить уже существующие плагины',
    )

    pack_export = pack_subparsers.add_parser(
        "export",
        help='Вывести YAML набора для текущей установки в stdout',
    )
    pack_export.add_argument(
        "--enabled-only",
        action="store_true",
        help='Включить только плагины из plugins.enabled',
    )
    pack_export.add_argument(
        "--name",
        default="my-hermes-pack",
        help='Имя набора в сохраняемом YAML',
    )

    pack_show = pack_subparsers.add_parser(
        "show", help='Прочитать и показать набор без установки'
    )
    pack_show.add_argument(
        "source", help='Путь к hermes-pack.yaml или адрес https://'
    )

    plugins_show = plugins_subparsers.add_parser(
        "show",
        aliases=["info"],
        help='Показать подробности плагина, включая создаваемые и обрабатываемые события',
    )
    plugins_show.add_argument("name", help='Имя или ключ плагина')

    plugins_parser.set_defaults(func=cmd_plugins)

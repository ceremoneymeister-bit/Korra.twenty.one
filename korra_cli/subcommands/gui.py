"""``hermes gui`` subcommand parser.

Extracted verbatim from ``korra_cli/main.py:main()`` (god-file Phase 2).
Handler injected to avoid importing ``main``.
"""

from __future__ import annotations

from typing import Callable


def build_gui_parser(subparsers, *, cmd_gui: Callable) -> None:
    """Attach the ``gui`` subcommand to ``subparsers``."""
    # =========================================================================
    gui_parser = subparsers.add_parser(
        "desktop",
        aliases=["gui"],
        help='Собрать и запустить приложение для компьютера',
        description=(
            'Запустить приложение Korra на Electron. По умолчанию устанавливаются зависимости Node, собирается приложение для текущей системы и запускается готовая сборка.'
        ),
    )
    gui_parser.add_argument(
        "--source",
        action="store_true",
        help='Запустить electron . с apps/desktop/dist вместо готового пакета',
    )
    gui_parser.add_argument(
        "--build-only",
        action="store_true",
        help='Собрать приложение без запуска; используется установщиком при --update',
    )
    gui_parser.add_argument(
        "--fake-boot",
        action="store_true",
        help='Включить фиксированные задержки загрузки для проверки стартового интерфейса',
    )
    gui_parser.add_argument(
        "--ignore-existing",
        action="store_true",
        help='При поиске сервера не использовать установленную команду из PATH',
    )
    gui_parser.add_argument(
        "--hermes-root",
        help='Задать папку исходников для приложения через HERMES_DESKTOP_HERMES_ROOT',
    )
    gui_parser.add_argument(
        "--cwd",
        help='Начальная папка проекта для бесед приложения; задаёт HERMES_DESKTOP_CWD',
    )
    gui_parser.add_argument(
        "--skip-build",
        action="store_true",
        help='Пропустить npm install и сборку, запустить готовое приложение из apps/desktop/release',
    )
    gui_parser.add_argument(
        "--force-build",
        action="store_true",
        help='Пересобрать полностью, даже если содержимое не изменилось',
    )
    gui_parser.add_argument(
        "--setup-tcc-identity",
        action="store_true",
        help=(
            'Только macOS: создать или импортировать самоподписанный сертификат в связку ключей, задать desktop.macos_signing_identity и переподписать приложение. Сохраняет разрешения на диск, управление, файлы и микрофон после пересборок. Повторный запуск безопасен.'
        ),
    )
    gui_parser.add_argument(
        "--identity",
        default="Hermes Local Signing",
        help='Имя сертификата для --setup-tcc-identity; по умолчанию штатное локальное имя подписи',
    )
    gui_parser.set_defaults(func=cmd_gui)

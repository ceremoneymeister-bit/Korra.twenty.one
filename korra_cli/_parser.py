"""
Top-level argparse construction for the hermes CLI.

Lives in its own module so other modules (e.g. ``relaunch.py``) can
introspect the parser to discover which flags exist without running the
``main`` fn.

Only the top-level parser and the ``chat`` subparser live here. Every other
subparser (model, gateway, sessions, …) is built inline in ``main.py``
because its dispatch is tightly coupled to module-level ``cmd_*`` functions.
"""

import argparse
import re
from functools import lru_cache


# argparse already routes its generated prose through gettext callbacks.
# Translate exact templates before interpolation: option names, choices,
# supplied values and error semantics are never rewritten.
_ARGPARSE_RU = {
    "usage: ": "Использование: ",
    "positional arguments": "Аргументы",
    "options": "Параметры",
    "show this help message and exit": "Показать эту справку и выйти",
    "show program's version number and exit": "Показать версию и выйти",
    " (default: %(default)s)": " (по умолчанию: %(default)s)",
    "%(prog)s: error: %(message)s\n": "%(prog)s: ошибка: %(message)s\n",
    "argument %(argument_name)s: %(message)s": "параметр %(argument_name)s: %(message)s",
    "unrecognized arguments: %s": "неизвестные аргументы: %s",
    "the following arguments are required: %s": "обязательные аргументы: %s",
    "one of the arguments %s is required": "нужен один из аргументов: %s",
    "not allowed with argument %s": "нельзя использовать вместе с %s",
    "expected one argument": "нужно одно значение",
    "expected at least one argument": "нужно хотя бы одно значение",
    "expected at most one argument": "допускается не более одного значения",
    "expected %s argument": "нужно значений: %s",
    "expected %s arguments": "нужно значений: %s",
    "invalid %(type)s value: %(value)r": "неверное значение типа %(type)s: %(value)r",
    "invalid choice: %(value)r (choose from %(choices)s)": "неверный выбор: %(value)r (доступны: %(choices)s)",
    "ignored explicit argument %r": "лишнее явно заданное значение: %r",
    "ambiguous option: %(option)s could match %(matches)s": "неоднозначный параметр %(option)s; возможны: %(matches)s",
    "can't open '%(filename)s': %(error)s": "не удалось открыть «%(filename)s»: %(error)s",
    "unknown parser %(parser_name)r (choices: %(choices)s)": "неизвестная команда %(parser_name)r (доступны: %(choices)s)",
    "conflicting option string: %s": "конфликт параметров: %s",
    "conflicting option strings: %s": "конфликт параметров: %s",
    "conflicting subparser: %s": "конфликт подкоманд: %s",
    "conflicting subparser alias: %s": "конфликт псевдонимов подкоманд: %s",
    "cannot have multiple subparser arguments": "нельзя задать несколько аргументов подкоманд",
    "cannot merge actions - two groups are named %r": "нельзя объединить действия: две группы называются %r",
    "dest= is required for options like %r": "для параметров вида %r требуется dest=",
    "'required' is an invalid argument for positionals": "required недопустим для позиционных аргументов",
    "invalid conflict_resolution value: %r": "неверное значение conflict_resolution: %r",
    "invalid option string %(option)r: must start with a character %(prefix_chars)r": "неверный параметр %(option)r: нужен начальный символ из %(prefix_chars)r",
    "mutually exclusive arguments must be optional": "взаимоисключающие аргументы должны быть необязательными",
    "unexpected option string: %s": "неожиданный параметр: %s",
    "argument \"-\" with mode %r": "аргумент «-» с режимом %r",
    "%r is not callable": "%r не является вызываемым объектом",
    ".__call__() not defined": ".__call__() не определён",
}


def _argparse_gettext(message):
    return _ARGPARSE_RU.get(message, message)


def _argparse_ngettext(singular, plural, count):
    message = singular if count == 1 else plural
    return _argparse_gettext(message)


argparse._ = _argparse_gettext
argparse.ngettext = _argparse_ngettext


class _RussianUsageMixin:
    """Localize argparse's generated usage label without changing arguments."""

    def _format_usage(self, usage, actions, groups, prefix):
        return super()._format_usage(
            usage, actions, groups, "Использование: " if prefix is None else prefix
        )


class _RussianHelpFormatter(_RussianUsageMixin, argparse.HelpFormatter):
    pass


class _RussianRawDescriptionHelpFormatter(
    _RussianUsageMixin, argparse.RawDescriptionHelpFormatter
):
    pass


class KorraArgumentParser(argparse.ArgumentParser):
    """Russian presentation for this parser and its inherited subparsers."""

    def __init__(self, *args, **kwargs):
        formatters = {
            argparse.HelpFormatter: _RussianHelpFormatter,
            argparse.RawDescriptionHelpFormatter: _RussianRawDescriptionHelpFormatter,
        }
        formatter = kwargs.get("formatter_class", argparse.HelpFormatter)
        kwargs["formatter_class"] = formatters.get(formatter, formatter)
        super().__init__(*args, **kwargs)
        self._positionals.title = "Аргументы"
        self._optionals.title = "Параметры"
        for action in self._actions:
            if isinstance(action, argparse._HelpAction):
                action.help = "Показать эту справку и выйти"


# `--profile` / `-p` is consumed by ``main._apply_profile_override`` before
# argparse runs (it sets ``HERMES_HOME`` and strips itself from ``sys.argv``),
# so it isn't on the parser. Listed here so all "carry over on relaunch"
# metadata lives in one file.
PRE_ARGPARSE_INHERITED_FLAGS: list[tuple[str, bool]] = [
    ("--profile", True),
    ("-p", True),
]


# Static snapshot fallback for ``top_level_value_flag_sets`` — used only if
# introspecting the live parser fails (e.g. argparse surface broken mid-edit).
# The derived path is authoritative; a parity test in
# tests/korra_cli/test_top_level_value_flags_parity.py fails CI if the parser
# grows a value-taking flag this snapshot lacks AND derivation regresses.
_VALUE_FLAGS_FALLBACK: frozenset[str] = frozenset(
    {
        "-z", "--oneshot",
        "-m", "--model",
        "--provider", "--reasoning",
        "-t", "--toolsets",
        "-r", "--resume",
        "-s", "--skills",
        "--usage-file",
        "--in",
    }
)
_OPTIONAL_VALUE_FLAGS_FALLBACK: frozenset[str] = frozenset({"-c", "--continue"})


@lru_cache(maxsize=1)
def top_level_value_flag_sets() -> tuple[frozenset[str], frozenset[str]]:
    """(required-value, optional-value) top-level flags, derived from the
    REAL parser.

    Introspects ``build_top_level_parser()`` (every option with nargs != 0)
    so the argv scanners in ``main.py`` (``_first_positional_argv``,
    ``_apply_profile_override``) can never drift from the argparse surface —
    the exact drift that made ``hermes --reasoning high chat …`` misread
    ``high`` as the subcommand and forced eager plugin discovery (#93530).
    Mirrors the ``update_cmd._holder_value_flags`` precedent, including the
    handwritten-snapshot fallback for a broken parser import. Cached per
    process.
    """
    try:
        parser = build_top_level_parser()[0]
        required: set[str] = set()
        optional: set[str] = set()
        for action in parser._actions:
            if not action.option_strings or action.nargs == 0:
                continue
            target = optional if action.nargs == "?" else required
            target.update(action.option_strings)
        return frozenset(required), frozenset(optional)
    except Exception:
        return _VALUE_FLAGS_FALLBACK, _OPTIONAL_VALUE_FLAGS_FALLBACK


def _inherited_flag(parser, *args, **kwargs):
    """Register a flag that ``korra_cli.relaunch`` should carry over when
    the CLI re-execs itself (e.g. after ``sessions browse`` picks a session,
    or after the setup wizard launches chat).

    Equivalent to ``parser.add_argument(...)`` plus tagging the resulting
    Action with ``inherit_on_relaunch = True`` so the relaunch table builder
    can find it via introspection.
    """
    action = parser.add_argument(*args, **kwargs)
    action.inherit_on_relaunch = True
    return action


_EPILOGUE = """
Примеры:
    korra                        Начать беседу
    korra chat -q "Здравствуйте" Ответить на один запрос
    korra --tui                  Открыть современный терминальный интерфейс
    korra --cli                  Открыть классический терминальный интерфейс
    korra -c                     Продолжить последнюю беседу
    korra -c "мой проект"        Продолжить беседу по названию
    korra --resume <session_id>  Продолжить беседу по ID
    korra --resume latest        Продолжить последнюю беседу, как -c
    korra --tui --resume latest --in ./dir   Открыть последнюю беседу проекта ./dir
    korra setup                  Открыть мастер настройки
    korra logout                 Выйти из учётной записи
    korra auth add <provider>    Добавить ключ или учётную запись провайдера
    korra auth list              Показать сохранённые ключи и учётные записи
    korra auth remove <p> <t>    Удалить ключ по номеру, ID или метке
    korra auth reset <provider>  Снять отметку об исчерпании лимита провайдера
    korra model                  Выбрать основную модель
    korra fallback [list]        Показать резервных провайдеров
    korra fallback add           Добавить резервного провайдера
    korra fallback remove        Удалить резервного провайдера
    korra config                 Показать настройки
    korra config edit            Открыть настройки в $EDITOR
    korra config set model gpt-4 Изменить значение настройки
    korra gateway                Запустить шлюз мессенджеров
    korra -s korra-agent,github   Загрузить выбранные навыки
    korra -w                     Запустить в отдельной рабочей копии Git
    korra gateway install        Установить фоновую службу шлюза
    korra sessions list          Показать прошлые беседы
    korra sessions browse        Найти и выбрать беседу
    korra sessions rename ID T   Переименовать беседу
    korra logs                   Показать последние 50 строк agent.log
    korra logs -f                Читать agent.log в реальном времени
    korra logs errors            Показать errors.log
    korra logs --since 1h        Показать записи за последний час
    korra debug share            Загрузить отчёт для поддержки
    korra console                Открыть безопасную консоль команд Корры
    korra update                 Обновить до последней версии
    korra dashboard              Открыть веб-панель на порту 9119
    korra dashboard --stop       Остановить запущенные веб-панели
    korra dashboard --status     Показать запущенные веб-панели

Справка по отдельной команде:
    korra <command> --help
"""


#: Каноническое имя команды форка. Апстримовое `hermes` осталось второй точкой
#: входа (см. ``[project.scripts]`` в pyproject.toml), но справка, примеры и
#: строка usage называют Korra.
CANONICAL_PROG = "korra"

#: Имена, под которыми запуск считается «легаси-алиасом»: справка тогда
#: показывает то имя, которым команду реально вызвали, иначе пользователь
#: копирует из примеров команду, которой у него в PATH может не быть.
_LEGACY_PROG_NAMES = frozenset({"hermes", "hermes.exe"})


def resolve_prog_name(argv0: str | None = None) -> str:
    """Имя команды для usage/справки: `korra`, либо легаси-алиас как вызвали.

    Берём basename ``sys.argv[0]``. Всё, что не входит в
    :data:`_LEGACY_PROG_NAMES` (питоновский `-m`, pytest, обёртки), схлопывается
    в :data:`CANONICAL_PROG` — иначе в usage полезли бы пути интерпретатора.
    """
    import os
    import sys

    raw = argv0 if argv0 is not None else (sys.argv[0] if sys.argv else "")
    base = os.path.basename(raw or "").strip().lower()
    return base if base in _LEGACY_PROG_NAMES else CANONICAL_PROG


def build_top_level_parser():
    """Build the top-level parser, the subparsers action, and the ``chat`` subparser.

    Returns ``(parser, subparsers, chat_parser)``. The caller wires
    ``chat_parser.set_defaults(func=cmd_chat)`` and continues registering
    other subparsers via ``subparsers.add_parser(...)``.
    """
    prog = resolve_prog_name()
    # Примеры написаны под `korra`; при запуске под легаси-именем подменяем
    # токен целиком, чтобы пользователь не копировал несуществующую команду.
    # Замена сдвигает каждую строку блока одинаково, поэтому колонка описаний
    # остаётся выровненной. Lookahead обязателен: без него под `hermes` из
    # примера `-s korra-agent` вышел бы несуществующий скилл `hermes-agent`.
    epilogue = (
        _EPILOGUE if prog == CANONICAL_PROG
        else re.sub(rf"\b{CANONICAL_PROG}(?![-\w])", prog, _EPILOGUE)
    )
    parser = KorraArgumentParser(
        prog=prog,
        description='Korra — ваш ИИ-помощник с доступом к инструментам',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=epilogue,
    )

    parser.add_argument(
        "--version", "-V", action="store_true", help='Показать версию и выйти'
    )
    parser.add_argument(
        "-z",
        "--oneshot",
        metavar="PROMPT",
        default=None,
        help=(
            'Разовый запрос: вывести в stdout только итоговый ответ. Без заставки, анимации, промежуточного вывода инструментов и строки session_id. Инструменты, память, правила и AGENTS.md из текущей папки загружаются как обычно; подтверждения действий пропускаются. Для скриптов и конвейеров.'
        ),
    )
    parser.add_argument(
        "--usage-file",
        metavar="PATH",
        default=None,
        help=(
            'Только для -z/--oneshot: после выполнения записать в PATH отчёт JSON о примерной стоимости, токенах, модели и числе вызовов API (api_calls). Отчёт сохраняется и при ошибке.'
        ),
    )
    # --model / --provider are accepted at the top level so they can pair
    # with -z without needing the `chat` subcommand.  If neither -z nor a
    # subcommand consumes them, they fall through harmlessly as None.
    # Mirrors `hermes chat --model ... --provider ...` semantics.
    _inherited_flag(
        parser,
        "-m",
        "--model",
        default=None,
        help=(
            'Модель для этого запуска, например anthropic/claude-sonnet-4.6. Работает с -z/--oneshot и --tui; также задаётся через HERMES_INFERENCE_MODEL.'
        ),
    )
    _inherited_flag(
        parser,
        "--provider",
        default=None,
        help=(
            'Провайдер для этого запуска, например openrouter или anthropic. Работает с -z/--oneshot и --tui. Постоянная настройка: model.provider в config.yaml или команда `korra setup`.'
        ),
    )
    _inherited_flag(
        parser,
        "--reasoning",
        default=None,
        metavar="LEVEL",
        help=(
            'Глубина рассуждений для этого запуска: none, minimal, low, medium, high, xhigh, max или ultra. Заменяет agent.reasoning_effort из config.yaml только на время запуска; отдельные настройки моделей — agent.reasoning_overrides.'
        ),
    )
    parser.add_argument(
        "-t",
        "--toolsets",
        default=None,
        help='Включить наборы инструментов через запятую. Работает с -z/--oneshot и --tui.',
    )
    parser.add_argument(
        "--resume",
        "-r",
        metavar="SESSION",
        default=None,
        help=(
            'Продолжить беседу по ID или названию; latest — последняя беседа в текущем проекте, как -c без имени'
        ),
    )
    parser.add_argument(
        "--no-restore-cwd",
        action="store_true",
        default=False,
        help='Не переходить в рабочую папку, сохранённую в возобновляемой беседе.',
    )
    parser.add_argument(
        "--in",
        dest="in_dir",
        metavar="DIR",
        default=None,
        help=(
            'Перейти в DIR перед запуском. С --resume latest или -c выбрать последнюю беседу этого проекта и остаться в DIR, не восстанавливая прежнюю рабочую папку.'
        ),
    )
    parser.add_argument(
        "--continue",
        "-c",
        dest="continue_last",
        nargs="?",
        const=True,
        default=None,
        metavar="SESSION_NAME",
        help='Продолжить беседу по названию; без названия — последнюю',
    )
    parser.add_argument(
        "--worktree",
        "-w",
        action="store_true",
        default=False,
        help='Запустить в отдельной рабочей копии Git для параллельной работы агентов',
    )
    _inherited_flag(
        parser,
        "--accept-hooks",
        action="store_true",
        default=False,
        help=(
            'Автоматически одобрять ещё не проверенные обработчики shell из config.yaml. Аналог hooks_auto_accept: true или HERMES_ACCEPT_HOOKS=1. Для CI и запусков без терминала.'
        ),
    )
    _inherited_flag(
        parser,
        "--skills",
        "-s",
        action="append",
        default=None,
        help='Заранее загрузить навыки: перечислите через запятую или повторите параметр',
    )
    _inherited_flag(
        parser,
        "--yolo",
        action="store_true",
        default=False,
        help='Пропускать все подтверждения опасных команд (на ваш риск)',
    )
    _inherited_flag(
        parser,
        "--pass-session-id",
        action="store_true",
        default=False,
        help='Включить ID беседы в системную инструкцию агента',
    )
    _inherited_flag(
        parser,
        "--ignore-user-config",
        action="store_true",
        default=False,
        help='Пропустить $HERMES_HOME/config.yaml и использовать исходные настройки; ключи из .env по-прежнему загружаются',
    )
    _inherited_flag(
        parser,
        "--ignore-rules",
        action="store_true",
        default=False,
        help='Не загружать AGENTS.md, SOUL.md, .cursorrules, память и заранее выбранные навыки',
    )
    _inherited_flag(
        parser,
        "--safe-mode",
        action="store_true",
        default=False,
        help='Режим диагностики: отключить настройки пользователя, AGENTS.md, память, плагины и серверы MCP. Включает --ignore-user-config и --ignore-rules.',
    )
    _inherited_flag(
        parser,
        "--tui",
        action="store_true",
        default=False,
        help='Открыть современный терминальный интерфейс',
    )
    _inherited_flag(
        parser,
        "--cli",
        action="store_true",
        default=False,
        help='Открыть классический терминальный интерфейс prompt_toolkit, независимо от display.interface=tui',
    )
    _inherited_flag(
        parser,
        "--dev",
        dest="tui_dev",
        action="store_true",
        default=False,
        help='С --tui: запускать исходники TypeScript через tsx без сборки dist',
    )

    subparsers = parser.add_subparsers(dest="command", help='Команда для выполнения')

    # =========================================================================
    # chat command
    # =========================================================================
    chat_parser = subparsers.add_parser(
        "chat",
        help='Беседа с агентом',
        description='Начать беседу с Коррой',
    )
    _query_group = chat_parser.add_mutually_exclusive_group()
    _query_group.add_argument(
        "-q", "--query",
        help=(
            'Отправить запрос. В обычном терминале он становится первым сообщением беседы. С --oneshot, -Q или без интерактивного терминала — получить ответ и выйти.'
        ),
    )
    _query_group.add_argument(
        "--query-file",
        metavar="PATH",
        help=(
            "Прочитать запрос из файла; '-' — из stdin. Текст не обрабатывается оболочкой: кавычки, $(...) и обратные кавычки сохраняются. Нельзя использовать вместе с -q."
        ),
    )
    chat_parser.add_argument(
        "--oneshot",
        dest="oneshot_exit",
        action="store_true",
        # Distinct dest: the top-level `-z/--oneshot PROMPT` is value-taking
        # and its dispatch sites do `if args.oneshot: _run_and_exit_oneshot(
        # args.oneshot)` — a shared boolean dest would be passed as the
        # prompt. `oneshot_exit` keeps the surfaces independent.
        default=False,
        help=(
            'С -q/--query-file: ответить и выйти. Автоматически включается без интерактивного терминала и с -Q/--quiet.'
        ),
    )
    chat_parser.add_argument(
        "--image", help='Путь к изображению для вложения в разовый запрос'
    )
    # `default=argparse.SUPPRESS` on flags that are ALSO declared on the
    # top-level parser: when the user writes `hermes -m foo chat`, argparse
    # first sets `args.model = "foo"` from the top-level parser, then
    # dispatches to the chat subparser. Without SUPPRESS the chat subparser's
    # own default (`None`) would silently clobber the top-level value because
    # the subparser shares the same namespace and `dest`. SUPPRESS keeps the
    # subparser action a no-op unless the user actually passes the flag after
    # the subcommand. Matches the pattern already used for `-s/--skills` and
    # the relaunch-inherited flags `-r/--resume`, `-c/--continue`,
    # `-w/--worktree`, `--yolo`, etc. (see tests/korra_cli/
    # test_argparse_flag_propagation.py).
    _inherited_flag(
        chat_parser,
        "-m", "--model",
        default=argparse.SUPPRESS,
        help='Модель, например anthropic/claude-sonnet-4',
    )
    chat_parser.add_argument(
        "-t", "--toolsets",
        default=argparse.SUPPRESS,
        help='Наборы инструментов для включения, через запятую',
    )
    _inherited_flag(
        chat_parser,
        "--reasoning",
        default=argparse.SUPPRESS,
        metavar="LEVEL",
        help=(
            'Глубина рассуждений для этой беседы: none, minimal, low, medium, high, xhigh, max или ultra. Временно заменяет agent.reasoning_effort; значения те же, что у /reasoning.'
        ),
    )
    _inherited_flag(
        chat_parser,
        "-s",
        "--skills",
        action="append",
        default=argparse.SUPPRESS,
        help='Заранее загрузить навыки: перечислите через запятую или повторите параметр',
    )
    _inherited_flag(
        chat_parser,
        "--provider",
        # No `choices=` here: user-defined providers from config.yaml `providers:`
        # are also valid values, and runtime resolution (resolve_runtime_provider)
        # handles validation/error reporting consistently with the top-level
        # `--provider` flag.
        default=argparse.SUPPRESS,
        help='Провайдер модели (по умолчанию auto): встроенный или добавленный в раздел providers: файла config.yaml.',
    )
    chat_parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        default=argparse.SUPPRESS,
        help='Подробный вывод',
    )
    chat_parser.add_argument(
        "-Q",
        "--quiet",
        action="store_true",
        help='Режим для скриптов: только итоговый ответ и сведения о беседе, без заставки, анимации и промежуточного вывода инструментов.',
    )
    chat_parser.add_argument(
        "--resume",
        "-r",
        metavar="SESSION_ID",
        default=argparse.SUPPRESS,
        help=(
            'Продолжить беседу по ID, показанному при выходе; latest — последняя беседа'
        ),
    )
    chat_parser.add_argument(
        "--no-restore-cwd",
        action="store_true",
        default=argparse.SUPPRESS,
        help='Не переходить в рабочую папку, сохранённую в возобновляемой беседе.',
    )
    chat_parser.add_argument(
        "--in",
        dest="in_dir",
        metavar="DIR",
        default=argparse.SUPPRESS,
        help=(
            'Перед запуском перейти в DIR; --resume latest и -c будут искать беседы проекта из этой папки.'
        ),
    )
    chat_parser.add_argument(
        "--continue",
        "-c",
        dest="continue_last",
        nargs="?",
        const=True,
        default=argparse.SUPPRESS,
        metavar="SESSION_NAME",
        help='Продолжить беседу по названию; без названия — последнюю',
    )
    chat_parser.add_argument(
        "--create-if-missing",
        action="store_true",
        default=argparse.SUPPRESS,
        help=(
            'С -c/--continue <name>: если беседы с таким названием нет, создать её и продолжить. Удобно для скриптов, которые отправляют сообщения в именованную беседу.'
        ),
    )
    chat_parser.add_argument(
        "--worktree",
        "-w",
        action="store_true",
        default=argparse.SUPPRESS,
        help='Запустить в отдельной рабочей копии Git для параллельной работы агентов в одном репозитории',
    )
    _inherited_flag(
        chat_parser,
        "--accept-hooks",
        action="store_true",
        default=argparse.SUPPRESS,
        help=(
            'Автоматически одобрять ещё не проверенные обработчики shell из config.yaml без запроса в терминале. См. hooks_auto_accept в config.yaml и HERMES_ACCEPT_HOOKS.'
        ),
    )
    chat_parser.add_argument(
        "--checkpoints",
        action="store_true",
        default=False,
        help='Создавать точки восстановления перед удалением и изменением файлов; восстановление через /rollback',
    )
    chat_parser.add_argument(
        "--max-turns",
        type=int,
        default=None,
        metavar="N",
        help='Максимум обращений к инструментам за ход беседы (по умолчанию 500 или agent.max_turns из настроек)',
    )
    chat_parser.add_argument(
        "--run-budget",
        type=float,
        default=None,
        metavar="SECONDS",
        dest="run_budget",
        help=(
            'Ограничить время каждого запуска в секундах. На 80%% времени агент получает указание завершать работу, а ожидание провайдера ограничивается остатком времени. По умолчанию выключено. Также задаётся через agent.run_budget_seconds в config.yaml. Для разовых запусков и проверок с жёстким сроком.'
        ),
    )
    _inherited_flag(
        chat_parser,
        "--yolo",
        action="store_true",
        default=argparse.SUPPRESS,
        help='Пропускать все подтверждения опасных команд (на ваш риск)',
    )
    _inherited_flag(
        chat_parser,
        "--pass-session-id",
        action="store_true",
        default=argparse.SUPPRESS,
        help='Включить ID беседы в системную инструкцию агента',
    )
    _inherited_flag(
        chat_parser,
        "--ignore-user-config",
        action="store_true",
        default=argparse.SUPPRESS,
        help='Пропустить $HERMES_HOME/config.yaml и использовать исходные настройки; ключи из .env по-прежнему загружаются. Для изолированных проверок, CI и интеграций.',
    )
    _inherited_flag(
        chat_parser,
        "--ignore-rules",
        action="store_true",
        default=argparse.SUPPRESS,
        help='Не загружать AGENTS.md, SOUL.md, .cursorrules, память и заранее выбранные навыки. Для полной изоляции добавьте --ignore-user-config.',
    )
    _inherited_flag(
        chat_parser,
        "--safe-mode",
        action="store_true",
        default=argparse.SUPPRESS,
        help='Режим диагностики: отключить настройки пользователя, AGENTS.md, память, плагины и MCP. Включает --ignore-user-config и --ignore-rules. Помогает отличить ошибку настройки от ошибки Корры.',
    )
    chat_parser.add_argument(
        "--source",
        default=None,
        help='Метка источника беседы для фильтрации (по умолчанию cli). Для интеграций, скрытых из списка бесед пользователя, укажите tool.',
    )
    _inherited_flag(
        chat_parser,
        "--tui",
        action="store_true",
        default=argparse.SUPPRESS,
        help='Открыть современный терминальный интерфейс',
    )
    _inherited_flag(
        chat_parser,
        "--cli",
        action="store_true",
        default=argparse.SUPPRESS,
        help='Открыть классический терминальный интерфейс prompt_toolkit, независимо от display.interface=tui',
    )
    _inherited_flag(
        chat_parser,
        "--dev",
        dest="tui_dev",
        action="store_true",
        default=argparse.SUPPRESS,
        help='С --tui: запускать исходники TypeScript через tsx без сборки dist',
    )

    return parser, subparsers, chat_parser

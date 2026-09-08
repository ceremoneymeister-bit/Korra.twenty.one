"""``hermes cron`` subcommand parser.

Extracted verbatim from ``korra_cli/main.py:main()`` — same arguments, same
``func=cmd_cron`` dispatch. The handler is injected so this module does not
import ``main`` (cycle avoidance).
"""

from __future__ import annotations

from typing import Callable

from korra_cli.subcommands._shared import add_accept_hooks_flag


def build_cron_parser(subparsers, *, cmd_cron: Callable) -> None:
    """Attach the ``cron`` subcommand (and its sub-actions) to ``subparsers``."""
    cron_parser = subparsers.add_parser(
        "cron", help='Управление расписанием', description='Управление задачами по расписанию'
    )
    cron_subparsers = cron_parser.add_subparsers(dest="cron_command")

    # cron list
    cron_list = cron_subparsers.add_parser("list", help='Показать задачи по расписанию')
    cron_list.add_argument("--all", action="store_true", help='Включить отключённые задачи')

    # cron create/add
    cron_create = cron_subparsers.add_parser(
        "create", aliases=["add"], help='Создать задачу по расписанию'
    )
    cron_create.add_argument(
        "schedule", help='Расписание, например 30m, every 2h или 0 9 * * *'
    )
    cron_create.add_argument(
        "prompt", nargs="?", help='Необязательное полное описание задачи или запрос'
    )
    cron_create.add_argument("--name", help='Необязательное понятное название задачи')
    cron_create.add_argument(
        "--deliver",
        help=(
            'Куда отправлять результат: origin, local, telegram, discord, signal, platform:chat_id или bot-chat[:profile]. Последний вариант отправляет результат в основной чат локального профиля как сообщение для ответа бота.'
        ),
    )
    cron_create.add_argument("--repeat", type=int, help='Необязательное число повторов')
    cron_create.add_argument(
        "--skill",
        dest="skills",
        action="append",
        help='Прикрепить навык; повторите параметр для нескольких навыков',
    )
    cron_create.add_argument(
        "--script",
        help=(
            'Путь к скрипту в папке scripts/ профиля. Обычно stdout добавляется в запрос агента; с --no-agent скрипт сам выполняет задачу, а stdout отправляется напрямую. .sh/.bash запускаются через bash, остальные — через Python.'
        ),
    )
    cron_create.add_argument(
        "--no-agent",
        dest="no_agent",
        action="store_true",
        default=False,
        help=(
            'Выполнять --script по расписанию без модели и отправлять stdout напрямую. Пустой вывод — без уведомления. Для мониторинга памяти, диска и CI.'
        ),
    )
    cron_create.add_argument(
        "--monitor-script",
        dest="monitor_script",
        help=(
            'Мониторинг: перед каждым запуском выполнять скрипт из scripts/ профиля. Если вывод не изменился побайтно, агент не запускается. При изменении в запрос добавляется разница с меткой MONITOR CHANGE DETECTED. Вывод должен быть стабильным, без временных меток. Несовместимо с --monitor-url и --no-agent.'
        ),
    )
    cron_create.add_argument(
        "--monitor-url",
        dest="monitor_url",
        help=(
            'Мониторинг: проверять HTTP(S)-адрес запросом GET с ограничением времени. При неизменном содержимом агент не запускается, как с --monitor-script.'
        ),
    )
    cron_create.add_argument(
        "--workdir",
        help='Абсолютный путь к рабочей папке задачи. Загружаются AGENTS.md, CLAUDE.md и .cursorrules из этой папки; она используется для терминала, файлов и code_exec. Без параметра инструкции проекта не загружаются.',
    )
    cron_create.add_argument(
        "--model",
        help=(
            'Закрепить модель за задачей. Настраивает только пользователь; инструмент cronjob этого не меняет. Без параметра используется cron.model или model.default из config.yaml.'
        ),
    )
    cron_create.add_argument(
        "--provider",
        dest="model_provider",
        help='Провайдер для --model, например openrouter или nous',
    )
    cron_create.add_argument(
        "--reasoning-effort",
        dest="reasoning_effort",
        help=(
            'Глубина рассуждений задачи: none, minimal, low, medium, high, xhigh, max или ultra. Заменяет agent.reasoning_effort и agent.reasoning_overrides. Неподдерживаемый уровень ограничивает провайдер. Без параметра используются общие настройки.'
        ),
    )
    cron_create.add_argument(
        "--continuity",
        dest="continuity",
        action="store_const",
        const=True,
        default=None,
        help=(
            'Передавать результат предыдущего запуска в новый запрос, чтобы продолжать работу и не повторять уже сообщённое. На первый запуск не влияет.'
        ),
    )

    # cron edit
    cron_edit = cron_subparsers.add_parser(
        "edit", help='Изменить задачу по расписанию'
    )
    cron_edit.add_argument("job_id", help='ID изменяемой задачи')
    cron_edit.add_argument("--schedule", help='Новое расписание')
    cron_edit.add_argument("--prompt", help='Новый запрос или описание задачи')
    cron_edit.add_argument("--name", help='Новое название задачи')
    cron_edit.add_argument("--deliver", help='Новое место доставки результата')
    cron_edit.add_argument("--repeat", type=int, help='Новое число повторов')
    cron_edit.add_argument(
        "--skill",
        dest="skills",
        action="append",
        help='Заменить навыки задачи указанным набором; повторите параметр для нескольких навыков',
    )
    cron_edit.add_argument(
        "--add-skill",
        dest="add_skills",
        action="append",
        help='Добавить навык к существующим; параметр можно повторять',
    )
    cron_edit.add_argument(
        "--remove-skill",
        dest="remove_skills",
        action="append",
        help='Удалить прикреплённый навык; параметр можно повторять',
    )
    cron_edit.add_argument(
        "--clear-skills",
        action="store_true",
        help='Удалить все навыки из задачи',
    )
    cron_edit.add_argument(
        "--script",
        help=(
            'Путь к скрипту в scripts/ профиля; пустая строка удаляет привязку. С --no-agent скрипт выполняет задачу самостоятельно, иначе его stdout добавляется в запрос агента.'
        ),
    )
    cron_edit.add_argument(
        "--no-agent",
        dest="no_agent",
        action="store_const",
        const=True,
        default=None,
        help=(
            'Выполнять задачу без агента; нужен --script или уже прикреплённый скрипт'
        ),
    )
    cron_edit.add_argument(
        "--agent",
        dest="no_agent",
        action="store_const",
        const=False,
        help='Выключить режим без агента и вернуть выполнение с моделью',
    )
    cron_edit.add_argument(
        "--continuity",
        dest="continuity",
        action="store_const",
        const=True,
        default=None,
        help=(
            'Передавать результат предыдущего запуска для продолжения работы без повторов'
        ),
    )
    cron_edit.add_argument(
        "--no-continuity",
        dest="continuity",
        action="store_const",
        const=False,
        help=(
            'Не передавать собственный предыдущий результат; ссылки context_from на другие задачи сохраняются'
        ),
    )
    cron_edit.add_argument(
        "--monitor-script",
        dest="monitor_script",
        help=(
            'Задать скрипт мониторинга; см. korra cron create --monitor-script. Пустая строка удаляет привязку.'
        ),
    )
    cron_edit.add_argument(
        "--monitor-url",
        dest="monitor_url",
        help=(
            'Задать адрес мониторинга; пустая строка удаляет привязку'
        ),
    )
    cron_edit.add_argument(
        "--workdir",
        help='Абсолютный путь к рабочей папке: загружает AGENTS.md и задаёт папку терминала. Пустая строка удаляет настройку.',
    )
    cron_edit.add_argument(
        "--model",
        help=(
            'Закрепить модель за задачей; инструмент cronjob этого не меняет. Пустая строка возвращает выбор через cron.model или model.default.'
        ),
    )
    cron_edit.add_argument(
        "--provider",
        dest="model_provider",
        help='Провайдер для --model; пустая строка удаляет настройку',
    )
    cron_edit.add_argument(
        "--reasoning-effort",
        dest="reasoning_effort",
        help=(
            'Глубина рассуждений задачи: none, minimal, low, medium, high, xhigh, max или ultra. Пустая строка возвращает общие настройки.'
        ),
    )

    # lifecycle actions
    cron_pause = cron_subparsers.add_parser("pause", help='Приостановить задачу по расписанию')
    cron_pause.add_argument("job_id", help='ID задачи для приостановки')

    cron_resume = cron_subparsers.add_parser("resume", help='Возобновить приостановленную задачу')
    cron_resume.add_argument("job_id", help='ID задачи для возобновления')
    cron_resume.add_argument("--at", dest="run_at", help='Назначить следующий запуск на время в формате ISO-8601')
    cron_resume.add_argument("--run-now", action="store_true", help='Назначить следующий запуск на сейчас')

    cron_run = cron_subparsers.add_parser(
        "run", help='Выполнить задачу при ближайшей проверке расписания'
    )
    cron_run.add_argument("job_id", help='ID задачи для запуска')
    add_accept_hooks_flag(cron_run)

    cron_remove = cron_subparsers.add_parser(
        "remove", aliases=["rm", "delete"], help='Удалить задачу по расписанию'
    )
    cron_remove.add_argument("job_id", help='ID удаляемой задачи')

    # cron status
    cron_subparsers.add_parser("status", help='Проверить, работает ли планировщик')

    cron_runs = cron_subparsers.add_parser(
        "runs", aliases=["history"], help='Показать сохранённую историю попыток выполнения'
    )
    cron_runs.add_argument("job_id", nargs="?", help='Необязательный фильтр по ID задачи')
    cron_runs.add_argument("--limit", type=int, default=20, help='Число строк (1–500)')

    # cron incidents — durable failure incidents (list/ack)
    cron_incidents = cron_subparsers.add_parser(
        "incidents", help='Показать или отметить просмотренными сохранённые сбои задач'
    )
    cron_incidents.add_argument(
        "--state",
        choices=["detected", "alerted", "closed"],
        help='Отфильтровать сбои по состоянию',
    )
    cron_incidents.add_argument(
        "incident_action",
        nargs="?",
        default="list",
        choices=["list", "ack"],
        help='Действие (по умолчанию list)',
    )
    cron_incidents.add_argument(
        "incident_id", nargs="?", help='ID сбоя для отметки о просмотре (ack)'
    )

    # cron notepad — per-job durable KV scratchpad (injected into the job
    # prompt each run; the running agent writes it via this CLI).
    cron_notepad = cron_subparsers.add_parser(
        "notepad",
        help='Читать и изменять блокнот задачи: значения по ключам, сохраняемые между запусками',
    )
    cron_notepad.add_argument("job_id", help='ID задачи, которой принадлежит блокнот')
    cron_notepad.add_argument(
        "notepad_action",
        nargs="?",
        default="list",
        choices=["get", "set", "delete", "list"],
        help='Действие (по умолчанию list)',
    )
    cron_notepad.add_argument("key", nargs="?", help='Ключ блокнота для get/set/delete')
    cron_notepad.add_argument("value", nargs="?", help='Сохраняемое значение (set)')

    # cron doctor
    cron_subparsers.add_parser("doctor", help='Проверить задачи по расписанию на типичные проблемы')

    # cron tick (mostly for debugging)
    cron_tick = cron_subparsers.add_parser("tick", help='Выполнить готовые задачи один раз и выйти')
    add_accept_hooks_flag(cron_tick)
    add_accept_hooks_flag(cron_parser)
    cron_parser.set_defaults(func=cmd_cron)

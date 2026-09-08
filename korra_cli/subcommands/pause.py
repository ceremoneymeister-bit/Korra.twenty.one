"""``hermes pause`` / ``hermes resume`` — the global emergency stop.

``hermes pause`` writes the ESTOP sentinel at ``$HERMES_HOME/ESTOP``, which
halts cron dispatch, kanban dispatch, and new gateway turns on their next
check. In-flight work is never killed. ``hermes resume`` removes the
sentinel and normal operation resumes on the next tick — no restart needed.

Ported from: gastownhall/gastown estop.go (MIT); related prior art:
#26778 (/panic — kill/exit semantics, different), #44617.
"""

from __future__ import annotations

import argparse


def cmd_pause(args: argparse.Namespace) -> int:
    """Engage the global emergency stop."""
    from agent.estop import engage, get_state, is_engaged

    reason = getattr(args, "reason", None)
    already = is_engaged()
    path = engage(reason=reason)
    state = get_state() or {}
    verb = "Пауза уже включена" if already else "Korra приостановлена"
    detail = f" — причина: {state['reason']}" if state.get("reason") else ""
    print(f"⏸️  {verb}{detail}")
    print(f"    файл паузы: {path}")
    print(
        "    Новые задачи расписания, доски и шлюза приостановлены.\n"
        "    Уже начатая работа продолжается. Снять паузу: `korra resume`."
    )
    return 0


def cmd_resume(args: argparse.Namespace) -> int:
    """Disengage the global emergency stop."""
    from agent.estop import disengage, sentinel_path

    if disengage():
        print("▶️ Korra продолжила работу; новые задачи запустятся при следующей проверке.")
    else:
        print(f"Korra не приостановлена; файла паузы нет: {sentinel_path()}.")
    return 0


def build_pause_parser(subparsers) -> None:
    """Attach the ``pause`` and ``resume`` subcommands to ``subparsers``."""
    pause_parser = subparsers.add_parser(
        "pause",
        help='Экстренная пауза: остановить новые задачи расписания, доски и шлюза',
        description=(
            'Приостановить новую работу расписания, доски и шлюза до команды korra resume. Уже выполняемые задачи продолжаются.'
        ),
    )
    pause_parser.add_argument(
        "--reason",
        default=None,
        help='Необязательная причина, которая сохраняется и показывается пользователям',
    )
    pause_parser.set_defaults(func=cmd_pause)

    resume_parser = subparsers.add_parser(
        "resume",
        help='Снять экстренную паузу, включённую через korra pause',
        description='Удалить метку ESTOP; новые задачи начнутся при следующей проверке',
    )
    resume_parser.set_defaults(func=cmd_resume)

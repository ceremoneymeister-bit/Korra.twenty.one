"""hermes hooks — inspect and manage shell-script hooks.

Usage::

    hermes hooks list
    hermes hooks test <event> [--for-tool X] [--payload-file F]
    hermes hooks revoke <command>
    hermes hooks doctor

Consent records live under ``~/.hermes/shell-hooks-allowlist.json`` and
hook definitions come from the ``hooks:`` block in ``~/.hermes/config.yaml``
(the same config read by the CLI / gateway at startup).

This module is a thin CLI shell over :mod:`agent.shell_hooks`; every
shared concern (payload serialisation, response parsing, allowlist
format) lives there.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List


def hooks_command(args) -> None:
    """Entry point for ``hermes hooks`` — dispatches to the requested action."""
    sub = getattr(args, "hooks_action", None)

    if not sub:
        print("Использование: korra hooks {list|test|revoke|doctor}")
        print("Подробности: `korra hooks --help`.")
        return

    if sub in {"list", "ls"}:
        _cmd_list(args)
    elif sub == "test":
        _cmd_test(args)
    elif sub in {"revoke", "remove", "rm"}:
        _cmd_revoke(args)
    elif sub == "doctor":
        _cmd_doctor(args)
    else:
        print(f"Неизвестная подкоманда hooks: {sub}")


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------

def _cmd_list(_args) -> None:
    from korra_cli.config import load_config
    from agent import outbound_webhooks, shell_hooks

    cfg = load_config()
    specs = shell_hooks.iter_configured_hooks(cfg)
    outbound = outbound_webhooks.iter_configured_targets(cfg)

    if not specs and not outbound:
        print("В config.yaml не настроены shell-хуки или исходящие вебхуки.")
        # Korra: docs-сайт удалён вместе с website/ — не отправляем
        # пользователя к несуществующему файлу.
        print("Схема и примеры: `korra hooks --help`.")
        return

    if not specs:
        print("В config.yaml не настроены shell-хуки.")
    else:
        by_event: Dict[str, List] = {}
        for spec in specs:
            by_event.setdefault(spec.event, []).append(spec)

        allowlist = shell_hooks.load_allowlist()
        approved = {
            (e.get("event"), e.get("command"))
            for e in allowlist.get("approvals", [])
            if isinstance(e, dict)
        }

        print(f"Настроенные shell-хуки; всего {len(specs)}:\n")

        for event in sorted(by_event.keys()):
            print(f"  [{event}]")
            for spec in by_event[event]:
                is_approved = (spec.event, spec.command) in approved
                status = "✓ разрешён" if is_approved else "✗ не разрешён"
                matcher_part = f" matcher={spec.matcher!r}" if spec.matcher else ""
                print(
                    f"    - {spec.command}{matcher_part} "
                    f"(timeout={spec.timeout}s, {status})"
                )

                if is_approved:
                    entry = shell_hooks.allowlist_entry_for(spec.event, spec.command)
                    if entry and entry.get("approved_at"):
                        print(f"      approved_at: {entry['approved_at']}")
                        mtime_now = shell_hooks.script_mtime_iso(spec.command)
                        mtime_at = entry.get("script_mtime_at_approval")
                        if mtime_now and mtime_at and mtime_now > mtime_at:
                            print(
                                f"      ⚠ сценарий изменён после подтверждения "
                                f"(было {mtime_at}, стало {mtime_now}); "
                                f"повторите проверку: `korra hooks doctor`"
                            )
            print()

    if outbound:
        print(f"Настроенные исходящие вебхуки; всего {len(outbound)}:\n")
        for target in outbound:
            signed = "подписан" if target.secret else "БЕЗ ПОДПИСИ"
            matcher_part = f" matcher={target.matcher!r}" if target.matcher else ""
            print(f"  - {target.label}")
            print(f"      адрес:   {target.url}")
            print(
                f"      события:  {', '.join(target.events)}{matcher_part} "
                f"(timeout={target.timeout}s, {signed})"
            )
        print()


# ---------------------------------------------------------------------------
# test
# ---------------------------------------------------------------------------

# Synthetic kwargs matching the real invoke_hook() call sites — these are
# passed verbatim to agent.shell_hooks.run_once(), which routes them through
# the same _serialize_payload() that production firings use.  That way the
# stdin a script sees under `hermes hooks test` and `hermes hooks doctor`
# is identical in shape to what it will see at runtime.
_DEFAULT_PAYLOADS = {
    "pre_tool_call": {
        "tool_name": "terminal",
        "args": {"command": "echo hello"},
        "session_id": "test-session",
        "task_id": "test-task",
        "tool_call_id": "test-call",
    },
    "post_tool_call": {
        "tool_name": "terminal",
        "args": {"command": "echo hello"},
        "session_id": "test-session",
        "task_id": "test-task",
        "tool_call_id": "test-call",
        "result": '{"output": "hello"}',
        "duration_ms": 42,
    },
    "pre_llm_call": {
        "session_id": "test-session",
        "user_message": "What is the weather?",
        "conversation_history": [],
        "is_first_turn": True,
        "model": "gpt-4",
        "platform": "cli",
    },
    "post_llm_call": {
        "session_id": "test-session",
        "model": "gpt-4",
        "platform": "cli",
    },
    "pre_verify": {
        "session_id": "test-session",
        "platform": "cli",
        "model": "gpt-4",
        "coding": True,
        "attempt": 0,
        "final_response": "All done — the change is applied.",
        "changed_paths": ["src/app.tsx"],
    },
    "on_session_start": {"session_id": "test-session"},
    "on_session_end": {
        "session_id": "test-session",
        "task_id": "test-task",
        "turn_id": "test-turn",
        "completed": True,
        "failed": False,
        "interrupted": False,
        "turn_exit_reason": "text_response(stop)",
        "model": "gpt-4",
        "platform": "cli",
    },
    "on_session_finalize": {"session_id": "test-session"},
    "on_session_reset": {"session_id": "test-session"},
    "pre_api_request": {
        "session_id": "test-session",
        "task_id": "test-task",
        "platform": "cli",
        "model": "claude-sonnet-4-6",
        "provider": "anthropic",
        "base_url": "https://api.anthropic.com",
        "api_mode": "anthropic_messages",
        "api_call_count": 1,
        "message_count": 4,
        "tool_count": 12,
        "approx_input_tokens": 2048,
        "request_char_count": 8192,
        "max_tokens": 4096,
    },
    "post_api_request": {
        "session_id": "test-session",
        "task_id": "test-task",
        "platform": "cli",
        "model": "claude-sonnet-4-6",
        "provider": "anthropic",
        "base_url": "https://api.anthropic.com",
        "api_mode": "anthropic_messages",
        "api_call_count": 1,
        "api_duration": 1.234,
        "finish_reason": "stop",
        "message_count": 4,
        "response_model": "claude-sonnet-4-6",
        "usage": {"input_tokens": 2048, "output_tokens": 512},
        "assistant_content_chars": 1200,
        "assistant_tool_call_count": 0,
        # Per-advisor metrics on a MoA turn, None otherwise. MoA returns only
        # the aggregator's response, so without this an observer cannot see the
        # fan-out or price it at each advisor's own model.
        "moa_references": None,
    },
    "subagent_stop": {
        "parent_session_id": "parent-sess",
        "child_role": None,
        "child_summary": "Synthetic summary for hooks test",
        "child_status": "completed",
        "tool_call_history": [
            {
                "tool_name": "write_file",
                "tool_input": {
                    "argument_keys": ["content", "path"],
                    "targets": {"path": "/tmp/report.txt"},
                },
                "input_bytes": 128,
                "output_bytes": 32,
                "status": "ok",
            }
        ],
        "duration_ms": 1234,
    },
}


def _cmd_test(args) -> None:
    from korra_cli.config import load_config
    from korra_cli.plugins import VALID_HOOKS
    from agent import shell_hooks

    event = args.event
    if event not in VALID_HOOKS:
        print(f"Неизвестное событие: {event!r}")
        print(f"Допустимые события: {', '.join(sorted(VALID_HOOKS))}")
        return

    # Synthetic kwargs in the same shape invoke_hook() would pass.  Merged
    # with --for-tool (overrides tool_name) and --payload-file (extra kwargs).
    payload = dict(_DEFAULT_PAYLOADS.get(event, {"session_id": "test-session"}))

    if getattr(args, "for_tool", None):
        payload["tool_name"] = args.for_tool

    if getattr(args, "payload_file", None):
        try:
            custom = json.loads(Path(args.payload_file).read_text(encoding="utf-8"))
            if isinstance(custom, dict):
                payload.update(custom)
            else:
                print(f"Внимание: {args.payload_file} не содержит объект JSON; файл пропущен.")
        except Exception as exc:
            print(f"Не удалось прочитать файл данных: {exc}")
            return

    specs = shell_hooks.iter_configured_hooks(load_config())
    specs = [s for s in specs if s.event == event]

    if getattr(args, "for_tool", None):
        specs = [
            s for s in specs
            if s.event not in {"pre_tool_call", "post_tool_call"}
            or s.matches_tool(args.for_tool)
        ]

    if not specs:
        print(f"Для события {event} shell-хуки не настроены.")
        if getattr(args, "for_tool", None):
            print(f"(с фильтром --for-tool={args.for_tool})")
        return

    print(f"Запускаю хуки для события '{event}'; количество: {len(specs)}:\n")
    for spec in specs:
        print(f"  → {spec.command}")
        result = shell_hooks.run_once(spec, payload)
        _print_run_result(result)
        print()


def _print_run_result(result: Dict[str, Any]) -> None:
    if result.get("error"):
        print(f"      ✗ ошибка: {result['error']}")
        return
    if result.get("timed_out"):
        print(f"      ✗ время ожидания истекло через {result['elapsed_seconds']} с")
        return

    rc = result.get("returncode")
    elapsed = result.get("elapsed_seconds", 0)
    print(f"      код={rc}  время={elapsed} с")

    stdout = (result.get("stdout") or "").strip()
    stderr = (result.get("stderr") or "").strip()
    if stdout:
        print(f"      stdout: {_truncate(stdout, 400)}")
    if stderr:
        print(f"      stderr: {_truncate(stderr, 400)}")

    parsed = result.get("parsed")
    if parsed:
        print(f"      разобрано (формат протокола): {json.dumps(parsed)}")
    else:
        print("      разобрано: <пусто; хук ничего не передал диспетчеру>")


def _truncate(s: str, n: int) -> str:
    return s if len(s) <= n else s[: n - 3] + "..."


# ---------------------------------------------------------------------------
# revoke
# ---------------------------------------------------------------------------

def _cmd_revoke(args) -> None:
    from agent import shell_hooks

    removed = shell_hooks.revoke(args.command)
    if removed == 0:
        print(f"Команда не найдена в списке разрешённых: {args.command}")
        return
    print(f"Удалено разрешений для команды {args.command}: {removed}")
    print(
        "Примечание: работающие процессы CLI и шлюза сохранят уже зарегистрированные "
        "обработчики до перезапуска."
    )


# ---------------------------------------------------------------------------
# doctor
# ---------------------------------------------------------------------------

def _cmd_doctor(_args) -> None:
    from korra_cli.config import load_config
    from agent import shell_hooks

    specs = shell_hooks.iter_configured_hooks(load_config())

    if not specs:
        print("Shell-хуки не настроены; проверять нечего.")
        return

    print(f"Проверяю настроенные shell-хуки; количество: {len(specs)}…\n")

    problems = 0
    for spec in specs:
        print(f"  [{spec.event}] {spec.command}")
        problems += _doctor_one(spec, shell_hooks)
        print()

    if problems:
        print(f"Найдено проблем: {problems}. Исправьте их перед использованием хуков.")
    else:
        print("Все shell-хуки исправны.")


def _doctor_one(spec, shell_hooks) -> int:
    problems = 0

    # 1. Script exists and is executable
    if shell_hooks.script_is_executable(spec.command):
        print("      ✓ сценарий существует и разрешён к запуску")
    else:
        problems += 1
        print("      ✗ сценарий отсутствует или не разрешён к запуску "
              "(исправьте путь или выполните chmod +x)")

    # 2. Allowlist status
    entry = shell_hooks.allowlist_entry_for(spec.event, spec.command)
    if entry:
        print(f"      ✓ разрешён; подтверждено {entry.get('approved_at', '?')}")
    else:
        problems += 1
        print("      ✗ не разрешён; хук не будет запускаться "
              "(один раз укажите --accept-hooks или подтвердите в терминале)")

    # 3. Mtime drift
    if entry and entry.get("script_mtime_at_approval"):
        mtime_now = shell_hooks.script_mtime_iso(spec.command)
        mtime_at = entry["script_mtime_at_approval"]
        if mtime_now and mtime_at and mtime_now > mtime_at:
            problems += 1
            print(f"      ⚠ сценарий изменён после подтверждения "
                  f"(было {mtime_at}, стало {mtime_now}); проверьте изменения, "
                  f"затем выполните `korra hooks revoke` и подтвердите снова")
        elif mtime_now and mtime_at and mtime_now == mtime_at:
            print("      ✓ сценарий не менялся после подтверждения")

    # 4. Produces valid JSON for a synthetic payload — only when the entry
    # is already allowlisted.  Otherwise `hermes hooks doctor` would execute
    # every script listed in a freshly-pulled config before the user has
    # reviewed them, which directly contradicts the documented workflow
    # ("spot newly-added hooks *before they register*").
    if not entry:
        print("      ℹ проверка JSON пропущена: хук ещё не разрешён. "
              "Подтвердите его в терминале или через --accept-hooks, затем "
              "повторите `korra hooks doctor`.")
    elif shell_hooks.script_is_executable(spec.command):
        payload = _DEFAULT_PAYLOADS.get(spec.event, {"extra": {}})
        result = shell_hooks.run_once(spec, payload)
        if result.get("timed_out"):
            problems += 1
            print(f"      ✗ время ожидания истекло через {result['elapsed_seconds']} с "
                  f"на тестовых данных; лимит {spec.timeout} с")
        elif result.get("error"):
            problems += 1
            print(f"      ✗ ошибка запуска: {result['error']}")
        else:
            rc = result.get("returncode")
            elapsed = result.get("elapsed_seconds", 0)
            stdout = (result.get("stdout") or "").strip()
            if stdout:
                try:
                    json.loads(stdout)
                    print(f"      ✓ на тестовых данных получен корректный JSON "
                          f"(код={rc}, {elapsed} с)")
                except json.JSONDecodeError:
                    problems += 1
                    print(f"      ✗ stdout не содержит корректный JSON (код={rc}, "
                          f"{elapsed} с): {_truncate(stdout, 120)}")
            else:
                print(f"      ✓ запуск завершён без stdout "
                      f"(код={rc}, {elapsed} с); хук только наблюдает")

    return problems

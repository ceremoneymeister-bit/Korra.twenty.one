"""Literal reminders share the durable scheduler without invoking an agent."""

from typing import Any, Mapping


def reminder_text(job: Mapping[str, Any]) -> str | None:
    value = job.get("reminder")
    if value is None or value == "":
        return None
    if not isinstance(value, str) or not value.strip() or len(value) > 8000:
        raise ValueError("Текст напоминания должен содержать от 1 до 8000 символов.")
    if any(job.get(key) for key in (
        "prompt", "skills", "skill", "script", "no_agent", "monitor_script", "monitor_url",
    )):
        raise ValueError("Напоминание содержит только готовый текст; уберите инструкции, навыки и скрипт.")
    return value.strip()


def validate_reminder_delivery(job: Mapping[str, Any]) -> None:
    if not job.get("reminder"):
        return
    targets = str(job.get("deliver") or "").split(",")
    if any(target.strip().split(":", 1)[0] == "bot-chat" for target in targets):
        raise ValueError("Напоминание адресовано человеку: выберите Telegram или сохранение в платформе. Внутренний чат запускает агента; для него используйте задачу с инструкцией.")

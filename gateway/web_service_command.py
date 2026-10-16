"""Small, local service replies for the non-terminal HTTP chat surfaces.

These are not CLI executions. The registry supplies names/aliases; only the
explicit subset below has a web contract. Ordinary text and skill invocations
keep their existing path, and no provider/auth/discovery code runs here.
"""
from __future__ import annotations

import logging
import re
import uuid
from typing import Any


logger = logging.getLogger(__name__)
_CONFIG_READ_ERROR_REPLY = (
    "Не удалось прочитать настройки профиля. Проверьте файл настроек в панели; "
    "параметры модели этой командой не проверялись и не менялись."
)


class _ConfigReadFailure(Exception):
    """A failed local read; only the fixed safe reply may cross the API."""


_SERVICE_COMMANDS = frozenset({
    "help", "status", "version", "model", "tools", "profile", "usage",
    "reasoning", "fast", "reload-skills", "compress", "new", "clear",
    "stop", "restart", "quit",
})
_COMMAND = re.compile(r"/([A-Za-z][A-Za-z0-9_-]*)(?:[ \t]+([^\r\n]*))?")


def parse_web_service_command(message: Any) -> tuple[str, str] | None:
    if not isinstance(message, str):
        return None
    match = _COMMAND.fullmatch(message.strip())
    if match is None:
        return None
    from korra_cli.commands import resolve_command

    command = resolve_command(match[1])
    if command is None or command.name not in _SERVICE_COMMANDS:
        return None
    return command.name, (match[2] or "").strip()


def _text(value: Any, fallback: str = "не задано") -> str:
    from gateway.platforms.api_server import _redact_api_error_text

    if not isinstance(value, (str, int, float, bool)) or not str(value).strip():
        return fallback
    if isinstance(value, bool):
        return "включено" if value else "выключено"
    return _redact_api_error_text(value, limit=200).replace("\n", " ").replace("\r", " ")


def _reply(adapter, command, args, session, profile, requested_model,
           requested_provider, model_options, route, session_model):
    if args:
        return (
            f"В веб-чате /{command} выполняется без аргументов. "
            "Изменения через эту команду не применены. Используйте настройки панели "
            "или соответствующую команду в терминале Korra."
        )
    if command == "help":
        from korra_cli.commands import resolve_command

        names = ("help", "status", "version", "model", "tools", "profile", "usage",
                 "reasoning", "fast", "reload-skills")
        lines = ["Служебные команды веб-чата (без обращения к модели):"]
        readonly = {
            "help": "показать служебные команды веб-чата",
            "status": "показать профиль, диалог, сохранённую модель и записанные токены",
            "profile": "показать имя текущего профиля",
            "model": "показать настройки модели профиля и сохранённый выбор диалога",
            "tools": "показать настроенные наборы инструментов веб-чата",
            "reasoning": "показать сохранённые настройки глубины",
            "fast": "показать сохранённые настройки ускорения",
            "usage": "показать записанный расход токенов диалога",
        }
        lines.extend(f"/{name} — {readonly.get(name) or resolve_command(name).description}" for name in names)
        lines.extend([
            "Здесь эти команды выполняются без аргументов; настройки меняются в панели.",
            "/reload-mcp — обновить MCP с отдельным подтверждением.",
            "/skills, /memory, /context, /curator — существующие команды навыков и памяти.",
            "/learn и /refine используют модель; это отдельные действия.",
            "Новый диалог и остановка ответа — кнопками чата. /compact здесь не сжимает контекст.",
            "Пока в диалоге выполняется ответ, дождитесь его завершения перед служебной командой.",
        ])
        return "\n".join(lines)
    if command == "version":
        from korra_cli.slash_exec import CommandContext, execute_command

        return execute_command("version", CommandContext(surface="api")).text
    if command in {"new", "clear"}:
        return "Чтобы начать новый диалог, нажмите «Новый чат». Эта команда не очищает текущую беседу."
    if command == "stop":
        return "Чтобы остановить выполняющийся ответ, нажмите кнопку остановки в чате. Команда /stop здесь не отменяет работу."
    if command in {"restart", "quit"}:
        return "Эта команда доступна в терминале Korra или в управлении установкой. Работа установки не изменена."
    if command == "compress":
        return "Сжатие контекста командой /compact (/compress) в веб-чате не поддерживается. Контекст не сжат. Можно начать новый чат; в терминале и мессенджерах доступна штатная команда сжатия."
    if command == "reload-skills":
        from agent.skill_commands import reload_skills

        refreshed = reload_skills()
        # Bound the reply; never copy skill bodies/descriptions into history.
        return (
            f"Каталог навыков профиля обновлён. Навыков: {refreshed['total']}. "
            "Новые диалоги получат обновлённый каталог. "
            "Начатые диалоги сохраняют прежние инструкции."
        )
    from korra_cli.profiles import get_active_profile_name

    profile_name = profile or get_active_profile_name()
    if command == "profile":
        return f"Профиль этого обращения: {_text(profile_name)}."
    if command in {"status", "usage"}:
        if not session:
            return "Сохранённых сведений об этом диалоге пока нет. Команда выполнена без обращения к модели."
        lines = [f"Профиль: {_text(profile_name)}.", f"Диалог: {_text(session.get('id'))}."]
        if command == "status":
            lines.append(f"Сохранённая модель диалога: {_text(session.get('model'))}.")
        lines.extend([
            f"Записанные токены диалога: вход {_text(session.get('input_tokens'), '0')}, выход {_text(session.get('output_tokens'), '0')}.",
            f"Вход указан без кеша. Отдельно: чтение кеша {_text(session.get('cache_read_tokens'), '0')}, запись кеша {_text(session.get('cache_write_tokens'), '0')}, рассуждения {_text(session.get('reasoning_tokens'), '0')}.",
            "Это сохранённый учёт, не проверка текущих лимитов подписки и не замер запроса провайдеру.",
        ])
        return "\n".join(lines)

    from korra_cli.config import FailedConfigRead, read_raw_config

    config = read_raw_config()
    if isinstance(config, FailedConfigRead):
        raise _ConfigReadFailure
    model = config.get("model") or {}
    model = model if isinstance(model, dict) else {"default": model}
    agent = config.get("agent") or {}
    agent = agent if isinstance(agent, dict) else {}
    stored = adapter._parse_session_model_config((session or {}).get("model_config"))
    lock = stored.get("browser_model_lock") or {}
    lock = lock if isinstance(lock, dict) else {}
    options = lock.get("model_options") or {}
    options = options if isinstance(options, dict) else {}
    model_options = model_options if isinstance(model_options, dict) else {}
    if command == "model":
        lines = [
            f"Модель в настройках профиля для новых диалогов: {_text(model.get('default') or model.get('model'))}.",
            f"Провайдер в настройках профиля: {_text(model.get('provider'))}.",
            f"Сохранённая модель этого диалога: {_text(lock.get('model') or (session or {}).get('model') or session_model)}.",
        ]
        selected_model = requested_model or (route or {}).get("model")
        selected_provider = requested_provider or (route or {}).get("provider")
        if selected_model or selected_provider:
            lines.append(f"Запрошено в этом обращении: {_text(selected_provider)} / {_text(selected_model)}.")
        lines.append("Это настройки и сохранённые сведения; фактическая модель последнего ответа здесь не проверяется. Выбор модели — в панели.")
        return "\n".join(lines)
    if command == "tools":
        platforms = config.get("platform_toolsets") or {}
        toolsets = platforms.get("api_server") if isinstance(platforms, dict) else None
        names = ", ".join(_text(name) for name in toolsets[:30]) if isinstance(toolsets, list) else "набор по умолчанию"
        return f"Настроенные наборы инструментов для веб-чата этого профиля: {names or 'пустой список'}. Это конфигурация, а не список реально подключённых инструментов текущей сессии. MCP-подключения этой командой не проверяются. Управление — в разделе «Инструменты»."
    if command == "reasoning":
        def effort(value):
            reasoning = value.get("reasoning")
            if isinstance(reasoning, dict) and reasoning.get("enabled") is False:
                return False
            return (reasoning.get("effort") or value.get("reasoning_effort")) if isinstance(reasoning, dict) else value.get("reasoning_effort")
        return (
            f"Глубина в настройках профиля: {_text(agent.get('reasoning_effort'))}. "
            f"В сохранённом выборе диалога: {_text(effort(options))}. "
            f"В параметрах этого обращения: {_text(effort(model_options))}. "
            "Фактическое значение у провайдера здесь не проверяется. Изменить глубину можно в настройках модели панели."
        )
    if command == "fast":
        def tier(value):
            raw = value["service_tier"] if "service_tier" in value else value.get("fast")
            if isinstance(raw, str):
                if raw.lower() in {"priority", "fast", "on"}:
                    return "быстрый (priority)"
                if raw.lower() in {"default", "normal", "standard", "off", "none"}:
                    return "обычный (default)"
            return raw
        return (
            f"Быстрый режим в настройках профиля: {_text(tier(agent))}. "
            f"В сохранённом выборе диалога: {_text(tier(options))}. "
            f"В параметрах этого обращения: {_text(tier(model_options))}. "
            "Фактический режим последнего ответа здесь не проверяется. Эта команда не включает ускорение и не меняет оплату."
        )
    raise AssertionError("unhandled local service command")


def run_web_service_command(adapter, user_message, session_id, *, profile=None,
                            source="api_server", owner=True, display_metadata=None,
                            requested_model=None, requested_provider=None,
                            model_options=None, route=None, session_model=None):
    """Run inside the request profile scope, in a worker; return a local result.

    Existing turn leases prevent a service pair from being inserted into the
    middle of an ongoing tool turn. Existing batch persistence commits the
    user/reply atomically, without changing any earlier context or prompts.
    """
    parsed = parse_web_service_command(user_message)
    if parsed is None:
        return None
    if not owner:
        return {"final_response": "Служебные команды установки доступны только владельцу; в групповом диалоге используйте управление комнатой.", "api_calls": 0, "completed": True}
    db = adapter._ensure_session_db() if session_id else None
    holder = f"api-service:{uuid.uuid4().hex}"
    acquired = False
    try:
        if session_id and db is None:
            return {"final_response": "Не удалось открыть историю диалога. Служебная команда не выполнена; проверьте доступность хранилища и повторите попытку.", "api_calls": 0, "completed": False, "failed": True}
        session = db.get_session(session_id) if db is not None else None
        if db is not None:
            if session is None:
                db.create_session(session_id, source or "api_server")
            acquired = db.try_acquire_session_turn_lease(session_id, holder, patience_s=0)
            if not acquired or db.get_compression_lock_holder(session_id):
                return {"final_response": "В этом диалоге выполняется ответ. Дождитесь его завершения и повторите служебную команду; текущая работа продолжается.", "api_calls": 0, "completed": True}
        reply = _reply(adapter, *parsed, session, profile, requested_model,
                       requested_provider, model_options, route, session_model)
        user = {"role": "user", "content": user_message}
        if display_metadata:
            user["display_metadata"] = dict(display_metadata)
        pair = [user, {"role": "assistant", "content": reply}]
        if db is not None:
            db.append_messages_batch(session_id, pair, turn_lease_holder=holder, human_turn=True)
        return {"final_response": reply, "messages": pair, "api_calls": 0, "completed": True}
    except _ConfigReadFailure:
        return {"final_response": _CONFIG_READ_ERROR_REPLY, "api_calls": 0, "completed": False, "failed": True}
    except Exception as exc:
        # Do not echo config/skill/DB exception text across the HTTP boundary.
        logger.warning("Web service /%s failed (%s)", parsed[0], type(exc).__name__)
        return {"final_response": "Не удалось выполнить или сохранить служебную команду. Проверьте журнал установки и повторите попытку.", "api_calls": 0, "completed": False, "failed": True}
    finally:
        if acquired:
            db.release_session_turn_lease(session_id, holder)

"""The native MCP slash command on non-delivering HTTP chat transports."""
import asyncio
import uuid
from types import SimpleNamespace
from contextvars import ContextVar


_MCP_SERVICE_COMMANDS = frozenset({'/reload-mcp', '/reload_mcp', '/approve', '/always', '/cancel'})


_RELOAD_ALIASES = frozenset({'/mcp-reload', '/reload-mcps'})


def _command_token(token):
    token = token.lower()
    return '/reload-mcp' if token in _RELOAD_ALIASES else token


def is_web_mcp_command(user_message):
    """Cheap gate: ordinary API turns need no command executor or gateway import."""
    if not isinstance(user_message, str):
        return False
    parts = user_message.strip().split(maxsplit=1)
    return bool(parts and _command_token(parts[0]) in _MCP_SERVICE_COMMANDS)


_active_command_run = ContextVar("mcp_command_run", default=None)


def _run_command(coro, active_run_id):
    token = _active_command_run.set(active_run_id)
    try:
        return asyncio.run(coro)
    finally:
        _active_command_run.reset(token)


class _WebMCPCommand:
    # Reuse the gateway's policy and opt-out persistence, with HTTP text replies.
    async def _handle_reload_mcp_command(self, event):
        from gateway.slash_commands import GatewaySlashCommandsMixin
        return await GatewaySlashCommandsMixin._handle_reload_mcp_command(self, event)

    def __init__(self, adapter, session_key):
        self.adapter = adapter
        self.session_key = session_key

    def _session_key_for_source(self, source):
        return self.session_key

    def _read_user_config(self):
        from korra_cli.config import load_config
        return load_config()

    async def _request_slash_confirm(self, *, event, command, title, message, handler):
        from tools import slash_confirm
        slash_confirm.register(self.session_key, uuid.uuid4().hex, command, handler)
        return message

    async def _execute_mcp_reload(self, event):
        from gateway.mcp_reload import try_enter_mcp_reload, MCP_RELOAD_BUSY
        lease = try_enter_mcp_reload()
        if lease is None:
            return MCP_RELOAD_BUSY
        try:
            # A reconnect can close a transport currently used by a live tool call.
            # Defer the operation until ongoing API work has completed.
            from gateway.run import _gateway_runner_ref
            runner = self.adapter.gateway_runner or _gateway_runner_ref()
            api_busy = (
                self.adapter._shutdown_interruptible_agents
                or self.adapter._inflight_agent_runs > 1
                or self.adapter._pending_agent_requests
                or any(not task.done() for run_id, task in self.adapter._active_run_tasks.items()
                       if run_id != _active_command_run.get())
            )
            gateway_busy = runner and (
                runner._running_agent_count() or runner._active_cron_job_count()
            )
            if api_busy or gateway_busy:
                return 'MCP пока не обновлены: дождитесь завершения текущих задач и повторите /reload-mcp.'
            from gateway.mcp_reload import reload_mcp_servers
            from gateway.platforms.api_server import _redact_api_error_text
            try:
                return reload_mcp_servers()[-1]
            except Exception as exc:
                return f'Не удалось обновить MCP: {_redact_api_error_text(exc)}'
        finally:
            lease.release()

def run_web_mcp_command(adapter, user_message, session_id, active_run_id=None, *, owner=True):
    """Return a service reply, or None for an unrelated ordinary message.

    Called in the executor inside the request's profile scope. Pending
    confirmations are bound to both the profile home and conversation ID.
    """
    if not isinstance(user_message, str):
        return None
    parts = user_message.strip().split(maxsplit=1)
    command = _command_token(parts[0]) if parts else ''
    if command not in _MCP_SERVICE_COMMANDS:
        return None
    if not owner:
        return 'Обновление MCP и его подтверждение доступны только владельцу установки.'
    if len(parts) > 1:
        if command in {'/reload-mcp', '/reload_mcp'}:
            return 'Команда /reload-mcp выполняется без аргументов.'
        return None
    from korra_constants import get_hermes_home
    from tools import slash_confirm
    if not session_id:
        if command in {'/reload-mcp', '/reload_mcp'}:
            return 'Для /reload-mcp нужен ID диалога, чтобы подтвердить обновление в этом чате.'
        return None
    key = f'api-mcp:{get_hermes_home().resolve()}:{session_id}'
    slash_confirm.clear_if_stale(key)
    pending = slash_confirm.get_pending(key)
    if command in {'/approve', '/always', '/cancel'}:
        if pending is None:
            return None
        choice = {'/approve': 'once', '/always': 'always', '/cancel': 'cancel'}[command]
        return _run_command(slash_confirm.resolve(key, pending['confirm_id'], choice), active_run_id)
    handler = _WebMCPCommand(adapter, key)
    return _run_command(handler._handle_reload_mcp_command(SimpleNamespace(source=None)), active_run_id)

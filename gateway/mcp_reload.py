"""Shared native MCP reconnect operation; callers own confirmation and history."""
import threading
from functools import wraps

from agent.i18n import t

# One process owns the native MCP transports, including shared/adopted profile
# connections. Admission is nonblocking: no model queue or scheduler is added.
_admission_lock = threading.Lock()
_active_agent_work = 0
_reload_active = False
MCP_RELOAD_BUSY = 'MCP обновляются или используются текущей задачей. Повторите запрос после её завершения.'


class _AdmissionLease:
    def __init__(self, reload=False):
        self.reload = reload
        self.released = False

    def release(self):
        global _active_agent_work, _reload_active
        with _admission_lock:
            if self.released:
                return
            self.released = True
            if self.reload:
                _reload_active = False
            else:
                _active_agent_work -= 1


def try_enter_agent_work():
    global _active_agent_work
    with _admission_lock:
        if _reload_active:
            return None
        _active_agent_work += 1
        return _AdmissionLease()


def try_enter_mcp_reload():
    global _reload_active
    # Detached delegates may outlive their foreground caller's shared lease.
    # Their existing registry counts queued and running work before handoff.
    from tools.async_delegation import active_count
    if active_count():
        return None
    with _admission_lock:
        if _reload_active or _active_agent_work:
            return None
        _reload_active = True
        return _AdmissionLease(reload=True)


def mcp_busy_result():
    return {'final_response': MCP_RELOAD_BUSY, 'error': MCP_RELOAD_BUSY,
            'failed': True, 'completed': False, 'api_calls': 0}


def guard_agent_mcp_work(function):
    """Hold a shared lease for the actual synchronous gateway worker lifetime."""
    @wraps(function)
    def admitted(*args, **kwargs):
        lease = try_enter_agent_work()
        if lease is None:
            return mcp_busy_result()
        try:
            return function(*args, **kwargs)
        finally:
            lease.release()
    return admitted


def reload_mcp_servers():
    """Reconnect the current profile using native config/secret resolution.

    Run in the caller's profile context. Return both the native tool names
    and a truthful summary, including partial connection failures.
    """
    from tools.mcp_tool import (
        _lock, _servers, _server_in_current_profile, _public_server_name,
        discover_mcp_tools, shutdown_mcp_servers, get_mcp_status,
    )

    def connected_names():
        with _lock:
            return {_public_server_name(name) for name in _servers
                    if _server_in_current_profile(name)}

    old_servers = connected_names()
    shutdown_mcp_servers()
    new_tools = discover_mcp_tools()
    connected_servers = connected_names()
    statuses = get_mcp_status()
    incomplete = [s for s in statuses if not s['connected'] and not s['disabled']]
    added = connected_servers - old_servers
    removed = old_servers - connected_servers
    reconnected = connected_servers & old_servers
    lines = [t('gateway.reload_mcp.header') if not incomplete
             else '⚠️ MCP обновлены частично: не все серверы подключены.']
    if reconnected:
        lines.append(t('gateway.reload_mcp.reconnected', names=', '.join(sorted(reconnected))))
    if added:
        lines.append(t('gateway.reload_mcp.added', names=', '.join(sorted(added))))
    if removed:
        lines.append(t('gateway.reload_mcp.removed', names=', '.join(sorted(removed))))
    if connected_servers:
        lines.append(t('gateway.reload_mcp.tools_available', tools=len(new_tools), servers=len(connected_servers)))
    else:
        lines.append(t('gateway.reload_mcp.none_connected'))
    # Server errors can contain credentials or URLs. Report names/status only;
    # the native MCP diagnostics retain the detailed connection failure.
    for status in incomplete:
        lines.append(f"Не подключён: {status['name']} ({status['status']}). Проверьте настройки MCP.")
    return new_tools, added, removed, reconnected, '\n'.join(lines)

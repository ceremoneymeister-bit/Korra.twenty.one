"""Shared native MCP reconnect operation; callers own confirmation and history."""
from agent.i18n import t


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

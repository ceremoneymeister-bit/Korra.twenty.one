"""Behavior contracts for cua-driver 0.10 permission-mode integration."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest


@pytest.fixture(autouse=True)
def _reset_computer_use_state():
    from tools.computer_use.tool import reset_backend_for_tests

    reset_backend_for_tests()
    yield
    reset_backend_for_tests()


def test_normal_hermes_session_maps_to_standard_mode():
    from tools.computer_use import tool as computer_use

    with patch(
        "tools.computer_use.cua_backend._cua_configured_permission_mode",
        return_value="standard",
    ):
        assert computer_use._cua_permission_mode("session-a") == "standard"


def test_approval_bypass_keeps_cua_driver_safety_ceiling():
    from tools.computer_use import tool as computer_use

    # The general approval bypass is deliberately irrelevant to the driver
    # mode. It suppresses ordinary Korra cards, not password/payment ceilings.
    with patch("tools.approval.is_approval_bypass_active_for_session", return_value=True), patch(
        "tools.computer_use.cua_backend._cua_configured_permission_mode",
        return_value="standard",
    ):
        assert computer_use._cua_permission_mode("session-a") == "standard"


def test_gateway_session_key_yolo_does_not_remove_driver_ceiling():
    from tools import approval
    from tools.computer_use import tool as computer_use

    gateway_key = "agent:main:telegram:private:12345"
    token = approval.set_current_session_key(gateway_key)
    try:
        # Pin manual policy so the test isolates the session toggle from
        # Korra's new autonomous default.
        with patch("tools.approval._get_approval_mode", return_value="manual"):
            approval.enable_session_yolo(gateway_key)
            assert computer_use._cua_permission_mode("db-sid-xyz") == "standard"
            approval.disable_session_yolo(gateway_key)
            assert computer_use._cua_permission_mode("db-sid-xyz") == "standard"
    finally:
        approval.disable_session_yolo(gateway_key)
        try:
            approval.reset_current_session_key(token)
        except Exception:
            approval.set_current_session_key("")


def test_mode_change_replaces_only_that_sessions_backend():
    from tools.computer_use import tool as computer_use

    created = []

    class _Backend:
        def __init__(self, permission_mode="standard"):
            self.permission_mode = permission_mode
            self.stopped = False
            created.append(self)

        def start(self):
            pass

        def stop(self):
            self.stopped = True

    configured_mode = "standard"
    with patch(
        "tools.computer_use.cua_backend._cua_configured_permission_mode",
        side_effect=lambda: configured_mode,
    ), patch(
        "tools.computer_use.cua_backend.CuaDriverBackend", _Backend
    ):
        standard = computer_use._get_backend("session-a")
        other = computer_use._get_backend("session-b")
        configured_mode = "bounded"
        bounded = computer_use._get_backend("session-a")

    assert getattr(standard, "permission_mode") == "standard"
    assert getattr(standard, "stopped") is True
    assert getattr(bounded, "permission_mode") == "bounded"
    assert bounded is not standard
    assert getattr(other, "permission_mode") == "standard"
    assert getattr(other, "stopped") is False


def test_mode_change_is_rechecked_after_stale_backend_stops():
    from tools.computer_use import tool as computer_use

    configured_mode = "standard"
    created = []

    class _Backend:
        def __init__(self, permission_mode="standard"):
            self.permission_mode = permission_mode
            created.append(self)

        def start(self):
            pass

        def stop(self):
            nonlocal configured_mode
            configured_mode = "standard"

    with patch(
        "tools.computer_use.cua_backend._cua_configured_permission_mode",
        side_effect=lambda: configured_mode,
    ), patch("tools.computer_use.cua_backend.CuaDriverBackend", _Backend):
        original = computer_use._get_backend("session-a")
        configured_mode = "bounded"
        replacement = computer_use._get_backend("session-a")

    assert getattr(original, "permission_mode") == "standard"
    assert getattr(replacement, "permission_mode") == "standard"
    assert replacement is not original
    assert [backend.permission_mode for backend in created] == [
        "standard",
        "standard",
    ]


def test_release_seam_stops_backend_and_clears_session_state():
    from tools.computer_use import tool as computer_use

    backend = Mock()
    computer_use._backends["session-a"] = backend
    computer_use._backend_call_locks["session-a"] = computer_use.threading.RLock()
    computer_use._backend_permission_modes["session-a"] = "bounded"
    computer_use._session_auto_approve["session-a"] = True
    computer_use._always_allow["session-a"] = {("click", "background")}

    assert computer_use.release_computer_use_session("session-a") is True
    assert computer_use.release_computer_use_session("session-a") is False
    backend.stop.assert_called_once_with()
    assert "session-a" not in computer_use._backend_permission_modes
    assert "session-a" not in computer_use._session_auto_approve
    assert "session-a" not in computer_use._always_allow


def test_yolo_toggle_does_not_restart_computer_use_backend():
    from tools import approval

    with patch("tools.computer_use.release_computer_use_session") as release:
        approval.enable_session_yolo("session-a")
        approval.disable_session_yolo("session-a")

    release.assert_not_called()


def test_session_clear_releases_computer_use_backend():
    from tools import approval

    with patch("tools.computer_use.release_computer_use_session") as release:
        approval.clear_session("session-a")

    release.assert_called_once_with("session-a")


def test_unrestricted_embedded_daemon_uses_private_socket_and_two_part_ack():
    from tools.computer_use import cua_backend

    process = Mock()
    process.poll.return_value = None
    process.stderr = []
    process.wait.return_value = 0
    status = SimpleNamespace(returncode=0, stdout="running", stderr="")
    stopped = SimpleNamespace(returncode=0, stdout="", stderr="")

    daemon = cua_backend._EmbeddedCuaDaemon("cua-driver", "unrestricted")
    with patch.object(cua_backend.sys, "platform", "linux"), patch.object(
        cua_backend,
        "_resolve_mcp_invocation",
        return_value=("/opt/cua-driver", ["mcp"]),
    ), patch.object(
        # This test pins the socket/ack contract, not overlay policy. Pin the
        # policy off so the environment-dependent auto-detect (headless CI vs
        # Wayland dev box) can't add a `--help` capability-probe subprocess.run
        # call that the fixed two-entry side_effect below doesn't budget for.
        cua_backend, "_cua_no_overlay", return_value=False,
    ), patch.object(cua_backend.subprocess, "Popen", return_value=process) as popen, patch.object(
        cua_backend.subprocess, "run", side_effect=[status, stopped]
    ):
        daemon.start()
        command = popen.call_args.args[0]
        env = popen.call_args.kwargs["env"]
        proxy_command, proxy_args = daemon.proxy_invocation()
        daemon.stop()

    assert command[:2] == ["/opt/cua-driver", "serve"]
    assert "--embedded" in command
    assert command[command.index("--permission-mode") + 1] == "unrestricted"
    assert "--dangerously-bypass-approvals" in command
    assert env["CUA_DRIVER_PERMISSION_MODE"] == "unrestricted"
    assert env["CUA_DRIVER_DANGEROUSLY_BYPASS_APPROVALS"] == "1"
    assert proxy_command == "/opt/cua-driver"
    assert proxy_args == ["mcp", "--embedded", "--socket", daemon.socket_path]


def test_standard_backend_does_not_spawn_an_embedded_daemon():
    from tools.computer_use.cua_backend import CuaDriverBackend

    standard = CuaDriverBackend(permission_mode="standard")
    unrestricted = CuaDriverBackend(permission_mode="unrestricted")

    assert standard._embedded_daemon is None
    assert unrestricted._embedded_daemon is not None


def test_retired_browser_grant_cannot_change_standard_runtime(tmp_path, monkeypatch):
    from tools.computer_use.cua_backend import _AsyncBridge, _CuaDriverSession

    (tmp_path / "config.yaml").write_text(
        "computer_use:\n  grant_existing_profile: true\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    session = _CuaDriverSession(_AsyncBridge())
    captured = {}

    async def drive_lifecycle():
        def capture_params(**kwargs):
            captured.update(kwargs)
            return MagicMock()

        with patch(
            "tools.computer_use.cua_backend.resolve_cua_driver_cmd",
            return_value="/opt/cua-driver",
        ), patch(
            "tools.computer_use.cua_backend._resolve_mcp_invocation",
            return_value=("/opt/cua-driver", ["mcp"]),
        ), patch(
            "mcp.StdioServerParameters", side_effect=capture_params
        ), patch(
            "mcp.client.stdio.stdio_client"
        ) as stdio_client, patch(
            "mcp.ClientSession"
        ) as client_session:
            stdio_client.return_value.__aenter__ = AsyncMock(
                return_value=(MagicMock(), MagicMock())
            )
            stdio_client.return_value.__aexit__ = AsyncMock(return_value=None)
            live_session = MagicMock()
            live_session.initialize = AsyncMock()
            live_session.list_tools = AsyncMock(return_value=MagicMock(tools=[]))
            client_session.return_value.__aenter__ = AsyncMock(
                return_value=live_session
            )
            client_session.return_value.__aexit__ = AsyncMock(return_value=None)

            async def stop_when_ready():
                while session._shutdown_event is None:
                    await asyncio.sleep(0)
                session._shutdown_event.set()

            stop_task = asyncio.create_task(stop_when_ready())
            try:
                await session._lifecycle_coro()
            finally:
                await stop_task

    asyncio.run(drive_lifecycle())

    assert captured["command"] == "/opt/cua-driver"
    assert captured["args"] == ["mcp"]


def test_transport_reset_invalidates_native_capabilities():
    from tools.computer_use.cua_backend import CuaDriverBackend

    backend = CuaDriverBackend(permission_mode="standard")
    backend._active_pid = 10
    backend._active_window_id = 20
    backend._snapshot_tokens = {1: "old-token"}

    backend._handle_transport_reset()

    assert backend._active_pid is None
    assert backend._active_window_id is None
    assert backend._snapshot_tokens == {}

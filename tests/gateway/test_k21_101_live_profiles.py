"""Live profile membership must reach adapters, status and the cron ticker."""

import asyncio
import shutil
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from gateway.config import GatewayConfig, Platform, PlatformConfig
from gateway.run import GatewayRunner


@pytest.fixture
def contour(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr("korra_constants.get_default_hermes_root", lambda: tmp_path)
    monkeypatch.setattr("korra_cli.profiles.get_active_profile_name", lambda: "default")
    runner = GatewayRunner.__new__(GatewayRunner)
    runner.config = GatewayConfig(multiplex_profiles=True)
    runner._running = True
    runner.adapters = {}
    runner._profile_adapters = {}
    runner._profile_failed_platforms = {}
    runner._background_tasks = set()
    runner.pairing_store = MagicMock()
    runner.pairing_stores = {"default": runner.pairing_store}
    runner._adapter_disconnect_timeout_secs = lambda: 1
    runner._bounded_adapter_teardown = AsyncMock()
    status = {}
    monkeypatch.setattr(
        "gateway.status.write_runtime_status", lambda **kw: status.update(kw)
    )

    async def start_one(name, home, claimed):
        runner._profile_adapters[name] = {Platform.TELEGRAM: MagicMock(token=name)}
        return 1

    runner._start_one_profile_adapters = AsyncMock(side_effect=start_one)
    return runner, tmp_path, status


@pytest.mark.asyncio
async def test_live_add_remove_and_repeated_scan_are_idempotent(contour):
    runner, home, status = contour
    await runner._start_secondary_profile_adapters()
    worker = home / "profiles" / "worker"
    worker.mkdir(parents=True)
    assert await runner._start_secondary_profile_adapters() == 1
    adapter = runner._profile_adapters["worker"][Platform.TELEGRAM]
    assert status["served_profiles"] == ["default", "worker"]
    assert await runner._start_secondary_profile_adapters() == 0
    assert runner._profile_adapters["worker"][Platform.TELEGRAM] is adapter
    runner._start_one_profile_adapters.assert_awaited_once()

    shutil.rmtree(worker)
    await runner._start_secondary_profile_adapters()
    assert status["served_profiles"] == ["default"]
    assert "worker" not in runner._profile_adapters
    assert "worker" not in runner.pairing_stores
    runner._bounded_adapter_teardown.assert_awaited_once_with(
        adapter, Platform.TELEGRAM, profile="worker"
    )
    assert not worker.exists()


@pytest.mark.asyncio
async def test_live_membership_preserves_allowlist(contour):
    runner, home, status = contour
    runner.config.multiplex_profile_allowlist = ["worker"]
    await runner._start_secondary_profile_adapters()
    for name in ("worker", "outside"):
        (home / "profiles" / name).mkdir(parents=True)
    await runner._start_secondary_profile_adapters()
    assert status["served_profiles"] == ["default", "worker"]
    assert set(runner._profile_adapters) == {"worker"}


@pytest.mark.asyncio
async def test_removed_between_scan_and_start_is_not_recreated(contour, monkeypatch):
    runner, home, status = contour
    # The scan saw this home, but DELETE completed before its result reached
    # the event loop. Startup must not enter the profile's mkdir-capable scope.
    stale = [("default", home), ("worker", home / "profiles" / "worker")]
    monkeypatch.setattr(
        "gateway.profile_lifecycle.profile_homes_snapshot", lambda config: stale
    )
    await runner._start_secondary_profile_adapters()
    runner._start_one_profile_adapters.assert_not_awaited()
    assert status["served_profiles"] == ["default"]
    assert not stale[1][1].exists()


@pytest.mark.asyncio
async def test_publication_lock_keeps_half_created_profile_out(contour):
    import threading
    from korra_cli.profiles import profile_creation_lock

    runner, home, status = contour
    entered, release = threading.Event(), threading.Event()

    def unfinished_create():
        with profile_creation_lock():
            worker = home / "profiles" / "worker"
            worker.mkdir(parents=True)
            entered.set()
            release.wait(timeout=5)
            (worker / "config.yaml").write_text("{}")

    creator = asyncio.create_task(asyncio.to_thread(unfinished_create))
    reconcile = None
    try:
        assert await asyncio.to_thread(entered.wait, 2)
        reconcile = asyncio.create_task(runner._start_secondary_profile_adapters())
        await asyncio.sleep(0.05)
        runner._start_one_profile_adapters.assert_not_awaited()
        release.set()
        await asyncio.wait_for(reconcile, 2)
        assert status["served_profiles"] == ["default", "worker"]
    finally:
        release.set()
        await creator
        if reconcile is not None and not reconcile.done():
            reconcile.cancel()
            await asyncio.gather(reconcile, return_exceptions=True)


@pytest.mark.asyncio
async def test_watcher_discovers_new_profile_without_manual_reconcile(
    contour, monkeypatch
):
    from gateway.profile_lifecycle import watch_profiles, stop_profile_watcher

    runner, home, status = contour
    monkeypatch.setattr("gateway.profile_lifecycle.PROFILE_SCAN_SECONDS", 0.01)
    task = asyncio.create_task(watch_profiles(runner))
    runner._profile_watcher_task = task
    try:
        async with asyncio.timeout(3):
            while not status.get("served_profiles"):
                await asyncio.sleep(0.01)
            (home / "profiles" / "worker").mkdir(parents=True)
            while "worker" not in status["served_profiles"]:
                await asyncio.sleep(0.01)
        assert "worker" in runner._profile_adapters
    finally:
        runner._running = False
        await stop_profile_watcher(runner)
    assert task.done()
    assert runner._live_profile_generations == {}


@pytest.mark.asyncio
async def test_deleted_profile_callbacks_cannot_reach_recreated_profile(contour):
    runner, home, _ = contour
    worker = home / "profiles" / "worker"
    worker.mkdir(parents=True)
    await runner._start_secondary_profile_adapters()
    old_message = runner._make_profile_message_handler("worker")
    old_busy = runner._make_profile_busy_session_handler("worker")
    old_event = runner._make_profile_platform_event_handler("worker")
    runner._handle_message = AsyncMock()
    runner._handle_active_session_busy_message = AsyncMock()
    runner._handle_gateway_platform_event = AsyncMock()

    worker.rename(home / "removed-worker")
    worker.mkdir()
    await runner._start_secondary_profile_adapters()
    event = SimpleNamespace(source=SimpleNamespace(profile=None))
    await old_message(event)
    await old_busy(event, "old-session")
    await old_event({}, event.source)
    runner._handle_message.assert_not_awaited()
    runner._handle_active_session_busy_message.assert_not_awaited()
    runner._handle_gateway_platform_event.assert_not_awaited()


def install_bot_factory(runner, monkeypatch, *, token="own-token"):
    from gateway.profile_lifecycle import profile_generation

    created = []
    cfg = PlatformConfig(enabled=True, token=token)
    monkeypatch.setattr(
        "gateway.config.load_gateway_config",
        lambda: GatewayConfig(
            multiplex_profiles=True,
            platforms={Platform.DISCORD: cfg},
        ),
    )
    monkeypatch.setattr("gateway.run._load_gateway_runtime_config", lambda: {})
    monkeypatch.setattr("korra_cli.plugins.discover_plugins", lambda: None)

    def factory(platform, config):
        adapter = SimpleNamespace(
            config=config,
            fatal_error_retryable=True,
            has_fatal_error=False,
            disconnect=AsyncMock(),
        )
        created.append(adapter)
        return adapter

    runner._create_adapter = factory
    runner._configure_profile_adapter = lambda adapter, name, platform: setattr(
        adapter, "_korra_profile_generation", profile_generation(runner, name)
    )

    async def disconnect(adapter, platform):
        await adapter.disconnect()

    runner._safe_adapter_disconnect = AsyncMock(side_effect=disconnect)
    runner._update_platform_runtime_status = MagicMock()
    runner._sync_voice_mode_state_to_adapter = lambda adapter: None
    runner._redeliver_failed_obligations_for_platform = AsyncMock()
    return created


@pytest.mark.asyncio
async def test_late_reconnect_cannot_revive_deleted_generation(contour, monkeypatch):
    runner, home, _ = contour
    worker = home / "profiles" / "worker"
    worker.mkdir(parents=True)
    await runner._start_secondary_profile_adapters()
    runner._profile_adapters["worker"] = {}
    created = install_bot_factory(runner, monkeypatch)
    entered, release = asyncio.Event(), asyncio.Event()

    async def connect(adapter, platform, **kw):
        entered.set()
        try:
            await release.wait()
        except asyncio.CancelledError:
            # A transport can finish late even after cancellation.
            await release.wait()
        return True

    runner._connect_adapter_with_timeout = connect
    task = asyncio.create_task(
        runner._run_secondary_profile_reconnect("worker", Platform.DISCORD)
    )
    runner._profile_failed_platforms["worker"] = {Platform.DISCORD: task}
    try:
        await asyncio.wait_for(entered.wait(), timeout=2)
        worker.rename(home / "removed-worker")
        worker.mkdir()
        runner._adapter_disconnect_timeout_secs = lambda: 0.01
        await runner._start_secondary_profile_adapters()
        new_adapter = runner._profile_adapters["worker"][Platform.TELEGRAM]
    finally:
        release.set()
        await asyncio.wait_for(task, timeout=2)
    assert runner._profile_adapters["worker"] == {Platform.TELEGRAM: new_adapter}
    created[0].disconnect.assert_awaited_once()
    assert runner._profile_failed_platforms == {}
    assert runner._connecting_profile_adapters == {}


@pytest.mark.asyncio
async def test_new_profile_cannot_duplicate_existing_secondary_bot(
    contour, monkeypatch
):
    runner, home, _ = contour
    await runner._start_secondary_profile_adapters()
    created = install_bot_factory(runner, monkeypatch, token="shared-test-token")
    runner._start_one_profile_adapters = (
        GatewayRunner._start_one_profile_adapters.__get__(runner)
    )
    runner._connect_initial_adapter_with_timeout = AsyncMock(return_value=True)
    for name in ("first", "second"):
        (home / "profiles" / name).mkdir(parents=True)
        await runner._start_secondary_profile_adapters()
    runner._connect_initial_adapter_with_timeout.assert_awaited_once()
    assert runner._profile_adapters["second"] == {}
    created[1].disconnect.assert_not_awaited()
    assert (
        runner._update_platform_runtime_status.call_args.kwargs["error_code"]
        == "duplicate_credential"
    )


def test_served_membership_removes_obsolete_platform_health(tmp_path, monkeypatch):
    from gateway.status import write_runtime_status, read_runtime_status

    monkeypatch.setattr(
        "gateway.status._get_runtime_status_path", lambda: tmp_path / "state.json"
    )
    write_runtime_status(platform="default:telegram", platform_state="connected")
    write_runtime_status(platform="worker:discord", platform_state="fatal")
    write_runtime_status(served_profiles=["default", "worker"])
    assert "worker:discord" in read_runtime_status()["platforms"]
    write_runtime_status(served_profiles=["default"])
    assert set(read_runtime_status()["platforms"]) == {"default:telegram"}


@pytest.mark.asyncio
async def test_api_create_becomes_served_and_delete_is_seen_without_restart(
    contour, monkeypatch
):
    import os
    from starlette.testclient import TestClient
    import gateway.status as gateway_status
    from gateway.profile_lifecycle import watch_profiles, stop_profile_watcher
    from korra_cli.profiles import resolve_profile_gateway_status
    from korra_cli.web_server import app, _SESSION_HEADER_NAME, _SESSION_TOKEN

    runner, home, _ = contour
    (home / "config.yaml").write_text("gateway:\n  multiplex_profiles: true\n")
    # Creation/deletion use the real API and real disk state. Only host service
    # hooks and optional bundle seeding are isolated from the developer machine.
    for hook in (
        "_maybe_register_gateway_service",
        "_maybe_unregister_gateway_service",
        "_cleanup_gateway_service",
        "_stop_profile_backends",
        "seed_profile_skills",
    ):
        monkeypatch.setattr(f"korra_cli.profiles.{hook}", lambda *args, **kwargs: None)
    monkeypatch.setattr("korra_cli.profiles._get_wrapper_dir", lambda: home / "bin")
    # Retain the real runtime status writer rather than contour's recorder.
    import importlib

    gateway_status = importlib.reload(gateway_status)
    monkeypatch.setattr(
        gateway_status, "_get_runtime_status_path", lambda: home / "gateway_state.json"
    )
    real_cmdline = gateway_status._read_process_cmdline
    monkeypatch.setattr(
        gateway_status,
        "_read_process_cmdline",
        lambda pid: (
            "python -m korra_cli.main gateway run"
            if pid == os.getpid()
            else real_cmdline(pid)
        ),
    )
    gateway_status._clear_running_pid_cache()
    gateway_status.write_runtime_status(
        gateway_state="running", served_profiles=["default"]
    )
    monkeypatch.setattr("gateway.profile_lifecycle.PROFILE_SCAN_SECONDS", 0.01)
    runner._start_one_profile_adapters = (
        GatewayRunner._start_one_profile_adapters.__get__(runner)
    )
    runner._profile_watcher_task = asyncio.create_task(watch_profiles(runner))
    client = TestClient(app)
    client.headers[_SESSION_HEADER_NAME] = _SESSION_TOKEN
    try:
        response = await asyncio.to_thread(
            client.post,
            "/api/profiles",
            json={
                "name": "live-worker",
                "display_name": "Тестовый агент",
                "no_skills": True,
            },
        )
        assert response.status_code == 200, response.text
        worker = home / "profiles" / "live-worker"
        async with asyncio.timeout(5):
            while resolve_profile_gateway_status(worker) != "served":
                await asyncio.sleep(0.01)
        response = await asyncio.to_thread(client.get, "/api/profiles")
        listed = {profile["name"]: profile for profile in response.json()["profiles"]}
        assert listed["live-worker"]["gateway_status"] == "served"
        response = await asyncio.to_thread(client.delete, "/api/profiles/live-worker")
        assert response.status_code == 200, response.text
        async with asyncio.timeout(5):
            while (
                "live-worker" in gateway_status.read_runtime_status()["served_profiles"]
            ):
                await asyncio.sleep(0.01)
        assert not worker.exists()
    finally:
        runner._running = False
        await stop_profile_watcher(runner)
        client.close()
        gateway_status._clear_running_pid_cache()


@pytest.mark.asyncio
async def test_reconnect_cannot_steal_bot_claimed_during_failure(contour, monkeypatch):
    runner, home, _ = contour
    (home / "profiles" / "worker").mkdir(parents=True)
    await runner._start_secondary_profile_adapters()
    created = install_bot_factory(runner, monkeypatch)
    runner._profile_adapters["worker"] = {}
    runner._profile_adapters["other"] = {
        Platform.DISCORD: SimpleNamespace(
            config=PlatformConfig(enabled=True, token="own-token"),
        )
    }
    runner._connect_adapter_with_timeout = AsyncMock(return_value=True)
    await runner._run_secondary_profile_reconnect("worker", Platform.DISCORD)
    runner._connect_adapter_with_timeout.assert_not_awaited()
    created[0].disconnect.assert_not_awaited()
    assert (
        runner._update_platform_runtime_status.call_args.kwargs["error_code"]
        == "duplicate_credential"
    )

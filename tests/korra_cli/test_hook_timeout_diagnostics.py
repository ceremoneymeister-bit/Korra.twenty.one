"""Timeout workers must retain their own result, context and attribution."""

import asyncio
import contextvars
import logging
import threading

import pytest
import yaml

from korra_cli import plugins


@pytest.mark.parametrize("late_failure", [False, True])
def test_late_worker_cannot_complete_next_callback(monkeypatch, late_failure):
    """Event ordering reproduces the reviewer's probe without elapsed-time bounds."""
    monkeypatch.setattr(plugins, "_resolve_hook_callback_timeout", lambda: 0.15)
    second_started = threading.Event()
    release_second = threading.Event()
    first_finished = threading.Event()
    manager = plugins.PluginManager()

    def first(**kwargs):
        assert second_started.wait(10)
        first_finished.set()
        if late_failure:
            raise RuntimeError("late-first")
        return "late-first"

    def second(**kwargs):
        second_started.set()
        assert first_finished.wait(10)
        assert release_second.wait(10)
        return "second"

    manager._hooks["post_tool_call"] = [first, second]
    try:
        assert manager.invoke_hook("post_tool_call") == []
        assert first_finished.wait(10)
        # The second callback is still held, even though the first has finished.
        assert ("post_tool_call", id(second)) in manager._hook_running_callbacks
        assert ("post_tool_call", id(second)) in manager._hook_timeout_suppressed_until
    finally:
        run = manager._hook_running_callbacks.get(("post_tool_call", id(second)))
        release_second.set()
        done = getattr(run, "done", None)
        if done is not None:
            assert done.wait(10)


def test_discovered_hook_reports_owner_profile_and_recovery(tmp_path, monkeypatch, caplog):
    home = tmp_path / "profiles" / "test-agent"
    plugin_dir = home / "plugins" / "diagnostic-probe"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "plugin.yaml").write_text(yaml.safe_dump({
        "name": "diagnostic-probe", "version": "0.1.0", "description": "Test hook",
    }))
    (plugin_dir / "__init__.py").write_text(
        "import threading\n"
        "release = threading.Event()\n"
        "starts = []\n"
        "def _on_post_tool_call(tool_name='', **kwargs):\n"
        "    starts.append(tool_name)\n"
        "    assert release.wait(10)\n"
        "    return 'ok'\n"
        "def register(ctx):\n"
        "    ctx.register_hook('post_tool_call', _on_post_tool_call)\n"
    )
    (home / "config.yaml").write_text(yaml.safe_dump({
        "plugins": {"enabled": ["diagnostic-probe"], "hook_callback_timeout": 0.05},
    }))
    bundled = tmp_path / "empty-bundled"
    bundled.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HERMES_BUNDLED_PLUGINS", str(bundled))
    monkeypatch.setenv("HOME", str(tmp_path / "os-home"))
    manager = plugins.PluginManager()
    manager.discover_and_load()
    loaded = manager._plugins["diagnostic-probe"]
    assert loaded.error is None
    module = loaded.module
    callback = module._on_post_tool_call
    key = ("post_tool_call", id(callback))
    caplog.set_level(logging.INFO, logger=plugins.__name__)
    caplog.clear()
    try:
        assert manager.invoke_hook("post_tool_call", args={"secret": "PAYLOAD-SECRET"}) == []
        assert manager.invoke_hook("post_tool_call") == []
        records_after_first_skip = len(caplog.records)
        for _ in range(20):
            assert manager.invoke_hook("post_tool_call") == []
        assert len(caplog.records) == records_after_first_skip
        assert len(module.starts) == 1
        run = manager._hook_running_callbacks[key]
        # Even after the cooldown expires, a held callback cannot run in parallel.
        with manager._hook_timeout_lock:
            original_deadline = manager._hook_timeout_suppressed_until[key]
            manager._hook_timeout_suppressed_until[key] = 0
        assert manager.invoke_hook("post_tool_call") == []
        assert len(module.starts) == 1
        with manager._hook_timeout_lock:
            manager._hook_timeout_suppressed_until[key] = original_deadline
        # Ambient profile changes cannot relabel an already-running callback.
        monkeypatch.setenv("HERMES_HOME", str(tmp_path / "other-profile"))
        module.release.set()
        assert run.done.wait(10)
        recovery = [r for r in caplog.records if "finished after timeout" in r.message]
        assert len(recovery) == 1
        assert "skipped=22" in recovery[0].message
        assert "elapsed=" in recovery[0].message
        assert str(home) in recovery[0].message
        assert all("plugin=diagnostic-probe" in r.message for r in caplog.records)
        assert "PAYLOAD-SECRET" not in caplog.text
        # Completion keeps the original cooldown; retry happens only after expiry.
        assert manager._hook_timeout_suppressed_until[key] == original_deadline
        assert manager.invoke_hook("post_tool_call") == []
        assert len(module.starts) == 1
        with manager._hook_timeout_lock:
            manager._hook_timeout_suppressed_until[key] = 0
        assert manager.invoke_hook("post_tool_call", tool_name="retried") == ["ok"]
        assert module.starts == ["", "retried"]
        assert "resumed after timeout cooldown" in caplog.text
    finally:
        module.release.set()
        manager.unload()


def test_worker_keeps_context_and_legacy_signature(monkeypatch):
    monkeypatch.setattr(plugins, "_resolve_hook_callback_timeout", lambda: 1)
    marker = contextvars.ContextVar("hook-test-marker")
    marker.set("caller-context")
    manager = plugins.PluginManager()

    def narrow(tool_name):
        return marker.get(), tool_name

    manager._hooks["post_tool_call"] = [narrow]
    assert manager.invoke_hook("post_tool_call", tool_name="terminal", future_field=True) == [
        ("caller-context", "terminal")
    ]


@pytest.mark.parametrize("timeout", [0, 1])
def test_async_callback_preserves_sync_dispatch_contract(monkeypatch, timeout):
    """invoke_hook returns coroutine objects; callers own awaiting them."""
    monkeypatch.setattr(plugins, "_resolve_hook_callback_timeout", lambda: timeout)
    manager = plugins.PluginManager()
    seen = []

    async def callback(tool_name):
        seen.append(tool_name)
        return "awaited"

    manager._hooks["post_tool_call"] = [callback]
    results = manager.invoke_hook("post_tool_call", tool_name="terminal")
    assert seen == []
    assert len(results) == 1
    assert asyncio.run(results[0]) == "awaited"
    assert seen == ["terminal"]


def test_hook_exception_diagnostic_does_not_include_payload(monkeypatch, caplog):
    monkeypatch.setattr(plugins, "_resolve_hook_callback_timeout", lambda: 1)
    manager = plugins.PluginManager()

    def callback(**kwargs):
        raise ValueError("PAYLOAD-SECRET")

    manager._hooks["post_tool_call"] = [callback]
    assert manager.invoke_hook("post_tool_call") == []
    assert "ValueError" in caplog.text
    assert "PAYLOAD-SECRET" not in caplog.text


def test_persistent_hang_reports_updated_duration_at_next_interval(monkeypatch, caplog):
    monkeypatch.setattr(plugins, "_resolve_hook_callback_timeout", lambda: 0.05)
    release = threading.Event()
    manager = plugins.PluginManager()

    def callback(**kwargs):
        assert release.wait(10)

    key = ("post_tool_call", id(callback))
    manager._hooks["post_tool_call"] = [callback]
    try:
        assert manager.invoke_hook("post_tool_call") == []
        assert manager.invoke_hook("post_tool_call") == []
        run = manager._hook_running_callbacks[key]
        warnings = len(caplog.records)
        assert manager.invoke_hook("post_tool_call") == []
        assert len(caplog.records) == warnings
        # Advance diagnostic age/interval without waiting a real minute.
        with manager._hook_timeout_lock:
            run.started_at -= 120
            run.next_skip_warning = 0
        assert manager.invoke_hook("post_tool_call") == []
        assert len(caplog.records) == warnings + 1
        assert "skipped=3" in caplog.records[-1].message
        elapsed = float(caplog.records[-1].message.split("elapsed=")[1].split("s")[0])
        assert elapsed >= 120
    finally:
        run = manager._hook_running_callbacks.get(key)
        release.set()
        done = getattr(run, "done", None)
        if done is not None:
            assert done.wait(10)


def test_completion_racing_timeout_is_reported_once(monkeypatch, caplog):
    monkeypatch.setattr(plugins, "_resolve_hook_callback_timeout", lambda: 0.05)
    release = threading.Event()
    run_factory = plugins._HookCallbackRun

    class CompleteAtDeadline(threading.Event):
        def wait(self, timeout=None):
            release.set()
            assert super().wait(10)
            # Reproduce completion after wait's deadline but before accounting.
            return False

    def new_run():
        run = run_factory()
        run.done = CompleteAtDeadline()
        return run

    monkeypatch.setattr(plugins, "_HookCallbackRun", new_run)
    manager = plugins.PluginManager()

    def callback(**kwargs):
        assert release.wait(10)
        raise ValueError("PAYLOAD-SECRET")

    manager._hooks["post_tool_call"] = [callback]
    caplog.set_level(logging.INFO, logger=plugins.__name__)
    assert manager.invoke_hook("post_tool_call") == []
    assert not manager._hook_running_callbacks
    assert len([r for r in caplog.records if "timed out after" in r.message]) == 1
    recovery = [r for r in caplog.records if "finished after timeout" in r.message]
    assert len(recovery) == 1
    assert "outcome=ValueError" in recovery[0].message
    assert "PAYLOAD-SECRET" not in caplog.text


def test_same_callback_name_uses_effective_plugin_ids_and_disposal(monkeypatch, caplog):
    monkeypatch.setattr(plugins, "_resolve_hook_callback_timeout", lambda: 1)
    manager = plugins.PluginManager()

    def callback(**kwargs):
        raise RuntimeError("PAYLOAD-SECRET")

    def other_callback(**kwargs):
        raise TypeError("PAYLOAD-SECRET")

    callback.__qualname__ = other_callback.__qualname__ = "_on_post_tool_call"
    first = plugins.PluginContext(plugins.PluginManifest(name="first", key="vendor/first"), manager)
    second = plugins.PluginContext(plugins.PluginManifest(name="second"), manager)
    handle = first.register_hook("post_tool_call", callback)
    second.register_hook("post_tool_call", other_callback)
    assert manager.invoke_hook("post_tool_call") == []
    assert "plugin=vendor/first" in caplog.text
    assert "plugin=second" in caplog.text
    handle.dispose()
    caplog.clear()
    assert manager.invoke_hook("post_tool_call") == []
    assert "plugin=vendor/first" not in caplog.text
    assert "plugin=second" in caplog.text
    assert "PAYLOAD-SECRET" not in caplog.text

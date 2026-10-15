"""The board's exact queue needs a live gateway, just like its general queue."""

import pytest

from korra_cli import kanban_db as kb
from korra_cli.kanban import _check_dispatcher_presence
from gateway.status import GatewayLiveness


@pytest.mark.parametrize("gateway_up", [False, True])
@pytest.mark.parametrize("worker_profile", [False, True])
def test_exact_queue_uses_the_board_owner_gateway_and_config(tmp_path, monkeypatch, gateway_up, worker_profile):
    home = tmp_path / "hermes"
    worker = home / "profiles" / "pm"
    worker.mkdir(parents=True)
    (home / "config.yaml").write_text("kanban:\n  dispatch_in_gateway: false\n  dispatch_exact_requests: true\n")
    (worker / "config.yaml").write_text("kanban:\n  dispatch_in_gateway: false\n  dispatch_exact_requests: false\n")
    monkeypatch.setenv("HERMES_HOME", str(worker if worker_profile else home))
    monkeypatch.delenv("KORRA_KANBAN_HOME", raising=False)
    monkeypatch.delenv("HERMES_KANBAN_HOME", raising=False)
    kb._EXACT_CONFIG_CACHE.clear()
    probed = []

    def probe(*, profile_dir, use_cache):
        assert use_cache is False
        probed.append(profile_dir)
        return GatewayLiveness(running=gateway_up, pid=4242 if gateway_up else None, source="test")

    monkeypatch.setattr("gateway.status.resolve_gateway_liveness", probe)
    running, message = _check_dispatcher_presence()
    assert running is gateway_up
    assert probed == [home]
    if gateway_up:
        assert "exact" in message
    else:
        assert "не запущен" in message

    # Exercise the same probe through the creation contract, with real owner
    # settings and a real durable request, including from an executor profile.
    from tools import kanban_tools as kt

    kb._INITIALIZED_PATHS.clear()
    kb.init_db()
    with kb.connect_closing() as conn:
        tid = kb.create_task(conn, title="one requested step", assignee="pm")
        execution = kt._created_card_execution(kb, conn, kb.get_task(conn, tid), subscribed=True)
        assert execution["state"] == ("queued" if gateway_up else "no_dispatcher")
        assert execution["will_start"] is gateway_up and execution["started"] is False
        assert kb.pending_exact_dispatch_ids(conn) == [tid]
    assert probed == [home, home]


def test_disabled_queues_do_not_report_a_live_gateway_as_a_dispatcher(tmp_path, monkeypatch):
    home = tmp_path / "hermes"
    home.mkdir()
    (home / "config.yaml").write_text("kanban:\n  dispatch_in_gateway: false\n  dispatch_exact_requests: false\n")
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.delenv("KORRA_KANBAN_HOME", raising=False)
    monkeypatch.delenv("HERMES_KANBAN_HOME", raising=False)
    kb._EXACT_CONFIG_CACHE.clear()
    monkeypatch.setattr("gateway.status.resolve_gateway_liveness", lambda **kw: GatewayLiveness(
        running=True, pid=4242, source="test",
    ))
    running, message = _check_dispatcher_presence()
    assert running is False
    assert "выключен" in message


def test_explicit_gateway_home_is_preserved(tmp_path, monkeypatch):
    home = tmp_path / "hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    target = home / "profiles" / "target"
    probed = []

    def probe(*, profile_dir, **kw):
        probed.append(profile_dir)
        return GatewayLiveness(running=True, pid=4242, source="test")

    monkeypatch.setattr("gateway.status.resolve_gateway_liveness", probe)
    assert _check_dispatcher_presence(hermes_home=target)[0] is True
    assert probed == [target]

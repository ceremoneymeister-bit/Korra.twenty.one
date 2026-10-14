"""K21-277: completed secondary-profile work must return to its own chat.

Real dispatch, SQLite stores, process restart and gateway watcher; only the
receiving adapter/model turn is replaced. No client transcripts or credentials.
"""

import asyncio
import json
import os
import queue
import subprocess
import sys
import threading
import time
from collections import OrderedDict
from types import SimpleNamespace

import pytest

import korra_state
from gateway.config import GatewayConfig, Platform
from gateway.run import GatewayRunner, _SESSION_DB_UNPINNED, _profile_runtime_scope
from gateway.session import SessionStore
from korra_constants import get_hermes_home
from tools import async_delegation as ad
from tools import process_registry as pr


@pytest.fixture
def fleet(tmp_path, monkeypatch):
    root = tmp_path / "hermes"
    profile = root / "profiles" / "worker"
    profile.mkdir(parents=True)
    monkeypatch.setenv("HERMES_HOME", str(root))
    monkeypatch.setattr(korra_state, "DEFAULT_DB_PATH", korra_state._IMPORT_DEFAULT_DB_PATH)
    monkeypatch.setattr(pr.process_registry, "completion_queue", queue.Queue())
    ad._reset_for_tests()
    cfg = GatewayConfig(multiplex_profiles=True)
    runner = object.__new__(GatewayRunner)
    runner.config = cfg
    runner.session_store = SessionStore(sessions_dir=root / "sessions", config=cfg)
    runner._session_db_pinned = _SESSION_DB_UNPINNED
    runner._session_db_handles = {}
    runner._session_db_handles_lock = threading.Lock()
    runner._session_sources = {}
    runner._completion_delivery_lock = threading.Lock()
    runner._completion_deliveries_inflight = set()
    runner._completion_deliveries_delivered = OrderedDict()
    runner._completion_delivery_retention = 2048
    runner._background_tasks = set()
    runner._running = True
    with _profile_runtime_scope(profile):
        runner.session_store._db.create_session("parent", source="dashboard")
    # A same-id root session must not influence the profile's verdict.
    runner.session_store._db.create_session("parent", source="cli")
    runner.session_store._db.end_session("parent", "session_reset")
    yield root, profile, runner
    if hasattr(runner, "_session_db_handle_cache"):
        runner.close_all_session_db_handles()
    runner.session_store.close_all_db_handles()
    ad._reset_for_tests()


def _run_watcher(monkeypatch, runner, ticks=2):
    calls = 0

    async def sleep(_delay):
        nonlocal calls
        calls += 1
        if calls >= ticks:
            runner._running = False

    monkeypatch.setattr(asyncio, "sleep", sleep)
    asyncio.run(runner._async_delegation_watcher(interval=0))


def _dispatch(profile, *, web, restarted, batch=False):
    key = "parent" if web else "agent:worker:telegram:dm:chat-1"
    if restarted:
        # Dispatch in a separate process, exit after durable completion. Its
        # process-local queue disappears; the watcher must recover this store.
        code = """
import json,time
from tools import async_delegation as ad
dispatch=ad.dispatch_async_delegation_batch if BATCH else ad.dispatch_async_delegation
task={'goals':['test task','second task']} if BATCH else {'goal':'test task'}
result={'results':[{'status':'completed','summary':'test result'},
                   {'status':'completed','summary':'second result'}]} if BATCH else {'status':'completed','summary':'test result'}
r=dispatch(**task, session_key=KEY,
    context=None, toolsets=None, role='leaf', model=None,
    origin_session_id=RAW, parent_session_id='parent',
    runner=lambda: result)
deadline=time.monotonic()+5
while time.monotonic()<deadline:
    row=ad.get_durable_delegation(r['delegation_id'])
    if row['state']=='completed':
        # Simulate an upgrade from a producer without profile stamps: the
        # containing store is the authoritative owner during restoration.
        with ad._transaction() as conn:
            event=json.loads(conn.execute('SELECT event_json FROM async_delegations WHERE delegation_id=?',(r['delegation_id'],)).fetchone()[0])
            event.pop('origin_profile',None)
            conn.execute('UPDATE async_delegations SET event_json=? WHERE delegation_id=?',(json.dumps(event),r['delegation_id']))
        print(json.dumps(r));break
    time.sleep(.01)
else: raise RuntimeError('worker did not finish')
""".replace("KEY", repr(key)).replace("RAW", repr("parent" if web else "")).replace("BATCH", repr(batch))
        env = dict(os.environ, HERMES_HOME=str(profile), PYTHONDONTWRITEBYTECODE="1")
        result = subprocess.run([sys.executable, "-c", code], env=env,
                                capture_output=True, text=True, timeout=15, check=True)
        return json.loads(result.stdout)["delegation_id"]
    with _profile_runtime_scope(profile):
        dispatch = ad.dispatch_async_delegation_batch if batch else ad.dispatch_async_delegation
        task = {"goals": ["test task", "second task"]} if batch else {"goal": "test task"}
        payload = {"results": [{"status": "completed", "summary": "test result"},
                               {"status": "completed", "summary": "second result"}]} if batch else {
            "status": "completed", "summary": "test result",
        }
        result = dispatch(
            **task, session_key=key, origin_session_id="parent" if web else "",
            context=None, toolsets=None, role="leaf", model=None,
            parent_session_id="parent",
            runner=lambda: payload,
        )
        deadline = time.monotonic() + 5
        while ad.active_count():
            assert time.monotonic() < deadline
            time.sleep(.01)
    return result["delegation_id"]


@pytest.mark.parametrize("web", [False, True], ids=["telegram", "web"])
@pytest.mark.parametrize("restarted", [False, True], ids=["live", "restart"])
@pytest.mark.parametrize("batch", [False, True], ids=["single", "batch"])
def test_secondary_completion_returns_to_own_store_and_transport(fleet, monkeypatch, web, restarted, batch):
    root, profile, runner = fleet
    delivered = []

    async def accept(event):
        delivered.append((get_hermes_home(), event.source.profile, event.text))

    async def wake(adapter, *, text, session_id, source=None):
        delivered.append((get_hermes_home(), session_id, text))

    primary = SimpleNamespace(handle_message=lambda _: pytest.fail("wrong bot"))
    secondary = SimpleNamespace(handle_message=accept)
    api = SimpleNamespace(supports_async_delivery=False)
    runner.adapters = {Platform.TELEGRAM: primary, Platform.API_SERVER: api}
    runner._profile_adapters = {"worker": {Platform.TELEGRAM: secondary}}
    monkeypatch.setattr("gateway.wake.deliver_wake", wake)
    did = _dispatch(profile, web=web, restarted=restarted, batch=batch)
    _run_watcher(monkeypatch, runner)
    assert len(delivered) == 1
    assert delivered[0][:2] == (profile, "parent" if web else "worker")
    assert "test result" in delivered[0][2]
    with _profile_runtime_scope(profile):
        row = ad.get_durable_delegation(did)
        assert row["delivery_state"] == "delivered"
        assert row["delivery_attempts"] == 1
    assert ad.get_durable_delegation(did) is None
    assert get_hermes_home() == root


def test_explicit_reset_drops_in_correct_profile(fleet, monkeypatch):
    _root, profile, runner = fleet
    did = _dispatch(profile, web=False, restarted=False)
    with _profile_runtime_scope(profile):
        runner.session_store._db.end_session("parent", "session_reset")
    runner.adapters = {}
    runner._profile_adapters = {}
    _run_watcher(monkeypatch, runner)
    with _profile_runtime_scope(profile):
        row = ad.get_durable_delegation(did)
        assert row["delivery_state"] == "dropped"
        assert row["result"]["summary"] == "test result"


def test_failed_transport_retries_in_profile(fleet, monkeypatch):
    _root, profile, runner = fleet
    attempts = []

    async def accept(event):
        attempts.append(get_hermes_home())
        if len(attempts) == 1:
            raise RuntimeError("temporary test transport failure")

    runner.adapters = {}
    runner._profile_adapters = {"worker": {Platform.TELEGRAM: SimpleNamespace(handle_message=accept)}}
    did = _dispatch(profile, web=False, restarted=False)
    _run_watcher(monkeypatch, runner, ticks=3)
    assert attempts == [profile, profile]
    with _profile_runtime_scope(profile):
        row = ad.get_durable_delegation(did)
        assert row["delivery_state"] == "delivered"
        assert row["delivery_attempts"] == 2


def test_unserved_profile_cannot_borrow_root_delivery(fleet, monkeypatch):
    _root, profile, runner = fleet
    did = _dispatch(profile, web=True, restarted=False)
    runner.config.multiplex_profile_allowlist = []
    runner.adapters = {}
    runner._profile_adapters = {}
    _run_watcher(monkeypatch, runner)
    with _profile_runtime_scope(profile):
        row = ad.get_durable_delegation(did)
        assert row["delivery_state"] == "pending"
        assert row["delivery_attempts"] == 0
    assert not pr.process_registry.completion_queue.empty()


def test_unreadable_profile_does_not_block_other_completions(fleet, monkeypatch):
    _root, profile, runner = fleet
    broken = profile.parent / "broken"
    broken.mkdir()
    (broken / "state.db").write_bytes(b"not a SQLite database")
    did = _dispatch(profile, web=False, restarted=True)
    seen = []

    async def accept(event):
        seen.append(event.text)

    runner.adapters = {}
    runner._profile_adapters = {"worker": {Platform.TELEGRAM: SimpleNamespace(handle_message=accept)}}
    _run_watcher(monkeypatch, runner)
    assert len(seen) == 1
    with _profile_runtime_scope(profile):
        assert ad.get_durable_delegation(did)["delivery_state"] == "delivered"


def test_same_raw_session_ids_in_different_profiles_do_not_coalesce(fleet, monkeypatch):
    _root, profile, runner = fleet
    other = profile.parent / "other"
    other.mkdir()
    with _profile_runtime_scope(other):
        runner.session_store._db.create_session("parent", source="dashboard")
    ids = [_dispatch(home, web=True, restarted=False) for home in (profile, other)]
    seen = []

    async def wake(adapter, *, text, session_id, source=None):
        seen.append((get_hermes_home(), session_id, text))

    monkeypatch.setattr("gateway.wake.deliver_wake", wake)
    runner.adapters = {Platform.API_SERVER: SimpleNamespace(supports_async_delivery=False)}
    runner._profile_adapters = {}
    _run_watcher(monkeypatch, runner)
    assert [item[:2] for item in seen] == [(profile, "parent"), (other, "parent")]
    for home, did in zip((profile, other), ids):
        with _profile_runtime_scope(home):
            assert ad.get_durable_delegation(did)["delivery_state"] == "delivered"


def test_restart_reports_unrecorded_outcome_without_rerunning_task(fleet, monkeypatch):
    _root, profile, runner = fleet
    # The previous process dies while its worker is running. There is no
    # recorded result to replay; report unknown rather than inventing success.
    code = """
import time
from tools import async_delegation as ad
ad._persist_dispatch({'delegation_id':'test-abandoned','session_key':'agent:worker:telegram:dm:chat-1',
    'parent_session_id':'parent','goal':'test task','dispatched_at':time.time(),
    **ad._capture_routing_origin()})
"""
    subprocess.run([sys.executable, "-c", code], check=True, timeout=15,
                   env=dict(os.environ, HERMES_HOME=str(profile), PYTHONDONTWRITEBYTECODE="1"))
    seen = []

    async def accept(event):
        seen.append(event.text)

    runner.adapters = {}
    runner._profile_adapters = {"worker": {Platform.TELEGRAM: SimpleNamespace(handle_message=accept)}}
    _run_watcher(monkeypatch, runner)
    assert len(seen) == 1
    assert "outcome unknown" in seen[0]
    with _profile_runtime_scope(profile):
        row = ad.get_durable_delegation("test-abandoned")
        assert row["state"] == "unknown"
        assert row["result"]["summary"] is None
        assert row["delivery_state"] == "delivered"

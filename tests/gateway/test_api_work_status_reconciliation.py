"""K21-338: disconnect retires real work and clears the persisted busy state."""

import asyncio
import threading
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gateway.config import Platform, PlatformConfig
from gateway.platforms.api_server import APIServerAdapter
from gateway.run import GatewayRunner
from gateway.status import read_runtime_status, write_runtime_status
from tests.gateway.restart_test_helpers import make_restart_runner


def _runner(api):
    runner, _ = make_restart_runner()
    runner.adapters = {Platform.API_SERVER: api}
    runner._external_drain_active = False
    runner._update_runtime_status = GatewayRunner._update_runtime_status.__get__(runner)
    return runner


def _agent():
    agent = MagicMock()
    agent.session_id = None
    agent.session_prompt_tokens = 0
    agent.session_completion_tokens = 0
    agent.session_total_tokens = 0
    agent._last_compaction_in_place = False
    agent._hermes_api_runtime = {}
    return agent


@pytest.mark.asyncio
@pytest.mark.parametrize("endpoint, body", [
    ("/v1/chat/completions", {"messages": [{"role": "user", "content": "hi"}], "stream": True}),
    ("/v1/responses", {"input": "hi", "stream": True}),
])
async def test_real_sse_disconnect_clears_work_and_status(endpoint, body):
    api = APIServerAdapter(PlatformConfig(enabled=True))
    runner = _runner(api)
    app = web.Application()
    app.router.add_post("/v1/chat/completions", api._handle_chat_completions)
    app.router.add_post("/v1/responses", api._handle_responses)
    app.router.add_get("/health/detailed", api._handle_health_detailed)
    loop = asyncio.get_running_loop()
    started, interrupted, cleared = asyncio.Event(), asyncio.Event(), asyncio.Event()
    release = threading.Event()
    agent = _agent()
    callbacks = {}

    def create(**kwargs):
        callbacks.update(kwargs)
        return agent

    def run(**kwargs):
        loop.call_soon_threadsafe(started.set)
        assert release.wait(10), "test did not release its executor thread"
        return {"final_response": "interrupted", "messages": []}

    agent.run_conversation.side_effect = run
    agent.interrupt.side_effect = lambda *_a: loop.call_soon_threadsafe(interrupted.set)
    original_persist = runner._persist_active_agents

    def persist():
        saved = original_persist()
        if read_runtime_status()["active_agents"] == 0:
            cleared.set()
        return saved

    runner._persist_active_agents = persist
    watcher = None
    try:
        with patch.object(api, "_create_agent", side_effect=create):
            async with TestClient(TestServer(app)) as client:
                response = await client.post(endpoint, json=body)
                await asyncio.wait_for(started.wait(), 10)
                assert api.active_agent_work_count() == 1
                runner._update_runtime_status("running")
                assert read_runtime_status()["active_agents"] == 1
                response.close()
                # Let aiohttp observe the closed socket before forcing a write.
                await asyncio.sleep(0)
                callbacks["stream_delta_callback"]("next delta")
                await asyncio.wait_for(interrupted.wait(), 10)
                # Cancellation of the async wrapper must not hide a thread
                # that is still unwinding. It is real work until release.
                assert api.active_agent_work_count() == 1
                watcher = asyncio.create_task(runner._drain_control_watcher(interval=0.01))
                release.set()
                await asyncio.wait_for(cleared.wait(), 10)
                assert api.active_agent_work_count() == 0
                assert api._shutdown_interruptible_agents == {}
                status = await (await client.get("/health/detailed")).json()
                assert status["active_agents"] == 0
                assert status["active_agent_counts"] == {"messaging": 0, "cron": 0, "api": 0}
                assert status["gateway_busy"] is False
    finally:
        release.set()
        runner._running = False
        if watcher:
            watcher.cancel()
            await asyncio.gather(watcher, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["normal", "error", "cancel", "timeout"])
async def test_executor_work_retires_on_every_exit(outcome):
    api = APIServerAdapter(PlatformConfig(enabled=True))
    loop = asyncio.get_running_loop()
    started, finished = asyncio.Event(), asyncio.Event()
    release = threading.Event()
    agent = _agent()

    def run(**kwargs):
        loop.call_soon_threadsafe(started.set)
        try:
            assert release.wait(10)
            if outcome == "error":
                raise RuntimeError("test crash")
            return {"final_response": "done", "messages": []}
        finally:
            loop.call_soon_threadsafe(finished.set)

    agent.run_conversation.side_effect = run
    task = None
    try:
        with patch.object(api, "_create_agent", return_value=agent):
            task = asyncio.create_task(api._run_agent(user_message="hi", conversation_history=[]))
            await asyncio.wait_for(started.wait(), 10)
            assert api.active_agent_work_count() == 1
            if outcome == "cancel":
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            elif outcome == "timeout":
                with pytest.raises(asyncio.TimeoutError):
                    await asyncio.wait_for(task, 0)
            if outcome in {"cancel", "timeout"}:
                assert api.active_agent_work_count() == 1
            release.set()
            await asyncio.wait_for(finished.wait(), 10)
            if outcome == "error":
                with pytest.raises(RuntimeError, match="test crash"):
                    await task
            elif outcome == "normal":
                await task
            # Drain polls actual work; no timing-based reset is involved.
            runner = _runner(api)
            _, timed_out = await runner._drain_active_agents(3)
            assert not timed_out
            assert api.active_agent_work_count() == 0
            assert api._shutdown_interruptible_agents == {}
    finally:
        release.set()
        if task:
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["api", "cron"])
async def test_idle_watcher_reconciles_without_drain_marker(source, monkeypatch):
    api = APIServerAdapter(PlatformConfig(enabled=True))
    runner = _runner(api)
    count = [1]
    monkeypatch.setattr(api, "active_agent_work_count", lambda: count[0] if source == "api" else 0)
    monkeypatch.setattr(runner, "_active_cron_job_count", lambda: count[0] if source == "cron" else 0)
    runner._update_runtime_status("running")
    snapshots = []

    async def next_tick(_interval):
        snapshots.append(read_runtime_status())
        if len(snapshots) == 1:
            count[0] = 0
        else:
            runner._running = False

    # Two real reconciliation ticks, driven by state instead of elapsed time.
    with patch("gateway.run.asyncio.sleep", side_effect=next_tick):
        await runner._drain_control_watcher()
    assert snapshots[0]["active_agents"] == 1
    assert snapshots[1]["active_agents"] == 0
    assert snapshots[0]["active_agent_counts"][source] == 1
    assert sum(snapshots[1]["active_agent_counts"].values()) == 0
    assert snapshots[1]["gateway_state"] == "running"


def test_sources_and_total_persist_together():
    write_runtime_status(gateway_state="running", active_agents=6,
                         active_agent_counts={"messaging": 1, "cron": 2, "api": 3})
    status = read_runtime_status()
    assert sum(status["active_agent_counts"].values()) == status["active_agents"]


@pytest.mark.asyncio
@pytest.mark.parametrize("responses", [False, True])
async def test_cancel_during_sse_prepare_interrupts_agent(responses):
    api = APIServerAdapter(PlatformConfig(enabled=True))
    agent = _agent()
    loop = asyncio.get_running_loop()
    started = asyncio.Event()
    release = threading.Event()
    ref = [None]

    def run(**_kwargs):
        loop.call_soon_threadsafe(started.set)
        assert release.wait(10)
        return {"final_response": "done", "messages": []}

    agent.run_conversation.side_effect = run
    agent.interrupt.side_effect = lambda *_a: release.set()
    response = MagicMock()
    response.prepare = AsyncMock(side_effect=asyncio.CancelledError)
    task = None
    try:
        with patch.object(api, "_create_agent", return_value=agent):
            task = asyncio.create_task(api._run_agent(user_message="hi", conversation_history=[], agent_ref=ref))
            await asyncio.wait_for(started.wait(), 10)
            with patch("gateway.platforms.api_server.web.StreamResponse", return_value=response):
                with pytest.raises(asyncio.CancelledError):
                    if responses:
                        await api._write_sse_responses(
                            request=MagicMock(headers={}), response_id="resp-test", model="test", created_at=0,
                            stream_q=asyncio.Queue(), agent_task=task, agent_ref=ref, conversation_history=[],
                            user_message="hi", instructions=None, conversation=None, store=False, session_id="s",
                        )
                    else:
                        await api._write_sse_chat_completion(
                            MagicMock(headers={}), "chat-test", "test", 0, asyncio.Queue(), task, ref,
                        )
            agent.interrupt.assert_called_once_with("SSE task cancelled")
            _, timed_out = await _runner(api)._drain_active_agents(3)
            assert not timed_out
            assert api.active_agent_work_count() == 0
    finally:
        release.set()
        if task:
            await asyncio.gather(task, return_exceptions=True)

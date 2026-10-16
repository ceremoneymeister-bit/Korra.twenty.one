"""Reload owns all native MCP transports until its actual worker settles."""
import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import MagicMock, patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gateway.config import PlatformConfig
from gateway.mcp_reload import try_enter_agent_work, try_enter_mcp_reload
from gateway.platforms.api_server import APIServerAdapter


def adapter():
    return APIServerAdapter(PlatformConfig(enabled=True, extra={'key': 'x' * 32}))


def assert_idle():
    lease = try_enter_mcp_reload()
    assert lease is not None
    lease.release()


def agent():
    value = MagicMock()
    value.session_prompt_tokens = value.session_completion_tokens = value.session_total_tokens = 0
    value.run_conversation.return_value = {'final_response': 'done', 'completed': True}
    return value


@pytest.mark.asyncio
async def test_reload_blocks_new_work_on_other_adapter_gateway_cron_and_runs():
    from gateway.run import TurnRunner
    from cron.scheduler import run_job
    from korra_constants import get_hermes_home
    (get_hermes_home() / 'config.yaml').write_text('approvals:\n  mcp_reload_confirm: false\n')
    first, second = adapter(), adapter()
    entered, release = threading.Event(), threading.Event()

    def reload():
        entered.set()
        assert release.wait(10)
        return [], set(), set(), set(), 'RELOADED'

    app = web.Application()
    app.router.add_post('/v1/runs', second._handle_runs)
    with patch('gateway.mcp_reload.reload_mcp_servers', side_effect=reload):
        reload_task = asyncio.create_task(first._run_agent('/reload-mcp', [], session_id='command'))
        try:
            assert await asyncio.to_thread(entered.wait, 10)
            with patch.object(second, '_create_agent', side_effect=AssertionError('MODEL CALLED')):
                refused, _ = await second._run_agent('ordinary', [], session_id='neighbor')
                assert refused['failed'] is True and refused['completed'] is False
                # The messaging worker must refuse before even reading ctx or
                # attempting a cache lookup/factory, across every profile.
                assert TurnRunner.run_sync(object())['failed'] is True
                with patch('cron.scheduler._run_job_inner', side_effect=AssertionError('JOB CALLED')):
                    success, text, _, _ = run_job({'id': 'test-cron'})
                assert success is False and 'Повторите' in text
                async with TestClient(TestServer(app)) as client:
                    response = await client.post('/v1/runs', json={'input': 'ordinary'},
                        headers={'Authorization': 'Bearer ' + 'x' * 32})
                    assert response.status == 202
                    run_id = (await response.json())['run_id']
                    task = second._active_run_tasks.get(run_id)
                    if task:
                        await task
                    assert second._run_statuses[run_id]['status'] == 'failed'
        finally:
            release.set()
            await reload_task
    assert_idle()
    with patch.object(second, '_create_agent', return_value=agent()) as factory:
        result, _ = await second._run_agent('ordinary', [], session_id='neighbor')
    assert result['final_response'] == 'done'
    factory.assert_called_once()
    assert_idle()


@pytest.mark.parametrize('surface', ['gateway', 'cron'])
def test_active_sync_lifecycle_refuses_reload_and_releases_after_exception(surface):
    from gateway.run import TurnRunner
    from cron.scheduler import run_job
    entered, release = threading.Event(), threading.Event()

    def hold(*args, **kwargs):
        entered.set()
        assert release.wait(10)
        raise RuntimeError('fixture worker failed')

    class PendingGateway:
        @property
        def _ctx(self):
            hold()

    def invoke():
        if surface == 'gateway':
            return TurnRunner.run_sync(PendingGateway())
        with patch('cron.scheduler._run_job_inner', side_effect=hold):
            return run_job({'id': 'test-cron'})

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(invoke)
        try:
            assert entered.wait(10)
            assert try_enter_mcp_reload() is None
        finally:
            release.set()
        with pytest.raises(RuntimeError, match='fixture worker failed'):
            future.result(timeout=10)
    assert_idle()


@pytest.mark.asyncio
async def test_cancelled_api_request_keeps_lease_until_its_worker_finishes():
    value = adapter()
    entered, release = threading.Event(), threading.Event()
    mock_agent = agent()
    def work(**kwargs):
        entered.set()
        assert release.wait(10)
        return {'final_response': 'done'}
    mock_agent.run_conversation.side_effect = work
    with patch.object(value, '_create_agent', return_value=mock_agent):
        task = asyncio.create_task(value._run_agent('ordinary', [], session_id='cancel'))
        try:
            assert await asyncio.to_thread(entered.wait, 10)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert try_enter_mcp_reload() is None
        finally:
            release.set()
        for _ in range(100):
            if not value._inflight_agent_runs:
                break
            await asyncio.sleep(.01)
    assert_idle()


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', [False, True])
async def test_gateway_cancelled_reload_keeps_exclusive_until_native_worker_finishes(failure):
    from gateway.run import GatewayRunner
    from gateway.platforms.base import MessageEvent
    value = object.__new__(GatewayRunner)
    entered, release = threading.Event(), threading.Event()
    def reload():
        entered.set()
        assert release.wait(10)
        if failure:
            raise RuntimeError('fixture reconnect failed')
        return [], set(), set(), set(), 'RELOADED'
    with patch('gateway.mcp_reload.reload_mcp_servers', side_effect=reload):
        task = asyncio.create_task(value._execute_mcp_reload(MessageEvent(text='/reload-mcp', source=None)))
        try:
            assert await asyncio.to_thread(entered.wait, 10)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert try_enter_agent_work() is None
            assert try_enter_mcp_reload() is None
        finally:
            release.set()
        for _ in range(100):
            lease = try_enter_mcp_reload()
            if lease is not None:
                lease.release()
                break
            await asyncio.sleep(.01)
    assert_idle()


def test_nested_work_leases_do_not_deadlock_or_release_each_other():
    outer = try_enter_agent_work()
    inner = try_enter_agent_work()
    assert outer is not None and inner is not None
    inner.release()
    inner.release()  # cleanup may race a cancelled task's completion callback
    assert try_enter_mcp_reload() is None
    outer.release()
    assert_idle()


@pytest.mark.asyncio
@pytest.mark.parametrize('stage', ['native_discovery', 'agent_worker'])
async def test_cancelled_runs_task_keeps_lease_until_actual_thread_finishes(stage):
    from agent.secret_scope import is_multiplex_active, set_multiplex_active
    value = adapter()
    entered, release = threading.Event(), threading.Event()
    mock_agent = agent()
    def work(*args, **kwargs):
        entered.set()
        assert release.wait(10)
        return {'final_response': 'done'}
    mock_agent.run_conversation.side_effect = work
    old_multiplex = is_multiplex_active()
    app = web.Application()
    app.router.add_post('/v1/runs', value._handle_runs)
    try:
        set_multiplex_active(stage == 'native_discovery')
        with (
            patch.object(value, '_create_agent', return_value=mock_agent),
            patch('tools.mcp_tool.ensure_native_profile_mcp_tools',
                  side_effect=work if stage == 'native_discovery' else lambda: None),
        ):
            async with TestClient(TestServer(app)) as client:
                response = await client.post('/v1/runs', json={'input': 'ordinary'},
                    headers={'Authorization': 'Bearer ' + 'x' * 32})
                assert response.status == 202
                run_id = (await response.json())['run_id']
                task = value._active_run_tasks[run_id]
                try:
                    assert await asyncio.to_thread(entered.wait, 10)
                    task.cancel()
                    with pytest.raises(asyncio.CancelledError):
                        await task
                    assert try_enter_mcp_reload() is None
                finally:
                    release.set()
                for _ in range(100):
                    lease = try_enter_mcp_reload()
                    if lease is not None:
                        lease.release()
                        break
                    await asyncio.sleep(.01)
        assert_idle()
    finally:
        release.set()
        set_multiplex_active(old_multiplex)


@pytest.mark.asyncio
async def test_factory_exception_releases_api_and_runs_admission():
    value = adapter()
    with patch.object(value, '_create_agent', side_effect=ValueError('fixture factory failed')):
        with pytest.raises(ValueError, match='fixture factory failed'):
            await value._run_agent('ordinary', [], session_id='factory-failure')
        assert_idle()
        app = web.Application()
        app.router.add_post('/v1/runs', value._handle_runs)
        async with TestClient(TestServer(app)) as client:
            response = await client.post('/v1/runs', json={'input': 'ordinary'},
                headers={'Authorization': 'Bearer ' + 'x' * 32})
            run_id = (await response.json())['run_id']
            task = value._active_run_tasks.get(run_id)
            if task:
                await task
            assert value._run_statuses[run_id]['status'] == 'failed'
        assert_idle()


def test_detached_delegate_keeps_reload_closed_after_foreground_returns():
    with patch('tools.async_delegation.active_count', return_value=1):
        assert try_enter_mcp_reload() is None
    assert_idle()

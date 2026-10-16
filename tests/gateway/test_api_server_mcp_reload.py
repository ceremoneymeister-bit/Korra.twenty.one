"""K21-375: web MCP reload must never become a model turn."""
from unittest.mock import patch

import pytest

from gateway.config import PlatformConfig
from gateway.platforms.api_server import APIServerAdapter


@pytest.mark.asyncio
async def test_reload_requests_existing_confirmation_without_creating_agent():
    adapter = APIServerAdapter(PlatformConfig(enabled=True, extra={"key": "x" * 32}))
    history = [{"role": "user", "content": "до"}, {"role": "assistant", "content": "ответ"}]
    with patch.object(adapter, "_create_agent", side_effect=AssertionError("MODEL CALLED")):
        result, usage = await adapter._run_agent("/reload-mcp", history, session_id="reload-test")
    assert "/approve" in result["final_response"]
    assert result["messages"] == history
    assert usage["total_tokens"] == 0


@pytest.mark.asyncio
async def test_real_native_mcp_reload_profile_secrets_and_dispatch(tmp_path, monkeypatch):
    import json
    import sys
    from pathlib import Path
    import yaml
    from gateway.run import _profile_runtime_scope
    from tools import mcp_tool
    from tools.registry import registry
    from gateway.platforms.api_server import _api_request_profile

    monkeypatch.setattr('agent.secret_scope.is_multiplex_active', lambda: True)
    homes = [tmp_path / 'alpha', tmp_path / 'beta']
    fixture = Path(__file__).resolve().parents[1] / 'fixtures' / 'mcp_reload_server.py'
    for home in homes:
        home.mkdir()
        (home / '.env').write_text(f'RELOAD_MARKER={home.name}\n')
        (home / 'config.yaml').write_text(yaml.safe_dump({
            'approvals': {'mcp_reload_confirm': False},
            'mcp_servers': {'local': {
                'command': sys.executable, 'args': [str(fixture)],
                'env': {'RELOAD_MARKER': '${RELOAD_MARKER}'},
                'tools': {'resources': False, 'prompts': False},
                'connect_timeout': 10,
            }},
        }))
    monkeypatch.setattr('korra_cli.profiles.get_profile_dir', lambda name: homes[0])
    adapter = APIServerAdapter(PlatformConfig(enabled=True, extra={'key': 'x' * 32}))
    profile_token = _api_request_profile.set('alpha')
    try:
        # Real sibling profile is already connected before alpha reloads.
        with _profile_runtime_scope(homes[1]):
            names = mcp_tool.discover_mcp_tools()
            assert 'mcp__local__echo' in names
            sibling_entry = registry.get_entry('mcp__local__echo')
            assert json.loads(sibling_entry.handler({'value': 'before'}))['result'] == 'beta:before'
        with _profile_runtime_scope(homes[0]):
            assert 'mcp__local__echo' in mcp_tool.discover_mcp_tools()
            previous_entry = registry.get_entry('mcp__local__echo')
            with patch.object(adapter, '_create_agent', side_effect=AssertionError('MODEL CALLED')):
                result, _ = await adapter._run_agent('/reload-mcp', [], session_id='s1')
            assert 'local' in result['final_response']
            assert 'частично' not in result['final_response']
            # The actual native registry schema and native dispatch, not a
            # fake reload return value, are available to the next turn.
            definitions = registry.get_definitions({'mcp__local__echo'}, quiet=True)
            assert definitions[0]['function']['name'] == 'mcp__local__echo'
            entry = registry.get_entry('mcp__local__echo')
            assert entry is not previous_entry
            assert json.loads(entry.handler({'value': 'next'}))['result'] == 'alpha:next'
        with _profile_runtime_scope(homes[1]):
            assert registry.get_entry('mcp__local__echo') is sibling_entry
            assert json.loads(sibling_entry.handler({'value': 'after'}))['result'] == 'beta:after'
    finally:
        _api_request_profile.reset(profile_token)
        for home in homes:
            with _profile_runtime_scope(home):
                mcp_tool.shutdown_mcp_servers()
        mcp_tool._stop_mcp_loop()


@pytest.mark.asyncio
@pytest.mark.parametrize('choice', ['/approve', '/cancel'])
async def test_confirmation_only_resolves_in_same_profile_and_session(tmp_path, monkeypatch, choice):
    from gateway.web_mcp_command import run_web_mcp_command
    from korra_constants import set_hermes_home_override, reset_hermes_home_override
    from tools import slash_confirm
    adapter = APIServerAdapter(PlatformConfig(enabled=True, extra={'key': 'x' * 32}))
    token = set_hermes_home_override(str(tmp_path))
    try:
        # The command helper runs inside the executor in production.
        import asyncio
        from contextvars import copy_context
        def call(text, sid):
            return run_web_mcp_command(adapter, text, sid)
        loop = asyncio.get_running_loop()
        async def command(text, sid):
            return await loop.run_in_executor(None, copy_context().run, call, text, sid)
        assert '/approve' in await command('/reload-mcp', 'one')
        assert await command(choice, 'two') is None
        with patch('gateway.mcp_reload.reload_mcp_servers', return_value=([], set(), set(), set(), 'RELOADED')) as reload:
            reply = await command(choice, 'one')
        assert ('RELOADED' in reply) == (choice == '/approve')
        assert reload.call_count == (choice == '/approve')
        slash_confirm.clear(f'api-mcp:{tmp_path.resolve()}:one')
    finally:
        reset_hermes_home_override(token)


@pytest.mark.asyncio
async def test_partial_connection_failure_has_no_success_header(tmp_path, monkeypatch):
    import yaml
    from gateway.platforms.api_server import _api_request_profile
    (tmp_path / 'config.yaml').write_text(yaml.safe_dump({
        'approvals': {'mcp_reload_confirm': False},
        'mcp_servers': {'broken': {'command': '/nonexistent/korra-test-mcp', 'connect_timeout': .2}},
    }))
    monkeypatch.setattr('korra_cli.profiles.get_profile_dir', lambda name: tmp_path)
    token = _api_request_profile.set('failure')
    adapter = APIServerAdapter(PlatformConfig(enabled=True, extra={'key': 'x' * 32}))
    try:
        with patch.object(adapter, '_create_agent', side_effect=AssertionError('MODEL CALLED')):
            result, _ = await adapter._run_agent('/reload-mcp', [], session_id='failure')
        assert 'частично' in result['final_response']
        assert 'broken' in result['final_response']
    finally:
        _api_request_profile.reset(token)
        from tools import mcp_tool
        mcp_tool.shutdown_mcp_servers()


@pytest.mark.asyncio
async def test_reload_refuses_while_another_api_run_is_pending(tmp_path, monkeypatch):
    from gateway.web_mcp_command import _WebMCPCommand
    adapter = APIServerAdapter(PlatformConfig(enabled=True, extra={'key': 'x' * 32}))
    adapter._pending_agent_requests = 1
    with patch('gateway.mcp_reload.reload_mcp_servers') as reload:
        response = await _WebMCPCommand(adapter, 'busy')._execute_mcp_reload(None)
    assert 'дождитесь' in response
    reload.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize('stream', [False, True])
async def test_real_http_reload_service_reply_and_confirmation(stream):
    import json
    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer
    key = 'test-key-' + 'x' * 32
    adapter = APIServerAdapter(PlatformConfig(enabled=True, extra={'key': key}))
    app = web.Application()
    app['api_server_adapter'] = adapter
    app.router.add_post('/v1/chat/completions', adapter._handle_chat_completions)
    with patch.object(adapter, '_create_agent', side_effect=AssertionError('MODEL CALLED')):
        async with TestClient(TestServer(app)) as client:
            response = await client.post('/v1/chat/completions', headers={
                'Authorization': f'Bearer {key}', 'X-Hermes-Session-Id': 'http-reload',
            }, json={'model': 'korra-agent', 'stream': stream, 'messages': [
                {'role': 'user', 'content': '/reload-mcp'},
            ]})
            assert response.status == 200
            text = await response.text()
            if stream:
                chunks = [json.loads(line[6:]) for line in text.splitlines()
                          if line.startswith('data: ') and line != 'data: [DONE]']
                reply = ''.join(c.get('choices', [{}])[0].get('delta', {}).get('content', '')
                                for c in chunks if c.get('choices'))
            else:
                reply = json.loads(text)['choices'][0]['message']['content']
            assert '/approve' in reply


def test_unrelated_slashes_remain_ordinary_messages():
    from gateway.web_mcp_command import run_web_mcp_command
    assert run_web_mcp_command(None, '/unknown', 's1') is None
    assert run_web_mcp_command(None, '/approve extra', 's1') is None
    assert 'без аргументов' in run_web_mcp_command(None, '/reload-mcp extra', 's1')


@pytest.mark.asyncio
async def test_reload_is_transient_and_next_normal_turn_keeps_existing_history():
    """Service commands follow learning commands' non-model history contract."""
    from unittest.mock import MagicMock
    adapter = APIServerAdapter(PlatformConfig(enabled=True, extra={'key': 'x' * 32}))
    history = [{'role': 'user', 'content': 'старое поручение'},
               {'role': 'assistant', 'content': 'старый результат'}]
    with patch.object(adapter, '_create_agent', side_effect=AssertionError('MODEL CALLED')):
        result, _ = await adapter._run_agent('/reload-mcp', history, session_id='history')
        cancelled, _ = await adapter._run_agent('/cancel', history, session_id='history')
    assert result['messages'] == cancelled['messages'] == history
    agent = MagicMock()
    agent.session_prompt_tokens = agent.session_completion_tokens = agent.session_total_tokens = 0
    agent.run_conversation.return_value = {'final_response': 'продолжил', 'messages': history}
    with patch.object(adapter, '_create_agent', return_value=agent):
        await adapter._run_agent('продолжи поручение', history, session_id='history')
    assert agent.run_conversation.call_args.kwargs['conversation_history'] == history
    assert agent.run_conversation.call_args.kwargs['user_message'] == 'продолжи поручение'


@pytest.mark.asyncio
async def test_runs_route_reload_and_followup_approval_never_create_agent():
    import asyncio
    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer
    key = 'test-key-' + 'x' * 32
    adapter = APIServerAdapter(PlatformConfig(enabled=True, extra={'key': key}))
    app = web.Application()
    app['api_server_adapter'] = adapter
    app.router.add_post('/v1/runs', adapter._handle_runs)
    app.router.add_get('/v1/runs/{run_id}', adapter._handle_get_run)
    async with TestClient(TestServer(app)) as client:
        async def run(message):
            response = await client.post('/v1/runs', headers={'Authorization': f'Bearer {key}'},
                                         json={'input': message, 'session_id': 'runs-mcp-test'})
            assert response.status == 202
            run_id = (await response.json())['run_id']
            task = adapter._active_run_tasks.get(run_id)
            if task:
                await asyncio.wait_for(task, timeout=10)
            status = await client.get(f'/v1/runs/{run_id}', headers={'Authorization': f'Bearer {key}'})
            return await status.json()
        with patch.object(adapter, '_create_agent', side_effect=AssertionError('MODEL CALLED')):
            first = await run('/reload-mcp')
            assert first['status'] == 'completed'
            assert '/approve' in first['output']
            # Real helper and busy guard execute while this /v1/runs task is
            # itself active. Its own task must not be mistaken for a neighbor.
            with patch('gateway.mcp_reload.reload_mcp_servers', return_value=([], set(), set(), set(), 'NATIVE RELOADED')) as reload:
                second = await run('/approve')
            assert second['status'] == 'completed'
            assert second['output'] == 'NATIVE RELOADED'
            reload.assert_called_once()
            assert second['usage']['total_tokens'] == 0


def test_room_dispatch_cannot_resolve_owner_mcp_command():
    from gateway.web_mcp_command import run_web_mcp_command
    assert 'только владельцу' in run_web_mcp_command(None, '/reload-mcp', 'room', owner=False)
    assert 'только владельцу' in run_web_mcp_command(None, '/always', 'room', owner=False)


@pytest.mark.asyncio
async def test_runs_room_reload_refuses_before_native_profile_operations():
    import asyncio
    from unittest.mock import AsyncMock
    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer
    adapter = APIServerAdapter(PlatformConfig(enabled=True, extra={'key': 'x' * 32}))
    app = web.Application()
    app['api_server_adapter'] = adapter
    app.router.add_post('/v1/runs', adapter._handle_runs)
    # Authentication/dispatch normalization has its own grant regressions.
    # Supply its validated output here to exercise the real runs lifecycle.
    async def normalized(request, body):
        return {**body, 'hosted_room_dispatch': {'room_id': 'room-test'}}, None
    with (
        patch.object(adapter, '_normalize_room_dispatch', new=AsyncMock(side_effect=normalized)),
        patch.object(adapter, '_create_agent', side_effect=AssertionError('MODEL CALLED')),
        patch('gateway.mcp_reload.reload_mcp_servers', side_effect=AssertionError('RELOAD CALLED')),
        patch('tools.mcp_tool.ensure_native_profile_mcp_tools', side_effect=AssertionError('DISCOVERY CALLED')),
    ):
        async with TestClient(TestServer(app)) as client:
            response = await client.post('/v1/runs', headers={'Authorization': 'Bearer ' + 'x' * 32},
                                         json={'input': '/reload-mcp', 'session_id': 'room-s1'})
            assert response.status == 202
            run_id = (await response.json())['run_id']
            task = adapter._active_run_tasks.get(run_id)
            if task:
                await asyncio.wait_for(task, timeout=10)
            assert adapter._run_statuses[run_id]['status'] == 'completed'
            assert 'только владельцу' in adapter._run_statuses[run_id]['output']


@pytest.mark.asyncio
async def test_responses_stored_history_survives_service_turn_and_continuation():
    from unittest.mock import MagicMock
    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer
    key = 'test-key-' + 'x' * 32
    adapter = APIServerAdapter(PlatformConfig(enabled=True, extra={'key': key}))
    app = web.Application()
    app['api_server_adapter'] = adapter
    app.router.add_post('/v1/responses', adapter._handle_responses)
    history = [{'role': 'user', 'content': 'задача до команды'},
               {'role': 'assistant', 'content': 'сохранённый ответ'}]
    agent = MagicMock()
    agent.session_prompt_tokens = agent.session_completion_tokens = agent.session_total_tokens = 0
    agent.run_conversation.return_value = {'final_response': 'продолжение'}
    async with TestClient(TestServer(app)) as client:
        with patch.object(adapter, '_create_agent', side_effect=AssertionError('MODEL CALLED')):
            response = await client.post('/v1/responses', headers={'Authorization': f'Bearer {key}'},
                                         json={'model': 'korra-agent', 'input': history + [
                                             {'role': 'user', 'content': '/reload-mcp'}]})
            assert response.status == 200
            body = await response.json()
        # Responses storage carries the exact old history, without duplicating
        # it or inserting the transient confirmation into the model transcript.
        stored = adapter._response_store.get(body['id'])
        assert stored['conversation_history'] == history
        with patch.object(adapter, '_create_agent', return_value=agent):
            next_response = await client.post('/v1/responses', headers={'Authorization': f'Bearer {key}'},
                                              json={'model': 'korra-agent', 'input': 'продолжи',
                                                    'previous_response_id': body['id']})
            assert next_response.status == 200
        assert agent.run_conversation.call_args.kwargs['conversation_history'] == history

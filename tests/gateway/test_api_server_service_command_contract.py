"""K21-378: independent web service-command contract, real HTTP and temp DBs.

Only model/auxiliary boundaries are forbidden. The command handler, registry,
profile scoping, config, SessionDB and SSE serialization run for real.
"""
from contextlib import ExitStack, contextmanager
import json
from unittest.mock import MagicMock, patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
import yaml

from gateway.config import PlatformConfig
from gateway.platforms.api_server import APIServerAdapter
from korra_state import SessionDB

KEY = 'synthetic-service-command-contract-000000000'
COMMANDS = [
    '/help', '/status', '/version', '/v', '/model', '/tools', '/profile',
    '/usage', '/reasoning', '/fast', '/reload-skills', '/reload_skills',
]
RESTRICTED = [
    '/model other-model', '/tools disable all', '/profile other',
    '/reasoning ultra', '/fast on', '/reload-skills --now', '/compact', '/compress',
    '/new', '/reset', '/clear', '/stop', '/restart', '/quit', '/exit',
]


@contextmanager
def no_models(adapter):
    with ExitStack() as stack:
        probes = []
        for target in [
            'agent.auxiliary_client.call_llm', 'agent.auxiliary_client.async_call_llm',
            'agent.title_generator.generate_title',
        ]:
            probes.append(stack.enter_context(patch(target, side_effect=AssertionError('AUXILIARY MODEL CALLED'))))
        probes.append(stack.enter_context(patch.object(adapter, '_create_agent', side_effect=AssertionError('MAIN MODEL CALLED'))))
        yield
        for probe in probes:
            probe.assert_not_called()


@pytest.fixture
def scoped_home(tmp_path, monkeypatch):
    home = tmp_path / 'profile-alpha'
    home.mkdir()
    monkeypatch.setenv('HERMES_HOME', str(home))
    monkeypatch.setenv('KORRA_HOME', str(home))
    (home / 'config.yaml').write_text(yaml.safe_dump({
        'model': {'default': 'alpha-model', 'provider': 'openai-codex'},
        'agent': {'reasoning_effort': 'high'},
        'platform_toolsets': {'api_server': ['file']},
    }))
    (home / '.env').write_text('TEST_ONLY_SECRET=must-not-appear-in-reply\n')
    db = SessionDB(db_path=home / 'state.db')
    db.create_session('contract-session', 'api_server', model='alpha-model', system_prompt='PINNED-PROMPT\n')
    db.append_message('contract-session', 'user', 'Исходная задача')
    db.append_message('contract-session', 'assistant', 'Исходный результат')
    yield home, db
    db.close()


def app_for(adapter):
    app = web.Application()
    app['api_server_adapter'] = adapter
    app.router.add_post('/v1/chat/completions', adapter._handle_chat_completions)
    return app


async def post_command(client, command, stream):
    response = await client.post('/v1/chat/completions', headers={
        'Authorization': 'Bearer ' + KEY,
        'X-Hermes-Session-Id': 'contract-session',
    }, json={
        'model': 'korra-agent',
        'messages': [{'role': 'user', 'content': command}], 'stream': stream,
    })
    assert response.status == 200
    if stream:
        chunks = [json.loads(line[6:]) for line in (await response.text()).splitlines()
                  if line.startswith('data: ') and line[6:] != '[DONE]']
        text = ''.join(c['choices'][0].get('delta', {}).get('content', '') for c in chunks if c.get('choices'))
        assert not any(c.get('error') for c in chunks), chunks
        return text
    data = await response.json()
    assert data['usage']['total_tokens'] == 0
    assert data['choices'][0]['finish_reason'] == 'stop', data
    return data['choices'][0]['message']['content']


@pytest.mark.asyncio
@pytest.mark.parametrize('command', COMMANDS)
@pytest.mark.parametrize('stream', [False, True])
async def test_service_commands_http_without_any_model_and_without_prompt_mutation(scoped_home, command, stream):
    home, db = scoped_home
    adapter = APIServerAdapter(PlatformConfig(enabled=True, extra={'key': KEY}))
    before = db.get_session('contract-session')
    before_messages = db.get_messages('contract-session')
    before_config = (home / 'config.yaml').read_bytes()
    with no_models(adapter):
        async with TestClient(TestServer(app_for(adapter))) as client:
            answer = await post_command(client, command, stream)
    assert answer.strip()
    assert 'must-not-appear-in-reply' not in answer
    assert 'MAIN MODEL CALLED' not in answer
    assert (home / 'config.yaml').read_bytes() == before_config
    after = db.get_session('contract-session')
    assert after['system_prompt'] == before['system_prompt']
    assert after['system_prompt_hash'] == before['system_prompt_hash']
    assert after['model'] == before['model']
    messages = db.get_messages('contract-session')
    assert messages[:-2] == before_messages
    assert [(row['role'], row['content']) for row in messages[-2:]] == [
        ('user', command), ('assistant', answer),
    ]
    adapter._close_cached_session_dbs()


@pytest.mark.asyncio
@pytest.mark.parametrize('command,failure,expected', [
    ('/model', 'config', 'Не удалось прочитать настройки профиля.'),
    ('/tools', 'config', 'Не удалось прочитать настройки профиля.'),
    ('/reasoning', 'config', 'Не удалось прочитать настройки профиля.'),
    ('/fast', 'config', 'Не удалось прочитать настройки профиля.'),
    ('/status', 'history', 'Не удалось открыть историю диалога.'),
    ('/reload-skills', 'reload', 'Не удалось выполнить или сохранить служебную команду.'),
])
async def test_service_command_failure_reaches_chat_stream(scoped_home, command, failure, expected):
    home, db = scoped_home
    adapter = APIServerAdapter(PlatformConfig(enabled=True, extra={'key': KEY}))
    before_messages = db.get_messages('contract-session')
    try:
        with no_models(adapter), ExitStack() as stack:
            if failure == 'config':
                (home / 'config.yaml').write_text('model: [')
            elif failure == 'history':
                stack.enter_context(patch.object(adapter, '_ensure_session_db', return_value=None))
            else:
                stack.enter_context(patch('agent.skill_commands.reload_skills',
                                          side_effect=RuntimeError('must-not-leak-secret')))
            async with TestClient(TestServer(app_for(adapter))) as client:
                response = await client.post('/v1/chat/completions', headers={
                    'Authorization': 'Bearer ' + KEY,
                    'X-Hermes-Session-Id': 'contract-session',
                }, json={
                    'model': 'korra-agent', 'stream': True,
                    'messages': [{'role': 'user', 'content': command}],
                })
                assert response.status == 200
                wire = await response.text()
        chunks = [json.loads(line[6:]) for line in wire.splitlines()
                  if line.startswith('data: ') and line[6:] != '[DONE]']
        terminal = chunks[-1]
        assert terminal['choices'][0]['finish_reason'] == 'error'
        assert terminal['error']['message'].startswith(expected)
        assert terminal['hermes']['error'] == terminal['error']['message']
        assert terminal['hermes']['failed'] is True
        assert terminal['hermes']['completed'] is False
        assert terminal['usage']['total_tokens'] == 0
        assert not any(c['choices'][0].get('delta', {}).get('content') for c in chunks)
        assert 'must-not-leak-secret' not in wire
        assert wire.rstrip().endswith('data: [DONE]')
        assert db.get_messages('contract-session') == before_messages
    finally:
        adapter._close_cached_session_dbs()


@pytest.mark.asyncio
@pytest.mark.parametrize('command', RESTRICTED)
async def test_unsupported_service_mutations_are_explicit_local_restrictions(scoped_home, command):
    home, _ = scoped_home
    before = (home / 'config.yaml').read_bytes()
    adapter = APIServerAdapter(PlatformConfig(enabled=True, extra={'key': KEY}))
    with no_models(adapter):
        result, usage = await adapter._run_agent(command, [], session_id='contract-session')
    assert result['api_calls'] == 0
    assert usage['total_tokens'] == 0
    assert result['final_response'].strip()
    assert any(word in result['final_response'].lower() for word in ['недоступ', 'не поддерж', 'не измен', 'без аргумент', 'терминал', 'настройк', 'не очищ', 'не отмен']), result
    assert (home / 'config.yaml').read_bytes() == before
    adapter._close_cached_session_dbs()


@pytest.mark.asyncio
@pytest.mark.parametrize('text', [
    '/custom-skill do work', '/tmp/status.csv', '/help\nПродолжи задачу',
    'Расскажи про /help', 'Продолжи исходную задачу',
])
async def test_ordinary_slash_paths_multiline_and_followup_keep_agent_routing(scoped_home, text):
    home, _db = scoped_home
    skill = home / 'skills' / 'custom-skill'
    skill.mkdir(parents=True)
    (skill / 'SKILL.md').write_text('---\nname: custom-skill\ndescription: Test skill\n---\nDo work.\n')
    adapter = APIServerAdapter(PlatformConfig(enabled=True, extra={'key': KEY}))
    history = [{'role': 'user', 'content': 'до'}, {'role': 'assistant', 'content': 'результат'}]
    agent = MagicMock()
    agent.session_prompt_tokens = agent.session_completion_tokens = agent.session_total_tokens = 0
    agent.run_conversation.return_value = {'final_response': 'продолжение', 'completed': True}
    with patch.object(adapter, '_create_agent', return_value=agent):
        await adapter._run_agent(text, history, session_id='contract-session')
    agent.run_conversation.assert_called_once()
    assert agent.run_conversation.call_args.kwargs['user_message'] == text
    assert agent.run_conversation.call_args.kwargs['conversation_history'] == history
    agent._spawn_background_review.assert_not_called()
    adapter._close_cached_session_dbs()


@pytest.mark.asyncio
@pytest.mark.parametrize('command', ['/help', '/status', '/model', '/tools'])
async def test_readonly_commands_need_no_provider_auth_or_mcp_discovery(tmp_path, monkeypatch, command):
    home = tmp_path / 'unconfigured'
    home.mkdir()
    monkeypatch.setenv('HERMES_HOME', str(home))
    monkeypatch.setenv('KORRA_HOME', str(home))
    adapter = APIServerAdapter(PlatformConfig(enabled=True, extra={'key': KEY}))
    with (
        no_models(adapter),
        patch('gateway.run._resolve_runtime_agent_kwargs', side_effect=AssertionError('AUTH RESOLUTION')),
        patch('tools.mcp_tool.discover_mcp_tools', side_effect=AssertionError('MCP DISCOVERY')),
    ):
        result, usage = await adapter._run_agent(command, [], session_id='unconfigured-chat')
    assert result['api_calls'] == 0
    assert usage['total_tokens'] == 0
    assert result['final_response'].strip()
    adapter._close_cached_session_dbs()


@pytest.mark.asyncio
@pytest.mark.parametrize('command', ['/help', '/status'])
async def test_readonly_during_active_turn_does_not_interrupt_or_queue_a_model(scoped_home, command):
    adapter = APIServerAdapter(PlatformConfig(enabled=True, extra={'key': KEY}))
    active_agent = MagicMock()
    adapter._shutdown_interruptible_agents[id(active_agent)] = active_agent
    _, db = scoped_home
    assert db.try_acquire_session_turn_lease('contract-session', 'ongoing-model-turn', patience_s=0)
    try:
        with no_models(adapter):
            result, usage = await adapter._run_agent(command, [], session_id='contract-session')
        assert result['api_calls'] == 0
        assert usage['total_tokens'] == 0
        assert result['final_response'].strip()
        active_agent.interrupt.assert_not_called()
        assert adapter._shutdown_interruptible_agents[id(active_agent)] is active_agent
        assert 'выполняется' in result['final_response']
    finally:
        db.release_session_turn_lease('contract-session', 'ongoing-model-turn')
        adapter._shutdown_interruptible_agents.clear()
        adapter._close_cached_session_dbs()


@pytest.mark.asyncio
async def test_model_report_is_scoped_to_actual_profile_and_does_not_reveal_sibling(tmp_path, monkeypatch):
    from gateway.run import _profile_runtime_scope
    from gateway.platforms.api_server import _api_request_profile
    homes = {}
    for name in ['alpha', 'beta']:
        home = tmp_path / name
        home.mkdir()
        (home / 'config.yaml').write_text(yaml.safe_dump({'model': {'default': name + '-model', 'provider': 'openai-codex'}}))
        (home / '.env').write_text('TEST_ONLY_SECRET=' + name + '-secret\n')
        homes[name] = home
    monkeypatch.setattr('korra_cli.profiles.get_profile_dir', lambda name: homes[name])
    adapter = APIServerAdapter(PlatformConfig(enabled=True, extra={'key': KEY}))
    for name in homes:
        token = _api_request_profile.set(name)
        try:
            with _profile_runtime_scope(homes[name]), no_models(adapter):
                result, _ = await adapter._run_agent('/model', [], session_id=name + '-session')
            assert name + '-model' in result['final_response']
            assert ('beta-model' if name == 'alpha' else 'alpha-model') not in result['final_response']
            assert '-secret' not in result['final_response']
        finally:
            _api_request_profile.reset(token)
    adapter._close_cached_session_dbs()


@pytest.mark.asyncio
async def test_profile_prefix_http_honors_served_profile_and_rejects_unknown(tmp_path, monkeypatch):
    from types import SimpleNamespace
    homes = {}
    for name in ['alpha', 'beta']:
        home = tmp_path / name
        home.mkdir()
        (home / 'config.yaml').write_text(yaml.safe_dump({'model': {'provider': 'openai-codex', 'default': name + '-model'}}))
        (home / '.env').write_text('API_SERVER_KEY=' + KEY + '-' + name + '\n')
        homes[name] = home
    monkeypatch.setattr('korra_cli.profiles.profiles_to_serve', lambda *a, **k: list(homes.items()))
    monkeypatch.setattr('korra_cli.profiles.get_profile_dir', lambda name: homes[name])
    adapter = APIServerAdapter(PlatformConfig(enabled=True, extra={'key': KEY}))
    adapter.gateway_runner = SimpleNamespace(config=SimpleNamespace(multiplex_profiles=True, multiplex_profile_allowlist=None))
    app = web.Application(middlewares=[adapter._make_profile_prefix_middleware()])
    app['api_server_adapter'] = adapter
    app.router.add_post('/p/{profile}/v1/chat/completions', adapter._handle_chat_completions)
    with no_models(adapter):
        async with TestClient(TestServer(app)) as client:
            for name in ['alpha', 'beta', 'unknown']:
                response = await client.post(f'/p/{name}/v1/chat/completions', headers={'Authorization': 'Bearer ' + KEY + '-' + name}, json={
                    'model': 'korra-agent', 'messages': [{'role': 'user', 'content': '/model'}], 'stream': False,
                })
                if name == 'unknown':
                    assert response.status == 404
                    continue
                assert response.status == 200
                denied = await client.post(f'/p/{name}/v1/chat/completions', headers={'Authorization': 'Bearer wrong-profile-key'}, json={'model': 'korra-agent', 'messages': [{'role': 'user', 'content': '/status'}]})
                assert denied.status == 401
                answer = (await response.json())['choices'][0]['message']['content']
                assert name + '-model' in answer
                assert ('beta-model' if name == 'alpha' else 'alpha-model') not in answer
    adapter._close_cached_session_dbs()


@pytest.mark.asyncio
async def test_model_report_distinguishes_pinned_session_from_changed_defaults(scoped_home):
    home, db = scoped_home
    (home / 'config.yaml').write_text(yaml.safe_dump({'model': {'provider': 'openai-codex', 'default': 'new-default-model'}}))
    adapter = APIServerAdapter(PlatformConfig(enabled=True, extra={'key': KEY}))
    with no_models(adapter):
        result, _ = await adapter._run_agent('/model', [], session_id='contract-session')
    assert 'для новых диалогов: new-default-model' in result['final_response']
    assert 'этого диалога: alpha-model' in result['final_response']
    assert db.get_session('contract-session')['model'] == 'alpha-model'
    adapter._close_cached_session_dbs()


@pytest.mark.asyncio
async def test_usage_report_uses_only_requested_session(scoped_home):
    _, db = scoped_home
    db.create_session('unrelated-session', 'api_server', model='other-model')
    db.update_token_counts('unrelated-session', input_tokens=991234, output_tokens=994321)
    adapter = APIServerAdapter(PlatformConfig(enabled=True, extra={'key': KEY}))
    with no_models(adapter):
        result, _ = await adapter._run_agent('/usage', [], session_id='contract-session')
    assert 'contract-session' in result['final_response']
    assert 'unrelated-session' not in result['final_response']
    assert '991234' not in result['final_response']
    assert '994321' not in result['final_response']
    assert 'вход 0, выход 0' in result['final_response']
    adapter._close_cached_session_dbs()


@pytest.mark.asyncio
async def test_reload_skills_refreshes_new_index_preserving_pinned_session(scoped_home):
    from agent.prompt_builder import build_skills_system_prompt, clear_skills_system_prompt_cache
    from agent.skill_commands import get_skill_commands
    home, db = scoped_home
    skills = home / 'skills'
    skills.mkdir(exist_ok=True)
    first = skills / 'first-test-skill'
    first.mkdir()
    (first / 'SKILL.md').write_text('---\nname: first-test-skill\ndescription: First synthetic skill\n---\nFirst body.\n')
    clear_skills_system_prompt_cache()
    old_index = build_skills_system_prompt(skills_dir_override=skills)
    assert 'first-test-skill' in old_index
    pinned = db.get_session('contract-session')['system_prompt']
    second = skills / 'second-test-skill'
    second.mkdir()
    (second / 'SKILL.md').write_text('---\nname: second-test-skill\ndescription: Second synthetic skill\n---\nSecond body.\n')
    adapter = APIServerAdapter(PlatformConfig(enabled=True, extra={'key': KEY}))
    try:
        with no_models(adapter):
            result, _ = await adapter._run_agent('/reload-skills', [], session_id='contract-session')
        assert not result.get('failed'), result
        assert '/second-test-skill' in get_skill_commands()
        new_index = build_skills_system_prompt(skills_dir_override=skills)
        assert 'second-test-skill' in new_index
        assert 'second-test-skill' not in old_index
        assert db.get_session('contract-session')['system_prompt'] == pinned
    finally:
        clear_skills_system_prompt_cache()
        adapter._close_cached_session_dbs()


@pytest.mark.asyncio
async def test_native_runs_service_reply_is_persisted_completed_and_model_free(scoped_home):
    import asyncio
    _, db = scoped_home
    adapter = APIServerAdapter(PlatformConfig(enabled=True, extra={'key': KEY}))
    app = web.Application()
    app['api_server_adapter'] = adapter
    app.router.add_post('/v1/runs', adapter._handle_runs)
    app.router.add_get('/v1/runs/{run_id}', adapter._handle_get_run)
    before = db.get_messages('contract-session')
    with no_models(adapter):
        async with TestClient(TestServer(app)) as client:
            response = await client.post('/v1/runs', headers={'Authorization': 'Bearer ' + KEY}, json={
                'input': '/model', 'session_id': 'contract-session',
            })
            assert response.status == 202
            run_id = (await response.json())['run_id']
            task = adapter._active_run_tasks.get(run_id)
            if task:
                await asyncio.wait_for(task, timeout=10)
            response = await client.get('/v1/runs/' + run_id, headers={'Authorization': 'Bearer ' + KEY})
            body = await response.json()
            assert body['status'] == 'completed'
            assert body['usage']['total_tokens'] == 0
            assert 'alpha-model' in body['output']
    after = db.get_messages('contract-session')
    assert after[:len(before)] == before
    assert len(after) == len(before) + 2
    assert after[-2]['content'] == '/model'
    assert after[-1]['content'] == body['output']
    adapter._close_cached_session_dbs()


@pytest.mark.asyncio
async def test_native_runs_validated_room_dispatch_cannot_read_owner_command(scoped_home):
    import asyncio
    from unittest.mock import AsyncMock
    _, db = scoped_home
    adapter = APIServerAdapter(PlatformConfig(enabled=True, extra={'key': KEY}))
    app = web.Application()
    app['api_server_adapter'] = adapter
    app.router.add_post('/v1/runs', adapter._handle_runs)
    before = db.get_messages('contract-session')
    # Grant authentication itself is exercised in test_api_server_room_grants;
    # here inject its validated result and exercise the new service branch.
    async def normalized(request, body):
        return {**body, 'hosted_room_dispatch': {'room_id': 'synthetic-room'}}, None
    with no_models(adapter), patch.object(adapter, '_normalize_room_dispatch', new=AsyncMock(side_effect=normalized)):
        async with TestClient(TestServer(app)) as client:
            response = await client.post('/v1/runs', headers={'Authorization': 'Bearer ' + KEY}, json={
                'input': '/model', 'session_id': 'contract-session',
            })
            assert response.status == 202
            run_id = (await response.json())['run_id']
            task = adapter._active_run_tasks.get(run_id)
            if task:
                await asyncio.wait_for(task, timeout=10)
            status = adapter._run_statuses[run_id]
            assert status['status'] == 'completed'
            assert 'только владельцу' in status['output']
            assert 'alpha-model' not in status['output']
    assert db.get_messages('contract-session') == before
    adapter._close_cached_session_dbs()


@pytest.mark.asyncio
async def test_responses_service_reply_then_ordinary_continuation_keeps_old_prefix(scoped_home):
    adapter = APIServerAdapter(PlatformConfig(enabled=True, extra={'key': KEY}))
    app = web.Application()
    app['api_server_adapter'] = adapter
    app.router.add_post('/v1/responses', adapter._handle_responses)
    history = [{'role': 'user', 'content': 'Исходная задача'}, {'role': 'assistant', 'content': 'Исходный результат'}]
    async with TestClient(TestServer(app)) as client:
        with no_models(adapter):
            response = await client.post('/v1/responses', headers={'Authorization': 'Bearer ' + KEY}, json={
                'model': 'korra-agent', 'input': history + [{'role': 'user', 'content': '/help'}],
            })
            assert response.status == 200
            data = await response.json()
        stored = adapter._response_store.get(data['id'])['conversation_history']
        assert stored[:len(history)] == history
        assert stored[-2]['content'] == '/help'
        assert 'Служебные команды' in stored[-1]['content']
        agent = MagicMock()
        agent.session_prompt_tokens = agent.session_completion_tokens = agent.session_total_tokens = 0
        agent.run_conversation.return_value = {'final_response': 'продолжение', 'completed': True}
        with patch.object(adapter, '_create_agent', return_value=agent):
            next_response = await client.post('/v1/responses', headers={'Authorization': 'Bearer ' + KEY}, json={
                'model': 'korra-agent', 'input': 'Продолжи исходную задачу', 'previous_response_id': data['id'],
            })
            assert next_response.status == 200
        agent.run_conversation.assert_called_once()
        assert agent.run_conversation.call_args.kwargs['user_message'] == 'Продолжи исходную задачу'
        assert agent.run_conversation.call_args.kwargs['conversation_history'] == stored
    adapter._close_cached_session_dbs()


@pytest.mark.asyncio
@pytest.mark.parametrize('command', ['/nosuchcmd', '/mcp-relod', '/nosuchcmd с аргументом'])
@pytest.mark.parametrize('stream', [False, True])
async def test_unknown_slash_command_gets_local_reply_without_model(scoped_home, command, stream):
    home, db = scoped_home
    adapter = APIServerAdapter(PlatformConfig(enabled=True, extra={'key': KEY}))
    before_messages = db.get_messages('contract-session')
    with no_models(adapter):
        async with TestClient(TestServer(app_for(adapter))) as client:
            answer = await post_command(client, command, stream)
    name = command.split()[0]
    assert answer == f'Команда {name} неизвестна или недоступна в веб-чате. Список — /help'
    messages = db.get_messages('contract-session')
    assert messages[:-2] == before_messages
    assert [(row['role'], row['content']) for row in messages[-2:]] == [
        ('user', command), ('assistant', answer),
    ]
    adapter._close_cached_session_dbs()


@pytest.mark.parametrize('alias', ['/mcp-reload', '/reload-mcps'])
def test_reload_mcp_aliases_resolve_and_reach_web_mcp_command(alias):
    from korra_cli.commands import resolve_command
    from gateway.web_mcp_command import is_web_mcp_command, run_web_mcp_command
    assert resolve_command(alias).name == 'reload-mcp'
    assert is_web_mcp_command(alias)
    assert 'без аргументов' in run_web_mcp_command(None, alias + ' extra', 's1')

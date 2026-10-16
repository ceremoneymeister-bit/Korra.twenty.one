"""K21-375: reload → real API factory/AIAgent loop → native MCP tool → final.

Only the remote model SDK boundary is replaced. MCP transport, profile/config,
agent construction, tool schema, conversation loop, dispatch and session DB
remain real. No remote HTTP request or paid model is used.
"""
import copy
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
import yaml

from gateway.config import PlatformConfig
from gateway.platforms.api_server import APIServerAdapter, _api_request_profile


def response(*, content=None, tool_calls=None):
    return SimpleNamespace(
        choices=[SimpleNamespace(
            message=SimpleNamespace(content=content, tool_calls=tool_calls),
            finish_reason='tool_calls' if tool_calls else 'stop',
        )],
        model='test-model', usage=None,
    )


@pytest.mark.asyncio
async def test_next_real_agent_turn_receives_schema_and_calls_native_mcp(tmp_path):
    from agent.secret_scope import is_multiplex_active, set_multiplex_active
    from gateway.run import _profile_runtime_scope
    from korra_cli.profiles import get_profile_dir
    from tools import mcp_tool
    from run_agent import AIAgent

    profile_name = 'reload-loop'
    home = get_profile_dir(profile_name)
    home.mkdir(parents=True, exist_ok=True)
    call_log = tmp_path / 'native-tool-calls.jsonl'
    fixture = Path(__file__).resolve().parents[1] / 'fixtures' / 'mcp_reload_server.py'
    (home / '.env').write_text('RELOAD_MARKER=profile-loop\n')
    (home / 'config.yaml').write_text(yaml.safe_dump({
        'model': {'provider': 'custom', 'default': 'test-model',
                  'base_url': 'http://127.0.0.1:9/v1', 'api_mode': 'chat_completions',
                  'context_length': 131072},
        'auxiliary': {'background_review': {'enabled': False}},
        'approvals': {'mcp_reload_confirm': False},
        'tools': {'tool_search': False},
        'platform_toolsets': {'api_server': ['mcp-local']},
        'mcp_servers': {'local': {
            'command': sys.executable, 'args': [str(fixture)],
            'env': {'RELOAD_MARKER': '${RELOAD_MARKER}', 'RELOAD_CALL_LOG': str(call_log)},
            'tools': {'resources': False, 'prompts': False}, 'connect_timeout': 10,
        }},
    }))
    # This is the normal multiplexer startup switch, not a mocked resolver.
    previous_multiplex = is_multiplex_active()
    set_multiplex_active(True)
    token = _api_request_profile.set(profile_name)
    history = [{'role': 'user', 'content': 'предыдущая задача'},
               {'role': 'assistant', 'content': 'предыдущий результат'}]
    expected_history = copy.deepcopy(history)
    sdk_client = MagicMock()
    requests = []

    def provider_create(**kwargs):
        requests.append(copy.deepcopy(kwargs))
        if len(requests) == 1:
            return response(tool_calls=[SimpleNamespace(
                id='native-echo-call', type='function',
                function=SimpleNamespace(name='mcp__local__echo', arguments='{"value":"next-turn"}'),
            )])
        if len(requests) == 2:
            return response(content='Готово: profile-loop:next-turn')
        raise AssertionError('Unexpected extra model request')

    sdk_client.chat.completions.create.side_effect = provider_create
    try:
        # Construct the adapter in a real scope as in profile middleware.
        with _profile_runtime_scope(home):
            adapter = APIServerAdapter(PlatformConfig(enabled=True, extra={'key': 'x' * 32}))
        with patch('run_agent.OpenAI', return_value=sdk_client):
            reload_result, reload_usage = await adapter._run_agent(
                '/reload-mcp', history, session_id='same-session',
            )
            assert 'local' in reload_result['final_response']
            assert reload_result['messages'] == history
            assert reload_usage['total_tokens'] == 0
            assert requests == []
            agent_ref = [None]
            result, _ = await adapter._run_agent(
                'Вызови echo со значением next-turn', history,
                session_id='same-session', agent_ref=agent_ref,
            )
        assert type(agent_ref[0]) is AIAgent
        assert result['final_response'] == 'Готово: profile-loop:next-turn'
        assert len(requests) == 2
        sent_schema = {tool['function']['name']: tool for tool in requests[0]['tools']}
        assert 'mcp__local__echo' in sent_schema
        assert sent_schema['mcp__local__echo']['function']['parameters']['required'] == ['value']
        sent_messages = requests[0]['messages']
        assert expected_history[0] in sent_messages and expected_history[1] in sent_messages
        tool_results = [msg for msg in requests[1]['messages'] if msg.get('role') == 'tool']
        assert len(tool_results) == 1
        assert tool_results[0]['tool_call_id'] == 'native-echo-call'
        assert 'profile-loop:next-turn' in tool_results[0]['content']
        native_calls = [json.loads(line) for line in call_log.read_text().splitlines()]
        assert len(native_calls) == 1
        assert native_calls[0]['name'] == 'echo'
        assert native_calls[0]['arguments'] == {'value': 'next-turn'}
        assert result['session_id'] == 'same-session'
        with _profile_runtime_scope(home):
            durable = adapter._ensure_session_db().get_messages_as_conversation('same-session')
        assert any(msg.get('role') == 'tool' and 'profile-loop:next-turn' in msg.get('content', '')
                   for msg in durable)
    finally:
        _api_request_profile.reset(token)
        with _profile_runtime_scope(home):
            mcp_tool.shutdown_mcp_servers()
        mcp_tool._stop_mcp_loop()
        set_multiplex_active(previous_multiplex)

"""K21-230: preserve automatic learning, owner settings and truthful accounting."""
import asyncio
import json
import threading
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from agent import background_review as br
import importlib.util
from pathlib import Path
_spec = importlib.util.spec_from_file_location("review_helpers", Path(__file__).parents[1] / "run_agent/test_background_review.py")
_helpers = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_helpers)
_bare_agent = _helpers._bare_agent
from korra_state import SessionDB


@pytest.mark.parametrize('seed', [
    '# owner comment\nmodel: {default: "fake"}\n',
    'auxiliary:\n  background_review:\n    model: "fake" # keep\nother: 0\n',
    'auxiliary: {background_review: {model: "fake"}}\n',
    '{}\n',
    'auxiliary:\n  vision: {}\n',
])
def test_loading_missing_switch_preserves_config_and_enables_learning(tmp_path, monkeypatch, seed):
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    path = tmp_path / 'config.yaml'
    path.write_text(seed)
    before = (path.read_bytes(), path.stat().st_ino, path.stat().st_mtime_ns)
    assert br.load_background_review_settings()[0] is True
    assert br.load_background_review_settings()[0] is True
    assert (path.read_bytes(), path.stat().st_ino, path.stat().st_mtime_ns) == before


@pytest.mark.parametrize('value,expected', [('true', True), ('false', False), ('null', False)])
def test_explicit_switch_is_untouched(tmp_path, monkeypatch, value, expected):
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    path = tmp_path / 'config.yaml'
    original = 'auxiliary: {background_review: {enabled: ' + value + '}}\n'
    path.write_text(original)
    assert br.load_background_review_settings()[0] is expected
    assert path.read_text() == original


@pytest.mark.parametrize('original', [
    'auxiliary: [unterminated\n',
    '[false]\n',
    'false\n',
    'auxiliary: false\n',
    'auxiliary: {background_review: false}\n',
    'auxiliary: {background_review: [false]}\n',
])
def test_invalid_config_stays_untouched_and_does_not_start_review(tmp_path, monkeypatch, original):
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    path = tmp_path / 'config.yaml'
    path.write_text(original)
    assert br.load_background_review_settings()[0] is False
    assert path.read_text() == original


@pytest.mark.parametrize('surface', ['cli', 'gateway'])
def test_manual_refine_without_topic_starts_with_automatic_off(surface):
    from korra_cli.cli_commands_mixin import CLICommandsMixin
    from gateway.slash_commands import GatewaySlashCommandsMixin
    import cli  # noqa: F401 — finish imports before replacing the thread factory
    agent = _bare_agent()
    agent.valid_tool_names = {'skill_manage'}
    agent._session_messages = [{'role': 'user', 'content': 'synthetic'}]
    owner = SimpleNamespace(agent=agent, conversation_history=agent._session_messages,
        _agent_cache_lock=threading.Lock(), _agent_cache={'test': agent},
        _running_agents={}, _session_key_for_source=lambda source: 'test')
    with patch('agent.background_review.load_background_review_settings', return_value=(False, {})), \
         patch('agent.background_review.spawn_background_review_thread', return_value=(lambda: None, 'review')), \
         patch('run_agent.threading.Thread') as thread, patch('cli._cprint'):
        if surface == 'cli':
            CLICommandsMixin._handle_refine_command(owner, '/refine')
        else:
            event = SimpleNamespace(source=object(), get_command_args=lambda: '')
            asyncio.run(GatewaySlashCommandsMixin._handle_refine_command(owner, event))
        thread.return_value.start.assert_called_once()
        assert agent._cached_system_prompt == 'test-cached-system-prompt'


@pytest.mark.parametrize('provider,key,status', [
    ('openai-codex', 'codex-access-synthetic', 'included'),
    ('anthropic', 'sk-ant-oat-synthetic', 'included'),
    ('anthropic', 'sk-ant-api-synthetic', 'estimated'),
])
def test_real_accounting_uses_fork_connection(tmp_path, provider, key, status):
    fork = SimpleNamespace(provider=provider, api_key=key, model='synthetic',
        session_cost_status='estimated', session_cost_source='official',
        session_api_calls=2, session_input_tokens=50, session_estimated_cost_usd=1.0)
    usage = br._snapshot_review_usage(fork)
    with SessionDB(tmp_path / 'state.db') as db:
        parent = SimpleNamespace(_session_db=db, session_id='parent')
        br._record_review_usage_to_parent(parent, usage)
        row = dict(db._conn.execute('SELECT * FROM session_model_usage').fetchone())
        assert row['cost_status'] == status
        assert row['billing_mode'] == ('subscription_included' if status == 'included' else '')
        assert row['estimated_cost_usd'] == (0.0 if status == 'included' else 1.0)
        assert row['api_call_count'] == 2
        assert key not in str(row)


def test_notification_off_does_not_hide_review_result(monkeypatch, caplog):
    agent = _bare_agent()
    agent.memory_notifications = 'off'
    parent_prints = []
    agent._safe_print = parent_prints.append
    fork = SimpleNamespace(_session_messages=[], _memory_enabled=True,
        _user_profile_enabled=False, close=lambda: None,
        shutdown_memory_provider=lambda: None)
    def run(**kwargs):
        fork._session_messages = [{'role': 'tool', 'tool_call_id': 'new',
            'content': json.dumps({'success': True, 'message': 'Entry added', 'target': 'memory'})}]
    fork.run_conversation = run
    with patch('agent.background_review.build_cache_parity_fork', return_value=(fork, {}, False)), \
         caplog.at_level('INFO', logger='agent.background_review'):
        br._run_review_in_thread(agent, [], 'review', review_memory=True)
    assert any('result=memory' in record.message for record in caplog.records)
    assert parent_prints == []


def test_review_settings_remain_profile_local(tmp_path, monkeypatch):
    first = tmp_path / 'first'
    second = tmp_path / 'second'
    first.mkdir()
    second.mkdir()
    first_config = '# keep\nmodel: {default: fake}\n'
    second_config = 'auxiliary: {background_review: {enabled: false}}\n'
    (first / 'config.yaml').write_text(first_config)
    (second / 'config.yaml').write_text(second_config)
    monkeypatch.setenv('HERMES_HOME', str(first))
    assert br.load_background_review_settings()[0] is True
    monkeypatch.setenv('HERMES_HOME', str(second))
    assert br.load_background_review_settings()[0] is False
    assert (first / 'config.yaml').read_text() == first_config
    assert (second / 'config.yaml').read_text() == second_config


@pytest.mark.parametrize('surface', ['cli', 'gateway'])
def test_refine_busy_does_not_announce_start(surface):
    import cli  # noqa: F401 — initialize CLI imports before patches
    from korra_cli.cli_commands_mixin import CLICommandsMixin
    from gateway.slash_commands import GatewaySlashCommandsMixin
    agent = _bare_agent()
    agent._session_messages = [{'role': 'user', 'content': 'synthetic'}]
    owner = SimpleNamespace(agent=agent, conversation_history=agent._session_messages,
        _agent_cache_lock=threading.Lock(), _agent_cache={'test': agent},
        _running_agents={}, _session_key_for_source=lambda source: 'test')
    with patch('agent.background_review.prepare_background_review_run', return_value=None), \
         patch('cli._cprint') as output:
        if surface == 'cli':
            CLICommandsMixin._handle_refine_command(owner, '/refine')
            message = str(output.call_args)
        else:
            event = SimpleNamespace(source=object(), get_command_args=lambda: '')
            message = asyncio.run(GatewaySlashCommandsMixin._handle_refine_command(owner, event))
    assert 'уже выполняется' in message
    assert '⚗' not in message


def test_fresh_profile_can_start_automatic_review_without_config_write(tmp_path, monkeypatch):
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    assert not (tmp_path / 'config.yaml').exists()
    assert br.load_background_review_settings()[0] is True
    agent = _bare_agent()
    with patch('agent.background_review.spawn_background_review_thread', return_value=(lambda: None, 'review')), \
         patch('run_agent.threading.Thread') as thread:
        assert agent._spawn_background_review([], review_memory=True) is True
    thread.return_value.start.assert_called_once()
    assert agent._cached_system_prompt == 'test-cached-system-prompt'
    assert not (tmp_path / 'config.yaml').exists()

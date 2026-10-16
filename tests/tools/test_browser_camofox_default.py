"""K21-336/K21-365: implicit choice, stale tabs, and page ownership over real Unix HTTP."""
import json
from unittest.mock import patch

import pytest
from korra_cli import camofox_embedded as emb
from tests.fakes.camofox_embedded import install, data, in_profile, choose_browser, UnixServer  # noqa: F401
from tools import browser_camofox as bc, browser_tool as bt


@pytest.fixture(autouse=True)
def clean():
    bc._sessions.clear()
    bc._health_cache = (0, '', True)
    yield
    bc._sessions.clear()
    bc._unix_session = None
    bc._health_cache = (0, '', True)
    if hasattr(bt, '_page_browser_backends'):
        bt._page_browser_backends.clear()


def test_explicit_service_runtime_and_picker_agree_without_rewriting_config(install, data):
    from korra_cli.nous_subscription import get_nous_subscription_features
    config = data / 'config.yaml'
    config.write_text('browser:\n  cloud_provider: camofox\n  inactivity_timeout: 120\n')
    before = config.read_bytes()
    emb.ensure_state()
    with UnixServer(emb.socket_path(), reply=b'{"ok":true,"tabId":"new","snapshot":"page"}'):
        assert emb.sync(signal=False)
        assert bc.is_camofox_mode()
        assert get_nous_subscription_features({'browser': {'cloud_provider': 'camofox'}}).browser.current_provider == 'Camofox'
        assert json.loads(bt.browser_navigate('https://example.com', 'implicit'))['success']
    assert config.read_bytes() == before


@pytest.mark.parametrize('status', [404, 410])
@pytest.mark.parametrize('action', ['snapshot', 'click', 'type', 'scroll', 'back', 'press', 'images', 'eval'])
def test_every_stale_action_tells_model_how_to_continue(install, data, status, action):
    choose_browser('Camofox')
    def respond(method, path, body):
        if '/tabs/old/' in path:
            return status, b'{"error":"Tab destroyed"}'
    with UnixServer(emb.socket_path(), respond=respond):
        session = bc._get_session('stale')
        session.update(tab_id='old', url='https://example.com/task')
        calls = {
            'snapshot': lambda: bt.browser_snapshot(task_id='stale'),
            'click': lambda: bt.browser_click('e1', task_id='stale'),
            'type': lambda: bt.browser_type('e1', 'value', task_id='stale'),
            'scroll': lambda: bt.browser_scroll('down', task_id='stale'),
            'back': lambda: bt.browser_back(task_id='stale'),
            'press': lambda: bt.browser_press('Enter', task_id='stale'),
            'images': lambda: bt.browser_get_images(task_id='stale'),
            'eval': lambda: bt._camofox_eval('1', task_id='stale'),
        }
        result = json.loads(calls[action]())
    assert not result['success']
    assert 'browser_navigate' in result['error'] and 'https://example.com/task' in result['error']
    assert session['tab_id'] is None


def test_navigate_replaces_a_410_tab_directly(install, data):
    choose_browser('Camofox')
    def respond(method, path, body):
        if '/tabs/old/' in path:
            return 410, b'{"error":"Tab destroyed"}'
        return 200, b'{"ok":true,"tabId":"new","snapshot":"page"}'
    with UnixServer(emb.socket_path(), respond=respond):
        bc._get_session('stale').update(tab_id='old')
        result = json.loads(bt.browser_navigate('https://example.com/new', 'stale'))
        assert result['success']
        assert bc._get_session('stale')['tab_id'] == 'new'


def test_fallback_page_stays_chromium_after_health_recovers(install, data):
    choose_browser('Camofox')
    def respond(method, path, body):
        if path == '/tabs':
            return 503, b'{"error":"browser broken"}'
        return 200, b'{"ok":true}'
    with UnixServer(emb.socket_path(), respond=respond) as server:
        with patch.object(emb, 'recover', return_value=False), patch.object(bt, '_run_browser_command', return_value={'success':True, 'data':{}}):
            result = json.loads(bt.browser_navigate('https://example.com', 'fallback'))
        assert result['success']
        bc._health_cache = (0, '', True)
        server.seen.clear()
        with patch.object(bt, '_run_browser_command', return_value={'success':True, 'data':{}}) as chromium:
            result = json.loads(bt.browser_click('e1', task_id='fallback'))
        assert result['success']
        chromium.assert_called()
        assert not any('/click' in r['path'] for r in server.seen)


def test_embedded_server_has_a_shared_small_tab_budget(install, data):
    emb.ensure_state()
    env = emb.server_env()
    assert 0 < int(env['MAX_TABS_GLOBAL']) <= 3
    assert int(env['MAX_TABS_PER_SESSION']) <= int(env['MAX_TABS_GLOBAL'])
    assert int(env['MAX_SESSIONS']) <= int(env['MAX_TABS_GLOBAL'])


def test_same_task_name_in_another_profile_does_not_inherit_page_backend(install, data):
    with in_profile(data, 'a'):
        bt._pin_page_browser('same', 'chromium')
        assert not bt._is_camofox_page('same')
    with in_profile(data, 'b'), patch.object(emb, 'health', return_value=True):
        choose_browser('Camofox')
        assert bt._is_camofox_page('same')


def test_resource_admission_does_not_restart_browser_or_switch_to_chromium(install, data):
    choose_browser('Camofox')
    def respond(method, path, body):
        if path == '/tabs':
            return 503, b'{"error":"Maximum concurrent sessions reached","code":"admission_rejected"}'
    with UnixServer(emb.socket_path(), respond=respond), patch.object(emb, 'recover') as recover, patch.object(bt, '_run_browser_command') as chromium:
        result = json.loads(bt.browser_navigate('https://example.com', 'limited'))
    assert not result['success'] and 'Maximum concurrent sessions' in result['error']
    recover.assert_not_called()
    chromium.assert_not_called()


def test_same_task_id_keeps_distinct_profile_sessions_and_login_files(install, data):
    emb.ensure_state()
    with UnixServer(emb.socket_path()):
        with in_profile(data, 'a') as a:
            first = bc._get_session('same')
            first['tab_id'] = 'tab-a'
            (a / 'chromium-login').write_bytes(b'existing chromium a')
        with in_profile(data, 'b') as b:
            second = bc._get_session('same')
            second['tab_id'] = 'tab-b'
            (b / 'chromium-login').write_bytes(b'existing chromium b')
        assert first['user_id'] != second['user_id']
        with in_profile(data, 'a'):
            assert bc._get_session('same') is first
            assert bc._drop_session('same') is first
        with in_profile(data, 'b'):
            assert bc._get_session('same') is second
        assert (a / 'chromium-login').read_bytes() == b'existing chromium a'
        assert (b / 'chromium-login').read_bytes() == b'existing chromium b'

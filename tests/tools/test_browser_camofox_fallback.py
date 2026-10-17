"""K21-336: Chromium first; one bounded bundled fallback over real Unix HTTP."""
import json
import time
import threading
from pathlib import Path
from unittest.mock import patch

import pytest

from korra_cli import camofox_embedded as emb
from tests.fakes.camofox_embedded import install, data, in_profile, choose_browser, UnixServer  # noqa: F401
from tools import browser_camofox as bc, browser_tool as bt

FIXTURES = Path(__file__).parent / 'fixtures' / 'camofox-blocks.json'


@pytest.fixture(autouse=True)
def clean():
    bc._sessions.clear()
    bt._page_browser_backends.clear()
    yield
    bc._sessions.clear()
    bc._unix_session = None
    bt._page_browser_backends.clear()
    if hasattr(bt, '_camofox_hosts'):
        bt._camofox_hosts.clear()


@pytest.mark.parametrize('row', json.loads(FIXTURES.read_text()), ids=lambda r: r['case'])
def test_real_block_pages_and_open_pages(row):
    assert bool(bt._chromium_failure_reason(row['payload'])) == row['blocked']


@pytest.mark.parametrize('payload', [
    {'title': 'How CAPTCHA works', 'snapshot': '- heading "How CAPTCHA works"\n- article: captcha and Cloudflare explained'},
    {'title': 'Cloudflare documentation', 'snapshot': '- heading "Cloudflare documentation"'},
    {'title': 'Home', 'snapshot': '- article: access denied errors and captcha', 'status': 403},
    {'success': False, 'error': 'Blocked: URL targets a cloud metadata endpoint'},
    {'title': 'Home'},  # snapshot failure is not an empty page
])
def test_ordinary_articles_and_safety_errors_are_not_blocks(payload):
    assert not bt._chromium_failure_reason(payload)


def chromium(command_snapshot='- heading "Access denied"'):
    def run(task, command, *args, **kwargs):
        if command == 'open':
            return {'success': True, 'data': {'title': 'Access denied', 'url': 'https://example.com/blocked'}}
        return {'success': True, 'data': {'snapshot': command_snapshot}}
    return run


@pytest.fixture
def local(install, data):
    emb.ensure_state()
    with patch.object(bt, '_get_session_info', return_value={'_first_nav': False}), \
         patch.object(bt, '_get_cloud_provider', return_value=None), \
         patch.object(bt, '_run_browser_command', side_effect=chromium()) as run:
        yield run


def test_block_retries_pins_actions_and_same_host_only(local, data):
    reply = json.dumps({'ok': True, 'tabId': 'fallback', 'url': 'https://example.com/ok', 'snapshot': '- heading "Content"'}).encode()
    with UnixServer(emb.socket_path(), reply=reply) as server:
        result = json.loads(bt.browser_navigate('https://example.com/blocked?private=query', 'task'))
        assert result['success'] and result['browser_backend'] == 'camofox'
        assert 'Chromium' in result['note'] and 'Camofox' in result['note']
        assert bt._is_camofox_page('task')
        assert emb.is_enabled() and (emb.state_dir() / 'fallback-requested').is_file()
        choose_browser('Local Browser')  # later settings saves retain an already requested spare
        assert emb.sync(signal=False) and emb.is_enabled()
        local.reset_mock()
        assert json.loads(bt.browser_click('e1', 'task'))['success']
        assert json.loads(bt.browser_navigate('https://example.com/next', 'task'))['success']
        local.assert_not_called()
        assert any('/navigate' in r['path'] for r in server.seen)
        bt.browser_navigate('https://other.example/', 'task')
        local.assert_called()


def test_failed_fallback_preserves_chromium_and_does_not_recurse(local):
    def respond(method, path, body):
        if path == '/tabs':
            return 503, b'{"error":"failed"}'
    with UnixServer(emb.socket_path(), respond=respond) as server, patch.object(emb, 'recover') as recover:
        result = json.loads(bt.browser_navigate('https://example.com/blocked', 'task'))
    assert result['title'] == 'Access denied' and 'snapshot' in result
    assert 'Camofox' in result['bot_detection_warning']
    assert not bt._is_camofox_page('task')
    assert sum(r['path'] == '/tabs' for r in server.seen) == 1
    recover.assert_not_called()


def test_normal_page_does_not_start_or_contact_service(local):
    local.side_effect = lambda *a, **kw: {'success': True, 'data': {'title': 'Home', 'snapshot': '- heading "Content"'}}
    with patch.object(emb, '_s6_svc') as start, UnixServer(emb.socket_path()) as server:
        assert json.loads(bt.browser_navigate('https://example.com/', 'task'))['success']
    assert not server.seen and not emb.is_enabled()
    start.assert_not_called()


@pytest.mark.parametrize('choice', [None, 'Local Browser'])
def test_local_and_implicit_browsers_have_fallback(local, choice):
    if choice:
        choose_browser(choice)
    assert not bc.is_camofox_mode()
    with UnixServer(emb.socket_path(), reply=b'{"ok":true,"tabId":"t","snapshot":"content"}'):
        assert json.loads(bt.browser_navigate('https://example.com', 't'))['browser_backend'] == 'camofox'


def test_owner_switch_and_memory_prevent_attempt(local, monkeypatch):
    for reason in ('memory', 'off'):
        if reason == 'memory':
            monkeypatch.setattr(emb, 'memory_limit_bytes', lambda: 2 * 1024**3)
        else:
            monkeypatch.setattr(emb, 'memory_limit_bytes', lambda: None)
            emb.disable(signal=False)
        with UnixServer(emb.socket_path()) as server:
            result = json.loads(bt.browser_navigate('https://example.com', reason))
        assert not server.seen
        assert ('вручную' if reason == 'memory' else 'выключен') in result['bot_detection_warning']


def test_hung_fallback_has_one_shared_deadline(local, monkeypatch):
    monkeypatch.setattr(bt, '_CAMOFOX_FALLBACK_SECONDS', 0.25)
    release = threading.Event()
    def respond(method, path, body):
        if path == '/tabs':
            release.wait(5)
        return 200, b'{"ok":true,"tabId":"t","snapshot":"content"}'
    with UnixServer(emb.socket_path(), respond=respond) as server:
        started = time.monotonic()
        try:
            result = json.loads(bt.browser_navigate('https://example.com', 'hung'))
            elapsed = time.monotonic() - started
        finally:
            release.set()
    assert result['title'] == 'Access denied' and 'bot_detection_warning' in result
    assert elapsed < 2  # generous scheduler tolerance; configured budget is 0.25s
    assert sum(r['path'] == '/tabs' for r in server.seen) == 1
    assert not bt._is_camofox_page('hung')


def test_timeout_also_tries_the_original_url(local):
    local.side_effect = lambda *a, **kw: {'success': False, 'error': 'Operation timed out'}
    with UnixServer(emb.socket_path(), reply=b'{"ok":true,"tabId":"t","snapshot":"content"}') as server:
        result = json.loads(bt.browser_navigate('https://hh.ru/search/vacancy', 'timeout'))
    assert result['browser_backend'] == 'camofox'
    assert any('https://hh.ru/search/vacancy' in r['body'] for r in server.seen)


@pytest.mark.parametrize('browser,external,cdp', [
    ({'cloud_provider': 'browserbase'}, '', ''),
    ({}, 'http://external.example', ''),
    ({}, '', 'http://cdp.example:9222'),
    ({'use_gateway': True}, '', ''),
    ({'engine': 'lightpanda'}, '', ''),
])
def test_separate_backends_do_not_gain_auto_fallback(local, data, monkeypatch, browser, external, cdp):
    import yaml
    (data / 'config.yaml').write_text(yaml.safe_dump({'browser': browser}))
    monkeypatch.setenv('CAMOFOX_URL', external)
    monkeypatch.setenv('BROWSER_CDP_URL', cdp)
    assert bt._camofox_auto_policy() is None
    with UnixServer(emb.socket_path()) as server:
        original = {'success': True, 'title': 'Access denied'}
        assert bt._maybe_camofox_page('https://example.com', 't', original) is original
    assert not server.seen


def test_explicit_camofox_skips_chromium(local):
    choose_browser('Camofox')
    with UnixServer(emb.socket_path(), reply=b'{"ok":true,"tabId":"t","snapshot":"content"}'):
        assert json.loads(bt.browser_navigate('https://example.com', 'explicit'))['success']
    local.assert_not_called()


def test_camofox_challenge_is_a_failed_rescue(local):
    with UnixServer(emb.socket_path(), reply=json.dumps({"ok": True, "tabId": "t", "snapshot": '- heading "Access denied"'}).encode()):
        result = json.loads(bt.browser_navigate('https://example.com', 'still-blocked'))
    assert result['title'] == 'Access denied' and not bt._is_camofox_page('still-blocked')


def test_one_profile_cannot_inherit_another_profiles_host_affinity(local, data):
    with in_profile(data, 'one'):
        bt._camofox_hosts[(str(bt.get_hermes_home()), 'same')] = {'example.com'}
    with in_profile(data, 'two'):
        with patch.object(bt, '_try_camofox_page', return_value={'success': True}) as fallback:
            bt.browser_navigate('https://example.com', 'same')
        local.assert_called()  # Chromium must be attempted before the spare in this profile
        fallback.assert_called_once()


def test_transition_log_contains_host_reason_outcome_without_query(local, caplog):
    import logging
    with UnixServer(emb.socket_path(), reply=b'{"ok":true,"tabId":"t","snapshot":"content"}'), caplog.at_level(logging.INFO):
        bt.browser_navigate('https://example.com/path?private=hidden', 'log')
    lines = [r.message for r in caplog.records if r.message.startswith('Camofox fallback host=')]
    assert len(lines) == 1 and 'example.com' in lines[0] and 'outcome=success' in lines[0]
    assert 'hidden' not in lines[0] and '/path' not in lines[0]


@pytest.mark.parametrize('reply', [
    {'url': 'https://example.com/challenge', 'snapshot': '- form'},
    {'url': 'https://example.com/', 'snapshot': '- heading "Server error"', 'httpStatus': 503},
    {'url': 'http://169.254.169.254/latest/meta-data/', 'snapshot': 'metadata must not be returned'},
])
def test_final_camofox_url_and_status_are_checked_before_accepting(local, reply):
    with UnixServer(emb.socket_path(), reply=json.dumps({'ok': True, 'tabId': 't', **reply}).encode()):
        result = json.loads(bt.browser_navigate('https://example.com', 'redirect'))
    assert result['title'] == 'Access denied' and 'browser_backend' not in result
    assert not bt._is_camofox_page('redirect')


def test_cleanup_drops_spare_session_and_affinity_after_chromium_was_opened(local, data):
    with UnixServer(emb.socket_path(), reply=b'{"ok":true,"tabId":"t","snapshot":"content"}'):
        bt.browser_navigate('https://example.com', 'cleanup')
        bt._pin_page_browser('cleanup', 'chromium')
        bt.cleanup_browser('cleanup')
    key = (str(data), 'cleanup')
    assert key not in bc._sessions and key not in bt._camofox_hosts


@pytest.mark.parametrize('status', [403, 429, 503])
def test_http_errors_need_leading_antibot_evidence(status):
    assert bt._chromium_failure_reason({'status': status, 'snapshot': '- StaticText "Unusual traffic from your computer network"'})
    assert not bt._chromium_failure_reason({'status': status, 'snapshot': '- heading "Article about captcha"\n- paragraph: A normal article'})


def test_failed_site_affinity_releases_the_host_back_to_chromium(local, data):
    key = (str(data), 'stuck')
    bt._camofox_hosts[key] = {'example.com'}
    with patch.object(emb, 'start_for_fallback', return_value=False):
        result = json.loads(bt.browser_navigate('https://example.com', 'stuck'))
    assert result['success'] is False and 'Chromium' in result['bot_detection_warning']
    assert key not in bt._camofox_hosts
    with patch.object(bt, '_try_camofox_page', side_effect=lambda url, task, payload, reason: payload) as fallback:
        bt.browser_navigate('https://example.com', 'stuck')
    local.assert_called()  # the next navigation starts with Chromium again
    assert fallback.call_args.args[3] != 'site affinity'

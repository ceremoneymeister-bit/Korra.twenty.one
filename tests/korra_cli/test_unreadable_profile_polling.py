"""Polling survives real permission failures, without claiming a complete view."""
import asyncio
import os
import socket
import tempfile
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path

import certifi
import httpx
import pytest
import uvicorn
from aiohttp import web
from aiohttp.test_utils import TestServer

from gateway.config import PlatformConfig
from gateway.platforms.api_server import APIServerAdapter
from korra_cli import web_server
from korra_cli.effect_decision_routes import effect_routes
from korra_constants import reset_hermes_home_override, set_hermes_home_override
from korra_state import SessionDB
from tools.effect_decisions import EffectDecisionStoreUnavailable, create_pending


@contextmanager
def reader_uid():
    # Root bypasses chmod. Use a real unprivileged identity for the request,
    # including its thread-pool filesystem calls, and always restore it.
    original = os.geteuid()
    if original == 0:
        os.seteuid(65534)
    try:
        yield
    finally:
        if original == 0:
            os.seteuid(original)


@asynccontextmanager
async def http_panel():
    """Real uvicorn + persistent HTTP, so a dropped connection fails the test."""
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        url = f'http://127.0.0.1:{sock.getsockname()[1]}'
        server = uvicorn.Server(uvicorn.Config(
            web_server.app, lifespan='off', access_log=False, log_config=None,
        ))
        task = asyncio.create_task(server.serve(sockets=[sock]))
        try:
            async with asyncio.timeout(10):
                while not server.started:
                    if task.done():
                        await task
                    await asyncio.sleep(0.01)
            yield url
        finally:
            server.should_exit = True
            await asyncio.wait_for(task, timeout=10)


@pytest.mark.linux_only
@pytest.mark.asyncio
@pytest.mark.parametrize('blocked', ['directory', 'ledger', 'state'])
@pytest.mark.parametrize('first_endpoint', ['runs', 'decisions', 'sessions'])
async def test_polling_isolates_unreadable_profile_and_recovers(monkeypatch, blocked, first_endpoint):
    # Independent of pytest's root-owned parent directories. No live HOME.
    with tempfile.TemporaryDirectory(prefix='korra-profile-permissions-', dir='/tmp') as temporary:
        root = Path(temporary)
        # httpx creates a fresh SSL context even for loopback HTTP. Make its
        # CA bundle readable to the test UID outside the root-owned venv.
        ca = root / 'ca.pem'
        ca.write_bytes(Path(certifi.where()).read_bytes())
        monkeypatch.setenv('SSL_CERT_FILE', str(ca))
        home = root / '.hermes'
        monkeypatch.setattr(Path, 'home', lambda: root)
        monkeypatch.setenv('HERMES_HOME', str(home))
        # conftest pins this separate default-path seam to its own root-owned
        # temp dir. Keep unscoped requests inside the same readable fixture.
        monkeypatch.setattr('korra_state.DEFAULT_DB_PATH', home / 'state.db')
        monkeypatch.setenv('API_SERVER_KEY', 'test-internal-key')
        monkeypatch.setattr(web_server, '_SESSION_TOKEN', 'test-panel-key')
        monkeypatch.setattr(web_server.app.state, 'auth_required', False, raising=False)
        decisions = {}
        for name in ('default', 'healthy', 'blocked'):
            directory = home if name == 'default' else home / 'profiles' / name
            directory.mkdir(parents=True, exist_ok=True)
            with SessionDB(directory / 'state.db') as db:
                db.create_session(name + '-chat', source='dashboard', profile_name=name)
                db.append_message(name + '-chat', role='user', content='test-only')
                assert db.try_acquire_session_turn_lease(name + '-chat', name + '-holder', ttl_seconds=300)
            decisions[name] = create_pending(
                kind='outbound_message', owner_id='test-owner', profile=name,
                source_session_id=name + '-chat', source_session_key=name + '-chat',
                payload={'platform': 'telegram', 'target': 'test-only', 'message': 'draft'},
                path=directory / 'effect_decisions.sqlite3',
            )[0]['id']
        adapter = APIServerAdapter(PlatformConfig(enabled=True, extra={'key': 'test-internal-key'}))

        @web.middleware
        async def scope(request, handler):
            name = request.match_info.get('profile', 'default')
            directory = home if name == 'default' else home / 'profiles' / name
            token = set_hermes_home_override(directory)
            try:
                return await handler(request)
            finally:
                reset_hermes_home_override(token)

        gateway = web.Application(middlewares=[scope])
        gateway.router.add_get('/api/effect-decisions', adapter._handle_effect_decisions)
        gateway.router.add_get('/p/{profile}/api/effect-decisions', adapter._handle_effect_decisions)
        async with TestServer(gateway) as upstream, http_panel() as panel_url:
            monkeypatch.setattr(web_server, '_API_SERVER_PROXY_TARGET', str(upstream.make_url('')).rstrip('/'))
            async with httpx.AsyncClient(
                base_url=panel_url,
                headers={'Authorization': 'Bearer test-panel-key'},
            ) as client:
                # Warm real lazy imports while the checkout is still accessible
                # to root; no API or filesystem implementation is mocked.
                for url in ('/api/chat/runs', '/api/chat/decisions?profile=healthy', '/api/sessions?profile=healthy',
                            '/api/chat/decisions', '/api/sessions'):
                    response = await client.get(url)
                    assert response.status_code == 200, response.text
                if os.geteuid() == 0:
                    for path in [root, *root.rglob('*')]:
                        os.chown(path, 65534, 65534)
                directory = home / 'profiles' / 'blocked'
                path = directory if blocked == 'directory' else directory / (
                    'effect_decisions.sqlite3' if blocked == 'ledger' else 'state.db')
                previous_mode = path.stat().st_mode & 0o777
                if blocked == 'directory' and os.geteuid() == 0:
                    os.chown(path, 0, 0)
                path.chmod(0o700 if blocked == 'directory' and os.geteuid() == 0 else 0)
                try:
                    with reader_uid():
                        with pytest.raises((PermissionError, OSError)):
                            (path / 'effect_decisions.sqlite3').stat() if blocked == 'directory' else path.read_bytes()
                        first_url = {
                            'runs': '/api/chat/runs',
                            'decisions': '/api/chat/decisions?profile=healthy',
                            'sessions': '/api/sessions?profile=blocked',
                        }[first_endpoint]
                        first = await client.get(first_url)
                        expected = 503 if first_endpoint == 'sessions' and blocked != 'ledger' else 200
                        assert first.status_code == expected, first.text
                        # Strict authorization never becomes permissive just
                        # because the public poll supports partial results.
                        with pytest.raises(EffectDecisionStoreUnavailable):
                            effect_routes(request_id=decisions['healthy'])
                        answer = await client.post('/api/chat/approval?profile=healthy', json={
                            'session_id': 'healthy-chat', 'request_id': decisions['healthy'], 'choice': 'once',
                        })
                        assert answer.status_code == 503, answer.text
                        for _ in range(2):
                            runs = await client.get('/api/chat/runs')
                            assert runs.status_code == 200, runs.text
                            assert runs.json()['unavailable_profiles'] == ['blocked']
                            assert any(r['profile'] == 'healthy' and r['status'] == 'running' for r in runs.json()['runs'])
                            center = await client.get('/api/chat/decisions?profile=healthy')
                            assert center.status_code == 200, center.text
                            assert center.json()['unavailable_profiles'] == ['blocked']
                            item = next(r for r in center.json()['data'] if r['request_id'] == decisions['healthy'])
                            assert item['choices'] == [] and item['decision_reason']
                            broken = await client.get('/api/chat/decisions?profile=blocked')
                            assert broken.status_code == 503, broken.text
                            assert 'data' not in broken.json()
                            healthy = await client.get('/api/sessions?profile=healthy')
                            assert healthy.status_code == 200, healthy.text
                            assert any(s['id'] == 'healthy-chat' for s in healthy.json()['sessions'])
                            default_sessions = await client.get('/api/sessions')
                            assert default_sessions.status_code == 200, default_sessions.text
                            assert any(s['id'] == 'default-chat' for s in default_sessions.json()['sessions'])
                            default_center = await client.get('/api/chat/decisions')
                            assert default_center.status_code == 200, default_center.text
                            assert default_center.json()['unavailable_profiles'] == ['blocked']
                            assert any(r['request_id'] == decisions['default'] for r in default_center.json()['data'])
                            own_sessions = await client.get('/api/sessions?profile=blocked')
                            assert own_sessions.status_code == (200 if blocked == 'ledger' else 503), own_sessions.text
                finally:
                    path.chmod(previous_mode)
                    if os.geteuid() == 0:
                        os.chown(path, 65534, 65534)
                recovered = await client.get('/api/chat/runs', headers={'If-None-Match': runs.headers['etag']})
                assert recovered.status_code == 200
                assert recovered.json().get('unavailable_profiles', []) == []
                assert any(r['profile'] == 'blocked' for r in recovered.json()['runs'])
                center = await client.get('/api/chat/decisions?profile=healthy')
                item = next(r for r in center.json()['data'] if r['request_id'] == decisions['healthy'])
                assert item['choices'] == ['once', 'deny']

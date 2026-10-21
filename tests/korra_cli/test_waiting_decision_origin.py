"""Real SQLite + panel/API boundary contract for both historical effect profiles."""
import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlparse

import httpx
import pytest
from aiohttp import ClientSession, web
from aiohttp.test_utils import TestServer
from fastapi import FastAPI

from gateway.config import PlatformConfig
from gateway.platforms.api_server import APIServerAdapter
from korra_cli import chat_activity, web_server
from korra_constants import get_hermes_home, reset_hermes_home_override, set_hermes_home_override
from korra_state import SessionDB
from tools import effect_decisions as effects


@pytest.fixture
def homes(tmp_path, monkeypatch):
    home = tmp_path / '.hermes'
    home.mkdir()
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    monkeypatch.setenv('HERMES_HOME', str(home))
    monkeypatch.setenv('API_SERVER_KEY', 'test-internal-key')
    for name in ('owner', 'sender'):
        (home / 'profiles' / name).mkdir(parents=True)
    db = SessionDB(home / 'profiles/owner/state.db')
    db.create_session('origin', source='dashboard', profile_name='owner')
    db.set_session_title('origin', 'Техническое задание')
    db.create_session('unrelated', source='dashboard', profile_name='owner')
    db.close()
    return home


def pending(home, stored_profile='sender', sid='origin', *, ledger='sender'):
    directory = home if ledger == 'default' else home / 'profiles' / ledger
    return effects.create_pending(
        kind='outbound_message', owner_id='test-owner', profile=stored_profile,
        source_session_id=sid, source_session_key=sid,
        payload={'platform': 'telegram', 'message': 'test draft', 'target': 'test-only'},
        path=directory / 'effect_decisions.sqlite3',
    )[0]


@asynccontextmanager
async def panel(monkeypatch, home):
    """Run actual gateway handlers behind panel proxy; no network effects."""
    adapter = APIServerAdapter(PlatformConfig(enabled=True, extra={'key': 'test-internal-key'}))

    @web.middleware
    async def scope(request, handler):
        profile = request.match_info.get('profile', 'default')
        directory = home if profile == 'default' else home / 'profiles' / profile
        token = set_hermes_home_override(directory)
        try:
            return await handler(request)
        finally:
            reset_hermes_home_override(token)

    gateway = web.Application(middlewares=[scope])
    for prefix in ('', '/p/{profile}'):
        gateway.router.add_get(prefix + '/api/effect-decisions', adapter._handle_effect_decisions)
        gateway.router.add_get(prefix + '/api/sessions/{session_id}/approvals', adapter._handle_session_approvals)
        gateway.router.add_post(prefix + '/api/sessions/{session_id}/approval', adapter._handle_session_approval)
    server = TestServer(gateway)
    await server.start_server()
    real_client = httpx.AsyncClient
    app = FastAPI()
    app.router.routes.extend(r for r in web_server.app.router.routes if getattr(r, 'path', '') in {
        '/api/chat/approvals', '/api/chat/approval', '/api/chat/decisions', '/api/chat/runs',
    })
    async with ClientSession() as wire:
        class Upstream:
            def __init__(self, *args, **kwargs): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *args): return False
            async def request(self, method, url, **kwargs):
                async with wire.request(method, server.make_url(urlparse(url).path), **kwargs) as response:
                    return httpx.Response(response.status, content=await response.read(), headers={'content-type': 'application/json'})
            async def get(self, url, **kwargs): return await self.request('GET', url, **kwargs)
            async def post(self, url, **kwargs): return await self.request('POST', url, **kwargs)
        async with real_client(transport=httpx.ASGITransport(app=app), base_url='http://panel') as client:
            with monkeypatch.context() as patch:
                patch.setattr(httpx, 'AsyncClient', Upstream)
                yield client
    await server.close()


@pytest.mark.parametrize('stored_profile,ledger', [('owner', 'owner'), ('sender', 'sender'), ('owner', 'sender')])
def test_waiting_row_resolves_owner_and_chat_in_global_and_filtered_views(homes, stored_profile, ledger):
    decision = pending(homes, stored_profile, ledger=ledger)
    global_rows = chat_activity.project_chat_activity([], profile=None, session_id=None)
    waiting = [r for r in global_rows if r['status'] == 'waiting_decision']
    assert [(r['profile'], r['session_id'], r['title']) for r in waiting] == [
        ('owner', 'origin', 'Техническое задание')]
    assert chat_activity.project_chat_activity([], profile='sender', session_id=None) == []
    assert [r['message_id'] for r in chat_activity.project_chat_activity([], profile='owner', session_id=None)] == [waiting[0]['message_id']]
    assert decision['status'] == 'pending'


@pytest.mark.parametrize('stored_profile', ['sender', 'owner'])
@pytest.mark.asyncio
async def test_old_formats_are_answerable_only_in_origin_and_execute_exactly_once(homes, monkeypatch, stored_profile):
    decision = pending(homes, stored_profile)
    other = pending(homes, 'owner', 'unrelated', ledger='owner')
    sent = []
    # The real resolver still claims the exact payload; replace only transport.
    from tools import send_message_tool
    def execute_gmail(decision_id, payload):
        sent.append((decision_id, payload, str(get_hermes_home())))
        return effects.finish_execution(decision_id, status='succeeded', outcome={'transport': {'success': True}})
    monkeypatch.setattr(send_message_tool, '_execute_gmail_decision', execute_gmail)
    path = homes / 'profiles/sender/effect_decisions.sqlite3'
    payload = {'platform': 'gmail', 'message': 'immutable draft', 'target': 'test-only'}
    with sqlite3.connect(path) as conn:
        conn.execute('UPDATE effect_decisions SET payload_json=?, payload_sha256=? WHERE id=?',
                     (effects.canonical_payload(payload), effects.payload_digest(payload), decision['id']))
    async with panel(monkeypatch, homes) as client:
        before_run = await client.get('/api/chat/runs')
        before_etag = before_run.headers['etag']
        for url in ('/api/chat/approvals?profile=owner&session_id=origin', '/api/chat/decisions?profile=owner'):
            response = await client.get(url)
            assert response.status_code == 200
            assert decision['id'] in [row['request_id'] for row in response.json()['data']]
        sender = await client.get('/api/chat/decisions?profile=sender')
        assert decision['id'] not in [row['request_id'] for row in sender.json()['data']]
        forbidden = await client.post('/api/chat/approval?profile=owner', json={
            'session_id': 'unrelated', 'request_id': decision['id'], 'choice': 'once'})
        assert forbidden.status_code == 409
        for _ in range(2):
            response = await client.post('/api/chat/approval?profile=owner', json={
                'session_id': 'origin', 'request_id': decision['id'], 'choice': 'once'})
            assert response.status_code == 200, response.text
            assert response.json()['effect_status'] == 'succeeded'
        after_run = await client.get('/api/chat/runs', headers={'If-None-Match': before_etag})
        assert after_run.status_code == 200 and after_run.headers['etag'] != before_etag
        assert [(r['profile'], r['session_id']) for r in after_run.json()['runs'] if r['status'] == 'waiting_decision'] == [('owner', 'unrelated')]
    assert sent == [(decision['id'], payload, str(homes / 'profiles/sender'))]
    saved = effects.get_decision(decision['id'], path=path)
    assert saved['payload'] == payload
    assert saved['payload_sha256'] == effects.payload_digest(payload)
    rows = chat_activity.project_chat_activity([], profile=None, session_id=None)
    assert [(r['profile'], r['session_id']) for r in rows if r['status'] == 'waiting_decision'] == [('owner', 'unrelated')]
    assert effects.get_decision(other['id'], path=homes / 'profiles/owner/effect_decisions.sqlite3')['status'] == 'pending'


@pytest.mark.parametrize('kind', ['deleted', 'ambiguous', 'telegram', 'compression'])
@pytest.mark.asyncio
async def test_legacy_origin_resolution_and_ledger_fallback(homes, monkeypatch, kind):
    sid = 'origin'
    if kind == 'deleted':
        sid = 'deleted-origin'
    elif kind == 'ambiguous':
        db = SessionDB(homes / 'state.db')
        db.create_session('origin', source='dashboard')
        db.close()
    elif kind == 'telegram':
        sid = 'agent:main:telegram:dm:test-owner'
        with sqlite3.connect(homes / 'profiles/owner/state.db') as conn:
            conn.execute('UPDATE sessions SET session_key=?,source=? WHERE id=?', (sid, 'telegram', 'origin'))
    elif kind == 'compression':
        db = SessionDB(homes / 'profiles/owner/state.db')
        db.end_session('origin', 'compression')
        db.create_session('compressed', source='dashboard', profile_name='owner', parent_session_id='origin')
        db.close()
    decision = pending(homes, 'owner', sid)
    rows = chat_activity.project_chat_activity([], profile=None, session_id=None)
    row = next(r for r in rows if r['status'] == 'waiting_decision')
    fallback = kind in {'deleted', 'ambiguous'}
    assert bool(row.get('decision_fallback')) == fallback
    assert row['profile'] == ('sender' if fallback else 'owner')
    if fallback:
        assert row['decision_reason']
    else:
        assert row['session_id'] == ('compressed' if kind == 'compression' else 'origin')
        assert row['title'] == 'Техническое задание'
    async with panel(monkeypatch, homes) as client:
        response = await client.get('/api/chat/decisions?profile=' + row['profile'])
        item = next(r for r in response.json()['data'] if r['request_id'] == decision['id'])
        response = await client.post('/api/chat/approval?profile=' + row['profile'], json={
            'session_id': item['source_session_id'], 'request_id': decision['id'], 'choice': 'deny'})
        assert response.status_code == 200, response.text
    assert not [r for r in chat_activity.project_chat_activity([], profile=None, session_id=None) if r['status'] == 'waiting_decision']


@pytest.mark.asyncio
async def test_old_uuid_origin_beyond_first_page_keeps_every_pending(homes, monkeypatch):
    from uuid import uuid4
    sid = str(uuid4())
    db = SessionDB(homes / 'profiles/owner/state.db')
    db.create_session(sid, source='dashboard', profile_name='owner')
    db.set_session_title(sid, 'Старое техническое задание')
    for index in range(105):
        db.create_session(f'newer-{index}', source='dashboard', profile_name='owner')
    db.close()
    ids = set()
    for index in range(105):
        row, _ = effects.create_pending(
            kind='outbound_message', owner_id='test-owner', profile='owner',
            source_session_id=sid, source_session_key=sid,
            payload={'platform': 'telegram', 'message': f'test draft {index}'},
            path=homes / 'profiles/sender/effect_decisions.sqlite3',
        )
        ids.add(row['id'])
    assert not (homes / 'profiles/sender/state.db').exists()
    async with panel(monkeypatch, homes) as client:
        for suffix in ('', '?profile=owner'):
            response = await client.get('/api/chat/runs' + suffix)
            assert response.status_code == 200, response.text
            rows = [r for r in response.json()['runs'] if r['status'] == 'waiting_decision']
            assert [(r['profile'], r['session_id'], r['title'], r['pending_decisions']) for r in rows] == [
                ('owner', sid, 'Старое техническое задание', len(ids))]
        sender = await client.get('/api/chat/runs?profile=sender')
        assert sender.json()['runs'] == []
        response = await client.get('/api/chat/decisions?profile=owner')
        assert {r['request_id'] for r in response.json()['data']} == ids
        response = await client.get('/api/chat/approvals?profile=owner&session_id=' + sid)
        assert {r['request_id'] for r in response.json()['data']} == ids


@pytest.mark.asyncio
async def test_unknown_stored_profile_keeps_blocked_card_in_physical_center(homes, monkeypatch):
    row = pending(homes, 'no-longer-present')
    async with panel(monkeypatch, homes) as client:
        response = await client.get('/api/chat/decisions?profile=sender')
        item = next(r for r in response.json()['data'] if r['request_id'] == row['id'])
        assert item['decision_fallback'] and item['decision_reason']
        assert item['choices'] == []
        response = await client.post('/api/chat/approval?profile=sender', json={
            'session_id': 'origin', 'request_id': row['id'], 'choice': 'deny'})
        assert response.status_code == 409
    assert effects.get_decision(row['id'], path=homes / 'profiles/sender/effect_decisions.sqlite3')['status'] == 'pending'


@pytest.mark.parametrize('second_status', ['pending', 'succeeded'])
@pytest.mark.asyncio
async def test_duplicate_id_cannot_execute_in_either_physical_ledger(homes, monkeypatch, second_status):
    row = pending(homes, 'owner')
    duplicate = pending(homes, 'owner', ledger='owner')
    path = homes / 'profiles/owner/effect_decisions.sqlite3'
    with sqlite3.connect(path) as conn:
        conn.execute('UPDATE effect_decisions SET id=?,status=? WHERE id=?', (row['id'], second_status, duplicate['id']))
    async with panel(monkeypatch, homes) as client:
        runs = (await client.get('/api/chat/runs')).json()['runs']
        sender_wait = next(r for r in runs if r['profile'] == 'sender')
        center = (await client.get('/api/chat/decisions?profile=sender')).json()['data']
        assert sender_wait['decision_fallback'] and center[0]['decision_fallback']
        assert sender_wait['decision_reason'] == center[0]['decision_reason']
        assert center[0]['choices'] == []
        for profile in ('owner', 'sender'):
            response = await client.post('/api/chat/approval?profile=' + profile, json={
                'session_id': 'origin', 'request_id': row['id'], 'choice': 'once'})
            assert response.status_code == 409
    assert effects.get_decision(row['id'], path=homes / 'profiles/sender/effect_decisions.sqlite3')['status'] == 'pending'


def test_routing_is_readonly_and_opens_each_profile_once(homes, monkeypatch):
    from korra_cli.effect_decision_routes import effect_routes
    pending(homes, 'owner')
    pending(homes, 'owner', 'unrelated')
    files = list(homes.rglob('*.db')) + list(homes.rglob('*.sqlite3'))
    before = {path: path.read_bytes() for path in files}
    calls = []
    real_init = SessionDB.__init__
    def record_open(self, *args, **kwargs):
        calls.append((kwargs.get('db_path'), kwargs.get('read_only')))
        real_init(self, *args, **kwargs)
    monkeypatch.setattr(SessionDB, '__init__', record_open)
    assert len(effect_routes()) == 2
    assert len(calls) == 1 and all(read_only for _, read_only in calls)
    assert {path: path.read_bytes() for path in files} == before

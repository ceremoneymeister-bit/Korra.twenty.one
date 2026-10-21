"""Polling resolves relevant origins, independently of finished ledger history."""
import sqlite3

import pytest

from korra_cli import effect_decision_routes
from korra_state import SessionDB
from tools import effect_decisions as effects
from tests.korra_cli.test_waiting_decision_origin import homes, panel  # noqa: F401


def _decision(home, *, ledger='sender', profile='owner', sid='origin', status='pending', suffix=''):
    path = home / 'profiles' / ledger / 'effect_decisions.sqlite3'
    item, _ = effects.create_pending(
        kind='outbound_message', owner_id='test-owner', profile=profile,
        source_session_id=sid, source_session_key=sid,
        payload={'platform': 'telegram', 'target': 'test-only', 'message': f'{sid}:{status}:{suffix}'},
        path=path,
    )
    if status != 'pending':
        with sqlite3.connect(path) as conn:
            conn.execute('UPDATE effect_decisions SET status=? WHERE id=?', (status, item['id']))
    return item['id']


def _finished_history(home, count=240):
    """Unique origins deliberately defeat identity memoization within a request."""
    with SessionDB(home / 'profiles/sender/state.db') as db:
        for index in range(count):
            sid = f'finished-{index}'
            db.create_session(sid, source='dashboard', profile_name='sender')
            _decision(home, profile='sender', sid=sid, status='succeeded')


@pytest.mark.parametrize('endpoint', ['approvals', 'decisions'])
@pytest.mark.asyncio
async def test_polling_origin_cost_does_not_grow_with_unrelated_finished_history(homes, monkeypatch, endpoint):
    wanted = _decision(homes)
    unrelated = _decision(homes, sid='unrelated')
    history = _decision(homes, ledger='owner', status='succeeded')
    calls = []
    real_resolve = effect_decision_routes._origin_candidates

    def observe(row, *args, **kwargs):
        calls.append(row['source_session_id'])
        return real_resolve(row, *args, **kwargs)

    monkeypatch.setattr(effect_decision_routes, '_origin_candidates', observe)
    url = '/api/chat/' + endpoint + '?profile=owner'
    if endpoint == 'approvals':
        url += '&session_id=origin'
    expected = {wanted, history} | ({unrelated} if endpoint == 'decisions' else set())
    async with panel(monkeypatch, homes) as client:
        before = await client.get(url)
        assert before.status_code == 200, before.text
        assert {item['request_id'] for item in before.json()['data']} == expected
        baseline_calls = len(calls)
        assert baseline_calls > 0

        _finished_history(homes)
        calls.clear()
        after = await client.get(url)
        assert after.status_code == 200, after.text
        assert {item['request_id'] for item in after.json()['data']} == expected
        assert len(calls) <= baseline_calls, (
            'Finished records in other chats must not trigger extra origin resolution',
            baseline_calls, len(calls),
        )
        assert not any(sid.startswith('finished-') for sid in calls)


@pytest.mark.asyncio
async def test_center_keeps_bounded_physical_history_and_all_attention(homes, monkeypatch):
    _finished_history(homes)
    # A finished cross-profile receipt belongs to the physical center,
    # independently of whether its old source chat is still resolvable.
    latest = _decision(homes, status='denied', suffix='latest')
    # Give the fixture deterministic recency without waiting on the clock.
    with sqlite3.connect(homes / 'profiles/sender/effect_decisions.sqlite3') as conn:
        conn.execute('UPDATE effect_decisions SET created_at=created_at+60 WHERE id=?', (latest,))
    pending = _decision(homes)
    review = _decision(homes, status='needs_review')
    async with panel(monkeypatch, homes) as client:
        sender = await client.get('/api/chat/decisions?profile=sender')
        assert sender.status_code == 200, sender.text
        items = sender.json()['data']
        ids = {item['request_id'] for item in items}
        assert latest in ids
        assert len(ids) < 240, 'The decision center must keep its bounded recent history'
        assert {pending, review}.isdisjoint(ids), 'Open decisions belong to the origin profile'
        assert all(item['effect_status'] not in {'pending', 'needs_review'} for item in items)

        owner = await client.get('/api/chat/decisions?profile=owner')
        assert owner.status_code == 200, owner.text
        assert {item['request_id'] for item in owner.json()['data']} == {pending, review}


@pytest.mark.parametrize('endpoint', ['approvals', 'decisions'])
@pytest.mark.asyncio
async def test_polling_rechecks_origin_after_chat_disappears(homes, monkeypatch, endpoint):
    pending = _decision(homes)
    url = '/api/chat/' + endpoint + '?profile=owner'
    if endpoint == 'approvals':
        url += '&session_id=origin'
    async with panel(monkeypatch, homes) as client:
        before = await client.get(url)
        assert before.status_code == 200, before.text
        assert pending in {item['request_id'] for item in before.json()['data']}
        with sqlite3.connect(homes / 'profiles/owner/state.db') as conn:
            conn.execute("DELETE FROM sessions WHERE id='origin'")
        after = await client.get(url)
        assert after.status_code == 200, after.text
        assert pending not in {item['request_id'] for item in after.json()['data']}
        fallback = await client.get('/api/chat/decisions?profile=sender')
        assert fallback.status_code == 200, fallback.text
        item = next(item for item in fallback.json()['data'] if item['request_id'] == pending)
        assert item['decision_fallback'] and item['decision_reason']


@pytest.mark.asyncio
async def test_terminal_duplicate_outside_selected_chat_blocks_pending_identity(homes, monkeypatch):
    pending = _decision(homes)
    duplicate = _decision(homes, ledger='owner', sid='unrelated', status='succeeded')
    with sqlite3.connect(homes / 'profiles/owner/effect_decisions.sqlite3') as conn:
        conn.execute('UPDATE effect_decisions SET id=? WHERE id=?', (pending, duplicate))
    async with panel(monkeypatch, homes) as client:
        approvals = await client.get('/api/chat/approvals?profile=owner&session_id=origin')
        assert approvals.status_code == 200, approvals.text
        assert pending not in {item['request_id'] for item in approvals.json()['data']}
        center = await client.get('/api/chat/decisions?profile=sender')
        assert center.status_code == 200, center.text
        item = next(item for item in center.json()['data'] if item['request_id'] == pending)
        assert item['decision_fallback'] and item['decision_reason']
        assert item['choices'] == []
        for profile in ('owner', 'sender'):
            response = await client.post('/api/chat/approval?profile=' + profile, json={
                'session_id': 'origin', 'request_id': pending, 'choice': 'once',
            })
            assert response.status_code == 409, response.text
    assert effects.get_decision(
        pending, path=homes / 'profiles/sender/effect_decisions.sqlite3',
    )['status'] == 'pending'


@pytest.mark.asyncio
async def test_chat_polling_keeps_legacy_gateway_routing_alias_without_session_key(homes, monkeypatch):
    key = 'agent:main:telegram:dm:test-only'
    owner = homes / 'profiles/owner'
    with SessionDB(owner / 'state.db') as db:
        assert not db.get_session('origin').get('session_key')
        db.save_gateway_routing_entry(
            key, '{"session_id":"origin"}', scope=str((owner / 'sessions').resolve()),
        )
    pending = _decision(homes, sid=key)
    async with panel(monkeypatch, homes) as client:
        for endpoint in (
            '/api/chat/approvals?profile=owner&session_id=origin',
            '/api/chat/decisions?profile=owner',
        ):
            response = await client.get(endpoint)
            assert response.status_code == 200, response.text
            item = next(item for item in response.json()['data'] if item['request_id'] == pending)
            assert item['source_session_id'] == 'origin'
            assert not item['decision_fallback']
            assert item['choices'] == ['once', 'deny']
        unrelated = await client.get('/api/chat/approvals?profile=owner&session_id=unrelated')
        assert unrelated.status_code == 200, unrelated.text
        assert pending not in {item['request_id'] for item in unrelated.json()['data']}

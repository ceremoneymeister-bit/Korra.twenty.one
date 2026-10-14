"""K21-203: continuous display history, real compaction and HTTP/profile boundary."""
from pathlib import Path

import pytest

from korra_state import SessionDB


@pytest.fixture
def homes(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    root = tmp_path / '.hermes'
    paths = {'default': root, 'worker': root / 'profiles' / 'worker'}
    for home in paths.values():
        home.mkdir(parents=True)
        (home / 'config.yaml').write_text('{}\n')
    monkeypatch.setenv('HERMES_HOME', str(root))
    return paths


@pytest.fixture
def client(homes, monkeypatch):
    from starlette.testclient import TestClient
    import korra_state
    from korra_cli.web_server import app, _SESSION_HEADER_NAME, _SESSION_TOKEN
    monkeypatch.setattr(korra_state, 'DEFAULT_DB_PATH', homes['default'] / 'state.db')
    client = TestClient(app)
    client.headers[_SESSION_HEADER_NAME] = _SESSION_TOKEN
    yield client
    client.close()


def turns(start, count, tag='turn'):
    return [{'role': role, 'content': f'{tag} {i} {role}', 'timestamp': 1700000000 - i}
            for i in range(start, start + count) for role in ('user', 'assistant')]


def summary():
    return {'role': 'user', 'content': 'Synthetic compression summary',
            '_compressed_summary': True, 'display_kind': 'hidden'}


def page(client, sid='chat', cursor=None, **params):
    response = client.get(f'/api/sessions/{sid}/messages', params={
        'include_history': 'true', 'display_limit': 70,
        **({'before_cursor': cursor} if cursor else {}), **params})
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.parametrize('rotate', [False, True])
def test_history_across_multiple_real_compactions(client, homes, rotate):
    expected = turns(0, 80)
    with SessionDB(homes['default'] / 'state.db') as db:
        sid = 'chat'
        db.create_session(sid, source='dashboard')
        db.append_messages_batch(sid, expected)
        for epoch in range(2):
            live = db.get_messages(sid)
            handoff = [summary(), *live[-4:]]
            if rotate:
                child = f'child-{epoch}'
                db.publish_compression_child(parent_session_id=sid, child_session_id=child,
                    source='dashboard', messages=handoff, require_compression_lease=False,
                    watermark=db.get_active_message_watermark(sid))
                sid = child
            else:
                db.archive_and_compact(sid, handoff,
                    watermark=db.get_active_message_watermark(sid), tail_count=4)
            extra = turns(80 + epoch * 3, 3)
            db.append_messages_batch(sid, extra)
            expected.extend(extra)
        model_before = db.get_messages_as_conversation(sid)
    pages, cursor = [], None
    for _ in range(10):
        result = page(client, cursor=cursor)
        assert len([row for row in result['messages'] if row['role'] in ('user', 'assistant')]) <= 70
        pages.append(result)
        cursor = result['pagination']['next_cursor']
        if not result['pagination']['has_more']:
            break
    else:
        pytest.fail('history failed to terminate')
    rows = [row for result in reversed(pages) for row in result['messages']]
    assert [row['content'] for row in rows] == [row['content'] for row in expected]
    assert len({row['display_id'] for row in rows}) == len(expected)
    assert len(pages[0]['messages']) == 70
    assert any(row['read_only'] for row in pages[0]['messages'])
    assert any(not row['read_only'] for row in pages[0]['messages'])
    with SessionDB(homes['default'] / 'state.db') as db:
        assert db.get_messages_as_conversation(sid) == model_before


@pytest.mark.parametrize('rotate', [False, True])
def test_new_identical_occurrences_are_not_copies(client, homes, rotate):
    repeated = [{'role': role, 'content': 'same', 'timestamp': 1234}
                for _ in range(4) for role in ('user', 'assistant')]
    with SessionDB(homes['default'] / 'state.db') as db:
        db.create_session('chat', source='dashboard')
        db.append_messages_batch('chat', repeated)
        if rotate:
            db.publish_compression_child(parent_session_id='chat', child_session_id='child',
                source='dashboard', messages=[summary()], require_compression_lease=False)
            sid = 'child'
        else:
            db.archive_and_compact('chat', [summary()])
            sid = 'chat'
        # Even same role/content/timestamp at the boundary is a new occurrence.
        db.append_messages_batch(sid, repeated)
    rows = page(client)['messages']
    assert len(rows) == 16
    assert len({row['display_id'] for row in rows}) == 16
    assert sum(row['read_only'] for row in rows) == 8


@pytest.mark.parametrize('rotate', [False, True])
def test_cursor_and_display_identity_survive_compaction_between_pages(client, homes, rotate):
    expected = turns(0, 80)
    with SessionDB(homes['default'] / 'state.db') as db:
        db.create_session('chat', source='dashboard')
        db.append_messages_batch('chat', expected)
    initial = page(client)
    identities = {row['content']: row['display_id'] for row in initial['messages']}
    with SessionDB(homes['default'] / 'state.db') as db:
        live = db.get_messages('chat')
        handoff = [summary(), *live[-80:]]
        if rotate:
            db.publish_compression_child(parent_session_id='chat', child_session_id='child',
                source='dashboard', messages=handoff, require_compression_lease=False)
            sid = 'child'
        else:
            db.archive_and_compact('chat', handoff, tail_count=80)
            sid = 'chat'
        db.append_messages_batch(sid, turns(80, 2))
    older = page(client, cursor=initial['pagination']['next_cursor'])
    oldest = page(client, cursor=older['pagination']['next_cursor'])
    assert [row['content'] for row in oldest['messages'] + older['messages'] + initial['messages']] == [
        row['content'] for row in expected]
    fresh = page(client)
    for row in fresh['messages']:
        if row['content'] in identities:
            assert row['display_id'] == identities[row['content']]
    assert all(row['id'] > 160 for row in fresh['messages'] if not row['read_only'])


def test_cursor_rejects_another_chain_profile_and_malformed_payload(client, homes):
    for name, home in homes.items():
        with SessionDB(home / 'state.db') as db:
            for sid in ('chat', 'other'):
                db.create_session(sid, source='dashboard')
                db.append_messages_batch(sid, turns(0, 80, name))
    cursor = page(client)['pagination']['next_cursor']
    for sid, params in [('chat', {'profile': 'worker'}), ('other', {}),
                        ('chat', {'before_cursor': 'garbage'})]:
        response = client.get(f'/api/sessions/{sid}/messages', params={
            'include_history': 'true', 'display_limit': 70, 'before_cursor': cursor, **params})
        assert response.status_code == 400, response.text
    assert client.get('/api/sessions/chat/messages', params={
        'include_history': 'true', 'profile': 'missing'}).status_code == 404
    assert client.get('/api/sessions/chat/messages', params={'before_cursor': cursor}).status_code == 400


def test_history_tools_count_as_steps_and_legacy_api_is_unchanged(client, homes):
    from tests.korra_cli.test_chat_history_pagination import _turn
    with SessionDB(homes['default'] / 'state.db') as db:
        db.create_session('chat', source='dashboard')
        expected = [row for i in range(80) for row in _turn('step', i, 3)]
        db.append_messages_batch('chat', expected)
        db.archive_and_compact('chat', [summary(), *db.get_messages('chat')[-16:]], tail_count=16)
        model = db.get_messages_as_conversation('chat')
        live = db.get_messages('chat')
    old = client.get('/api/sessions/chat/messages', params={'display_limit': 70}).json()
    assert [row['id'] for row in old['messages']] == [row['id'] for row in live]
    assert old['pagination']['archived_before'] is True
    result = page(client)
    assert sum(row['role'] == 'user' for row in result['messages']) == 35
    assert len(result['messages']) == 35 * 8
    assert result['messages'][0]['role'] == 'user'
    with SessionDB(homes['default'] / 'state.db') as db:
        assert db.get_messages_as_conversation('chat') == model


def test_legacy_parent_chain_without_provenance_reads_readonly(client, homes):
    with SessionDB(homes['default'] / 'state.db') as db:
        db.create_session('chat', source='dashboard')
        db.append_messages_batch('chat', turns(0, 80))
        db.publish_compression_child(parent_session_id='chat', child_session_id='child',
            source='dashboard', messages=[summary(), *db.get_messages('chat')[-4:]],
            require_compression_lease=False)
        db.append_messages_batch('child', turns(80, 2))
        db._execute_write(lambda conn: conn.execute("DELETE FROM display_compactions"))
    result = page(client)
    assert len(result['messages']) == 70
    assert len({row['display_id'] for row in result['messages']}) == 70
    assert result['messages'][-1]['content'] == 'turn 81 assistant'
    with SessionDB(homes['default'] / 'state.db', read_only=True) as db:
        assert db._conn.execute("SELECT COUNT(*) FROM display_compactions").fetchone()[0] == 0


def test_explicit_branch_never_inherits_parent_history(client, homes):
    with SessionDB(homes['default'] / 'state.db') as db:
        db.create_session('parent', source='dashboard')
        db.append_messages_batch('parent', turns(0, 80))
        db.end_session('parent', 'compression')
        db.create_session('chat', source='dashboard', parent_session_id='parent',
                          model_config={'_branched_from': 'parent'})
        db.append_messages_batch('chat', turns(0, 1, 'branch'))
    result = page(client)
    assert [row['content'] for row in result['messages']] == ['branch 0 user', 'branch 0 assistant']
    assert result['pagination']['has_more'] is False


def test_concurrent_watermark_tail_survives_without_duplicates(client, homes):
    with SessionDB(homes['default'] / 'state.db') as db:
        db.create_session('chat', source='dashboard')
        db.append_messages_batch('chat', turns(0, 80))
        before = db.get_messages('chat')
        watermark = db.get_active_message_watermark('chat')
        db.append_messages_batch('chat', turns(80, 2))
        db.archive_and_compact('chat', [summary(), *before[-4:]], watermark=watermark, tail_count=4)
    first = page(client)
    assert len(first['messages']) == 70
    assert len({row['display_id'] for row in first['messages']}) == 70
    assert first['messages'][-1]['content'] == 'turn 81 assistant'


def test_long_chain_reaches_actual_tip_and_cycle_fails_closed(client, homes):
    with SessionDB(homes['default'] / 'state.db') as db:
        db.create_session('chat', source='dashboard')
        db.append_messages_batch('chat', turns(0, 1))
        # Real rows/SQL, cheap single transaction to exercise the >100 resolver limit.
        def seed(conn):
            previous = 'chat'
            for i in range(120):
                sid = f'chain-{i}'
                conn.execute("UPDATE sessions SET end_reason='compression',ended_at=? WHERE id=?", (i, previous))
                conn.execute("INSERT INTO sessions(id,source,parent_session_id,started_at) VALUES (?,'dashboard',?,?)",
                             (sid, previous, i + 1))
                previous = sid
        db._execute_write(seed)
        db.append_messages_batch('chain-119', turns(1, 1))
    result = page(client)
    assert result['session_id'] == 'chain-119'
    assert len(result['messages']) == 4
    with SessionDB(homes['default'] / 'state.db') as db:
        def cycle(conn):
            conn.execute("UPDATE sessions SET parent_session_id='chain-119' WHERE id='chat'")
            conn.execute("UPDATE sessions SET end_reason='compression' WHERE id='chain-119'")
        db._execute_write(cycle)
    assert client.get('/api/sessions/chat/messages', params={'include_history': 'true'}).status_code == 409


def test_oversized_copied_tail_has_explicit_failure_and_context_stays_usable(client, homes):
    with SessionDB(homes['default'] / 'state.db') as db:
        db.create_session('chat', source='dashboard')
        db.append_messages_batch('chat', turns(0, 600))
        db.archive_and_compact('chat', [summary(), *db.get_messages('chat')[-1100:]], tail_count=1100)
        assert len(db.get_messages_as_conversation('chat')) == 1101
    result = client.get('/api/sessions/chat/messages', params={'include_history': 'true'})
    assert result.status_code == 409


def test_provenance_is_deleted_with_transcript(client, homes):
    with SessionDB(homes['default'] / 'state.db') as db:
        db.create_session('chat', source='dashboard')
        db.append_messages_batch('chat', turns(0, 2))
        db.archive_and_compact('chat', [summary(), *db.get_messages('chat')[-2:]], tail_count=2)
        assert db._conn.execute('SELECT COUNT(*) FROM display_compactions').fetchone()[0] == 1
        db.clear_messages('chat')
        assert db._conn.execute('SELECT COUNT(*) FROM display_compactions').fetchone()[0] == 0


def test_deleted_cursor_boundary_fails_without_changing_live_context(client, homes):
    with SessionDB(homes['default'] / 'state.db') as db:
        db.create_session('chat', source='dashboard')
        db.append_messages_batch('chat', turns(0, 80))
    initial = page(client)
    with SessionDB(homes['default'] / 'state.db') as db:
        db.clear_messages('chat')
        db.append_messages_batch('chat', turns(80, 1))
    result = client.get('/api/sessions/chat/messages', params={
        'include_history': 'true', 'before_cursor': initial['pagination']['next_cursor']})
    assert result.status_code == 409
    assert len(page(client)['messages']) == 2


def test_display_projection_retains_real_ask_and_hides_summary(client, homes):
    from agent.context_compressor import SUMMARY_PREFIX, HISTORICAL_TASK_HEADING, _SUMMARY_END_MARKER
    with SessionDB(homes['default'] / 'state.db') as db:
        db.create_session('chat', source='dashboard')
        db.append_messages_batch('chat', turns(0, 80))
        ask = {'role': 'user', 'content': 'REAL ASK', 'timestamp': 99}
        db.append_messages_batch('chat', [ask])
        carrier = {**ask, 'content': f'{SUMMARY_PREFIX}\n{HISTORICAL_TASK_HEADING}\nold task\n\n{_SUMMARY_END_MARKER}\n\nREAL ASK',
                   '_compressed_summary': True, 'display_kind': 'hidden'}
        db.archive_and_compact('chat', [carrier], tail_count=1)
    result = page(client)
    assert len(result['messages']) == 71  # preserve the complete older user/agent turn
    rows = result['messages']
    assert sum(row.get('display_content', row['content']) == 'REAL ASK' for row in rows) == 1
    assert rows[-1]['display_content'] == 'REAL ASK'
    assert 'display_kind' not in rows[-1]


def test_large_history_decodes_only_the_requested_page(client, homes, monkeypatch):
    with SessionDB(homes['default'] / 'state.db') as db:
        db.create_session('chat', source='dashboard')
        db.append_messages_batch('chat', turns(0, 10000))
        db.archive_and_compact('chat', [summary(), *db.get_messages('chat')[-4:]], tail_count=4)
    with SessionDB(homes['default'] / 'state.db', read_only=True) as db:
        decoded = []
        original = db._decode_message_rows
        def decode(rows):
            decoded.extend(row['id'] for row in rows)
            return original(rows)
        monkeypatch.setattr(db, '_decode_message_rows', decode)
        tip, rows, cursor, more = db.get_history_display_page('chat', limit=70)
        assert len(rows) == 70 and cursor and more
        # Summary + 70 rows + lookahead, independent of 20,000 archived rows.
        assert len(decoded) <= 72
        assert tip == 'chat'


def test_row_cap_continues_every_tool_row_without_gaps(client, homes):
    from tests.korra_cli.test_chat_history_pagination import _turn
    with SessionDB(homes['default'] / 'state.db') as db:
        db.create_session('chat', source='dashboard')
        expected = [row for i in range(45) for row in _turn('many', i, 20)]
        db.append_messages_batch('chat', expected)
        db.archive_and_compact('chat', [summary(), *db.get_messages('chat')[-42:]], tail_count=42)
    pages, cursor = [], None
    for _ in range(10):
        result = page(client, cursor=cursor)
        assert len(result['messages']) <= 1000
        assert result['messages'][0]['role'] in ('user', 'assistant')
        pages.append(result)
        cursor = result['pagination']['next_cursor']
        if not result['pagination']['has_more']:
            break
    else:
        pytest.fail('capped history never terminated')
    rows = [row for result in reversed(pages) for row in result['messages']]
    assert [row['content'] for row in rows] == [row['content'] for row in expected]
    assert len({row['display_id'] for row in rows}) == len(expected)


def test_restored_model_tail_keeps_original_display_identity(client, homes):
    with SessionDB(homes['default'] / 'state.db') as db:
        db.create_session('chat', source='dashboard')
        db.append_messages_batch('chat', [
            {'role': 'user', 'content': ' question \n', 'timestamp': 123},
            {'role': 'assistant', 'content': ' answer \n', 'timestamp': 124}])
    initial = page(client)
    with SessionDB(homes['default'] / 'state.db') as db:
        # This is the actual core restore path: sanitized content, no public ids.
        live = db.get_messages_as_conversation('chat')
        db.archive_and_compact('chat', [summary(), *live], tail_count=2)
    result = page(client)
    assert [row['display_id'] for row in result['messages']] == [row['display_id'] for row in initial['messages']]


def test_mixed_inplace_and_rotated_generations_keep_all_occurrences(client, homes):
    with SessionDB(homes['default'] / 'state.db') as db:
        db.create_session('chat', source='dashboard')
        db.append_messages_batch('chat', turns(0, 80))
        expected = turns(0, 80)
        sid = 'chat'
        for epoch, rotate in enumerate((False, True, False, True)):
            handoff = [summary(), *db.get_messages(sid)[-4:]]
            if rotate:
                child = f'mixed-{epoch}'
                db.publish_compression_child(parent_session_id=sid, child_session_id=child,
                    source='dashboard', messages=handoff, require_compression_lease=False)
                sid = child
            else:
                db.archive_and_compact(sid, handoff, tail_count=4)
            extra = turns(80 + 3 * epoch, 3)
            db.append_messages_batch(sid, extra)
            expected.extend(extra)
    pages, cursor = [], None
    for _ in range(10):
        result = page(client, cursor=cursor)
        pages.append(result)
        cursor = result['pagination']['next_cursor']
        if not result['pagination']['has_more']:
            break
    rows = [row for result in reversed(pages) for row in result['messages']]
    assert [row['content'] for row in rows] == [row['content'] for row in expected]
    assert len({row['display_id'] for row in rows}) == len(expected)


def test_imported_parent_chain_order_is_independent_of_physical_ids(client, homes):
    original = turns(0, 80)
    middle = [summary(), *original[-6:], *turns(80, 3)]
    newest = [summary(), *middle[-4:], *turns(83, 2)]
    sessions = [
        {'id': 'chat', 'source': 'dashboard', 'started_at': 1, 'ended_at': 2,
         'end_reason': 'compression', 'messages': original},
        {'id': 'middle', 'source': 'dashboard', 'started_at': 3, 'ended_at': 4,
         'end_reason': 'compression', 'parent_session_id': 'chat', 'messages': middle},
        {'id': 'newest', 'source': 'dashboard', 'started_at': 5,
         'parent_session_id': 'middle', 'messages': newest},
    ]
    # Root rows get the largest ids; chain order must still be chronological.
    imported = client.post('/api/sessions/import', json={'sessions': sessions[::-1]})
    assert imported.status_code == 200, imported.text
    pages, cursor = [], None
    for _ in range(10):
        result = page(client, cursor=cursor)
        pages.append(result)
        cursor = result['pagination']['next_cursor']
        if not result['pagination']['has_more']:
            break
    rows = [row for result in reversed(pages) for row in result['messages']]
    assert [row['content'] for row in rows] == [row['content'] for row in original + turns(80, 5)]
    assert len({row['display_id'] for row in rows}) == 170


@pytest.mark.parametrize('rotate', [False, True])
def test_identical_occurrences_survive_three_copied_generations(client, homes, rotate):
    def repeated(count):
        return [{'role': role, 'content': 'same', 'timestamp': 1234}
                for _ in range(count) for role in ('user', 'assistant')]
    with SessionDB(homes['default'] / 'state.db') as db:
        sid = 'chat'
        db.create_session(sid, source='dashboard')
        assert db.append_messages_batch(sid, repeated(60)) == 120
        for epoch in range(3):
            handoff = [summary(), *db.get_messages(sid)[-4:]]
            if rotate:
                child = f'repeated-{epoch}'
                db.publish_compression_child(parent_session_id=sid, child_session_id=child,
                    source='dashboard', messages=handoff, require_compression_lease=False)
                sid = child
            else:
                db.archive_and_compact(sid, handoff, tail_count=4)
            assert db.append_messages_batch(sid, repeated(1)) == 2
    first = page(client)
    older = page(client, cursor=first['pagination']['next_cursor'])
    rows = older['messages'] + first['messages']
    assert len(rows) == 126
    assert len({row['display_id'] for row in rows}) == 126
    assert not older['pagination']['has_more']


def test_legacy_store_initializes_only_additive_schema(client, homes):
    with SessionDB(homes['default'] / 'state.db') as db:
        db.create_session('chat', source='dashboard')
        db.append_messages_batch('chat', turns(0, 1))
        model = db.get_messages_as_conversation('chat')
        db._execute_write(lambda conn: conn.execute('DROP TABLE display_compactions'))
    assert len(page(client)['messages']) == 2
    with SessionDB(homes['default'] / 'state.db', read_only=True) as db:
        assert db.get_messages_as_conversation('chat') == model
        assert db._conn.execute('SELECT COUNT(*) FROM display_compactions').fetchone()[0] == 0


def test_legacy_inplace_generations_without_summary_flag_are_discovered(client, homes):
    from agent.context_compressor import SUMMARY_PREFIX, HISTORICAL_TASK_HEADING, _SUMMARY_END_MARKER
    def legacy_summary():
        return {'role': 'user', 'display_kind': 'hidden',
                'content': f'{SUMMARY_PREFIX}\n{HISTORICAL_TASK_HEADING}\nold task\n\n{_SUMMARY_END_MARKER}'}
    expected = turns(0, 80)
    with SessionDB(homes['default'] / 'state.db') as db:
        db.create_session('chat', source='dashboard')
        db.append_messages_batch('chat', expected)
        for epoch in range(3):
            db.archive_and_compact('chat', [legacy_summary(), *db.get_messages('chat')[-4:]], tail_count=4)
            extra = turns(80 + epoch, 1)
            db.append_messages_batch('chat', extra)
            expected.extend(extra)
        db._execute_write(lambda conn: conn.execute('DELETE FROM display_compactions'))
    pages, cursor = [], None
    for _ in range(10):
        result = page(client, cursor=cursor)
        pages.append(result)
        cursor = result['pagination']['next_cursor']
        if not result['pagination']['has_more']:
            break
    rows = [row for result in reversed(pages) for row in result['messages']]
    assert [row['content'] for row in rows] == [row['content'] for row in expected]
    assert len({row['display_id'] for row in rows}) == len(expected)

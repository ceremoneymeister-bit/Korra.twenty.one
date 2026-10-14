"""Read-only display paging across compaction generations.

Copy recognition is restricted to the suffix/prefix at a compaction boundary.
It is occurrence preserving: equal messages elsewhere are never deduplicated.
No model-facing reads or persisted transcript fields are changed.
"""
from __future__ import annotations

import base64
from contextlib import contextmanager
import hashlib
import json
from dataclasses import dataclass

from agent.compaction_display import project_compaction_message_for_display
from agent.context_compressor import is_compaction_summary_message


MAX_CHAIN = 256
MAX_CARRIED_ROWS = 1000


class HistoryCursorError(ValueError):
    """Malformed cursor or a cursor from another profile/conversation."""


class HistoryUnavailableError(ValueError):
    """Damaged/too deep history or a cursor whose boundary was removed."""


class HistoryNotFoundError(LookupError):
    """The conversation was deleted before the read snapshot started."""


@dataclass
class Segment:
    session_id: str
    start: int
    end: int


def _display(db, raw):
    row = db._decode_message_rows([raw])[0]
    if is_compaction_summary_message(row):
        projected = project_compaction_message_for_display(row)
        if projected is None:
            return None
        row['display_content'] = projected.get('content')
        row.pop('display_kind', None)
    return row


def _key(row):
    # Timestamp alone is never identity; duplicate occurrences are matched
    # one-to-one only inside a copied contiguous boundary tail.
    content = row.get('display_content', row.get('content'))
    if row['role'] in ('user', 'assistant') and isinstance(content, str):
        from agent.memory_manager import sanitize_context

        content = sanitize_context(content).strip()
    return (row['role'], json.dumps(content,
            sort_keys=True, ensure_ascii=True), row.get('timestamp'),
            row.get('tool_call_id'), json.dumps(row.get('tool_calls'), sort_keys=True),
            row.get('tool_name'))


def _overlap(older, newer):
    """Length of longest older suffix equal to newer prefix (linear KMP)."""
    if not older or not newer:
        return 0
    pattern = [_key(row) for row in newer]
    prefix = [0] * len(pattern)
    for i in range(1, len(pattern)):
        n = prefix[i - 1]
        while n and pattern[i] != pattern[n]:
            n = prefix[n - 1]
        if pattern[i] == pattern[n]:
            n += 1
        prefix[i] = n
    n = 0
    for row in older:
        key = _key(row)
        while n and (n == len(pattern) or key != pattern[n]):
            n = prefix[n - 1]
        if n < len(pattern) and key == pattern[n]:
            n += 1
    return n


@contextmanager
def _snapshot(db):
    with db._read_ctx() as conn:
        owned = not conn.in_transaction
        if owned:
            conn.execute('BEGIN')
        try:
            yield conn
        finally:
            if owned:
                conn.rollback()


def _tip(conn, sid):
    from korra_state_common import _sql_session_last_active

    seen = set()
    for _ in range(MAX_CHAIN):
        if sid in seen:
            raise HistoryUnavailableError('Compaction history contains a cycle')
        seen.add(sid)
        raw = conn.execute(
            f"""SELECT child.id FROM sessions parent
                JOIN sessions child ON child.parent_session_id=parent.id
                WHERE parent.id=? AND parent.end_reason='compression'
                AND json_extract(COALESCE(child.model_config, '{{}}'), '$._branched_from') IS NULL
                AND json_extract(COALESCE(child.model_config, '{{}}'), '$._delegate_from') IS NULL
                AND COALESCE(child.source, '') != 'tool'
                ORDER BY CASE WHEN child.end_reason='compression' THEN 0
                              WHEN child.ended_at IS NULL THEN 1 ELSE 2 END,
                         {_sql_session_last_active('child')} DESC,
                         child.started_at DESC, child.id DESC LIMIT 1""", (sid,),
        ).fetchone()
        if not raw:
            return sid
        sid = raw['id']
    raise HistoryUnavailableError('Compaction history exceeds 256 sessions')


def _lineage(db, conn, sid):
    chain, seen = [], set()
    raw = conn.execute('SELECT * FROM sessions WHERE id=?', (sid,)).fetchone()
    current = dict(raw) if raw else None
    while current:
        if current['id'] in seen:
            raise HistoryUnavailableError('Compaction history contains a cycle')
        if len(chain) >= MAX_CHAIN:
            raise HistoryUnavailableError('Compaction history exceeds 256 sessions')
        chain.append(current['id'])
        seen.add(current['id'])
        if (not current.get('parent_session_id') or db._is_explicit_fork_child_row(current)
                or current.get('source') == 'tool'):
            break
        raw = conn.execute('SELECT * FROM sessions WHERE id=?',
                           (current['parent_session_id'],)).fetchone()
        if not raw or raw['end_reason'] != 'compression':
            break
        current = dict(raw)
    return chain[::-1]


def _boundary_rows(db, conn, segment, *, latest):
    # Include superseded originals (active=0, compacted=0) here solely for
    # copy provenance; the actual page never surfaces Undo/Rewind rows.
    rows = conn.execute(
        'SELECT * FROM messages WHERE session_id=? AND id>=? AND id<? '
        f"ORDER BY id {'DESC' if latest else 'ASC'} LIMIT ?",
        (segment.session_id, segment.start, segment.end, MAX_CARRIED_ROWS + 2),
    ).fetchall()
    if latest:
        rows.reverse()
    displayed = [view for raw in rows if (view := _display(db, raw)) is not None]
    return displayed


def _segments(db, conn, chain, records):
    from korra_state_common import _PREVIEW_STANDALONE_SUMMARY_SQL, _PREVIEW_MERGED_SUMMARY_SQL

    summary_sql = ("(m._compressed_summary=1 OR (m.role IN ('user','assistant') AND ("
                   + _PREVIEW_STANDALONE_SUMMARY_SQL + ' OR ' + _PREVIEW_MERGED_SUMMARY_SQL + ')))')
    previous_summary_sql = summary_sql.replace('m.', 'previous.')
    segments = []
    for sid in chain:
        recorded_starts = [start for (record_sid, start) in records if record_sid == sid]
        metadata_end = min(recorded_starts, default=2**63 - 1)
        # Only pre-journal generations need discovery. SQLite checks markers
        # and known legacy prefixes; Python never loads all message bodies.
        # A long live suffix is never scanned
        # once its publication boundary has been recorded.
        starts = [0, *recorded_starts]
        for raw in conn.execute(
            "SELECT m.id FROM messages m WHERE m.session_id=? AND m.id<? "
            f"AND {summary_sql} AND COALESCE(("
            f"SELECT {previous_summary_sql} FROM messages previous "
            "WHERE previous.session_id=m.session_id AND previous.id<m.id "
            "ORDER BY previous.id DESC LIMIT 1),0)=0",
            (sid, metadata_end),
        ):
            starts.append(raw['id'])
        active_start = conn.execute(
            'SELECT MIN(id) FROM messages WHERE session_id=? AND active=1 AND id<?',
            (sid, metadata_end),
        ).fetchone()[0]
        if active_start:
            starts.append(active_start)
        starts = sorted(set(starts))
        if len(starts) > 1024:
            raise HistoryUnavailableError('Compaction history exceeds 1024 generations')
        starts.append(2**63 - 1)
        segments.extend(Segment(sid, start, end) for start, end in zip(starts, starts[1:]))
    # An empty leading generation is harmless but costs unnecessary queries.
    return [segment for segment in segments if conn.execute(
        'SELECT 1 FROM messages WHERE session_id=? AND id>=? AND id<? LIMIT 1',
        (segment.session_id, segment.start, segment.end),
    ).fetchone()]


def get_history_page(db, session_id, *, limit, before_cursor=None, max_rows=1000):
    """Return resolved tip, page, opaque older cursor, has_more; read-only."""
    with _snapshot(db) as conn:
        session_id = _tip(conn, session_id)
        chain = _lineage(db, conn, session_id)
        if not chain:
            raise HistoryNotFoundError('Session not found')
        scope = hashlib.sha256(str(db.db_path.resolve()).encode()).hexdigest()[:24]
        root = chain[0]
        anchor = None
        if before_cursor is not None:
            try:
                if len(before_cursor) > 2048:
                    raise ValueError
                payload = json.loads(base64.urlsafe_b64decode(
                    before_cursor + '=' * (-len(before_cursor) % 4)))
                version, cursor_scope, cursor_root, anchor = payload
                if version != 1 or cursor_scope != scope or cursor_root != root:
                    raise ValueError
                if not isinstance(anchor, int) or isinstance(anchor, bool) or anchor < 1:
                    raise ValueError
            except (ValueError, TypeError, UnicodeError):
                raise HistoryCursorError('Cursor does not belong to this profile/conversation') from None

        records = {}
        for sid in chain:
            for raw in conn.execute(
                'SELECT start_message_id, pairs_json, overflow FROM display_compactions WHERE session_id=?',
                (sid,),
            ):
                if raw['overflow']:
                    raise HistoryUnavailableError('Copied compaction tail exceeds 1000 rows')
                records[(sid, raw['start_message_id'])] = {'pairs': json.loads(raw['pairs_json'])}
                if len(records) > 1024:
                    raise HistoryUnavailableError('Compaction history exceeds 1024 generations')
        segments = _segments(db, conn, chain, records)
        if len(segments) > 1024:
            raise HistoryUnavailableError('Compaction history exceeds 1024 generations')
        origins = {}  # newest physical row -> first durable occurrence
        suppressed = set()
        origin_sessions = {}
        for older, newer in zip(segments, segments[1:]):
            record = records.get((newer.session_id, newer.start))
            if record is not None:
                pairs = record['pairs']
            else:
                # Pre-journal installations/imports: infer only a contiguous
                # suffix/prefix at a compression boundary, preserving repeats.
                left = _boundary_rows(db, conn, older, latest=True)
                right = _boundary_rows(db, conn, newer, latest=False)
                count = _overlap(left, right)
                if count > MAX_CARRIED_ROWS:
                    raise HistoryUnavailableError('Copied compaction tail exceeds 1000 rows')
                pairs = [(old['id'], new['id']) for old, new in
                         zip(left[len(left)-count:], right[:count])]
            for old_id, new_id in pairs:
                origin = origins.get(old_id, old_id)
                origins[new_id] = origin
                origin_sessions[origin] = origin_sessions.get(origin, older.session_id)
                suppressed.add(old_id)

        # Locate the durable anchor in the current generation. Physical clone
        # ids can change during compaction; original display identity cannot.
        position = None
        if anchor is not None:
            candidates = [physical for physical, origin in origins.items() if origin == anchor]
            candidate = candidates[-1] if candidates else anchor
            for index, segment in enumerate(segments):
                raw = conn.execute(
                    'SELECT id FROM messages WHERE session_id=? AND id=? '
                    'AND id>=? AND id<? AND (active=1 OR compacted=1)',
                    (segment.session_id, candidate, segment.start, segment.end),
                ).fetchone()
                if raw and candidate not in suppressed:
                    position = (index, candidate)
                    break
            if position is None:
                # Distinguish an alien physical id from a formerly valid
                # boundary deleted by rewind/prune.
                raw = conn.execute('SELECT session_id FROM messages WHERE id=?', (anchor,)).fetchone()
                if raw and raw['session_id'] not in chain:
                    raise HistoryCursorError('Cursor row is outside this compaction chain')
                raise HistoryUnavailableError('History cursor boundary is no longer available')

        selected, shown, agent_counted = [], 0, False
        has_more = False
        stopped = False
        scanned = 0
        for index in range(len(segments)-1, -1, -1):
            segment = segments[index]
            if position is not None and index > position[0]:
                continue
            end = min(segment.end, position[1]) if position and index == position[0] else segment.end
            cursor = conn.execute(
                'SELECT * FROM messages WHERE session_id=? AND id>=? AND id<? '
                'AND (active=1 OR compacted=1) ORDER BY id DESC',
                (segment.session_id, segment.start, end),
            )
            for raw in cursor:
                if raw['id'] in suppressed:
                    continue
                row = _display(db, raw)
                if row is None:
                    continue
                if stopped:
                    has_more = True
                    break
                scanned += 1
                origin = origins.get(row['id'], row['id'])
                row.update(source_session_id=row['session_id'],
                    origin_session_id=origin_sessions.get(origin, row['session_id']),
                    display_id=f'{root}:{origin}',
                    read_only=not (row['active'] == 1 and row['session_id'] == session_id))
                selected.append(row)
                if row['role'] == 'user':
                    if row.get('display_kind') in (None, ''):
                        shown += 1
                    agent_counted = False
                    if shown >= limit:
                        stopped = True
                elif row['role'] == 'assistant' and not agent_counted:
                    if row.get('display_kind') in (None, ''):
                        shown += 1
                    agent_counted = True
                if scanned >= max_rows:
                    # Keep tool result adjacent to its call on the next page.
                    while len(selected) > 1 and selected[-1]['role'] == 'tool':
                        selected.pop()
                    stopped = True
            if has_more:
                break
        selected.reverse()
        next_cursor = None
        if selected:
            first_origin = origins.get(selected[0]['id'], selected[0]['id'])
            next_cursor = base64.urlsafe_b64encode(json.dumps(
                [1, scope, root, first_origin], separators=(',', ':')).encode()).decode().rstrip('=')
        return session_id, selected, next_cursor, has_more


def record_compaction(db, conn, *, old_tail, session_id, new_start):
    """Record copy provenance at publication, before future appends can look
    identical to a copied tail. Carries no model data and cascades on
    transcript deletion. Runs inside the compaction transaction.
    """
    left = [view for raw in reversed(old_tail) if (view := _display(db, raw)) is not None]
    segment = Segment(session_id, new_start, 2**63 - 1)
    right = _boundary_rows(db, conn, segment, latest=False)
    count = _overlap(left, right)
    pairs = [[old['id'], new['id']] for old, new in zip(left[len(left)-count:], right[:count])]
    total = conn.execute(
        'SELECT COUNT(*) FROM messages WHERE session_id=? AND id>=?',
        (session_id, new_start),
    ).fetchone()[0]
    conn.execute(
        'INSERT INTO display_compactions(session_id,start_message_id,pairs_json,overflow) VALUES (?,?,?,?)',
        (session_id, new_start, json.dumps(pairs), int(count > MAX_CARRIED_ROWS or total > MAX_CARRIED_ROWS + 2)),
    )

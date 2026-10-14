"""Read-only display paging across compaction generations.

Copy provenance is captured at publication; copies occupy their original slots.
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
# Existing publication status column: 1 = oversized, 2 = unprovable copies.
INCOMPLETE_PROVENANCE = 2


class HistoryCursorError(ValueError):
    """Malformed cursor or a cursor from another profile/conversation."""


class HistoryUnavailableError(ValueError):
    """Damaged/too deep history or a cursor whose boundary was removed."""


class HistoryAmbiguousError(HistoryUnavailableError):
    """Legacy copies cannot be identified safely; use separate live/archive views."""


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
    # Only legacy reconstruction uses content/time as evidence. New live
    # messages are identified by their durable source row, before reinsertion.
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
        for generation, raw in enumerate(conn.execute(
            "SELECT m.id FROM messages m WHERE m.session_id=? AND m.id<? "
            f"AND {summary_sql} AND COALESCE(("
            f"SELECT {previous_summary_sql} FROM messages previous "
            "WHERE previous.session_id=m.session_id AND previous.id<m.id "
            "ORDER BY previous.id DESC LIMIT 1),0)=0",
            (sid, metadata_end),
        )):
            boundary = raw['id']
            # The first compressor pass preserves the beginning BEFORE its
            # summary. Recover that publication boundary, not the summary's
            # position inside the new generation.
            if generation == 0:
                # protect_first_n decays after the first compression. Later
                # summaries start their generations without a protected head.
                head = _boundary_rows(db, conn, Segment(sid, 0, boundary), latest=False)
                preceding = _boundary_rows(db, conn, Segment(sid, 0, boundary), latest=True)
                count = _overlap(preceding, head)
                if 0 < count < len(preceding):
                    boundary = preceding[-count]['id']
                elif count == len(preceding) and len(preceding) <= 4 and sid != chain[0]:
                    # A child's initial handoff already starts at its first
                    # protected row; there is no older generation in this sid.
                    boundary = 0
            starts.append(boundary)
        active_start = conn.execute(
            'SELECT MIN(id) FROM messages WHERE session_id=? AND active=1 AND id<?',
            (sid, metadata_end),
        ).fetchone()[0]
        if active_start:
            # A summary/carrier within the active handoff is not another
            # publication; protected head + summary + tail is one generation.
            starts = [start for start in starts if start < active_start]
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
                if not isinstance(anchor, int) or isinstance(anchor, bool) or not 1 <= anchor <= 2**63 - 1:
                    raise HistoryCursorError('Cursor anchor must be an integer from 1 to 9223372036854775807')
            except HistoryCursorError:
                raise
            except (ValueError, TypeError, UnicodeError):
                raise HistoryCursorError('Cursor does not belong to this profile/conversation') from None

        records = {}
        for sid in chain:
            for raw in conn.execute(
                'SELECT start_message_id, pairs_json, overflow FROM display_compactions WHERE session_id=?',
                (sid,),
            ):
                if raw['overflow'] == INCOMPLETE_PROVENANCE:
                    raise HistoryAmbiguousError('Compaction has no reliable message provenance')
                if raw['overflow']:
                    raise HistoryUnavailableError('Copied compaction tail exceeds 1000 rows')
                records[(sid, raw['start_message_id'])] = {'pairs': json.loads(raw['pairs_json'])}
                if len(records) > 1024:
                    raise HistoryUnavailableError('Compaction history exceeds 1024 generations')
        segments = _segments(db, conn, chain, records)
        if len(segments) > 1024:
            raise HistoryUnavailableError('Compaction history exceeds 1024 generations')
        origins = {}  # physical copy -> original chronological slot
        representatives = {}  # original slot -> newest physical copy
        copies = set()
        slots_by_session = {}
        for older, newer in zip(segments, segments[1:]):
            record = records.get((newer.session_id, newer.start))
            if record is not None:
                pairs = record['pairs']
            else:
                left = _generation_rows(db, conn, older)
                right = _boundary_rows(db, conn, newer, latest=False)
                pairs = _legacy_pairs(left, right)
            for old_id, new_id in pairs:
                origin = origins.get(old_id, old_id)
                origins[new_id] = origin
                representatives[origin] = new_id
                copies.add(new_id)
                if old_id not in origins:
                    slots_by_session.setdefault(older.session_id, set()).add(old_id)

        # Cursors address the original chronological slot, which never moves
        # when a protected head or tail is copied into a later generation.
        position = None
        if anchor is not None:
            for index, segment in enumerate(segments):
                raw = conn.execute(
                    'SELECT id,active,compacted FROM messages WHERE session_id=? AND id=? '
                    'AND id>=? AND id<?',
                    (segment.session_id, anchor, segment.start, segment.end),
                ).fetchone()
                if raw and anchor not in copies and (
                        raw['active'] or raw['compacted'] or anchor in representatives):
                    position = (index, anchor)
                    break
            if position is None:
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
            slots = [slot for slot in slots_by_session.get(segment.session_id, ())
                     if segment.start <= slot < end]
            slot_clause = (' OR id IN (' + ','.join('?' for _ in slots) + ')') if slots else ''
            cursor = conn.execute(
                'SELECT * FROM messages WHERE session_id=? AND id>=? AND id<? '
                f'AND (active=1 OR compacted=1{slot_clause}) ORDER BY id DESC',
                (segment.session_id, segment.start, end, *slots),
            )
            for raw in cursor:
                if raw['id'] in copies:
                    continue
                origin = raw['id']
                if origin in representatives:
                    raw = conn.execute('SELECT * FROM messages WHERE id=?',
                                       (representatives[origin],)).fetchone()
                    if raw is None or not (raw['active'] or raw['compacted']):
                        continue
                row = _display(db, raw)
                if row is None:
                    continue
                if stopped:
                    has_more = True
                    break
                scanned += 1
                row.update(source_session_id=row['session_id'],
                    origin_session_id=segment.session_id,
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


def _generation_rows(db, conn, segment):
    """Bounded windows include the protected beginning and the copied tail."""
    head = _boundary_rows(db, conn, segment, latest=False)
    tail = _boundary_rows(db, conn, segment, latest=True)
    return sorted({row['id']: row for row in head + tail}.values(), key=lambda row: row['id'])


def _legacy_key(row):
    key = _key(row)
    # A prune rewrites tool bodies but preserves the tool occurrence/call.
    return (key[0], key[2], key[3], key[5]) if row['role'] == 'tool' else key


def _legacy_pairs(left, right):
    """Reconstruct an ordered handoff with durable timestamps/call identities.

    Repeated indistinguishable occurrences or changed timestamps cannot prove
    provenance. Never guess by text globally: the HTTP reader falls back to
    the existing separate live and archive views in that case.
    """
    by_key = {}
    # Text shared by distinct tool calls (including empty assistant call
    # rows) is ordinary new activity, not evidence of a copied occurrence.
    content_keys = {key[:2] + key[3:] for row in left if (key := _key(row))}
    occurrence_keys = {(row['role'], row.get('timestamp'), row.get('tool_call_id')) for row in left}
    for index, row in enumerate(left):
        by_key.setdefault(_legacy_key(row), []).append(index)
    pairs, previous = [], -1
    for index, row in enumerate(right):
        matches = by_key.get(_legacy_key(row), [])
        if not matches:
            if any((key := _key(later))[:2] + key[3:] in content_keys or
                   (later['role'], later.get('timestamp'), later.get('tool_call_id')) in occurrence_keys
                   for later in right[index:]):
                raise HistoryAmbiguousError('Legacy compaction has no reliable message provenance')
            break  # subsequent appends are not part of the handoff
        if len(matches) != 1 or matches[0] <= previous:
            raise HistoryAmbiguousError('Legacy compaction contains ambiguous repeated occurrences')
        previous = matches[0]
        pairs.append([left[previous]['id'], row['id']])
    if len(pairs) > MAX_CARRIED_ROWS:
        raise HistoryUnavailableError('Copied compaction tail exceeds 1000 rows')
    return pairs


def record_compaction(db, conn, *, source_session_id, source_start, source_ids, messages,
                      session_id, new_start, cloned_pairs=()):
    """Capture occurrence ids before the SQL writer overwrites _row_id.

    Restored/legacy callers without ids use bounded, evidence-based matching.
    No timestamps are used when the source occurrence is known. This runs in
    the publication transaction, before future appends can resemble copies.
    """
    source_end = new_start if source_session_id == session_id else 2**63 - 1
    ids = [value for value in source_ids if isinstance(value, int) and 0 < value < 2**63]
    valid = {}
    if ids:
        placeholders = ','.join('?' for _ in ids)
        valid = {row['id']: row['role'] for row in conn.execute(
            f'SELECT id,role FROM messages WHERE session_id=? AND id>=? AND id<? AND id IN ({placeholders})',
            (source_session_id, source_start, source_end, *ids))}
    pairs = []
    used = set()
    for source, msg in zip(source_ids, messages):
        if source in valid and source not in used and valid[source] == msg.get('role'):
            # Standalone summaries do not represent an original message.
            if project_compaction_message_for_display(msg) is not None:
                pairs.append([source, msg['_row_id']])
                used.add(source)
    # Include protected head and rewritten rows for restored callers that
    # intentionally omit ids. Their persisted timestamps still prove copies.
    older = Segment(source_session_id, source_start, source_end)
    left = _generation_rows(db, conn, older)
    right = _boundary_rows(db, conn, Segment(session_id, new_start, 2**63-1), latest=False)
    pairs.extend(cloned_pairs)
    used.update(old for old, _ in cloned_pairs)
    matched_new = {new for _, new in pairs}
    ambiguous = False
    try:
        inferred = _legacy_pairs([row for row in left if row['id'] not in used],
                                 [row for row in right if row['id'] not in matched_new])
    except HistoryAmbiguousError:
        inferred = []
        ambiguous = True
    except HistoryUnavailableError:
        inferred = []
    pairs.extend(inferred)
    total = conn.execute('SELECT COUNT(*) FROM messages WHERE session_id=? AND id>=?',
                         (session_id, new_start)).fetchone()[0]
    conn.execute(
        'INSERT INTO display_compactions(session_id,start_message_id,pairs_json,overflow) VALUES (?,?,?,?)',
        (session_id, new_start, json.dumps(pairs), 1 if total > MAX_CARRIED_ROWS + 2 else INCOMPLETE_PROVENANCE if ambiguous else 0),
    )

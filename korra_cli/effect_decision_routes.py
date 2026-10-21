"""Read-only identity routing for historical exact-effect ledgers.

The physical home owns execution. ``effect.profile`` historically meant either
that home or the origin profile, so it cannot by itself identify the chat.
"""
from __future__ import annotations

import contextlib
import json
import sqlite3
from pathlib import Path

from tools.effect_decisions import DecisionConflict, EffectDecisionStoreUnavailable


def profile_targets() -> list[tuple[str, Path]]:
    from korra_cli import profiles
    return [(name, profiles.get_profile_dir(name)) for name in profiles.list_profile_names()]


def _origin_candidates(row: dict, targets: list[tuple[str, Path]], databases: dict, stack, unavailable_profiles=None) -> list[dict]:
    from korra_cli.kanban_decisions import _chat_chain, _NON_OWNER_SOURCES
    from korra_state import SessionDB

    sid = row['source_session_id']
    candidates = []
    for profile, home in targets:
        path = home / 'state.db'
        try:
            if not path.is_file():
                continue
            # Never bootstrap/heal a store while determining authorization.
            if profile not in databases:
                db = stack.enter_context(contextlib.closing(SessionDB(db_path=path, read_only=True)))
                databases[profile] = (db, str((home / 'sessions').resolve()))
            db, scope = databases[profile]
            source = db.get_session(sid)
            matches = [source] if source else []
            if not source and sid == row['source_session_key']:
                # Older messaging effects stored a routing key in both ID
                # columns. A UUID that disappeared is not a routing key.
                with db._read_ctx() as conn:
                    matches = [dict(r) for r in conn.execute(
                        'SELECT * FROM sessions WHERE session_key=?', (sid,),
                    ).fetchall()]
                    routing = conn.execute(
                        'SELECT entry_json FROM gateway_routing WHERE scope=? AND session_key=?',
                        (scope, sid),
                    ).fetchone()
                if routing:
                    entry = json.loads(routing['entry_json'])
                    target = db.get_session(str(entry.get('session_id') or ''))
                    if target:
                        matches.append(target)
            chains = set()
            for source in matches:
                if (source.get('source') in _NON_OWNER_SOURCES
                        or (source.get('profile_name') or profile) != profile):
                    continue
                chain = frozenset(
                    member for member in _chat_chain(db, source['id'])
                    if (member_row := db.get_session(member))
                    and member_row.get('source') not in _NON_OWNER_SOURCES
                    and (member_row.get('profile_name') or profile) == profile
                )
                if chain in chains:
                    continue
                chains.add(chain)
                # Compression has an explicit, persisted lineage. Do not
                # choose a same-key unrelated chat by recency.
                tip = db.get_compression_lineage(source['id'])[-1]
                tip_row = dict(db.get_session(tip) or source)
                tip_row['title'] = tip_row.get('title') or source.get('title')
                candidates.append({'profile': profile, 'session_id': tip,
                                   'session': tip_row, 'chain': chain})
        except (OSError, sqlite3.Error, ValueError, KeyError, IndexError, TypeError, AttributeError) as exc:
            if unavailable_profiles is None:
                raise EffectDecisionStoreUnavailable() from exc
            unavailable_profiles.add(profile)
    return candidates


def effect_routes(*, targets=None, request_id: str = '', pending_only: bool = False,
                  statuses: tuple[str, ...] | None = None,
                  source_sessions: set[str] | None = None,
                  unavailable_profiles: set[str] | None = None) -> list[dict]:
    """Resolve selected physical records; retain unresolved waits in their centre.

    Pollers select actionable states or one chat's verified aliases in SQL.
    Pollers may collect unavailable profiles for a partial, non-answerable
    view. Without that collector any unreadable store fails closed.
    Exact answers still inspect every ledger for the requested ID, regardless
    of status. All connections and memoized origins live only for this call.
    """
    targets = [(name or 'default', Path(home)) for name, home in (targets if targets is not None else profile_targets())]
    rows, counts = [], {}
    with contextlib.ExitStack() as stack:
        connections = []
        for ledger, home in targets:
            path = home / 'effect_decisions.sqlite3'
            try:
                if not path.exists():
                    continue
                conn = stack.enter_context(contextlib.closing(sqlite3.connect(
                    path.resolve().as_uri() + '?mode=ro', uri=True, timeout=1,
                )))
                conn.row_factory = sqlite3.Row
                connections.append((ledger, conn))
                clauses, params = [], []
                if request_id:
                    clauses.append('id=?')
                    params.append(request_id)
                else:
                    selected_statuses = ('pending',) if pending_only else statuses
                    if selected_statuses is not None:
                        clauses.append('status IN (' + ','.join('?' for _ in selected_statuses) + ')')
                        params.extend(selected_statuses)
                # Chunk aliases as well as IDs for SQLite's variable limit.
                sources = sorted(source_sessions) if source_sessions is not None and not request_id else None
                batches = [None] if sources is None else [sources[i:i + 400] for i in range(0, len(sources), 400)]
                ledger_rows = []
                for batch in batches:
                    batch_clauses, batch_params = list(clauses), list(params)
                    if batch is not None:
                        batch_clauses.append('source_session_id IN (' + ','.join('?' for _ in batch) + ')')
                        batch_params.extend(batch)
                    where = ' WHERE ' + ' AND '.join(batch_clauses) if batch_clauses else ''
                    records = conn.execute(
                        'SELECT id, profile, source_session_id, source_session_key, status, created_at, updated_at '
                        'FROM effect_decisions' + where, batch_params,
                    ).fetchall()
                    ledger_rows.extend({**dict(record), 'ledger_profile': ledger} for record in records)
                # An empty selection must not conceal a corrupt/missing table.
                if not batches:
                    conn.execute('SELECT id FROM effect_decisions LIMIT 0')
                rows.extend(ledger_rows)
            except (OSError, sqlite3.Error) as exc:
                if unavailable_profiles is None:
                    raise EffectDecisionStoreUnavailable() from exc
                unavailable_profiles.add(ledger)
        # A terminal copy under a different source still makes an actionable
        # ID ambiguous. Probe selected primary keys, not every historical ID.
        identities = sorted({row['id'] for row in rows})
        for ledger, conn in connections:
            try:
                for offset in range(0, len(identities), 400):
                    batch = identities[offset:offset + 400]
                    for record in conn.execute(
                        'SELECT id FROM effect_decisions WHERE id IN (' + ','.join('?' for _ in batch) + ')', batch,
                    ):
                        counts[record['id']] = counts.get(record['id'], 0) + 1
            except sqlite3.Error as exc:
                if unavailable_profiles is None:
                    raise EffectDecisionStoreUnavailable() from exc
                unavailable_profiles.add(ledger)
    result = []
    databases, origins = {}, {}
    with contextlib.ExitStack() as stack:
        for row in rows:
            identity = (row['source_session_id'], row['source_session_key'])
            if identity not in origins:
                # Complete the scan before deciding whether *any* route is
                # answerable: an unreadable home may hide a duplicate ID.
                origins[identity] = _origin_candidates(row, targets, databases, stack, unavailable_profiles)
        for row in rows:
            if pending_only and row['status'] != 'pending':
                continue
            identity = (row['source_session_id'], row['source_session_key'])
            candidates = origins[identity]
            ledger = row['ledger_profile']
            origin = candidates[0] if len(candidates) == 1 else None
            # Unknown row.profile must not manufacture a cross-profile link.
            valid_profile = row['profile'] == ledger or any(c['profile'] == row['profile'] for c in candidates)
            # A deleted origin remains answerable in the physical centre. Its
            # profile must still be an actual profile, never arbitrary input.
            if not candidates:
                valid_profile = row['profile'] in {name for name, _ in targets}
            reason = ''
            if unavailable_profiles:
                reason = 'Часть профилей недоступна. Принадлежность решения не проверена; ответ временно заблокирован.'
            elif counts[row['id']] > 1:
                reason = 'Идентификатор решения найден в нескольких профилях. Ответ заблокирован до проверки.'
            elif not valid_profile:
                reason = 'Профиль исходного запроса не подтверждён. Ответ заблокирован до проверки.'
            elif len(candidates) > 1:
                reason = 'Исходный чат неоднозначен. Проверьте решение в центре решений агента-отправителя.'
            elif not origin:
                reason = 'Исходный чат удалён или недоступен. Решение сохранено в центре решений агента-отправителя.'
            fallback = bool(reason)
            result.append({
                **row, 'stored_profile': row['profile'],
                'profile': ledger if fallback else origin['profile'],
                'session_id': row['source_session_id'] if fallback else origin['session_id'],
                'session': {} if fallback else origin['session'],
                'chain': frozenset() if fallback else origin['chain'],
                'decision_fallback': fallback, 'decision_reason': reason,
                'answerable': not unavailable_profiles and valid_profile and counts[row['id']] == 1,
            })
    return result


def authorize_route(route: dict, session_id: str, profile: str) -> None:
    """Authorize origin chat or exact physical-centre answer, never a guessed ID."""
    physical = profile == route['ledger_profile'] and session_id == route['source_session_id']
    origin = (not route['decision_fallback'] and profile == route['profile']
              and session_id in route['chain'])
    if not route['answerable'] or not (physical or origin):
        raise DecisionConflict('Запрос подтверждения не принадлежит этому чату или профилю')


def decorate_decision(item: dict, route: dict) -> dict:
    """Keep UI navigation/answer identity consistent with the activity row."""
    return {
        **item, 'source_session_id': route['session_id'],
        'decision_fallback': route['decision_fallback'],
        'decision_reason': route['decision_reason'],
        **({'choices': []} if not route['answerable'] else {}),
    }


def resolve_ledger_decision(request_id: str, choice: str, *, source_session_id: str, profile: str):
    """Execute in the authenticated physical home with the stored exact identity."""
    from tools.effect_decisions import resolve_effect_decision
    from korra_constants import get_hermes_home

    # Also include the active home for isolated/single-profile API deployments.
    targets = profile_targets()
    active_home = Path(get_hermes_home()).resolve()
    targets = [(name, active_home if name == profile else home) for name, home in targets]
    routes = effect_routes(targets=targets, request_id=request_id)
    if not routes:
        # Preserve the executor's not-found behavior (and its testable seam).
        return resolve_effect_decision(request_id, choice, source_session_id=source_session_id, profile=profile)
    if len(routes) != 1 or routes[0]['ledger_profile'] != profile:
        raise DecisionConflict('Профиль запроса подтверждения неоднозначен')
    route = routes[0]
    authorize_route(route, source_session_id, profile)
    return resolve_effect_decision(request_id, choice,
                                   source_session_id=route['source_session_id'], profile=route['stored_profile'])

"""N10: a nonspawnable profile explains itself once, on either lane.

Adapted from finn763/teknium1's upstream 39c737465e + 89cb27ab6f.
Use the real profile resolver and a real temporary board.
"""
from pathlib import Path

import pytest

from korra_cli import kanban_db as kb


@pytest.fixture
def board(tmp_path, monkeypatch):
    home = tmp_path / 'hermes'
    home.mkdir()
    monkeypatch.setenv('HERMES_HOME', str(home))
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    (home / 'config.yaml').write_text('kanban:\n  review_dispatch: true\n')
    kb.init_db()


def _card(conn, lane, assignee='missing-profile'):
    tid = kb.create_task(conn, title=lane, assignee=assignee)
    if lane == 'review':
        assert kb.request_review(conn, tid)
    return tid


def _diagnoses(conn, tid):
    return [e for e in kb.list_events(conn, tid) if e.kind == 'skipped_nonspawnable']


@pytest.mark.parametrize('lane', ['ready', 'review'])
def test_unknown_assignee_is_recorded_once_and_reassign_gets_new_diagnosis(board, lane):
    with kb.connect_closing() as conn:
        tid = _card(conn, lane)
        for _ in range(2):
            result = kb.dispatch_once(conn)
            assert result.skipped_nonspawnable == [tid]
        assert len(_diagnoses(conn, tid)) == 1
        assert _diagnoses(conn, tid)[0].payload == {'assignee': 'missing-profile'}
        assert kb.get_task(conn, tid).status == lane
        assert kb.assign_task(conn, tid, 'another-missing-profile')
        for _ in range(2):
            kb.dispatch_once(conn)
        assert [e.payload['assignee'] for e in _diagnoses(conn, tid)] == [
            'missing-profile', 'another-missing-profile',
        ]


def test_both_lanes_are_diagnosed_in_one_tick_without_dry_run_writes(board):
    with kb.connect_closing() as conn:
        ids = [_card(conn, lane) for lane in ('ready', 'review')]
        changes = conn.total_changes
        before = {tid: kb.list_events(conn, tid) for tid in ids}
        dry = kb.dispatch_once(conn, dry_run=True)
        assert set(dry.skipped_nonspawnable) == set(ids)
        assert conn.total_changes == changes
        assert before == {tid: kb.list_events(conn, tid) for tid in ids}
        for _ in range(2):
            kb.dispatch_once(conn)
        assert all(len(_diagnoses(conn, tid)) == 1 for tid in ids)
        changes = conn.total_changes
        kb.dispatch_once(conn, dry_run=True)
        assert conn.total_changes == changes


@pytest.mark.parametrize('lane', ['ready', 'review'])
def test_manual_terminal_lane_remains_claimable_after_diagnostic(board, lane):
    with kb.connect_closing() as conn:
        tid = _card(conn, lane, 'operator-terminal')
        assert kb.dispatch_once(conn).skipped_nonspawnable == [tid]
        claim = kb.claim_review_task if lane == 'review' else kb.claim_task
        assert claim(conn, tid, claimer='operator-terminal') is not None
        assert kb.get_task(conn, tid).status == 'running'


def test_human_and_terminal_columns_are_not_restricted_or_diagnosed(board):
    with kb.connect_closing() as conn:
        human = kb.create_task(conn, title='human', actor_kind='human')
        done = _card(conn, 'ready')
        assert kb.complete_task(conn, done, result='manual completion')
        archived = _card(conn, 'ready')
        assert kb.archive_task(conn, archived)
        assert kb.dispatch_once(conn).skipped_nonspawnable == []
        assert all(_diagnoses(conn, tid) == [] for tid in (human, done, archived))
        assert kb.get_task(conn, human).status == 'ready'

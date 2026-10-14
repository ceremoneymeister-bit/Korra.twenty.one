"""K21-335: one card survives owner rework, with attempt and graph fences."""
from pathlib import Path
import json
import os
import subprocess
import sys

import pytest

from korra_cli import kanban_db as kb
from korra_cli.kanban import run_slash


@pytest.fixture
def board(tmp_path, monkeypatch):
    home = tmp_path / 'hermes'
    home.mkdir()
    monkeypatch.setenv('HERMES_HOME', str(home))
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    for key in ('HERMES_KANBAN_TASK', 'KORRA_KANBAN_TASK'):
        monkeypatch.delenv(key, raising=False)
    kb.init_db()
    return home


def finished(conn, *, acceptance=None):
    tid = kb.create_task(conn, title='Original result', assignee='default', acceptance=acceptance)
    assert kb.claim_task(conn, tid)
    run = kb.latest_run(conn, tid)
    assert kb.complete_task(conn, tid, result='original full result', summary='old summary',
                            expected_run_id=run.id, as_worker=True)
    if acceptance == 'owner':
        assert kb.accept_result(conn, tid, author='owner', version=kb.submitted_version(conn, tid))["ok"]
    return tid, run.id


@pytest.mark.parametrize('acceptance', [None, 'owner'])
def test_done_rework_preserves_history_fences_runs_and_waits_for_owner(board, acceptance, monkeypatch):
    monkeypatch.setattr(kb, 'exact_dispatch_requests_enabled', lambda: True)
    with kb.connect_closing() as conn:
        tid, run1 = finished(conn, acceptance=acceptance)
        child = kb.create_task(conn, title='dependent', assignee='default', parents=[tid])
        assert kb.complete_task(conn, child, result='child old result')
        version = kb.submitted_version(conn, tid)
        out = kb.return_for_rework(conn, tid, comment='Correct inputs', author='owner',
                                   version=version, request_id='rework-1')
        assert out['ok'] and out['status'] == 'ready'
        task = kb.get_task(conn, tid)
        assert task.id == tid and task.result is None and task.completed_at is None
        assert task.current_run_id is None and task.claim_lock is None and task.worker_pid is None
        assert task.acceptance == 'owner'
        assert kb.get_task(conn, child).status == 'todo'
        assert kb.pending_exact_dispatch_ids(conn) == [tid]
        assert kb.check_respawn_guard(conn, tid) is None
        assert kb.latest_run(conn, tid).result == 'original full result'
        assert not kb.complete_task(conn, tid, result='late v1', expected_run_id=run1, as_worker=True)
        assert kb.claim_task(conn, tid)
        run2 = kb.latest_run(conn, tid).id
        assert run2 != run1
        assert not kb.complete_task(conn, tid, result='late v1', expected_run_id=run1, as_worker=True)
        assert not kb.block_task(conn, tid, reason='late', expected_run_id=run1)
        assert not kb.heartbeat_worker(conn, tid, expected_run_id=run1)
        before = len(kb.list_events(conn, tid))
        again = kb.return_for_rework(conn, tid, comment='Correct inputs', author='owner',
                                     version=version, request_id='rework-1')
        assert again['ok'] and again['duplicate']
        assert len(kb.list_events(conn, tid)) == before
        assert kb.get_task(conn, tid).current_run_id == run2
        assert kb.complete_task(conn, tid, result='corrected result', expected_run_id=run2, as_worker=True)
        assert kb.get_task(conn, tid).status == 'review'
        assert kb.get_task(conn, child).status == 'todo'
        assert kb.accept_result(conn, tid, author='owner', version=kb.submitted_version(conn, tid))["ok"]
        assert kb.get_task(conn, child).status == 'ready'


def test_done_rework_requires_current_version_and_preserves_legacy_result(board):
    with kb.connect_closing() as conn:
        tid, _ = finished(conn)
        version = kb.submitted_version(conn, tid)
        assert kb.edit_completed_task_result(conn, tid, result='backfilled result')
        stale = kb.return_for_rework(conn, tid, comment='fix', author='owner', version=version)
        assert not stale['ok'] and stale['reason'] == 'stale'
        current = kb.submitted_version(conn, tid)
        assert kb.return_for_rework(conn, tid, comment='fix', author='owner', version=current)['ok']
        event = next(e for e in kb.list_events(conn, tid) if e.kind == 'owner_changes_requested')
        assert event.payload['previous_result'] == 'backfilled result'
        assert event.payload['previous_status'] == 'done'


def test_tool_and_cli_return_the_same_card(board):
    from tools import kanban_tools as kt
    with kb.connect_closing() as conn:
        tid, _ = finished(conn)
        version = kb.submitted_version(conn, tid)
    args = {'task_id': tid, 'reason': 'fix via tool', 'version': version, 'request_id': 'tool-1'}
    out = json.loads(kt._handle_request_changes(args))
    assert out.get('ok'), out
    assert json.loads(kt._handle_request_changes(args))['duplicate']
    with kb.connect_closing() as conn:
        cli_tid, _ = finished(conn)
        cli_version = kb.submitted_version(conn, cli_tid)
    command = f'return-for-rework {cli_tid} --reason "fix via CLI" --card-version {cli_version} --request-id cli-1'
    assert 'Возвращено' in run_slash(command)
    assert 'Возвращено' in run_slash(command)
    with kb.connect_closing() as conn:
        assert kb.get_task(conn, cli_tid).status == 'ready'
        assert [c.body for c in kb.list_comments(conn, cli_tid)] == ['fix via CLI']


@pytest.mark.parametrize('child_status', ['ready', 'review', 'running', 'done'])
def test_rework_invalidates_every_dependent_phase_and_stops_after_commit(board, monkeypatch, child_status):
    with kb.connect_closing() as conn:
        tid, _ = finished(conn)
        child = kb.create_task(conn, title='dependent', assignee='default', parents=[tid])
        if child_status == 'running':
            assert kb.claim_task(conn, child)
            kb._set_worker_pid(conn, child, 87654)
        elif child_status == 'review':
            assert kb.request_review(conn, child)
        elif child_status == 'done':
            assert kb.complete_task(conn, child, result='dependent old result')
        stopped = []

        def stop(pid, lock):
            assert not conn.in_transaction
            assert kb.get_task(conn, child).status == 'todo'
            stopped.append(pid)
            return {'terminated': True}

        monkeypatch.setattr(kb, '_terminate_reclaimed_worker', stop)
        assert kb.return_for_rework(conn, tid, comment='fix', author='owner',
                                   version=kb.submitted_version(conn, tid))['ok']
        assert kb.get_task(conn, child).status == 'todo'
        assert stopped == ([87654] if child_status == 'running' else [])
        if child_status == 'running':
            assert kb.latest_run(conn, child).outcome == 'reclaimed'


def test_failed_invalidation_rolls_back_the_entire_owner_transition(board, monkeypatch):
    with kb.connect_closing() as conn:
        tid, _ = finished(conn)
        child = kb.create_task(conn, title='dependent', assignee='default', parents=[tid])
        before = kb.list_events(conn, tid)

        def fail(*args, **kwargs):
            raise RuntimeError('invalidation failed')

        monkeypatch.setattr(kb, 'invalidate_descendants_for_parent_reopen', fail)
        with pytest.raises(RuntimeError, match='invalidation failed'):
            kb.return_for_rework(conn, tid, comment='fix', author='owner',
                                 version=kb.submitted_version(conn, tid))
        assert kb.get_task(conn, tid).status == 'done'
        assert kb.get_task(conn, child).status == 'ready'
        assert kb.list_comments(conn, tid) == []
        assert kb.list_events(conn, tid) == before


def test_worker_and_public_visitor_cannot_return_the_owners_done_card(board, monkeypatch):
    from tools import kanban_tools as kt
    from gateway.session_context import set_session_vars, clear_session_vars

    with kb.connect_closing() as conn:
        tid, run_id = finished(conn)
        version = kb.submitted_version(conn, tid)
    args = {'task_id': tid, 'reason': 'fix', 'version': version, 'request_id': 'unauthorized'}
    tokens = set_session_vars(platform='telegram', chat_type='dm', chat_id='visitor', user_id='visitor')
    try:
        assert 'belongs to the owner' in json.loads(kt._handle_request_changes(args))['error']
    finally:
        clear_session_vars(tokens)
    monkeypatch.setenv('HERMES_KANBAN_TASK', tid)
    monkeypatch.setenv('HERMES_KANBAN_RUN_ID', str(run_id))
    assert 'error' in json.loads(kt._handle_request_changes(args))
    with kb.connect_closing() as conn:
        assert kb.get_task(conn, tid).status == 'done'
        assert kb.list_comments(conn, tid) == []


def test_rework_waits_for_its_parent_and_keeps_exact_request(board, monkeypatch):
    monkeypatch.setattr(kb, 'exact_dispatch_requests_enabled', lambda: True)
    with kb.connect_closing() as conn:
        parent = kb.create_task(conn, title='upstream', assignee='default')
        tid, _ = finished(conn)
        # A late link adds an unfinished dependency to an already done card.
        kb.link_tasks(conn, parent, tid)
        out = kb.return_for_rework(conn, tid, comment='fix', author='owner',
                                   version=kb.submitted_version(conn, tid))
        assert out['ok'] and out['status'] == 'todo'
        assert kb.pending_exact_dispatch_ids(conn) == []
        assert not kb.claim_task(conn, tid)
        assert kb.complete_task(conn, parent, result='new parent result')
        assert kb.pending_exact_dispatch_ids(conn) == [tid]


def test_rework_through_the_real_korra_command(board):
    """A19-01: «korra kanban … return-for-rework» доходит до подкоманды, а не печатает версию Korra."""
    with kb.connect_closing() as conn:
        tid, _ = finished(conn)
        version = kb.submitted_version(conn, tid)
    root = Path(__file__).resolve().parents[2]
    proc = subprocess.run(
        [sys.executable, '-m', 'korra_cli.main', 'kanban', 'return-for-rework', tid,
         '--reason', 'исправить по CLI', '--card-version', str(version), '--request-id', 'cli-real'],
        cwd=root, env={**os.environ, 'HERMES_HOME': str(board)}, capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    assert 'Возвращено' in proc.stdout, proc.stdout[-2000:]
    with kb.connect_closing() as conn:
        assert kb.get_task(conn, tid).status == 'ready'
        assert [c.body for c in kb.list_comments(conn, tid)] == ['исправить по CLI']

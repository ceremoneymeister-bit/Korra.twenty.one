"""«Веду сам» и «в очередь» при создании карточки (случай «Оператора терминала» 19.10).

running по умолчанию на деле означал «отдать диспетчеру»: агент записывал собственную работу,
через 16 с диспетчер запускал второго исполнителя. Теперь: queued/running — очередь (поведение
прежнее), self — собственная работа автора, диспетчер и сторожа её не трогают.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from korra_cli import kanban_db as kb


@pytest.fixture
def kanban_home(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    kb.init_db()
    return home


def _spawner(spawns):
    def fake_spawn(task, workspace, board=None):
        spawns.append(task.id)
        return 4242
    return fake_spawn


def test_running_and_queued_still_queue_for_dispatcher(kanban_home, all_assignees_spawnable):
    spawns: list[str] = []
    with kb.connect() as conn:
        old = kb.create_task(conn, title="old name", assignee="alice", initial_status="running")
        new = kb.create_task(conn, title="new name", assignee="alice", initial_status="queued")
        assert kb.get_task(conn, old).status == "ready"
        assert kb.get_task(conn, new).status == "ready"
        kb.dispatch_once(conn, spawn_fn=_spawner(spawns))
    assert set(spawns) == {old, new}


def test_self_card_is_running_and_never_dispatched(kanban_home, all_assignees_spawnable):
    spawns: list[str] = []
    with kb.connect() as conn:
        tid = kb.create_task(conn, title="веду сам", assignee="operator", created_by="operator",
                             initial_status="self")
        task = kb.get_task(conn, tid)
        assert task.status == "running"
        assert task.actor_kind == "self"
        assert task.claim_lock == "self:operator"
        assert task.claim_expires is None
        for _ in range(3):
            kb.dispatch_once(conn, spawn_fn=_spawner(spawns), default_assignee="alice")
        assert kb.reconcile_orphaned_running(conn) == []
        assert kb.count_running_tasks(conn) == 0
        assert kb.get_task(conn, tid).status == "running"
    assert spawns == []


def test_self_card_does_not_take_dispatcher_slot(kanban_home, all_assignees_spawnable):
    spawns: list[str] = []
    with kb.connect() as conn:
        kb.create_task(conn, title="веду сам", created_by="operator", initial_status="self")
        queued = kb.create_task(conn, title="в очередь", assignee="alice")
        kb.dispatch_once(conn, spawn_fn=_spawner(spawns), max_in_progress=1)
    assert spawns == [queued]


def test_self_card_completes(kanban_home):
    with kb.connect() as conn:
        tid = kb.create_task(conn, title="веду сам", created_by="operator", initial_status="self")
        assert kb.complete_task(conn, tid, result="сделано")
        assert kb.get_task(conn, tid).status == "done"


def test_self_card_reopened_is_still_not_dispatched(kanban_home, all_assignees_spawnable):
    spawns: list[str] = []
    with kb.connect() as conn:
        tid = kb.create_task(conn, title="веду сам", assignee="operator", created_by="operator",
                             initial_status="self")
        conn.execute("UPDATE tasks SET status='ready', claim_lock=NULL WHERE id=?", (tid,))
        kb.dispatch_once(conn, spawn_fn=_spawner(spawns))
    assert spawns == []


def test_unknown_initial_status_rejected(kanban_home):
    with kb.connect() as conn, pytest.raises(ValueError):
        kb.create_task(conn, title="x", assignee="alice", initial_status="started")


# --- сторожа и миграция видят настоящую, давно идущую self-карточку ---------


def test_old_self_card_is_not_stale(kanban_home):
    """Окно stale реально пройдено: started_at сутки назад, порог минута."""
    with kb.connect() as conn:
        tid = kb.create_task(conn, title="веду сам", created_by="operator", initial_status="self")
        regular = kb.create_task(conn, title="обычная", assignee="alice")
        conn.execute(
            "UPDATE tasks SET status='running', claim_lock='host:1', started_at=? WHERE id=?",
            (int(time.time()) - 86400, regular),
        )
        conn.execute("UPDATE tasks SET started_at = ? WHERE id = ?", (int(time.time()) - 86400, tid))
        conn.commit()
        stale = kb.detect_stale_running(conn, stale_timeout_seconds=60)
        assert regular in stale, "окно stale должно быть реально пройдено"
        assert tid not in stale
        task = kb.get_task(conn, tid)
        assert task.status == "running" and task.claim_lock == "self:operator"


def test_reinit_db_migration_keeps_self_card(kanban_home):
    """Повторная инициализация (миграция inflight-карточек без run) не
    превращает self-карточку в запуск исполнителя."""
    with kb.connect() as conn:
        tid = kb.create_task(conn, title="веду сам", created_by="operator", initial_status="self")
    kb._INITIALIZED_PATHS.clear()
    kb.init_db()
    with kb.connect() as conn:
        task = kb.get_task(conn, tid)
        assert task.status == "running"
        assert task.claim_lock == "self:operator"
        assert task.current_run_id is None
        assert kb.list_runs(conn, tid) == []


# --- слои защиты от диспетчера, каждый по отдельности --------------------------


def _force_ready(conn, tid, status="ready"):
    conn.execute("UPDATE tasks SET status=?, claim_lock=NULL WHERE id=?", (status, tid))
    conn.commit()


def test_exact_dispatch_scan_skips_self_card(kanban_home):
    with kb.connect() as conn:
        tid = kb.create_task(conn, title="веду сам", assignee="operator", created_by="operator",
                             initial_status="self")
        _force_ready(conn, tid)
        with kb.write_txn(conn):
            kb._append_event(conn, tid, kb.EXACT_REQUEST_EVENT, {"source": "test"})
        assert tid not in kb.pending_exact_dispatch_ids(conn)


def test_has_spawnable_ready_ignores_self_card(kanban_home, all_assignees_spawnable):
    with kb.connect() as conn:
        tid = kb.create_task(conn, title="веду сам", assignee="operator", created_by="operator",
                             initial_status="self")
        _force_ready(conn, tid)
        assert kb.has_spawnable_ready(conn) is False


def test_review_claim_skips_self_card(kanban_home):
    with kb.connect() as conn:
        tid = kb.create_task(conn, title="веду сам", assignee="operator", created_by="operator",
                             initial_status="self")
        _force_ready(conn, tid, status="review")
        assert kb.claim_review_task(conn, tid) is None
        assert kb.get_task(conn, tid).claim_lock is None


def test_self_card_does_not_take_per_profile_slot(kanban_home, all_assignees_spawnable):
    spawns: list[str] = []
    with kb.connect() as conn:
        kb.create_task(conn, title="веду сам", assignee="alice", created_by="alice",
                       initial_status="self")
        queued = kb.create_task(conn, title="в очередь", assignee="alice")
        kb.dispatch_once(conn, spawn_fn=_spawner(spawns), max_in_progress_per_profile=1)
    assert spawns == [queued]


# --- пауза и блокировка возвращают карточку автору ------------------------------


@pytest.mark.parametrize("kind", [None, "needs_input", kb.OWNER_PAUSE_KIND])
def test_unblocked_self_card_returns_to_its_author(kanban_home, all_assignees_spawnable, kind):
    spawns: list[str] = []
    with kb.connect() as conn:
        tid = kb.create_task(conn, title="веду сам", created_by="operator", initial_status="self")
        assert kb.block_task(conn, tid, reason="нужен ответ", kind=kind)
        assert kb.get_task(conn, tid).status == "blocked"
        assert kb.unblock_task(conn, tid)
        task = kb.get_task(conn, tid)
        assert task.status == "running"
        assert task.claim_lock == "self:operator" and task.claim_expires is None
        kb.dispatch_once(conn, spawn_fn=_spawner(spawns))
        assert kb.get_task(conn, tid).status == "running"
    assert spawns == []


def test_self_card_waiting_for_parent_returns_to_its_author(kanban_home, all_assignees_spawnable):
    spawns: list[str] = []
    with kb.connect() as conn:
        parent = kb.create_task(conn, title="родитель", assignee="alice")
        tid = kb.create_task(conn, title="веду сам", created_by="operator", initial_status="self")
        kb.link_tasks(conn, parent, tid)
        assert kb.block_task(conn, tid, reason="жду родителя", kind="dependency")
        assert kb.get_task(conn, tid).status == "todo"
        kb.claim_task(conn, parent)
        assert kb.complete_task(conn, parent, result="готово")
        kb.recompute_ready(conn)
        task = kb.get_task(conn, tid)
        assert task.status == "running" and task.claim_lock == "self:operator"
        kb.dispatch_once(conn, spawn_fn=_spawner(spawns))
    assert spawns == []


# --- без уведомлений и регистр -------------------------------------------------


def test_self_card_does_not_inherit_parent_subscriptions(kanban_home):
    with kb.connect() as conn:
        parent = kb.create_task(conn, title="родитель", assignee="alice")
        kb.add_notify_sub(conn, task_id=parent, platform="telegram", chat_id="42")
        child = kb.create_task(conn, title="обычный шаг", assignee="alice", parents=(parent,))
        own = kb.create_task(conn, title="веду сам", created_by="operator",
                             parents=(parent,), initial_status="self")
        assert kb.list_notify_subs(conn, child)
        assert kb.list_notify_subs(conn, own) == []


@pytest.mark.parametrize("value", ["Self", " SELF ", "Queued", "BLOCKED"])
def test_initial_status_is_case_insensitive(kanban_home, value):
    with kb.connect() as conn:
        tid = kb.create_task(conn, title="x", assignee="alice", created_by="operator",
                             initial_status=value)
        task = kb.get_task(conn, tid)
    expected = {"self": "running", "queued": "ready", "blocked": "blocked"}[value.strip().lower()]
    assert task.status == expected


def test_self_card_records_the_worker_task_that_created_it(kanban_home):
    with kb.connect() as conn:
        parent = kb.create_task(conn, title="задача исполнителя", assignee="pm")
        own = kb.create_task(conn, title="веду сам", created_by="pm", initial_status="self",
                             self_creator_task=parent)
        plain = kb.create_task(conn, title="обычная", assignee="pm", self_creator_task=parent)
        assert kb.self_card_creator_task(conn, own) == parent
        assert kb.self_card_creator_task(conn, plain) is None


# --- CLI: умолчание — очередь с подсказкой; self — «веду сам» -------------------


def _cli(argv: str) -> str:
    from korra_cli import kanban as kc
    return kc.run_slash(argv)


def test_cli_default_is_the_queue_with_a_hint(kanban_home):
    out = _cli("create 'по умолчанию' --assignee alice")
    tid = out.split("Created ", 1)[1].split()[0]
    with kb.connect() as conn:
        task = kb.get_task(conn, tid)
    assert task.status == "ready" and (task.actor_kind or "") != "self"
    assert "в очереди: диспетчер запустит исполнителя alice" in out
    assert "--initial-status self" in out
    assert "веду сам" not in out.split("--initial-status self")[0]


def test_cli_running_is_the_same_queue(kanban_home):
    out = _cli("create 'прежнее имя' --assignee alice --initial-status running")
    assert "(ready, исполнитель=alice)" in out
    assert "в очереди: диспетчер запустит исполнителя alice" in out


@pytest.mark.parametrize("flag", ["self", "Self"])
def test_cli_self_says_self_work_and_reads_back(kanban_home, flag):
    out = _cli(f"create 'моя работа' --created-by operator --initial-status {flag}")
    assert "(running, исполнитель=-)" in out
    assert "веду сам: карточка в работе у автора" in out
    assert "в очереди" not in out
    tid = out.split("Created ", 1)[1].split()[0]
    shown = json.loads(_cli(f"show {tid} --json"))["task"]
    assert shown["status"] == "running"
    assert shown["self_work"] is True and shown["actor_kind"] == "self"
    assert shown["claim_lock"] == "self:operator"


def test_cli_blocked_says_it_waits_for_a_human(kanban_home):
    out = _cli("create 'нужен человек' --assignee alice --initial-status blocked")
    assert "(blocked, исполнитель=alice)" in out
    assert "ждёт человека" in out and "korra kanban unblock" in out
    assert "в очереди" not in out


def test_cli_json_create_matches_requested_state(kanban_home):
    queued = json.loads(_cli("create q --assignee alice --json"))
    own = json.loads(_cli("create s --created-by operator --initial-status self --json"))
    assert queued["status"] == "ready" and queued["self_work"] is False
    assert own["status"] == "running" and own["self_work"] is True


def test_claim_task_refuses_self_card(kanban_home):
    """Второй слой защиты (первый — выборка диспетчера) проверяется отдельно."""
    with kb.connect() as conn:
        tid = kb.create_task(conn, title="веду сам", assignee="operator", created_by="operator",
                             initial_status="self")
        _force_ready(conn, tid)
        assert kb.claim_task(conn, tid) is None
        assert kb.get_task(conn, tid).status == "ready"

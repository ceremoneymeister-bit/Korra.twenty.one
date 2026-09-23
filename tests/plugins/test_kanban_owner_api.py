"""Owner-facing Kanban API (K21-139): answer, pause, archive and attention."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from korra_cli import kanban_db as kb

API = "/api/plugins/kanban"


def _load_plugin():
    plugin_file = Path(__file__).resolve().parents[2] / "plugins" / "kanban" / "dashboard" / "plugin_api.py"
    spec = importlib.util.spec_from_file_location("kanban_plugin_owner_api_test", plugin_file)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def kanban_home(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    kb.init_db()
    return home


@pytest.fixture
def client(kanban_home):
    app = FastAPI()
    app.include_router(_load_plugin().router, prefix=API)
    return TestClient(app)


def _running_task(conn, title="t", assignee="worker"):
    tid = kb.create_task(conn, title=title, assignee=assignee)
    with kb.write_txn(conn):
        conn.execute("UPDATE tasks SET status='ready' WHERE id=?", (tid,))
    assert kb.claim_task(conn, tid, claimer="worker") is not None
    return tid


def _question(title="Заменить ссылки", question="Подтвердите 4 замены"):
    with kb.connect_closing() as conn:
        tid = _running_task(conn, title)
        kb.block_task(conn, tid, reason=question, kind="needs_input")
    return tid


def test_blocked_task_exposes_question_and_revision(client):
    tid = _question()
    task = client.get(f"{API}/tasks/{tid}").json()["task"]
    assert task["owner_attention"] == "question"
    assert task["block_reason"] == "Подтвердите 4 замены"
    assert isinstance(task["block_revision"], int)
    board = client.get(f"{API}/board").json()
    card = next(t for c in board["columns"] for t in c["tasks"] if t["id"] == tid)
    assert card["block_revision"] == task["block_revision"]


def test_respond_resumes_once_and_keeps_answer(client):
    tid = _question()
    rev = client.get(f"{API}/tasks/{tid}").json()["task"]["block_revision"]
    body = {"answer": "Подтверждаю", "revision": rev, "request_id": "req-1"}
    first = client.post(f"{API}/tasks/{tid}/respond", json=body)
    assert first.status_code == 200, first.text
    assert first.json()["status"] == "ready" and first.json()["duplicate"] is False
    again = client.post(f"{API}/tasks/{tid}/respond", json=body)
    assert again.status_code == 200 and again.json()["duplicate"] is True
    detail = client.get(f"{API}/tasks/{tid}").json()
    assert [c["body"] for c in detail["comments"]] == ["Подтверждаю"]
    assert detail["task"]["block_reason"] is None
    assert detail["task"]["owner_attention"] is None


def test_respond_to_replaced_question_is_explained(client):
    tid = _question(question="Первый вопрос")
    old = client.get(f"{API}/tasks/{tid}").json()["task"]["block_revision"]
    with kb.connect_closing() as conn:
        kb.unblock_task(conn, tid)
        with kb.write_txn(conn):
            conn.execute("UPDATE tasks SET status='ready' WHERE id=?", (tid,))
        kb.claim_task(conn, tid, claimer="worker")
        kb.block_task(conn, tid, reason="Второй вопрос", kind="needs_input")
    r = client.post(f"{API}/tasks/{tid}/respond",
                    json={"answer": "да", "revision": old, "request_id": "req-2"})
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "question_changed"


def test_respond_when_not_waiting(client):
    with kb.connect_closing() as conn:
        tid = _running_task(conn)
    r = client.post(f"{API}/tasks/{tid}/respond", json={"request_id": "req-3"})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "not_waiting"


def test_board_pause_is_owner_pause_and_stops_worker(client, monkeypatch):
    stopped = []
    monkeypatch.setattr(
        kb, "_terminate_reclaimed_worker",
        lambda pid, claim_lock, **_: stopped.append(pid) or {"terminated": True},
    )
    with kb.connect_closing() as conn:
        tid = _running_task(conn)
        with kb.write_txn(conn):
            conn.execute("UPDATE tasks SET worker_pid = 5151 WHERE id = ?", (tid,))
    r = client.patch(f"{API}/tasks/{tid}", json={"status": "blocked", "block_reason": "Подожди"})
    assert r.status_code == 200, r.text
    task = r.json()["task"]
    assert task["block_kind"] == kb.OWNER_PAUSE_KIND
    assert task["owner_attention"] == "paused"
    assert stopped == [5151]


def test_board_archive_of_unfinished_parent_keeps_child_waiting(client, monkeypatch):
    monkeypatch.setattr(kb, "_terminate_reclaimed_worker", lambda *a, **k: {"terminated": True})
    tid = _question()
    with kb.connect_closing() as conn:
        child = kb.create_task(conn, title="next", assignee="worker", parents=[tid])
    assert client.patch(f"{API}/tasks/{tid}", json={"status": "archived"}).status_code == 200
    assert client.get(f"{API}/tasks/{child}").json()["task"]["status"] == "todo"
    r = client.patch(f"{API}/tasks/{child}", json={"status": "ready"})
    assert r.status_code == 409


def test_attention_lists_questions_across_boards(client):
    tid = _question()
    kb.create_board("second")
    with kb.connect_closing(board="second") as conn:
        other = _running_task(conn, "Другое")
        kb.block_task(conn, other, reason="Какой срок?", kind="needs_input")
        paused = _running_task(conn, "Пауза")
        kb.block_task(conn, paused, reason="позже", kind=kb.OWNER_PAUSE_KIND)
    data = client.get(f"{API}/attention").json()
    by_task = {i["task_id"]: i for i in data["items"]}
    assert by_task[tid]["kind"] == "question" and by_task[tid]["board"] == "default"
    assert by_task[other]["board"] == "second" and by_task[other]["question"] == "Какой срок?"
    assert isinstance(by_task[other]["revision"], int)
    assert by_task[paused]["kind"] == "paused"
    assert data["counts"]["question"] == 2 and data["count"] == 2
    assert data["errors"] == []


def test_failure_breaker_block_is_a_problem_not_a_question(client):
    with kb.connect_closing() as conn:
        tid = _running_task(conn)
        kb.block_task(conn, tid, reason="q", kind="needs_input")
        kb.unblock_task(conn, tid)
        with kb.write_txn(conn):
            conn.execute("UPDATE tasks SET status='blocked' WHERE id=?", (tid,))
            kb._append_event(conn, tid, "gave_up", {"error": "crashed twice"})
    task = client.get(f"{API}/tasks/{tid}").json()["task"]
    assert task["owner_attention"] == "problem"
    assert task["block_revision"] is None


# ---------------------------------------------------------------------------
# Owner acceptance (K21-141)
# ---------------------------------------------------------------------------

def _submitted_via_form(client, title="3 названия"):
    r = client.post(f"{API}/tasks", json={
        "title": title, "body": "…", "assignee": "pm", "acceptance": "owner",
    })
    assert r.status_code == 200, r.text
    tid = r.json()["task"]["id"]
    assert r.json()["task"]["acceptance"] == "owner"
    with kb.connect_closing() as conn:
        assert kb.claim_task(conn, tid, claimer="worker") is not None
        run_id = kb.get_task(conn, tid).current_run_id
        kb.complete_task(conn, tid, result="1. Поручения — знакомое слово", summary="3 варианта",
                         expected_run_id=run_id, as_worker=True)
    return tid


def test_form_task_waits_for_the_owner_and_is_accepted(client):
    tid = _submitted_via_form(client)
    task = client.get(f"{API}/tasks/{tid}").json()["task"]
    assert task["status"] == "review" and task["owner_attention"] == "accept"
    assert task["result"].startswith("1. Поручения")
    version = task["submitted_version"]
    assert isinstance(version, int)
    attention = client.get(f"{API}/attention").json()
    item = next(i for i in attention["items"] if i["task_id"] == tid)
    assert item["kind"] == "accept" and item["version"] == version
    r = client.post(f"{API}/tasks/{tid}/accept", json={"version": version, "request_id": "acc-1"})
    assert r.status_code == 200, r.text
    assert r.json()["task"]["status"] == "done"
    assert client.get(f"{API}/attention").json()["count"] == 0


def test_owner_returns_result_with_a_remark(client):
    tid = _submitted_via_form(client)
    version = client.get(f"{API}/tasks/{tid}").json()["task"]["submitted_version"]
    r = client.post(f"{API}/tasks/{tid}/request-changes", json={
        "version": version, "request_id": "rw-1", "comment": "Добавь четвёртый вариант",
    })
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "ready"
    detail = client.get(f"{API}/tasks/{tid}").json()
    assert detail["comments"][-1]["body"] == "Добавь четвёртый вариант"
    stale = client.post(f"{API}/tasks/{tid}/accept", json={"version": version, "request_id": "acc-2"})
    assert stale.status_code == 409
    assert stale.json()["detail"]["code"] == "not_waiting_acceptance"


def test_accept_of_an_outdated_version_is_explained(client):
    tid = _submitted_via_form(client)
    r = client.post(f"{API}/tasks/{tid}/accept", json={"version": 1, "request_id": "acc-3"})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "result_changed"


def test_human_step_is_owner_attention_when_ready(client):
    r = client.post(f"{API}/tasks", json={
        "title": "Обучить команду", "actor_kind": "human",
        "plan_id": "plan-1", "plan_title": "Новая система ботов",
    })
    assert r.status_code == 200, r.text
    task = r.json()["task"]
    assert task["assignee"] is None and task["owner_attention"] == "human_step"
    item = next(i for i in client.get(f"{API}/attention").json()["items"] if i["task_id"] == task["id"])
    assert item["kind"] == "human_step" and item["plan_title"] == "Новая система ботов"

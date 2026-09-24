"""Owner-facing Kanban notifications (K21-142).

The owner hears about questions, problems, results waiting for acceptance
and final results — in their language, with the full question — and is not
pinged for every intermediate plan step or for their own pause.
"""

import asyncio

from gateway.config import Platform
from gateway.run import GatewayRunner
from korra_cli import kanban_db as kb


class RecordingAdapter:
    def __init__(self):
        self.sent = []

    async def send(self, chat_id, text, metadata=None):
        self.sent.append({"chat_id": chat_id, "text": text})

    async def handle_message(self, event):
        pass


async def _tick(monkeypatch, runner):
    real_sleep = asyncio.sleep

    async def fake_sleep(delay):
        if delay == 5:
            return None
        runner._running = False
        await real_sleep(0)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    await runner._kanban_notifier_watcher(interval=1)


def _runner(adapter):
    runner = GatewayRunner.__new__(GatewayRunner)
    runner._running = True
    runner.adapters = {Platform.TELEGRAM: adapter}
    runner._kanban_sub_fail_counts = {}
    runner._kanban_dispatcher_lock_handle = object()
    return runner


def _board(tmp_path, monkeypatch, name):
    monkeypatch.setenv("HERMES_KANBAN_DB", str(tmp_path / f"{name}.db"))
    kb.init_db()


def _run(monkeypatch):
    adapter = RecordingAdapter()
    asyncio.run(_tick(monkeypatch, _runner(adapter)))
    return adapter.sent


def test_submitted_result_is_announced_for_review(tmp_path, monkeypatch):
    _board(tmp_path, monkeypatch, "submitted")
    conn = kb.connect()
    try:
        tid = kb.create_task(conn, title="3 названия", assignee="pm", acceptance="owner")
        kb.add_notify_sub(conn, task_id=tid, platform="telegram", chat_id="chat-1")
        kb.claim_task(conn, tid, claimer="w")
        run_id = kb.get_task(conn, tid).current_run_id
        kb.complete_task(conn, tid, result="…", summary="Три варианта с пояснениями",
                         expected_run_id=run_id, as_worker=True)
    finally:
        conn.close()
    sent = _run(monkeypatch)
    assert len(sent) == 1
    assert "Результат готов к вашей проверке" in sent[0]["text"]
    assert "Три варианта с пояснениями" in sent[0]["text"]


def test_question_is_delivered_in_full(tmp_path, monkeypatch):
    _board(tmp_path, monkeypatch, "question")
    question = "Подтвердите 4 замены:\n" + "\n".join(f"{i}. old{i} → new{i}" for i in range(1, 5))
    conn = kb.connect()
    try:
        tid = kb.create_task(conn, title="Замены", assignee="rop")
        kb.add_notify_sub(conn, task_id=tid, platform="telegram", chat_id="chat-1")
        kb.claim_task(conn, tid, claimer="w")
        kb.block_task(conn, tid, reason=question, kind="needs_input")
    finally:
        conn.close()
    sent = _run(monkeypatch)
    assert len(sent) == 1
    assert "Нужно ваше решение" in sent[0]["text"]
    assert "4. old4 → new4" in sent[0]["text"]


def test_intermediate_plan_step_and_owner_pause_are_quiet(tmp_path, monkeypatch):
    _board(tmp_path, monkeypatch, "quiet")
    conn = kb.connect()
    try:
        step = kb.create_task(conn, title="Аудит", assignee="rop", plan_id="p1", plan_title="План")
        final = kb.create_task(conn, title="Итог", assignee="rop", parents=[step], acceptance="owner")
        paused = kb.create_task(conn, title="Пауза", assignee="rop")
        for tid in (step, final, paused):
            kb.add_notify_sub(conn, task_id=tid, platform="telegram", chat_id="chat-1")
        kb.claim_task(conn, step, claimer="w")
        kb.complete_task(conn, step, result="аудит", as_worker=True)
        kb.block_task(conn, paused, reason="позже", kind=kb.OWNER_PAUSE_KIND)
    finally:
        conn.close()
    assert _run(monkeypatch) == []


# --- Live run 24.09 on the stand (dario): what the owner was not told, or told twice ---


def _submit(conn, tid, summary, result="…"):
    kb.claim_task(conn, tid, claimer="w")
    run_id = kb.get_task(conn, tid).current_run_id
    kb.complete_task(conn, tid, result=result, summary=summary,
                     expected_run_id=run_id, as_worker=True)


def test_owner_step_ready_is_announced_once(tmp_path, monkeypatch):
    """The step before the owner's own step finishes quietly, but the owner
    must hear that it is now their turn — the chat promised it."""
    _board(tmp_path, monkeypatch, "human-step")
    conn = kb.connect()
    try:
        plan = {"plan_id": "p1", "plan_title": "Письмо"}
        step = kb.create_task(conn, title="Три темы", assignee="writer", **plan)
        choose = kb.create_task(conn, title="Выбрать тему", actor_kind="human", parents=[step], **plan)
        for tid in (step, choose):
            kb.add_notify_sub(conn, task_id=tid, platform="telegram", chat_id="chat-1")
        kb.claim_task(conn, step, claimer="w")
        kb.complete_task(conn, step, result="1. А\n2. Б\n3. В", summary="Три темы", as_worker=True)
        assert kb.get_task(conn, choose).status == "ready"
    finally:
        conn.close()
    sent = _run(monkeypatch)
    assert len(sent) == 1, sent
    assert "Ваш шаг" in sent[0]["text"]
    assert "Выбрать тему" in sent[0]["text"]


def test_owner_actions_do_not_notify_the_owner(tmp_path, monkeypatch):
    """Accepting a result and finishing one's own step are the owner's own
    actions: no second message, no model wake (live: «Задача выполнена»
    after «Принять»)."""
    _board(tmp_path, monkeypatch, "owner-actions")
    conn = kb.connect()
    try:
        letter = kb.create_task(conn, title="Письмо", assignee="writer", acceptance="owner")
        own = kb.create_task(conn, title="Позвонить клиенту", actor_kind="human")
        for tid in (letter, own):
            kb.add_notify_sub(conn, task_id=tid, platform="telegram", chat_id="chat-1")
        _submit(conn, letter, "Письмо готово")
        version = kb.submitted_version(conn, letter)
        assert kb.accept_result(conn, letter, author="Владелец", version=version, request_id="a1")["ok"]
        assert kb.complete_task(conn, own, result="Позвонил", summary="Позвонил")
    finally:
        conn.close()
    sent = _run(monkeypatch)
    assert len(sent) == 1, sent
    assert "Результат готов к вашей проверке" in sent[0]["text"]


def test_permission_question_points_to_the_card(tmp_path, monkeypatch):
    """A permission for external changes is decided only on the card; the
    message must not invite an answer in the chat."""
    _board(tmp_path, monkeypatch, "approval")
    conn = kb.connect()
    try:
        tid = kb.create_task(conn, title="CRM", assignee="manager")
        kb.add_notify_sub(conn, task_id=tid, platform="telegram", chat_id="chat-1")
        kb.claim_task(conn, tid, claimer="w")
        kb.block_task(conn, tid, reason="Статус → «Предложение отправлено»", kind=kb.APPROVAL_BLOCK_KIND)
    finally:
        conn.close()
    sent = _run(monkeypatch)
    assert len(sent) == 1
    text = sent[0]["text"]
    assert "Нужно ваше разрешение" in text
    assert "Статус → «Предложение отправлено»" in text
    assert "Ответьте здесь" not in text


def test_new_version_after_owner_remark_is_marked(tmp_path, monkeypatch):
    """After «Вернуть с замечанием» the next result is a new version, not a
    repeat (live: the chat called it «повторное уведомление»)."""
    _board(tmp_path, monkeypatch, "rework")
    conn = kb.connect()
    try:
        tid = kb.create_task(conn, title="Список для CRM", assignee="manager", acceptance="owner")
        kb.add_notify_sub(conn, task_id=tid, platform="telegram", chat_id="chat-1")
        _submit(conn, tid, "Список готов")
        first = kb.submitted_version(conn, tid)
        assert kb.return_for_rework(conn, tid, comment="Добавь ответственного", author="Владелец",
                                    version=first, request_id="r1")["ok"]
        _submit(conn, tid, "Список готов")
    finally:
        conn.close()
    sent = _run(monkeypatch)
    assert len(sent) == 2, sent
    assert "новая версия" not in sent[0]["text"]
    assert "новая версия" in sent[1]["text"]

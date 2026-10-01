import os
import time
import json
from datetime import datetime, timezone

import pytest

from korra_state import SessionDB
from korra_cli import chat_activity
from tools.effect_decisions import EffectDecisionStoreUnavailable, create_pending


def _session(home, session_id, *, source, title, holder=None):
    db = SessionDB(home / "state.db")
    db.create_session(session_id, source=source)
    db.set_session_title(session_id, title)
    db.append_message(session_id, role="user", content=f"Задача {title}")
    db.set_session_read(session_id)
    time.sleep(0.01)
    db.append_message(session_id, role="assistant", content="Готово")
    if holder:
        assert db.try_acquire_session_turn_lease(
            session_id, holder, ttl_seconds=300
        )
    db.close()


def test_panel_restart_keeps_durable_turn_running(monkeypatch, tmp_path):
    home = tmp_path / "lawyer"
    home.mkdir()
    _session(
        home,
        "telegram-chat",
        source="telegram",
        title="Договор клиента",
        holder=f"pid={os.getpid()}:turn=telegram",
    )
    monkeypatch.setattr(
        chat_activity, "_profile_targets", lambda _profile: [("lawyer", home)]
    )

    browser = [{
        "message_id": "browser-message",
        "session_id": "telegram-chat",
        "profile": "lawyer",
        # This is exactly what the panel-local DeliveryLedger used to report
        # after its process restarted and lost the in-memory task object.
        "status": "interrupted",
        "updated_at": 1,
        "history_count": 0,
        "user_message": {"role": "user", "content": "Проверить"},
    }]
    runs = chat_activity.project_chat_activity(
        browser, profile="lawyer", session_id="telegram-chat"
    )

    assert len(runs) == 1  # exact stream recovery never receives synthetic ids
    assert runs[0]["status"] == "running"
    assert runs[0]["source"] == "telegram"
    assert runs[0]["title"] == "Договор клиента"
    assert runs[0]["delivery"] == "pending"


def test_scheduled_runs_do_not_appear_as_unread_or_running_chats(monkeypatch, tmp_path):
    _session(tmp_path, "run-1", source="cron", title="Отчёт", holder="cron-run")
    _session(tmp_path, "discussion", source="telegram", title="Обсуждение отчёта")
    monkeypatch.setattr(chat_activity, "_profile_targets", lambda _: [("default", tmp_path)])
    runs = chat_activity.project_chat_activity([], profile=None, session_id=None)
    assert {run["session_id"] for run in runs} == {"discussion"}
    db = SessionDB(tmp_path / "state.db")
    assert db.get_session("run-1")["source"] == "cron"
    db.close()


def test_kanban_worker_is_visible_while_running_but_never_an_unread_answer(
    monkeypatch, tmp_path
):
    # Чата исполнителя нет в списке: «Новый ответ» от него некому было бы
    # снять. Идущий запуск при этом виден в «В работе» со своим названием.
    _session(
        tmp_path, "worker-running", source="kanban", title="Work kanban task t_1",
        holder=f"pid={os.getpid()}:turn=kanban",
    )
    _session(tmp_path, "worker-done", source="kanban", title="Work kanban task t_2")
    monkeypatch.setattr(chat_activity, "_profile_targets", lambda _: [("default", tmp_path)])

    runs = chat_activity.project_chat_activity([], profile=None, session_id=None)

    assert [(run["session_id"], run["status"], run["title"]) for run in runs] == [
        ("worker-running", "running", "Work kanban task t_1")
    ]


def test_global_projection_keeps_parallel_channels_and_server_unread(
    monkeypatch, tmp_path
):
    home = tmp_path / "lawyer"
    home.mkdir()
    _session(
        home,
        "telegram-chat",
        source="telegram",
        title="Telegram",
        holder=f"pid={os.getpid()}:turn=telegram",
    )
    _session(
        home,
        "cli-chat",
        source="cli",
        title="Терминал",
        holder=f"pid={os.getpid()}:turn=cli",
    )
    create_pending(
        kind="outbound_message",
        owner_id="owner",
        profile="lawyer",
        source_session_id="decision-chat",
        source_session_key="telegram:dm:1",
        payload={"channel": "telegram", "recipient": "owner", "text": "Черновик"},
        path=home / "effect_decisions.sqlite3",
    )
    monkeypatch.setattr(
        chat_activity, "_profile_targets", lambda _profile: [("lawyer", home)]
    )

    runs = chat_activity.project_chat_activity([], profile=None, session_id=None)
    running = [item for item in runs if item["status"] == "running"]
    ready = [item for item in runs if item["status"] == "completed"]
    waiting = [item for item in runs if item["status"] == "waiting_decision"]

    assert {(item["session_id"], item["source"]) for item in running} == {
        ("telegram-chat", "telegram"),
        ("cli-chat", "cli"),
    }
    assert {item["session_id"] for item in ready} == {
        "telegram-chat",
        "cli-chat",
    }
    assert all(item["unread"] for item in ready)
    assert [item["session_id"] for item in waiting] == ["decision-chat"]


def test_pending_decision_replaces_same_turn_lease_instead_of_counting_twice(
    monkeypatch, tmp_path
):
    home = tmp_path / "lawyer"
    home.mkdir()
    _session(
        home,
        "decision-chat",
        source="browser",
        title="Один запрос",
        holder=f"pid={os.getpid()}:turn=browser",
    )
    create_pending(
        kind="outbound_message",
        owner_id="owner",
        profile="lawyer",
        source_session_id="decision-chat",
        source_session_key="browser:decision-chat",
        payload={"channel": "telegram", "recipient": "owner", "text": "Черновик"},
        path=home / "effect_decisions.sqlite3",
    )
    monkeypatch.setattr(
        chat_activity, "_profile_targets", lambda _profile: [("lawyer", home)]
    )

    browser = [{
        "message_id": "browser-message",
        "session_id": "decision-chat",
        "profile": "lawyer",
        "status": "running",
        "updated_at": 1,
        "history_count": 0,
        "user_message": {"role": "user", "content": "Один запрос"},
    }]
    runs = chat_activity.project_chat_activity(
        browser, profile=None, session_id=None
    )
    active = [
        item for item in runs
        if item["session_id"] == "decision-chat"
        and item["status"] in {"running", "waiting_decision", "stale"}
    ]

    assert len(active) == 1
    assert active[0]["message_id"] == "browser-message"
    assert active[0]["status"] == "waiting_decision"


def test_several_decisions_in_one_chat_are_one_row_that_carries_their_number(
    monkeypatch, tmp_path
):
    # Four birthday greetings waiting in one conversation are four answers
    # the owner owes, not one; the row stays single so «работ» is not inflated.
    home = tmp_path / "bitrix"
    home.mkdir()
    _session(home, "greetings", source="browser", title="Поздравления")
    for index in range(4):
        create_pending(
            kind="outbound_message",
            owner_id="owner",
            profile="bitrix",
            source_session_id="greetings",
            source_session_key="browser:greetings",
            payload={"channel": "telegram", "recipient": f"client-{index}", "text": "Поздравление"},
            path=home / "effect_decisions.sqlite3",
        )
    create_pending(
        kind="outbound_message",
        owner_id="owner",
        profile="bitrix",
        source_session_id="invoice",
        source_session_key="browser:invoice",
        payload={"channel": "telegram", "recipient": "owner", "text": "Счёт"},
        path=home / "effect_decisions.sqlite3",
    )
    monkeypatch.setattr(chat_activity, "_profile_targets", lambda _profile: [("bitrix", home)])

    browser = [{
        "message_id": "browser-greetings",
        "session_id": "greetings",
        "profile": "bitrix",
        "status": "running",
        "updated_at": 1,
        "history_count": 0,
        "user_message": {"role": "user", "content": "Поздравь"},
    }]
    runs = chat_activity.project_chat_activity(browser, profile=None, session_id=None)
    waiting = {item["session_id"]: item for item in runs if item["status"] == "waiting_decision"}

    assert set(waiting) == {"greetings", "invoice"}
    assert waiting["greetings"]["message_id"] == "browser-greetings"
    assert waiting["greetings"]["pending_decisions"] == 4
    assert waiting["invoice"]["pending_decisions"] == 1
    assert sum(item["pending_decisions"] for item in waiting.values()) == 5


def test_corrupt_effect_store_is_not_hidden_as_an_empty_activity_list(
    monkeypatch, tmp_path
):
    home = tmp_path / "lawyer"
    home.mkdir()
    _session(home, "decision-chat", source="browser", title="Решение")
    db_path = home / "effect_decisions.sqlite3"
    create_pending(
        kind="outbound_message",
        owner_id="owner",
        profile="lawyer",
        source_session_id="decision-chat",
        source_session_key="browser:decision-chat",
        payload={"target": "owner", "message": "Черновик"},
        path=db_path,
    )
    damaged = bytearray(db_path.read_bytes())
    damaged[5:29] = b"\x17\x03\x03\x00\x13" + (b"T" * 19)
    db_path.write_bytes(damaged)
    monkeypatch.setattr(
        chat_activity, "_profile_targets", lambda _profile: [("lawyer", home)]
    )

    with pytest.raises(EffectDecisionStoreUnavailable):
        chat_activity.project_chat_activity([], profile="lawyer", session_id=None)


def test_expired_lease_is_stale_not_running(monkeypatch, tmp_path):
    home = tmp_path / "default"
    home.mkdir()
    _session(
        home,
        "stale-chat",
        source="cli",
        title="Старый ход",
        holder=f"pid={os.getpid()}:turn=stale",
    )
    db = SessionDB(home / "state.db")
    db._conn.execute(
        "UPDATE session_turn_leases SET expires_at = ?", (time.time() - 1,)
    )
    db._conn.commit()
    db.close()
    monkeypatch.setattr(
        chat_activity, "_profile_targets", lambda _profile: [("", home)]
    )

    runs = chat_activity.project_chat_activity([], profile=None, session_id=None)
    lease = next(item for item in runs if item["message_id"].startswith("activity-lease-"))
    assert lease["status"] == "stale"
    assert lease["delivery"] == "unknown"


def test_fresh_gateway_marker_overrides_an_expired_generic_lease(
    monkeypatch, tmp_path
):
    home = tmp_path / "default"
    home.mkdir()
    _session(
        home,
        "gateway-chat",
        source="telegram",
        title="Живой gateway",
        holder=f"pid={os.getpid()}:turn=old",
    )
    db = SessionDB(home / "state.db")
    db._conn.execute(
        "UPDATE session_turn_leases SET expires_at = ?", (time.time() - 1,)
    )
    db._conn.commit()
    db.save_gateway_routing_entry(
        "telegram:dm:1",
        json.dumps({
            "session_id": "gateway-chat",
            "active_turn_token": "exact-token",
            "active_turn_started_at": datetime.now(timezone.utc).isoformat(),
            "origin": {"platform": "telegram"},
        }),
        scope=str((home / "sessions").resolve()),
    )
    db.close()
    state = home / "state"
    state.mkdir()
    (state / "gateway.heartbeat").write_text(
        json.dumps({"pid": os.getpid()}), encoding="utf-8"
    )
    monkeypatch.setattr(
        chat_activity, "_profile_targets", lambda _profile: [("", home)]
    )

    runs = chat_activity.project_chat_activity([], profile=None, session_id=None)
    activity = [item for item in runs if item["session_id"] == "gateway-chat"]
    assert [item["status"] for item in activity].count("running") == 1
    assert all(item["status"] != "stale" for item in activity)


def test_gateway_heartbeat_requires_fresh_live_producer(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    heartbeat = state / "gateway.heartbeat"
    heartbeat.write_text(f'{{"pid": {os.getpid()}}}', encoding="utf-8")

    assert chat_activity._gateway_heartbeat_live(tmp_path, time.time()) is True
    old = time.time() - 120
    os.utime(heartbeat, (old, old))
    assert chat_activity._gateway_heartbeat_live(tmp_path, time.time()) is False


def test_gateway_heartbeat_pid_probe_never_signals_process(monkeypatch, tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    (state / "gateway.heartbeat").write_text('{"pid": 4242}', encoding="utf-8")
    observed = []

    monkeypatch.setattr(
        chat_activity.psutil,
        "pid_exists",
        lambda pid: observed.append(pid) or True,
    )
    monkeypatch.setattr(
        os,
        "kill",
        lambda *_args: pytest.fail("PID liveness probe must not signal a process"),
    )

    assert chat_activity._gateway_heartbeat_live(tmp_path, time.time()) is True
    assert observed == [4242]


def test_work_title_and_service_activity_survive_hidden_history(monkeypatch, tmp_path):
    _session(tmp_path, "service", source="agent_service", title="Подготовить договор", holder="service-run")
    _session(tmp_path, "worker", source="kanban", title="Work kanban task t_1", holder="kanban-run")
    db = SessionDB(tmp_path / "state.db")
    db.update_session_meta("worker", json.dumps({"_work_title": "Проверить договор"}))
    db.close()
    monkeypatch.setattr(chat_activity, "_profile_targets", lambda _: [("", tmp_path)])
    runs = chat_activity.project_chat_activity([], profile=None, session_id=None)
    assert {r["session_id"]: r["title"] for r in runs} == {"service": "Подготовить договор", "worker": "Проверить договор"}

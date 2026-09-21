import stat
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tools import effect_decisions as decisions
from tools import send_message_tool as outbound


def _create(db_path, **overrides):
    values = {
        "kind": "outbound_message",
        "owner_id": "owner-1",
        "profile": "sales",
        "source_session_id": "chat-1",
        "source_session_key": "api:chat-1",
        "payload": {
            "platform": "telegram",
            "account": "bot-main",
            "target": "2002",
            "target_label": "telegram:2002",
            "message": "Черновик ответа",
            "attachments": [
                {"name": "offer.pdf", "sha256": "a" * 64, "size": 42}
            ],
        },
        "path": db_path,
    }
    values.update(overrides)
    return decisions.create_pending(**values)


def test_pending_decision_survives_reopen_and_database_is_private(tmp_path):
    db_path = tmp_path / "effect-decisions.sqlite3"
    created, is_new = _create(db_path)

    assert is_new is True
    restored = decisions.get_decision(created["id"], path=db_path)
    assert restored is not None
    assert restored["status"] == decisions.PENDING
    assert restored["payload"]["message"] == "Черновик ответа"
    assert stat.S_IMODE(db_path.stat().st_mode) == 0o600


def test_corrupt_database_fails_closed_without_recreate_or_byte_changes(tmp_path):
    db_path = tmp_path / "effect-decisions.sqlite3"
    _create(db_path)

    damaged = bytearray(db_path.read_bytes())
    # Reproduce the live incident shape: a late TLS record overwrote bytes
    # 5..28 while leaving the rest of the SQLite file recoverable.
    damaged[5:29] = b"\x17\x03\x03\x00\x13" + (b"T" * 19)
    db_path.write_bytes(damaged)
    damaged_bytes = bytes(damaged)

    with pytest.raises(
        decisions.EffectDecisionStoreUnavailable,
        match="left unchanged and requires recovery",
    ):
        decisions.list_profile_decisions(profile="sales", path=db_path)

    assert db_path.read_bytes() == damaged_bytes
    assert not db_path.with_name(db_path.name + "-journal").exists()
    assert not db_path.with_name(db_path.name + "-wal").exists()


def test_preexisting_empty_database_is_not_silently_reinitialized(tmp_path):
    db_path = tmp_path / "effect-decisions.sqlite3"
    db_path.touch()

    with pytest.raises(decisions.EffectDecisionStoreUnavailable):
        _create(db_path)

    assert db_path.exists()
    assert db_path.stat().st_size == 0


def test_same_pending_payload_is_deduplicated_but_changed_attachment_is_new(tmp_path):
    db_path = tmp_path / "effect-decisions.sqlite3"
    first, first_is_new = _create(db_path)
    duplicate, duplicate_is_new = _create(db_path)
    changed_payload = dict(first["payload"])
    changed_payload["attachments"] = [
        {"name": "offer.pdf", "sha256": "b" * 64, "size": 42}
    ]
    changed, changed_is_new = _create(db_path, payload=changed_payload)

    assert first_is_new is True
    assert duplicate_is_new is False
    assert duplicate["id"] == first["id"]
    assert changed_is_new is True
    assert changed["id"] != first["id"]
    assert changed["payload_sha256"] != first["payload_sha256"]


def test_decision_is_bound_to_exact_session_and_has_no_broad_grants(tmp_path):
    db_path = tmp_path / "effect-decisions.sqlite3"
    pending, _ = _create(db_path)

    with pytest.raises(decisions.DecisionConflict, match="belong"):
        decisions.decide(
            pending["id"],
            source_session_id="chat-of-another-owner",
            choice="once",
            path=db_path,
        )
    with pytest.raises(decisions.DecisionConflict, match="belong"):
        decisions.decide(
            pending["id"],
            source_session_id="chat-1",
            profile="another-profile",
            choice="once",
            path=db_path,
        )
    with pytest.raises(decisions.DecisionConflict, match="only once or deny"):
        decisions.decide(
            pending["id"],
            source_session_id="chat-1",
            choice="always",
            path=db_path,
        )

    approved = decisions.decide(
        pending["id"], source_session_id="chat-1", choice="once", path=db_path
    )
    assert approved["status"] == decisions.APPROVED


def test_duplicate_approval_and_execution_are_idempotent_not_double_dispatch(tmp_path):
    db_path = tmp_path / "effect-decisions.sqlite3"
    pending, _ = _create(db_path)
    first = decisions.decide(
        pending["id"], source_session_id="chat-1", choice="once", path=db_path
    )
    duplicate = decisions.decide(
        pending["id"], source_session_id="chat-1", choice="once", path=db_path
    )
    assert duplicate["status"] == first["status"] == decisions.APPROVED

    claimed = decisions.claim_execution(
        pending["id"],
        expected_payload_sha256=pending["payload_sha256"],
        path=db_path,
    )
    assert claimed["status"] == decisions.EXECUTING
    with pytest.raises(decisions.DecisionConflict, match="not approved"):
        decisions.claim_execution(
            pending["id"],
            expected_payload_sha256=pending["payload_sha256"],
            path=db_path,
        )

    sent = decisions.finish_execution(
        pending["id"],
        status=decisions.SUCCEEDED,
        outcome={"message_id": "remote-7"},
        path=db_path,
    )
    replay = decisions.finish_execution(
        pending["id"],
        status=decisions.SUCCEEDED,
        outcome={"message_id": "ignored-duplicate"},
        path=db_path,
    )
    assert sent["outcome"] == replay["outcome"] == {"message_id": "remote-7"}


def test_crash_after_claim_becomes_unknown_and_is_never_replayed(tmp_path, monkeypatch):
    db_path = tmp_path / "effect-decisions.sqlite3"
    pending, _ = _create(db_path)
    decisions.decide(
        pending["id"], source_session_id="chat-1", choice="once", path=db_path
    )
    decisions.claim_execution(
        pending["id"],
        expected_payload_sha256=pending["payload_sha256"],
        path=db_path,
    )

    monkeypatch.setattr(decisions, "_PROCESS_INSTANCE", "restarted-process")
    recovered = decisions.get_decision(pending["id"], path=db_path)

    assert recovered is not None
    assert recovered["status"] == decisions.UNKNOWN
    assert recovered["outcome"] == {
        "reason": "executor_restarted",
        "retry": False,
    }
    with pytest.raises(decisions.DecisionConflict, match="unknown"):
        decisions.claim_execution(
            pending["id"],
            expected_payload_sha256=pending["payload_sha256"],
            path=db_path,
        )


def test_approval_projection_contains_full_draft_and_only_exact_choices(tmp_path):
    pending, _ = _create(tmp_path / "effect-decisions.sqlite3")
    event = decisions.approval_payload(pending)

    assert event["request_id"] == pending["id"]
    assert event["decision_kind"] == "outbound_message"
    assert event["effect_status"] == "pending"
    assert event["source_session_id"] == "chat-1"
    assert "telegram:2002" in event["command"]
    assert "Черновик ответа" in event["command"]
    assert "offer.pdf" in event["command"]
    assert event["choices"] == ["once", "deny"]
    assert event["allow_session"] is False
    assert event["allow_permanent"] is False


def test_payment_projection_is_exact_and_approval_fails_closed_without_executor(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    pending, _ = decisions.create_pending(
        kind="payment",
        owner_id="owner-1",
        profile="sales",
        source_session_id="chat-1",
        source_session_key="api:chat-1",
        payload={
            "recipient": "ООО Поставщик",
            "amount": "1250.40",
            "currency": "rub",
        },
    )

    event = decisions.approval_payload(pending)
    assert event["decision_kind"] == "payment"
    assert "Получатель: ООО Поставщик" in event["command"]
    assert "Сумма: 1250.40 RUB" in event["command"]
    assert event["choices"] == ["once", "deny"]

    with pytest.raises(
        decisions.EffectExecutorUnavailable, match="no payment was performed"
    ):
        decisions.resolve_effect_decision(
            pending["id"], "once", source_session_id="chat-1"
        )
    assert decisions.get_decision(pending["id"])["status"] == decisions.PENDING

    denied = decisions.resolve_effect_decision(
        pending["id"], "deny", source_session_id="chat-1"
    )
    assert denied["status"] == decisions.DENIED


@pytest.mark.parametrize(
    "payload",
    [
        {"recipient": "", "amount": "1", "currency": "USD"},
        {"recipient": "merchant", "amount": "0", "currency": "USD"},
        {"recipient": "merchant", "amount": "NaN", "currency": "USD"},
        {"recipient": "merchant", "amount": "1", "currency": "US"},
    ],
)
def test_payment_requires_exact_recipient_positive_amount_and_currency(tmp_path, payload):
    with pytest.raises(ValueError):
        decisions.create_pending(
            kind="payment",
            owner_id="owner-1",
            profile="sales",
            source_session_id="chat-1",
            source_session_key="api:chat-1",
            payload=payload,
            path=tmp_path / "effect-decisions.sqlite3",
        )


def test_profile_history_keeps_three_sequential_requests_and_filters_identity(tmp_path):
    db_path = tmp_path / "effect-decisions.sqlite3"
    first, _ = _create(db_path, payload={"target_label": "email:first", "message": "one"})
    second, _ = _create(db_path, payload={"target_label": "slack:second", "message": "two"})
    third, _ = _create(
        db_path,
        source_session_id="chat-2",
        source_session_key="api:chat-2",
        payload={"target_label": "telegram:third", "message": "three"},
    )
    decisions.decide(
        first["id"], source_session_id="chat-1", choice="deny", path=db_path
    )
    decisions.decide(
        second["id"], source_session_id="chat-1", choice="once", path=db_path
    )
    decisions.claim_execution(
        second["id"], expected_payload_sha256=second["payload_sha256"], path=db_path
    )
    decisions.finish_execution(
        second["id"], status=decisions.SUCCEEDED, path=db_path
    )

    history = decisions.list_profile_decisions(profile="sales", path=db_path)
    assert [item["id"] for item in history] == [third["id"], second["id"], first["id"]]
    assert [item["status"] for item in history] == [
        decisions.PENDING,
        decisions.SUCCEEDED,
        decisions.DENIED,
    ]
    assert decisions.list_profile_decisions(profile="another", path=db_path) == []


def _bind_session(**overrides):
    from gateway.session_context import set_session_vars

    values = {
        "platform": "api_server",
        "chat_id": "owner-browser",
        "thread_id": "",
        "user_id": "owner-1",
        "session_key": "api:chat-1",
        "session_id": "chat-1",
        "profile": "sales",
    }
    values.update(overrides)
    return set_session_vars(**values)


def test_current_chat_reply_bypasses_but_third_party_send_is_durable(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    notify = Mock(return_value=True)
    monkeypatch.setattr("tools.approval.notify_gateway_request", notify)
    pconfig = SimpleNamespace(token="bot-token", extra={"account_id": "main"})

    tokens = _bind_session(platform="telegram", chat_id="1001")
    try:
        same_chat = outbound._queue_outbound_decision(
            platform_name="telegram",
            pconfig=pconfig,
            chat_id="1001",
            thread_id=None,
            cleaned_message="Ответ владельцу",
            media_files=[],
            force_document=False,
            used_home_channel=False,
            args={"target": "telegram:1001", "message": "Ответ владельцу"},
        )
        external = outbound._queue_outbound_decision(
            platform_name="telegram",
            pconfig=pconfig,
            chat_id="2002",
            thread_id=None,
            cleaned_message="Черновик клиенту",
            media_files=[],
            force_document=False,
            used_home_channel=False,
            args={"target": "telegram:2002", "message": "Черновик клиенту"},
        )
    finally:
        from gateway.session_context import reset_session_vars

        reset_session_vars()

    assert same_chat is None
    assert external is not None and external["status"] == decisions.PENDING
    assert external["payload"]["chat_id"] == "2002"
    assert notify.call_count == 1
    assert notify.call_args.args[0] == "api:chat-1"
    assert notify.call_args.args[1]["choices"] == ["once", "deny"]


def test_cron_origin_delivery_is_external_even_when_chat_context_matches(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    pconfig = SimpleNamespace(token="bot-token", extra={"account_id": "main"})
    tokens = _bind_session(platform="telegram", chat_id="1001")
    try:
        decision = outbound._queue_outbound_decision(
            platform_name="telegram",
            pconfig=pconfig,
            chat_id="1001",
            thread_id=None,
            cleaned_message="Плановый отчёт",
            media_files=[],
            force_document=False,
            used_home_channel=False,
            args={"target": "telegram:1001", "message": "Плановый отчёт"},
            source_session_id="cron:daily:run-1",
            source_session_key="cron:daily:run-1",
            source_profile="sales",
            source_label="cron",
        )
    finally:
        from gateway.session_context import reset_session_vars

        reset_session_vars()

    assert decision is not None
    assert decision["status"] == decisions.PENDING
    assert decision["source_session_id"] == "cron:daily:run-1"


def test_approved_outbound_message_dispatches_once(tmp_path, monkeypatch):
    from gateway.config import Platform

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    pconfig = SimpleNamespace(enabled=True, token="bot-token", extra={"account_id": "main"})
    config = SimpleNamespace(platforms={Platform.TELEGRAM: pconfig})
    dispatch = Mock(return_value={"success": True, "message_id": "remote-1"})
    monkeypatch.setattr(outbound, "prepare_send_message_platforms", lambda: None)
    monkeypatch.setattr("gateway.config.load_gateway_config", lambda: config)
    monkeypatch.setattr(outbound, "_dispatch_resolved_send", dispatch)

    tokens = _bind_session()
    try:
        pending = outbound._queue_outbound_decision(
            platform_name="telegram",
            pconfig=pconfig,
            chat_id="2002",
            thread_id=None,
            cleaned_message="Точный текст",
            media_files=[],
            force_document=False,
            used_home_channel=False,
            args={"target": "telegram:2002", "message": "Точный текст"},
        )
    finally:
        from gateway.session_context import reset_session_vars

        reset_session_vars()

    first = outbound.resolve_outbound_message_decision(
        pending["id"], "once", source_session_id="chat-1"
    )
    duplicate = outbound.resolve_outbound_message_decision(
        pending["id"], "once", source_session_id="chat-1"
    )

    assert first["status"] == duplicate["status"] == decisions.SUCCEEDED
    assert dispatch.call_count == 1
    assert dispatch.call_args.kwargs["cleaned_message"] == "Точный текст"


def test_original_attachment_change_cannot_substitute_approved_snapshot(
    tmp_path, monkeypatch
):
    from gateway.config import Platform

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    attachment = tmp_path / "offer.pdf"
    attachment.write_bytes(b"approved bytes")
    pconfig = SimpleNamespace(enabled=True, token="bot-token", extra={"account_id": "main"})
    config = SimpleNamespace(platforms={Platform.TELEGRAM: pconfig})
    dispatch = Mock(return_value={"success": True})
    monkeypatch.setattr(outbound, "prepare_send_message_platforms", lambda: None)
    monkeypatch.setattr("gateway.config.load_gateway_config", lambda: config)
    monkeypatch.setattr(outbound, "_dispatch_resolved_send", dispatch)

    tokens = _bind_session()
    try:
        pending = outbound._queue_outbound_decision(
            platform_name="telegram",
            pconfig=pconfig,
            chat_id="2002",
            thread_id=None,
            cleaned_message="Файл во вложении",
            media_files=[(str(attachment), False)],
            force_document=True,
            used_home_channel=False,
            args={"target": "telegram:2002", "message": "MEDIA:offer.pdf"},
        )
    finally:
        from gateway.session_context import reset_session_vars

        reset_session_vars()

    attachment.write_bytes(b"changed after approval request")
    result = outbound.resolve_outbound_message_decision(
        pending["id"], "once", source_session_id="chat-1"
    )

    assert result["status"] == decisions.SUCCEEDED
    delivered_path = dispatch.call_args.kwargs["media_files"][0][0]
    assert delivered_path != str(attachment)
    assert Path(delivered_path).read_bytes() == b"approved bytes"
    assert stat.S_IMODE(Path(delivered_path).stat().st_mode) == 0o600
    assert pending["payload"]["tool_args"]["message"].endswith(
        f"MEDIA:{delivered_path}"
    )


def test_tampered_durable_attachment_fails_before_transport(tmp_path, monkeypatch):
    from gateway.config import Platform

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    attachment = tmp_path / "offer.pdf"
    attachment.write_bytes(b"approved bytes")
    pconfig = SimpleNamespace(enabled=True, token="bot-token", extra={"account_id": "main"})
    config = SimpleNamespace(platforms={Platform.TELEGRAM: pconfig})
    dispatch = Mock(return_value={"success": True})
    monkeypatch.setattr(outbound, "prepare_send_message_platforms", lambda: None)
    monkeypatch.setattr("gateway.config.load_gateway_config", lambda: config)
    monkeypatch.setattr(outbound, "_dispatch_resolved_send", dispatch)

    tokens = _bind_session()
    try:
        pending = outbound._queue_outbound_decision(
            platform_name="telegram",
            pconfig=pconfig,
            chat_id="2002",
            thread_id=None,
            cleaned_message="Файл во вложении",
            media_files=[(str(attachment), False)],
            force_document=True,
            used_home_channel=False,
            args={"target": "telegram:2002", "message": "MEDIA:offer.pdf"},
        )
    finally:
        from gateway.session_context import reset_session_vars

        reset_session_vars()

    Path(pending["payload"]["attachments"][0]["path"]).write_bytes(b"tampered")
    result = outbound.resolve_outbound_message_decision(
        pending["id"], "once", source_session_id="chat-1"
    )

    assert result["status"] == decisions.FAILED
    assert result["outcome"] == {"reason": "attachment_changed", "retry": False}
    dispatch.assert_not_called()

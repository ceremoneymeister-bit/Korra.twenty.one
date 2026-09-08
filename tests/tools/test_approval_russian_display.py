"""Russian approval cards must retain canonical permission and replay identity."""

from tools import approval
from tools.approval_display import approval_description_for_display


def test_russian_reason_keeps_detector_and_allowlist_keys(monkeypatch):
    monkeypatch.setenv("KORRA_LANGUAGE", "ru")
    dangerous, key, reason = approval.detect_dangerous_command("python -c 'print(1)'")
    assert dangerous
    assert key == reason == "script execution via -e/-c flag"
    assert approval_description_for_display(reason) == "выполнение скрипта через параметр -e/-c"
    approval.approve_session("ru-display-key", key)
    try:
        assert approval.is_approved("ru-display-key", key)
        assert not approval.is_approved("ru-display-key", approval_description_for_display(reason))
    finally:
        approval.clear_session("ru-display-key")


def test_gateway_notification_and_replay_are_russian_without_mutating_queue(monkeypatch):
    monkeypatch.setenv("KORRA_LANGUAGE", "ru")
    original = {
        "command": "python -c 'print(1)'",
        "description": "script execution via -e/-c flag",
        "pattern_key": "script execution via -e/-c flag",
        "pattern_keys": ["script execution via -e/-c flag"],
        "allow_session": False,
        "allow_permanent": False,
    }
    received = []

    def notify(data):
        received.append(data)
        replay = approval.get_pending_gateway_approval("ru-display-replay")
        listed = approval.list_gateway_approvals("ru-display-replay")
        assert replay == data == listed[0]
        assert data["description"] == "выполнение скрипта через параметр -e/-c"
        assert data["command"] == original["command"]
        assert data["pattern_key"] == original["pattern_key"]
        assert data["pattern_keys"] == original["pattern_keys"]
        assert data["allow_session"] is False
        assert data["allow_permanent"] is False
        assert approval._gateway_queues["ru-display-replay"][0].data["description"] == original["description"]
        assert approval.resolve_gateway_approval(
            "ru-display-replay", "once", request_id=data["request_id"]
        ) == 1

    decision = approval._await_gateway_decision("ru-display-replay", notify, original)
    assert decision["resolved"] is True
    assert decision["choice"] == "once"
    assert len(received) == 1
    assert original["description"] == "script execution via -e/-c flag"
    assert approval.get_pending_gateway_approval("ru-display-replay") is None


def test_cli_callback_receives_russian_reason_and_original_command(monkeypatch):
    monkeypatch.setenv("KORRA_LANGUAGE", "ru")
    captured = []

    def callback(command, reason, **kwargs):
        captured.append((command, reason, kwargs))
        return "deny"

    result = approval.prompt_dangerous_approval(
        "rm -r reports", "recursive delete", approval_callback=callback,
        allow_permanent=False, allow_session=False,
    )
    assert result == "deny"
    assert captured == [(
        "rm -r reports", "удаление папки со всем содержимым",
        {"allow_permanent": False, "allow_session": False},
    )]


def test_external_reason_and_explicit_english_are_preserved(monkeypatch):
    monkeypatch.setenv("KORRA_LANGUAGE", "ru")
    reason = "Vendor policy review: ABC-123"
    assert approval_description_for_display(reason) == reason
    assert approval_description_for_display("recursive delete; disk copy") == (
        "удаление папки со всем содержимым; копирование диска"
    )
    monkeypatch.setenv("KORRA_LANGUAGE", "en")
    assert approval_description_for_display("recursive delete") == "recursive delete"

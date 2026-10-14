"""K21-345: an unavailable explicit attachment must not disappear silently."""

import argparse
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from gateway.config import Platform, PlatformConfig
from gateway.platforms.base import BasePlatformAdapter, MessageEvent, MessageType, SendResult
from gateway.run import GatewayRunner
from gateway.session import SessionSource, build_session_key
from tools import send_message_tool as smt


@pytest.fixture
def sending(monkeypatch):
    config = SimpleNamespace(
        platforms={Platform.TELEGRAM: PlatformConfig(enabled=True, token="fake-token")},
        get_home_channel=lambda _platform: None,
    )
    monkeypatch.setattr("gateway.config.load_gateway_config", lambda: config)
    monkeypatch.setattr(smt, "prepare_send_message_platforms", lambda: None)
    dispatch = Mock(return_value={"success": True})
    decision = Mock(return_value=None)
    monkeypatch.setattr(smt, "_dispatch_resolved_send", dispatch)
    monkeypatch.setattr(smt, "_queue_outbound_decision", decision)
    return dispatch, decision


@pytest.mark.parametrize("kind", ["missing", "deleted", "wrong_profile", "relative", "unquoted_relative", "unknown_extension", "directory", "denied_symlink", "strict"])
@pytest.mark.parametrize("owner", [True, False])
def test_send_refuses_entire_payload_before_dispatch(tmp_path, monkeypatch, sending, kind, owner):
    home = tmp_path / "profile-a"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    valid = home / "cache/documents/Отчёт с пробелами.pdf"
    valid.parent.mkdir(parents=True)
    valid.write_bytes(b"pdf")
    invalid = home / "cache/documents/missing.pdf"
    if kind == "deleted":
        invalid.write_bytes(b"pdf")
        invalid.unlink()
    elif kind == "wrong_profile":
        invalid = tmp_path / "profile-b/cache/documents/missing.pdf"
    elif kind in ("relative", "unquoted_relative"):
        monkeypatch.chdir(home)
        invalid = "cache/documents/Отчёт с пробелами.pdf"
    elif kind == "unknown_extension":
        invalid = home / "cache/documents/missing.rbz"
    elif kind == "directory":
        invalid.mkdir()
    elif kind == "denied_symlink":
        secret = home / "auth.json"
        secret.write_text("fake credentials")
        invalid.symlink_to(secret)
    elif kind == "strict":
        invalid = tmp_path / "outside.pdf"
        invalid.write_bytes(b"pdf")
        monkeypatch.setenv("HERMES_MEDIA_DELIVERY_STRICT", "1")
        monkeypatch.setenv("HERMES_MEDIA_TRUST_RECENT_FILES", "0")
    invalid_tag = f"MEDIA:{invalid}" if kind in ("unquoted_relative", "unknown_extension") else f'MEDIA:"{invalid}"'
    result = json.loads(smt.send_message_tool(
        {"target": "telegram:12345", "message": f'Готово\nMEDIA:"{valid}"\n{invalid_tag}'},
        owner_initiated=owner,
    ))
    assert result.get("success") is False
    assert result["refused_before_send"] is True
    assert "absolute" in result["error"]
    assert str(invalid).split()[0] in result["error"]
    sending[0].assert_not_called()
    sending[1].assert_not_called()


def test_valid_spaced_file_and_protected_examples_still_send(tmp_path, sending):
    valid = tmp_path / "Отчёт с пробелами.pdf"
    valid.write_bytes(b"pdf")
    message = f'Готово\nMEDIA:"{valid}"\n```\nMEDIA:relative/example.pdf\n```'
    result = json.loads(smt.send_message_tool(
        {"target": "telegram:12345", "message": message}, owner_initiated=True,
    ))
    assert result["success"] is True
    payload = sending[0].call_args.kwargs
    assert payload["media_files"] == [(str(valid), False)]
    assert "MEDIA:relative/example.pdf" in payload["cleaned_message"]


def test_cli_uses_real_preflight_and_returns_failure(tmp_path, monkeypatch, sending, capsys):
    from korra_cli import send_cmd

    parser = argparse.ArgumentParser()
    send_cmd.register_send_subparser(parser.add_subparsers(dest="command"))
    args = parser.parse_args(["send", "--to", "telegram:12345", "--json", f"MEDIA:{tmp_path}/gone.pdf"])
    monkeypatch.setattr(send_cmd, "_load_hermes_env", lambda: None)
    with pytest.raises(SystemExit) as exc:
        send_cmd.cmd_send(args)
    assert exc.value.code != 0
    assert json.loads(capsys.readouterr().out)["refused_before_send"] is True
    sending[0].assert_not_called()


class RecordingAdapter(BasePlatformAdapter):
    def __init__(self):
        super().__init__(PlatformConfig(enabled=True, token="fake-token"), Platform.TELEGRAM)
        self.send = AsyncMock(return_value=SendResult(success=True, message_id="notice"))
        self.send_document = AsyncMock(return_value=SendResult(success=True, message_id="doc"))
        self.send_multiple_images = AsyncMock(return_value=SendResult(success=True))

    async def connect(self, *, is_reconnect=False):
        return True

    async def disconnect(self):
        pass

    async def send(self, chat_id, content, reply_to=None, metadata=None):
        return SendResult(success=True)

    async def get_chat_info(self, chat_id):
        return {"type": "dm"}


@pytest.mark.asyncio
@pytest.mark.parametrize("streamed", [False, True])
@pytest.mark.parametrize("with_valid", [False, True])
async def test_final_reply_reports_missing_media_and_keeps_valid_attachments(tmp_path, streamed, with_valid):
    adapter = RecordingAdapter()
    event = MessageEvent(
        text="Пришли файл", message_type=MessageType.TEXT, message_id="request",
        source=SessionSource(platform=Platform.TELEGRAM, chat_id="12345", chat_type="dm", thread_id="17"),
    )
    response = f"MEDIA:{tmp_path}/gone.pdf"
    valid = tmp_path / "Отчёт с пробелами.pdf"
    if with_valid:
        valid.write_bytes(b"pdf")
        response += f'\nMEDIA:"{valid}"'
    if streamed:
        runner = SimpleNamespace(
            _thread_metadata_for_source=lambda *args: {"thread_id": "17"},
            _reply_anchor_for_event=lambda _event: "request",
        )
        await GatewayRunner._deliver_media_from_response(runner, response, event, adapter)
    else:
        async def handler(_event):
            return response

        adapter.set_message_handler(handler)
        await adapter._process_message_background(event, build_session_key(event.source))
    notices = [call.kwargs.get("content", "") for call in adapter.send.await_args_list]
    assert any("Не удалось прикрепить" in text for text in notices)
    assert all("gone.pdf" not in text and "MEDIA:" not in text for text in notices)
    assert adapter.send_document.await_count == int(with_valid)
    if with_valid:
        assert adapter.send_document.await_args.kwargs["file_path"] == str(valid)


@pytest.mark.asyncio
@pytest.mark.parametrize("with_valid", [False, True])
async def test_background_reply_reports_unavailable_media(tmp_path, monkeypatch, with_valid):
    adapter = RecordingAdapter()
    source = SessionSource(platform=Platform.TELEGRAM, chat_id="12345", chat_type="dm", thread_id="17")
    response = f"MEDIA:{tmp_path}/gone.pdf"
    if with_valid:
        valid = tmp_path / "report.pdf"
        valid.write_bytes(b"pdf")
        response += f"\nMEDIA:{valid}"
    monkeypatch.setattr("gateway.run._load_gateway_config", lambda: {})
    runner = SimpleNamespace(
        _adapter_for_source=lambda _source: adapter,
        _thread_metadata_for_source=lambda *args: {"thread_id": "17"},
        _resolve_session_agent_runtime=lambda **kwargs: ("fake-model", {"api_key": "fake-key"}),
        _resolve_enabled_toolsets_for_source=lambda *args: [],
        _provider_routing={},
        _resolve_session_reasoning_config=lambda **kwargs: None,
        _resolve_session_service_tier=lambda **kwargs: None,
        _resolve_turn_agent_config=lambda *args: {},
        _run_in_executor_with_context=AsyncMock(return_value={"final_response": response, "messages": []}),
    )
    await GatewayRunner._run_background_task_inner(runner, "report", source, "fake-task")
    assert "Не удалось прикрепить" in adapter.send.await_args.kwargs["content"]
    assert adapter.send.await_args.kwargs["metadata"]["thread_id"] == "17"
    assert adapter.send_document.await_count == int(with_valid)

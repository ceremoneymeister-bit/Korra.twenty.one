"""Tests for gateway/wake.py — background wake delivery.

Two strategies:
* push-capable adapters keep the synthetic MessageEvent / handle_message path;
* the stateless API server (supports_async_delivery=False) self-POSTs
  /v1/chat/completions with the RAW session id in X-Hermes-Session-Id, so the
  wake turn resumes the REAL session instead of a parallel invisible one
  keyed by build_session_key().
"""

import asyncio

import pytest

from gateway.config import Platform
from gateway.session import SessionSource
from gateway.wake import deliver_wake, adapter_supports_push


class PushAdapter:
    """Default adapter shape — no supports_async_delivery attribute."""

    def __init__(self):
        self.handled = []

    async def handle_message(self, event):
        self.handled.append(event)


class ApiServerLikeAdapter:
    supports_async_delivery = False

    def __init__(self, host="0.0.0.0", port=0, key="test-key", model="hermes"):
        self._host = host
        self._port = port
        self._api_key = key
        self._model_name = model

    async def handle_message(self, event):  # pragma: no cover — must NOT be hit
        raise AssertionError("non-push adapter must not receive handle_message wakes")


def _source():
    return SessionSource(
        platform=Platform.TELEGRAM,
        chat_id="chat-1",
        chat_type="group",
    )


def test_adapter_supports_push_default_true():
    assert adapter_supports_push(PushAdapter()) is True
    assert adapter_supports_push(ApiServerLikeAdapter()) is False


def test_named_profile_wake_self_posts_to_profile_route(tmp_path, monkeypatch):
    """The shared API listener must resume the profile, even for a raw id."""
    web = pytest.importorskip("aiohttp.web")
    from gateway.run import _profile_runtime_scope

    root = tmp_path / "hermes"
    profile = root / "profiles" / "worker"
    profile.mkdir(parents=True)
    (profile / ".env").write_text("API_SERVER_KEY=test-worker-key-1234567890\n")
    monkeypatch.setenv("HERMES_HOME", str(root))
    seen = []

    async def handler(request):
        seen.append((request.path, request.headers.get("X-Hermes-Session-Id")))
        return web.json_response({"choices": []})

    async def run():
        app = web.Application()
        app.router.add_post("/p/worker/v1/chat/completions", handler)
        http = web.AppRunner(app)
        await http.setup()
        site = web.TCPSite(http, "127.0.0.1", 0)
        await site.start()
        try:
            adapter = ApiServerLikeAdapter(port=site._server.sockets[0].getsockname()[1])
            with _profile_runtime_scope(profile):
                await deliver_wake(adapter, text="test result", session_id="parent")
        finally:
            await http.cleanup()

    asyncio.run(run())
    assert seen == [("/p/worker/v1/chat/completions", "parent")]


async def _serve(handler):
    """Spin an in-process aiohttp server on an ephemeral loopback port."""
    from aiohttp import web

    app = web.Application()
    app.router.add_post("/v1/chat/completions", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    return runner, port


def test_deliver_wake_non_push_self_posts_raw_session_id(monkeypatch):
    """The self-post carries the RAW session id header + bearer auth and a
    single user message with stream=false — the exact entry point real
    gateway turns use."""
    from aiohttp import web

    seen = {}

    async def handler(request):
        seen["session_id"] = request.headers.get("X-Hermes-Session-Id")
        seen["auth"] = request.headers.get("Authorization")
        seen["body"] = await request.json()
        return web.json_response({"choices": [{"message": {"content": "ok"}}]})

    async def run():
        runner, port = await _serve(handler)
        try:
            adapter = ApiServerLikeAdapter(host="0.0.0.0", port=port, key="sekrit")
            await deliver_wake(adapter, text="task done — wake", session_id="raw-sid-42")
        finally:
            await runner.cleanup()

    asyncio.run(run())
    assert seen["session_id"] == "raw-sid-42"
    assert seen["auth"] == "Bearer sekrit"
    assert seen["body"]["stream"] is False
    assert seen["body"]["messages"] == [
        {"role": "user", "content": "task done — wake"}
    ]


def test_deliver_wake_retries_429_then_succeeds(monkeypatch):
    """HTTP 429 (max_concurrent_runs cap) is transient — retried with backoff."""
    from aiohttp import web

    import gateway.wake as wake_mod

    monkeypatch.setattr(wake_mod, "_RETRY_DELAYS_SECONDS", (0.01, 0.01, 0.01))
    calls = {"n": 0}

    async def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            return web.json_response({"error": "busy"}, status=429)
        return web.json_response({"choices": []})

    async def run():
        runner, port = await _serve(handler)
        try:
            adapter = ApiServerLikeAdapter(port=port)
            await deliver_wake(adapter, text="x", session_id="sid")
        finally:
            await runner.cleanup()

    asyncio.run(run())
    assert calls["n"] == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("root_key", ["test-root-key-1234567890", ""])
@pytest.mark.parametrize("profile_name", ["worker", "other"])
async def test_profile_wake_authenticates_with_url_owner(tmp_path, monkeypatch, root_key, profile_name):
    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer
    from types import SimpleNamespace

    from gateway.config import GatewayConfig, PlatformConfig
    from gateway.platforms.api_server import APIServerAdapter
    from gateway.run import _profile_runtime_scope

    root = tmp_path / "hermes"
    for name in ("worker", "other"):
        home = root / "profiles" / name
        home.mkdir(parents=True)
        (home / ".env").write_text(f"API_SERVER_KEY=test-{name}-key-1234567890\n")
    monkeypatch.setenv("HERMES_HOME", str(root))
    monkeypatch.setenv("API_SERVER_KEY", "test-ambient-key-1234567890")
    adapter = APIServerAdapter(PlatformConfig(enabled=True, extra={"key": root_key}))
    adapter.gateway_runner = SimpleNamespace(config=GatewayConfig(multiplex_profiles=True))
    accepted = []

    async def authenticated(request):
        refusal = adapter._check_auth(request)
        if refusal is not None:
            return refusal
        accepted.append((request.path, request.headers["X-Hermes-Session-Id"]))
        return web.json_response({"choices": []})

    app = web.Application(middlewares=[adapter._make_profile_prefix_middleware()])
    app.router.add_post("/p/{profile}/v1/chat/completions", authenticated)
    async with TestClient(TestServer(app)) as client:
        adapter._host = "127.0.0.1"
        adapter._port = client.server.port
        with _profile_runtime_scope(root / "profiles" / profile_name):
            await deliver_wake(adapter, text="completed", session_id="parent")
    assert accepted == [(f"/p/{profile_name}/v1/chat/completions", "parent")]


@pytest.mark.asyncio
@pytest.mark.parametrize("key", ["", "weak", "your_api_key_here"])
async def test_profile_wake_never_borrows_root_key(tmp_path, monkeypatch, key):
    from gateway.run import _profile_runtime_scope

    root = tmp_path / "hermes"
    home = root / "profiles" / "worker"
    home.mkdir(parents=True)
    (home / ".env").write_text(f"API_SERVER_KEY={key}\n")
    monkeypatch.setenv("HERMES_HOME", str(root))
    monkeypatch.setenv("API_SERVER_KEY", "test-ambient-key-1234567890")
    with _profile_runtime_scope(home), pytest.raises(RuntimeError, match="profile-scoped API_SERVER_KEY"):
        await deliver_wake(ApiServerLikeAdapter(key="test-root-key-1234567890"),
                           text="completed", session_id="parent")



@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("internal", [False, True])
async def test_web_wake_preserves_internal_marker_to_agent(tmp_path, monkeypatch, stream, internal):
    """A19R2-02: real HTTP self-post/parser/agent wrapper; no model network."""
    from unittest.mock import MagicMock, patch

    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer
    from gateway.config import PlatformConfig
    from gateway.platforms.api_server import APIServerAdapter
    from korra_state import SessionDB

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    text = "[ASYNC DELEGATION BATCH COMPLETE — test] api_calls=1 /opt/data/cache/delegation/test.log"
    with SessionDB(tmp_path / "state.db") as db:
        db.create_session("parent", source="dashboard")
        db.append_message("parent", "user", "Проверь результат")
        db.append_message("parent", "assistant", "Проверяю")
        adapter = APIServerAdapter(PlatformConfig(enabled=True, extra={"key": "test-api-key"}))
        adapter._session_db = db
        agent = MagicMock()
        agent.run_conversation.return_value = {"final_response": "Помощник закончил поручение"}
        agent.session_prompt_tokens = agent.session_completion_tokens = agent.session_total_tokens = 0
        app = web.Application()
        app.router.add_post("/v1/chat/completions", adapter._handle_chat_completions)
        with patch.object(adapter, "_create_agent", return_value=agent):
            async with TestClient(TestServer(app)) as client:
                adapter._host, adapter._port = "127.0.0.1", client.server.port
                if internal and not stream:
                    await deliver_wake(adapter, text=text, session_id="parent")
                else:
                    headers = {"Authorization": "Bearer test-api-key", "X-Hermes-Session-Id": "parent"}
                    if internal:
                        headers["X-Hermes-Internal-Notification"] = "1"
                    response = await client.post("/v1/chat/completions", headers=headers,
                        json={"messages": [{"role": "user", "content": text}], "stream": stream})
                    assert response.status == 200, await response.text()
                    await response.read()
        kwargs = agent.run_conversation.call_args.kwargs
        assert kwargs.get("persist_user_display_kind") == ("internal_notification" if internal else None)
        assert kwargs["user_message"] == text
        assert [(row["role"], row["content"]) for row in kwargs["conversation_history"]] == [
            ("user", "Проверь результат"), ("assistant", "Проверяю"),
        ]

@pytest.mark.asyncio
async def test_telegram_wake_is_internal_and_does_not_send_scaffolding():
    adapter = PushAdapter()
    text = "[ASYNC DELEGATION BATCH COMPLETE] Model: ? api_calls=1"
    await deliver_wake(adapter, source=_source(), text=text)
    event, = adapter.handled
    assert event.internal is True
    assert event.source.platform is Platform.TELEGRAM
    assert event.text == text


@pytest.mark.asyncio
async def test_web_wake_real_agent_persists_marker_before_model_call(tmp_path, monkeypatch):
    """Real HTTP -> agent -> SQLite -> history, replacing only the model call."""
    from unittest.mock import patch

    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer
    from openai.types.chat import ChatCompletion
    from gateway.config import PlatformConfig
    from gateway.platforms.api_server import APIServerAdapter
    from korra_state import SessionDB
    from run_agent import AIAgent

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    text = "[ASYNC DELEGATION BATCH COMPLETE — test] api_calls=1 /opt/data/cache/delegation/test.log"
    with SessionDB(tmp_path / "state.db") as db:
        db.create_session("parent", source="dashboard")
        db.append_message("parent", "user", "Поручи проверку")
        db.append_message("parent", "assistant", "Поручение запущено")
        agent = AIAgent(api_key="test-key", base_url="http://127.0.0.1:1/v1", model="gpt-4o-mini",
                        provider="openai", quiet_mode=True, skip_context_files=True, skip_memory=True,
                        skip_background_review=True, session_db=db, session_id="parent", enabled_toolsets=[])
        agent._session_db_created = True
        agent._cached_system_prompt = "SYSTEM"
        agent._skip_mcp_refresh = True
        agent._disable_streaming = True
        seen = []

        def model_call(kwargs):
            # The synthetic input is typed even if the process crashes here.
            row = db.get_messages_as_conversation("parent")[-1]
            assert row["role"] == "user" and row["content"] == text
            assert row.get("display_kind") == "internal_notification"
            seen.append(kwargs["messages"])
            return ChatCompletion(id="test", object="chat.completion", created=1, model="gpt-4o-mini",
                choices=[{"index": 0, "message": {"role": "assistant", "content": "Проверка завершена"}, "finish_reason": "stop"}],
                usage={"prompt_tokens": 10, "completion_tokens": 3, "total_tokens": 13})

        monkeypatch.setattr(agent, "_interruptible_api_call", model_call)
        adapter = APIServerAdapter(PlatformConfig(enabled=True, extra={"key": "test-api-key"}))
        adapter._session_db = db
        app = web.Application()
        app.router.add_post("/v1/chat/completions", adapter._handle_chat_completions)
        app.router.add_get("/api/sessions/{session_id}/messages", adapter._handle_session_messages)
        with patch.object(adapter, "_create_agent", return_value=agent):
            async with TestClient(TestServer(app)) as client:
                adapter._host, adapter._port = "127.0.0.1", client.server.port
                await deliver_wake(adapter, text=text, session_id="parent")
                response = await client.get("/api/sessions/parent/messages", headers={"Authorization": "Bearer test-api-key"})
                assert response.status == 200
                rows = (await response.json())["data"]
        assert seen, "wake never reached the model"
        assert seen[0][-1]["role"] == "user" and seen[0][-1]["content"] == text
        assert all("display_kind" not in row for row in seen[0])
        assert rows[-2]["display_kind"] == "internal_notification"
        assert rows[-1]["content"] == "Проверка завершена"
        assert [row["role"] for row in rows] == ["user", "assistant", "user", "assistant"]

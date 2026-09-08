"""Request-only capabilities on durable /v1/runs admission and execution."""

import asyncio
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import MagicMock, patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gateway.config import PlatformConfig
from gateway.platforms.api_server import APIServerAdapter, RunIdempotencyStore
from gateway.session_context import get_trusted_tool_scope


CAPABILITY = "mcs1.order_a.signature"
AUTH = {"Authorization": "Bearer test-gateway-key"}


@pytest.fixture
def adapter(tmp_path):
    instance = APIServerAdapter(
        PlatformConfig(enabled=True, extra={"key": "test-gateway-key"})
    )
    instance._run_idempotency_store.close()
    instance._run_idempotency_store = RunIdempotencyStore(str(tmp_path / "runs.db"))
    yield instance
    instance._run_idempotency_store.close()


def app_for(adapter):
    app = web.Application()
    app.router.add_post("/v1/runs", adapter._handle_runs)
    app.router.add_get("/v1/runs/{run_id}", adapter._handle_get_run)
    app.router.add_get("/v1/runs/{run_id}/events", adapter._handle_run_events)
    return app


def make_agent(run):
    agent = MagicMock()
    agent.run_conversation.side_effect = run
    agent.session_prompt_tokens = 0
    agent.session_completion_tokens = 0
    agent.session_total_tokens = 0
    return agent


async def finish_run(client, run_id):
    response = await client.get(f"/v1/runs/{run_id}/events", headers=AUTH)
    assert response.status == 200
    return await asyncio.wait_for(response.text(), timeout=10)


@pytest.mark.asyncio
async def test_scope_reaches_executor_without_entering_payload_or_store(adapter, tmp_path):
    observed = []

    def run(**kwargs):
        observed.append((get_trusted_tool_scope(), kwargs))
        return {"final_response": "done"}

    agent = make_agent(run)
    async with TestClient(TestServer(app_for(adapter))) as client:
        with patch.object(adapter, "_create_agent", return_value=agent) as create:
            response = await client.post(
                "/v1/runs",
                json={"input": "inspect sources", "session_id": "order-session"},
                headers={**AUTH, "Idempotency-Key": "attempt-1", "X-Hermes-Tool-Scope": CAPABILITY},
            )
            assert response.status == 202
            accepted = await response.json()
            events = await finish_run(client, accepted["run_id"])
            status_response = await client.get(f"/v1/runs/{accepted['run_id']}", headers=AUTH)
            status = await status_response.json()

    assert status["status"] == "completed"
    assert [scope for scope, _ in observed] == [CAPABILITY]
    assert get_trusted_tool_scope() == ""
    surfaces = json.dumps([accepted, status, events, dict(response.headers)])
    surfaces += str(create.call_args) + str(observed[0][1])
    surfaces += str(adapter._response_store) + str(adapter._run_statuses)
    with sqlite3.connect(tmp_path / "runs.db") as connection:
        surfaces += str(connection.execute("SELECT * FROM run_idempotency").fetchall())
    for secret in (CAPABILITY, "test-gateway-key"):
        assert secret not in surfaces
        for path in tmp_path.glob("runs.db*"):
            assert secret.encode() not in path.read_bytes()


@pytest.mark.asyncio
async def test_native_profile_discovers_mcp_off_loop_before_agent_snapshot(adapter, tmp_path, monkeypatch):
    from contextlib import contextmanager
    import threading
    from korra_constants import get_hermes_home, set_hermes_home_override, reset_hermes_home_override
    from agent.secret_scope import set_secret_scope, reset_secret_scope

    profile_home = tmp_path / "analysis"
    profile_home.mkdir()
    observed = []
    loop_thread = threading.get_ident()
    profile_key = "profile-api-test-key-1234567890"
    profile_auth = {"Authorization": f"Bearer {profile_key}"}

    @contextmanager
    def profile_scope(profile):
        assert profile == "analysis"
        token = set_hermes_home_override(str(profile_home))
        secret_token = set_secret_scope({"API_SERVER_KEY": profile_key})
        try:
            yield
        finally:
            reset_secret_scope(secret_token)
            reset_hermes_home_override(token)

    def discover():
        observed.append(("discover", str(get_hermes_home()), threading.get_ident()))

    def create(**kwargs):
        observed.append(("create", str(get_hermes_home()), threading.get_ident()))
        return make_agent(lambda **kw: {"final_response": "done"})

    monkeypatch.setattr(adapter, "_resolve_request_profile", lambda request: request.match_info.get("profile"))
    monkeypatch.setattr(adapter, "_profile_scope", profile_scope)
    monkeypatch.setattr(adapter, "_create_agent", create)
    monkeypatch.setattr("tools.mcp_tool.ensure_native_profile_mcp_tools", discover)
    app = web.Application(middlewares=[adapter._make_profile_prefix_middleware()])
    app.router.add_post("/p/{profile}/v1/runs", adapter._handle_runs)
    app.router.add_get("/p/{profile}/v1/runs/{run_id}/events", adapter._handle_run_events)
    async with TestClient(TestServer(app)) as client:
        response = await client.post("/p/analysis/v1/runs", json={"input": "check source"}, headers=profile_auth)
        assert response.status == 202
        result = await response.json()
        events = await client.get(f"/p/analysis/v1/runs/{result['run_id']}/events", headers=profile_auth)
        assert "run.completed" in await asyncio.wait_for(events.text(), 10)
    assert [item[:2] for item in observed] == [("discover", str(profile_home)), ("create", str(profile_home))]
    assert observed[0][2] != loop_thread


@pytest.mark.asyncio
@pytest.mark.parametrize("scope", ["bad scope", "x" * 257, "mcs1/payload"])
async def test_invalid_scope_is_rejected_before_run_creation(adapter, scope):
    async with TestClient(TestServer(app_for(adapter))) as client:
        with patch.object(adapter, "_create_agent") as create:
            response = await client.post(
                "/v1/runs", json={"input": "go"},
                headers={**AUTH, "Idempotency-Key": "attempt", "X-Hermes-Tool-Scope": scope},
            )
    assert response.status == 400
    create.assert_not_called()
    assert adapter._run_statuses == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("configured_key,authorization,status", [
    ("", "", 403),
    ("test-gateway-key", "Bearer wrong-key", 401),
])
async def test_scope_requires_authenticated_channel(adapter, configured_key, authorization, status):
    with patch.object(adapter, "_expected_api_key", return_value=configured_key):
        async with TestClient(TestServer(app_for(adapter))) as client:
            with patch.object(adapter, "_create_agent") as create:
                response = await client.post(
                    "/v1/runs", json={"input": "go"},
                    headers={"Authorization": authorization, "Idempotency-Key": "attempt", "X-Hermes-Tool-Scope": CAPABILITY},
                )
    assert response.status == status
    create.assert_not_called()
    assert adapter._run_statuses == {}


@pytest.mark.asyncio
async def test_scoped_run_requires_dispatch_idempotency_key(adapter):
    async with TestClient(TestServer(app_for(adapter))) as client:
        with patch.object(adapter, "_create_agent") as create:
            response = await client.post(
                "/v1/runs", json={"input": "go"},
                headers={**AUTH, "X-Hermes-Tool-Scope": CAPABILITY},
            )
            result = await response.json()
    assert response.status == 400
    assert result["error"]["code"] == "idempotency_key_required"
    create.assert_not_called()
    assert adapter._run_statuses == {}


@pytest.mark.asyncio
async def test_real_store_fallback_fails_closed_for_scoped_runs(adapter, tmp_path):
    adapter._run_idempotency_store.close()
    # Parent directory deliberately absent: exercise the real SQLite fallback.
    adapter._run_idempotency_store = RunIdempotencyStore(str(tmp_path / "missing" / "runs.db"))
    assert adapter._run_idempotency_store.durable is False
    async with TestClient(TestServer(app_for(adapter))) as client:
        with patch.object(adapter, "_create_agent") as create:
            response = await client.post(
                "/v1/runs", json={"input": "go"},
                headers={**AUTH, "Idempotency-Key": "attempt", "X-Hermes-Tool-Scope": CAPABILITY},
            )
            result = await response.json()
    assert response.status == 503
    assert result["error"]["code"] == "run_idempotency_unavailable"
    create.assert_not_called()
    assert adapter._run_statuses == {}
    rows = adapter._run_idempotency_store._conn.execute("SELECT COUNT(*) FROM run_idempotency").fetchone()
    assert rows == (0,)


@pytest.mark.asyncio
async def test_scope_identity_survives_restart_and_conflicts_on_change(adapter, tmp_path):
    headers = {**AUTH, "Idempotency-Key": "attempt", "X-Hermes-Tool-Scope": CAPABILITY}
    body = {"input": "go", "session_id": "same-session"}
    agent = make_agent(lambda **kwargs: {"final_response": "done"})
    async with TestClient(TestServer(app_for(adapter))) as client:
        with patch.object(adapter, "_create_agent", return_value=agent):
            first = await client.post("/v1/runs", json=body, headers=headers)
            assert first.status == 202
            run_id = (await first.json())["run_id"]
            await finish_run(client, run_id)
    agent.run_conversation.assert_called_once()

    restarted = APIServerAdapter(PlatformConfig(enabled=True, extra={"key": "test-gateway-key"}))
    restarted._run_idempotency_store.close()
    restarted._run_idempotency_store = RunIdempotencyStore(str(tmp_path / "runs.db"))
    try:
        async with TestClient(TestServer(app_for(restarted))) as client:
            with patch.object(restarted, "_create_agent") as create:
                replay = await client.post("/v1/runs", json=body, headers=headers)
                assert replay.status == 202
                assert (await replay.json())["run_id"] == run_id
                assert replay.headers["Idempotency-Replayed"] == "true"
                for scope in ("mcs1.order_b.signature", ""):
                    conflict = await client.post(
                        "/v1/runs", json=body,
                        headers={**headers, "X-Hermes-Tool-Scope": scope},
                    )
                    assert conflict.status == 409
                    assert (await conflict.json())["error"]["code"] == "idempotency_key_conflict"
                create.assert_not_called()
    finally:
        restarted._run_idempotency_store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("failed", [False, True])
async def test_worker_scope_is_cleared_after_success_or_exception(adapter, failed):
    observed = []

    def run(**kwargs):
        observed.append(get_trusted_tool_scope())
        if failed and len(observed) == 1:
            raise RuntimeError("extractor stopped")
        return {"final_response": "done"}

    agent = make_agent(run)
    loop = asyncio.get_running_loop()
    original_executor = loop.run_in_executor
    with ThreadPoolExecutor(max_workers=1) as executor:
        # Reuse one actual executor thread to detect leaked ContextVars.
        def submit(_executor, func, *args):
            return original_executor(executor, func, *args)

        async with TestClient(TestServer(app_for(adapter))) as client:
            with patch.object(loop, "run_in_executor", side_effect=submit):
                with patch.object(adapter, "_create_agent", return_value=agent):
                    for index, scope in enumerate((CAPABILITY, "")):
                        response = await client.post(
                            "/v1/runs",
                            json={"input": "go", "session_id": "shared-session"},
                            headers={**AUTH, "Idempotency-Key": f"attempt-{index}", "X-Hermes-Tool-Scope": scope},
                        )
                        assert response.status == 202
                        events = await finish_run(client, (await response.json())["run_id"])
                        expected = "run.failed" if failed and index == 0 else "run.completed"
                        assert expected in events
                        assert await original_executor(executor, get_trusted_tool_scope) == ""
    assert observed == [CAPABILITY, ""]


@pytest.mark.asyncio
async def test_body_cannot_supply_trusted_tool_scope(adapter):
    observed = []

    def run(**kwargs):
        observed.append(get_trusted_tool_scope())
        return {"final_response": "done"}

    async with TestClient(TestServer(app_for(adapter))) as client:
        with patch.object(adapter, "_create_agent", return_value=make_agent(run)):
            response = await client.post(
                "/v1/runs",
                json={"input": "go", "trusted_tool_scope": CAPABILITY, "_tool_scope_token": CAPABILITY},
                headers=AUTH,
            )
            assert response.status == 202
            await finish_run(client, (await response.json())["run_id"])
    assert observed == [""]


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["exception", "structured_error", "provider_error", "success"])
async def test_echoed_capability_is_scrubbed_from_all_run_egress(adapter, tmp_path, caplog, outcome):
    from gateway.platforms.api_server import _ProviderAuthResolutionError
    from tools import approval

    def create_agent(**callbacks):
        if outcome == "provider_error":
            raise _ProviderAuthResolutionError("provider rejected " + CAPABILITY)

        def run(**kwargs):
            token = get_trusted_tool_scope()
            # Include full echoes, character-split echoes and interleaved events.
            callbacks["stream_delta_callback"]("before " + token + " ")
            for char in token:
                callbacks["stream_delta_callback"](char)
                callbacks["tool_progress_callback"]("tool.started", tool_name="document", preview=token)
            callbacks["stream_delta_callback"](" after")
            callbacks["tool_progress_callback"]("reasoning.available", preview=token)
            notify = approval._gateway_notify_cbs[approval.get_current_session_key()]
            notify({"command": "inspect " + token, "description": {"nested": [token]}})
            if outcome == "exception":
                raise RuntimeError("backend rejected " + token)
            if outcome == "structured_error":
                return {"failed": True, "error": "backend rejected " + token}
            return {"final_response": "output " + token, "pending_steer": [{"text": token}]}

        return make_agent(run)

    async with TestClient(TestServer(app_for(adapter))) as client:
        with patch.object(adapter, "_create_agent", side_effect=create_agent):
            response = await client.post(
                "/v1/runs", json={"input": "inspect"},
                headers={**AUTH, "Idempotency-Key": "echo-attempt", "X-Hermes-Tool-Scope": CAPABILITY},
            )
            assert response.status == 202
            run_id = (await response.json())["run_id"]
            events = await finish_run(client, run_id)
            status = await (await client.get(f"/v1/runs/{run_id}", headers=AUTH)).json()
    assert status["status"] == ("completed" if outcome == "success" else "failed")
    if outcome != "provider_error":
        decoded = [json.loads(line[6:]) for line in events.splitlines() if line.startswith("data: ")]
        deltas = "".join(item["delta"] for item in decoded if item.get("event") == "message.delta")
        assert deltas == "before [REDACTED] [REDACTED] after"
        assert any(item.get("event") == "approval.request" for item in decoded)
    with sqlite3.connect(tmp_path / "runs.db") as connection:
        rows = connection.execute("SELECT * FROM run_idempotency").fetchall()
    assert CAPABILITY not in json.dumps([events, status, rows], default=str)
    assert CAPABILITY not in caplog.text
    for record in caplog.records:
        if record.name == "gateway.platforms.api_server":
            assert CAPABILITY not in record.getMessage()
            assert record.exc_info is None
    for path in tmp_path.glob("runs.db*"):
        assert CAPABILITY.encode() not in path.read_bytes()
    # Private exact-value filters use the existing bounded terminal-state TTL.
    adapter._sweep_orphaned_runs_once(status["updated_at"] + adapter._RUN_STATUS_TTL + 1)
    assert run_id not in adapter._run_scope_redactors


def test_existing_status_snapshot_never_receives_unredacted_update(adapter):
    from gateway.platforms.api_server_runs import _RunScopeRedactor

    run_id = "run-status-snapshot"
    adapter._run_scope_redactors[run_id] = _RunScopeRedactor(CAPABILITY)
    # An HTTP reader may already hold this dict while a worker callback updates
    # the status. Its old snapshot must stay safe throughout the next publish.
    published = adapter._set_run_status(run_id, "running")
    updated = adapter._set_run_status(
        run_id, "failed", error="backend rejected " + CAPABILITY,
    )
    assert CAPABILITY not in json.dumps(published)
    assert updated["error"] == "backend rejected [REDACTED]"

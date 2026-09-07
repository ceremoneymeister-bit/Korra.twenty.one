"""Юниты чистой логики раннера: отбор заданий и идемпотентность курсора."""
from __future__ import annotations

import hashlib
import io
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "metal_calc"))

import runner


@pytest.fixture(autouse=True)
def isolated_runner_state(tmp_path, monkeypatch):
    """Unit tests never touch the shared /opt/data handoff state."""
    monkeypatch.setattr(runner, "STATE_DIR", tmp_path / "handoff")
    monkeypatch.setenv("METAL_CALC_SCOPE_SECRET", "22" * 32)


def scoped_job(job: dict) -> dict:
    expires_at = 4_102_444_800
    return {
        **job,
        "delivery_session_id": runner.handoff_session_id(job, expires_at),
        "delivery_tool_scope": runner.scoped_order_capability(job, expires_at),
    }


def state(order_id: str, events: list[tuple[str, str, str]]) -> dict:
    return {
        "order_id": order_id,
        "pipeline_events": [
            {"stage": stage, "event": event, "at": at} for stage, event, at in events
        ],
    }


def test_approved_stages_become_jobs() -> None:
    states = [
        state("ord-1", [("blank", "proposed", "t1"), ("blank", "approved", "t2")]),
        state("ord-2", [("route", "approved", "t3")]),
    ]
    jobs = runner.pending_handoffs(states, {})
    assert [(j["order_id"], j["stage"]) for j in jobs] == [
        ("ord-1", "blank"),
        ("ord-2", "route"),
    ]


def test_cursor_makes_it_idempotent() -> None:
    states = [state("ord-1", [("blank", "approved", "t2")])]
    jobs = runner.pending_handoffs(states, {})
    cursor = {jobs[0]["cursor_key"]: "done"}
    assert runner.pending_handoffs(states, cursor) == []


def test_unroutable_events_ignored() -> None:
    states = [
        state(
            "ord-1",
            [
                ("quote", "approved", "t1"),      # завершающая стадия — некому передавать
                ("blank", "supply_confirmed", "t2"),
                ("route", "proposed", "t3"),
            ],
        )
    ]
    assert runner.pending_handoffs(states, {}) == []


def test_reapproval_is_a_new_job() -> None:
    states = [state("ord-1", [("blank", "approved", "t1"), ("blank", "approved", "t9")])]
    cursor = {"ord-1:blank:t1:0": "done"}
    jobs = runner.pending_handoffs(states, cursor)
    assert [j["cursor_key"] for j in jobs] == ["ord-1:blank:t9:1"]


# ── V3 (схема разделения V2) ─────────────────────────────────────────────


def wf_state(order_id: str, events: list[tuple[str, str]]) -> dict:
    return {
        "order_id": order_id,
        "workflow_events": [{"event": event, "at": at} for event, at in events],
    }


def time_costed_state(order_id: str = "ord-9", at: str = "t6") -> dict:
    return {
        "order_id": order_id,
        "workflow": {"status": "COSTING_COMPLETE", "calculation_revision": 1},
        "workflow_events": [{"event": "time_costed", "at": at}],
    }


def complete_time_costed_with_pass(state_value: dict) -> None:
    calculation_revision = state_value["workflow"]["calculation_revision"]
    state_value["workflow"]["status"] = "QA_MECHANICAL_PASS"
    state_value["book"] = {"calculation_revision": calculation_revision, "digest": "book"}
    state_value["qa"] = {
        str(calculation_revision): {
            "mechanical": {
                "verdict": "PASS",
                "computed_by": "engine",
                "checks": [{"code": "sum", "ok": True}],
            }
        }
    }
    state_value["workflow_events"].extend(
        [
            {"event": "book_assembled", "at": "t7"},
            {"event": "qa_mechanical:PASS", "at": "t8"},
        ]
    )


def test_v3_events_become_jobs_with_profiles() -> None:
    states = [
        wf_state(
            "ord-9",
            [
                ("input_frozen", "t1"),
                ("bom_validated", "t2"),
                ("route_variant_proposed:variant-1", "t3"),
                ("route_frozen:variant-1", "t4"),
                ("blank_costed", "t5"),
                ("time_costed", "t6"),
                ("book_assembled", "t7"),
            ],
        )
    ]
    jobs = runner.pending_handoffs(states, {})
    assert [(j["stage"], j.get("profile"), j["mode"]) for j in jobs] == [
        ("input_frozen", "raschet-route", "agent"),
        ("route_frozen", "raschet-blank", "agent"),
        ("blank_costed", "raschet-time", "agent"),
        ("time_costed", None, "deterministic_book"),
    ]
    # Карточка форматируется без KeyError.
    assert "{order_id}" in jobs[0]["card"]
    jobs[0]["card"].format(order_id="ord-9", revision=3)


def test_v3_cursor_is_idempotent_and_suffix_insensitive() -> None:
    states = [wf_state("ord-9", [("route_frozen:variant-2", "t4")])]
    jobs = runner.pending_handoffs(states, {})
    assert len(jobs) == 1 and jobs[0]["cursor_key"] == "ord-9:wf:route_frozen:t4:0"
    cursor = {jobs[0]["cursor_key"]: "done"}
    assert runner.pending_handoffs(states, cursor) == []


def test_mixed_legacy_and_v3_orders_coexist() -> None:
    states = [
        state("ord-old", [("blank", "approved", "t2")]),
        wf_state("ord-new", [("input_frozen", "t1")]),
    ]
    jobs = runner.pending_handoffs(states, {})
    assert {(j["order_id"], j["profile"]) for j in jobs} == {
        ("ord-old", "raschet-route"),
        ("ord-new", "raschet-route"),
    }


def test_time_costed_routes_to_deterministic_book_machine() -> None:
    jobs = runner.pending_handoffs(
        [wf_state("ord-9", [("time_costed", "t6")])], {}
    )

    assert len(jobs) == 1
    job = jobs[0]
    assert job["stage"] == "time_costed"
    assert job["mode"] == "deterministic_book"
    assert "profile" not in job
    assert "target" not in job
    assert "metadata" not in job
    assert "card" not in job


def test_book_machine_calls_typed_services_without_profile_api(monkeypatch) -> None:
    calls: list[tuple] = []

    class Root:
        def __init__(self, path, writable=False):
            calls.append(("root", path, writable))

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    class Service:
        def __init__(self, registry, _packs):
            self.registry = registry

        def book_assemble(self, order_id, revision, extras, *, actor_role):
            calls.append(("book", order_id, revision, extras, actor_role))
            return {"revision": revision + 1}

        def qa_run_mechanical(self, order_id, revision, *, actor_role):
            calls.append(("qa", order_id, revision, actor_role))
            return {"revision": revision + 1}

    class Registry:
        def get(self, _order_id):
            return 7, {"workflow": {"status": "COSTING_COMPLETE"}}

    import metal_calc.packs2
    import metal_calc.securefs
    import metal_calc.service3

    monkeypatch.setattr(metal_calc.securefs, "SecureRoot", Root)
    monkeypatch.setattr(metal_calc.packs2, "PipelinePackStore", lambda root: root)
    monkeypatch.setattr(metal_calc.service3, "WorkflowService", Service)
    monkeypatch.setattr(
        runner,
        "profile_api",
        lambda *_args: pytest.fail("deterministic stage must not resolve an agent profile"),
    )
    job = runner.pending_handoffs(
        [wf_state("ord-9", [("time_costed", "t6")])], {}
    )[0]

    assert runner.execute_book_machine(job, 7, Registry()) is True
    assert ("book", "ord-9", 7, {}, "book_machine") in calls
    assert ("qa", "ord-9", 8, "book_machine") in calls


def test_duplicate_time_costed_events_have_distinct_cursor_keys() -> None:
    states = [
        wf_state(
            "ord-9",
            [("time_costed", "t6"), ("time_costed", "t6")],
        )
    ]

    jobs = runner.pending_handoffs(states, {})
    assert [job["cursor_key"] for job in jobs] == [
        "ord-9:wf:time_costed:t6:0",
        "ord-9:wf:time_costed:t6:1",
    ]
    remaining = runner.pending_handoffs(states, {jobs[0]["cursor_key"]: "done"})
    assert [job["cursor_key"] for job in remaining] == ["ord-9:wf:time_costed:t6:1"]


def test_agent_stage_uses_profile_api(monkeypatch) -> None:
    job = runner.pending_handoffs(
        [wf_state("ord-9", [("input_frozen", "t1")])], {}
    )[0]
    monkeypatch.setattr(
        runner,
        "profile_api",
        lambda profile: (f"http://profile/{profile}", "profile-key"),
    )

    assert runner.target_api(job) == (
        "http://profile/raschet-route",
        "profile-key",
    )


def test_missing_agent_profile_fails_closed_without_request(tmp_path, monkeypatch) -> None:
    job = runner.pending_handoffs(
        [wf_state("ord-9", [("input_frozen", "t1")])], {}
    )[0]
    monkeypatch.setattr(runner, "PROFILES_ROOT", tmp_path / "profiles")
    monkeypatch.setattr(
        runner,
        "_request",
        lambda *args, **kwargs: pytest.fail("no request without profile API config"),
    )

    assert runner.execute(scoped_job(job), revision=7) is False


def test_agent_profile_api_is_resolved_from_profile_env(tmp_path, monkeypatch) -> None:
    profile_dir = tmp_path / "profiles" / "raschet-route"
    profile_dir.mkdir(parents=True)
    (profile_dir / ".env").write_text(
        "API_SERVER_PORT=8765\nAPI_SERVER_KEY=book-secret\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(runner, "PROFILES_ROOT", tmp_path / "profiles")
    job = runner.pending_handoffs(
        [wf_state("ord-9", [("input_frozen", "t1")])], {}
    )[0]

    assert runner.target_api(job) == ("http://127.0.0.1:8765", "book-secret")


def test_execute_keeps_deterministic_session_id_for_agent_retry(monkeypatch) -> None:
    job = runner.pending_handoffs(
        [wf_state("ord-9", [("input_frozen", "t1")])], {}
    )[0]
    calls: list[dict] = []
    logs: list[str] = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self) -> bytes:
            return json.dumps(
                {"choices": [{"message": {"content": "internal result"}}]}
            ).encode()

    monkeypatch.setattr(
        runner,
        "profile_api",
        lambda profile: ("http://127.0.0.1:8650", "book-key"),
    )

    def request(url, key, payload, headers=None, timeout=30):
        calls.append(
            {
                "url": url,
                "key": key,
                "payload": payload,
                "headers": headers,
                "timeout": timeout,
            }
        )
        return Response()

    monkeypatch.setattr(runner, "_request", request)
    monkeypatch.setattr(runner, "log", logs.append)

    scoped = scoped_job(job)
    assert runner.execute(scoped, revision=12, attempt_no=3) is True
    session_id = scoped["delivery_session_id"]
    capability = scoped["delivery_tool_scope"]
    assert calls[0]["payload"]["id"] == session_id
    assert calls[1]["headers"]["X-Hermes-Session-Id"] == session_id
    assert calls[1]["headers"]["X-Hermes-Tool-Scope"] == capability
    assert all(call["key"] == "book-key" for call in calls)
    assert capability not in "\n".join(logs)
    assert hashlib.sha256(session_id.encode("utf-8")).hexdigest()[:12] in "\n".join(logs)


def test_execute_rejects_empty_200_response(monkeypatch) -> None:
    job = runner.pending_handoffs(
        [wf_state("ord-9", [("input_frozen", "t1")])], {}
    )[0]

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self) -> bytes:
            return json.dumps({"choices": []}).encode()

    monkeypatch.setattr(runner, "profile_api", lambda _profile: ("http://book", "key"))
    monkeypatch.setattr(runner, "_request", lambda *args, **kwargs: Response())

    assert runner.execute(scoped_job(job), revision=12) is False


def test_execute_does_not_retry_ambiguous_chat_timeout(monkeypatch) -> None:
    job = runner.pending_handoffs(
        [wf_state("ord-9", [("input_frozen", "t1")])], {}
    )[0]
    calls = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self) -> bytes:
            return b"{}"

    def request(url, *args, **kwargs):
        calls.append(url)
        if url.endswith("/v1/chat/completions"):
            raise TimeoutError("response may have been lost after server accepted the turn")
        return Response()

    monkeypatch.setattr(runner, "profile_api", lambda _profile: ("http://book", "key"))
    monkeypatch.setattr(runner, "_request", request)
    monkeypatch.setattr(runner, "_session_has_card", lambda *_args: False)

    assert runner.execute(scoped_job(job), revision=12) is False
    assert calls == [
        "http://book/api/sessions",
        "http://book/v1/chat/completions",
    ]


def test_execute_reconciles_accepted_card_after_ambiguous_timeout(monkeypatch) -> None:
    job = runner.pending_handoffs(
        [wf_state("ord-9", [("input_frozen", "t1")])], {}
    )[0]

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self) -> bytes:
            return b"{}"

    def request(url, *args, **kwargs):
        if url.endswith("/v1/chat/completions"):
            raise TimeoutError("ambiguous")
        return Response()

    monkeypatch.setattr(runner, "profile_api", lambda _profile: ("http://route", "key"))
    monkeypatch.setattr(runner, "_request", request)
    monkeypatch.setattr(runner, "_session_has_card", lambda *_args: True)

    assert runner.execute(scoped_job(job), revision=12) is True


def test_session_reconciliation_reads_real_api_envelope(monkeypatch) -> None:
    card = "exact immutable card"

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self) -> bytes:
            return json.dumps(
                {
                    "object": "list",
                    "session_id": "handoff-ord-9-input_frozen-r12",
                    "data": [
                        {"role": "assistant", "content": "working"},
                        {"role": "user", "content": card},
                    ],
                }
            ).encode()

    monkeypatch.setattr(urllib.request, "urlopen", lambda *_args, **_kwargs: Response())

    assert runner._session_has_card(
        "http://route", "key", "handoff-ord-9-input_frozen-r12", card
    ) is True


def test_session_reconciliation_rejects_wrong_envelope_or_session(monkeypatch) -> None:
    payloads = iter(
        [
            {"messages": [{"role": "user", "content": "card"}]},
            {
                "object": "list",
                "session_id": "another-session",
                "data": [{"role": "user", "content": "card"}],
            },
        ]
    )

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self) -> bytes:
            return json.dumps(next(payloads)).encode()

    monkeypatch.setattr(urllib.request, "urlopen", lambda *_args, **_kwargs: Response())
    assert runner._session_has_card("http://route", "key", "wanted", "card") is False
    assert runner._session_has_card("http://route", "key", "wanted", "card") is False


def test_execute_sends_stable_chat_idempotency_key(monkeypatch) -> None:
    job = runner.pending_handoffs(
        [wf_state("ord-9", [("input_frozen", "t1")])], {}
    )[0]
    chat_headers = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self) -> bytes:
            return json.dumps(
                {"choices": [{"message": {"content": "accepted"}}]}
            ).encode()

    def request(url, *args, **kwargs):
        if url.endswith("/v1/chat/completions"):
            chat_headers.append(kwargs["headers"])
        return Response()

    monkeypatch.setattr(runner, "profile_api", lambda _profile: ("http://route", "key"))
    monkeypatch.setattr(runner, "_request", request)

    assert runner.execute(scoped_job(job), revision=12) is True
    assert runner.execute(scoped_job(job), revision=12) is True
    assert chat_headers[0]["Idempotency-Key"] == chat_headers[1]["Idempotency-Key"]
    assert chat_headers[0]["X-Hermes-Session-Id"] == chat_headers[1]["X-Hermes-Session-Id"]
    assert chat_headers[0]["X-Hermes-Session-Id"].startswith("handoff-")
    assert chat_headers[0]["X-Hermes-Tool-Scope"].startswith("mcs1.")
    assert chat_headers[0]["X-Hermes-Tool-Scope"] == chat_headers[1]["X-Hermes-Tool-Scope"]


def test_distinct_trigger_events_get_distinct_sessions_and_idempotency_keys(monkeypatch) -> None:
    jobs = runner.pending_handoffs(
        [
            wf_state(
                "ord-9",
                [("route_frozen:variant-1", "t1"), ("route_frozen:variant-2", "t2")],
            )
        ],
        {},
    )
    observed = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self) -> bytes:
            return json.dumps(
                {"choices": [{"message": {"content": "accepted"}}]}
            ).encode()

    def request(url, *args, **kwargs):
        if url.endswith("/v1/chat/completions"):
            observed.append(
                (kwargs["headers"]["X-Hermes-Session-Id"], kwargs["headers"]["Idempotency-Key"])
            )
        return Response()

    monkeypatch.setattr(runner, "profile_api", lambda _profile: ("http://route", "key"))
    monkeypatch.setattr(runner, "_request", request)

    assert runner.execute(scoped_job(jobs[0]), revision=12) is True
    assert runner.execute(scoped_job(jobs[1]), revision=12) is True
    assert observed[0][0] != observed[1][0]
    assert observed[0][1] != observed[1][1]


def test_only_latest_matching_agent_trigger_is_current() -> None:
    current = wf_state(
        "ord-9",
        [("route_frozen:variant-1", "t1"), ("route_frozen:variant-2", "t2")],
    )
    current["workflow"] = {"status": "ROUTE_FROZEN", "calculation_revision": 3}
    jobs = runner.pending_handoffs([current], {})

    assert runner.agent_trigger_is_current(jobs[0], current) is False
    assert runner.agent_trigger_is_current(jobs[1], current) is True

    current["workflow"]["status"] = "DETAILED_COSTING"
    assert runner.agent_trigger_is_current(jobs[1], current) is True

    current["workflow"]["status"] = "COSTING_COMPLETE"
    assert runner.agent_trigger_is_current(jobs[1], current) is False


def test_tech_trigger_survives_partial_writes_but_not_route_return() -> None:
    current = wf_state("ord-9", [("input_frozen", "t1")])
    current["workflow"] = {"status": "BOM_VALIDATED", "calculation_revision": 1}
    job = runner.pending_handoffs([current], {})[0]
    assert runner.agent_trigger_is_current(job, current) is True

    current["workflow"]["status"] = "ROUTE_OPTIONS_READY"
    assert runner.agent_trigger_is_current(job, current) is True

    current["workflow_events"].append(
        {"event": "route_return:ROUTE_OPTIONS_READY", "at": "t2"}
    )
    assert runner.agent_trigger_is_current(job, current) is False


def test_route_return_schedules_distinct_current_tech_handoff() -> None:
    current = wf_state(
        "ord-9",
        [
            ("input_frozen", "t1"),
            ("route_frozen:variant-1", "t2"),
            ("route_return:ROUTE_OPTIONS_READY", "t3"),
        ],
    )
    current["workflow"] = {
        "status": "ROUTE_OPTIONS_READY",
        "calculation_revision": 2,
    }
    jobs = runner.pending_handoffs([current], {})
    returned = next(job for job in jobs if job["stage"] == "route_return")

    assert returned["profile"] == "raschet-route"
    assert returned["cursor_key"] == "ord-9:wf:route_return:t3:2"
    assert runner.agent_trigger_is_current(returned, current) is True
    old_input = next(job for job in jobs if job["stage"] == "input_frozen")
    assert runner.agent_trigger_is_current(old_input, current) is False


def test_execute_contains_session_network_failure(monkeypatch) -> None:
    job = runner.pending_handoffs(
        [wf_state("ord-9", [("input_frozen", "t1")])], {}
    )[0]
    monkeypatch.setattr(runner, "profile_api", lambda _profile: ("http://book", "key"))
    monkeypatch.setattr(
        runner,
        "_request",
        lambda *args, **kwargs: (_ for _ in ()).throw(urllib.error.URLError("offline")),
    )

    assert runner.execute(scoped_job(job), revision=12) is False


def test_other_session_conflict_is_rejected(monkeypatch) -> None:
    job = runner.pending_handoffs(
        [wf_state("ord-9", [("input_frozen", "t1")])], {}
    )[0]
    chat_calls = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self) -> bytes:
            return json.dumps(
                {"choices": [{"message": {"content": "accepted"}}]}
            ).encode()

    def request(url, *args, **kwargs):
        if url.endswith("/api/sessions"):
            body = json.dumps({"error": {"code": "other_conflict"}}).encode()
            raise urllib.error.HTTPError(url, 409, "conflict", {}, io.BytesIO(body))
        chat_calls.append(url)
        return Response()

    monkeypatch.setattr(runner, "profile_api", lambda _profile: ("http://book", "key"))
    monkeypatch.setattr(runner, "_request", request)

    assert runner.execute(scoped_job(job), revision=12) is False
    assert chat_calls == []


def test_structured_session_exists_conflict_continues_to_chat(monkeypatch) -> None:
    job = runner.pending_handoffs(
        [wf_state("ord-9", [("input_frozen", "t1")])], {}
    )[0]
    chat_calls = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self) -> bytes:
            return json.dumps(
                {"choices": [{"message": {"content": "accepted"}}]}
            ).encode()

    def request(url, *args, **kwargs):
        if url.endswith("/api/sessions"):
            body = json.dumps({"error": {"code": "session_exists"}}).encode()
            raise urllib.error.HTTPError(url, 409, "conflict", {}, io.BytesIO(body))
        chat_calls.append(url)
        return Response()

    monkeypatch.setattr(runner, "profile_api", lambda _profile: ("http://book", "key"))
    monkeypatch.setattr(runner, "_request", request)

    assert runner.execute(scoped_job(job), revision=12) is True
    assert chat_calls == ["http://book/v1/chat/completions"]


def test_legacy_tick_retries_without_advancing_event_cursor(monkeypatch) -> None:
    class Registry:
        def all_states(self):
            return [state("ord-9", [("blank", "approved", "t6")])]

        def get(self, order_id):
            assert order_id == "ord-9"
            return 7, {}

    persisted: dict = {}
    attempts: list[int] = []
    outcomes = iter((False, True))
    monkeypatch.setattr(runner, "load_cursor", lambda: persisted.copy())
    monkeypatch.setattr(
        runner,
        "save_cursor",
        lambda cursor: (persisted.clear(), persisted.update(cursor)),
    )

    def execute(job, revision, attempt_no=0):
        assert job["cursor_key"] == "ord-9:blank:t6:0"
        assert revision == 7
        attempts.append(attempt_no)
        return next(outcomes)

    monkeypatch.setattr(runner, "execute", execute)

    runner.tick(Registry())
    assert persisted["attempts:ord-9:blank:t6:0"] == 1
    assert persisted["retry_not_before:ord-9:blank:t6:0"] > 0

    persisted.pop("retry_not_before:ord-9:blank:t6:0")
    runner.tick(Registry())
    assert attempts == [0, 1]
    assert "attempts:ord-9:blank:t6:0" not in persisted
    assert persisted["ord-9:blank:t6:0"]


def test_agent_retry_freezes_delivery_revision_across_benign_registry_mutation(
    monkeypatch,
) -> None:
    current = wf_state("ord-9", [("route_frozen:variant-1", "t1")])
    current["workflow"] = {"status": "ROUTE_FROZEN", "calculation_revision": 1}

    class Registry:
        revision = 12

        def all_states(self):
            return [current]

        def get(self, order_id):
            assert order_id == "ord-9"
            return self.revision, current

    registry = Registry()
    persisted: dict = {}
    deliveries = []
    monkeypatch.setattr(runner, "load_cursor", lambda: persisted.copy())
    monkeypatch.setattr(
        runner,
        "save_cursor",
        lambda cursor: (persisted.clear(), persisted.update(cursor)),
    )
    monkeypatch.setattr(
        runner,
        "execute",
        lambda job, revision, attempt_no=0: deliveries.append(
            (
                job["cursor_key"],
                revision,
                attempt_no,
                job["delivery_card"],
                job["delivery_session_id"],
                job["delivery_tool_scope"],
            )
        )
        or False,
    )

    runner._tick_locked(registry)
    assert deliveries[0][:3] == ("ord-9:wf:route_frozen:t1:0", 12, 0)
    assert persisted["delivery_revision:ord-9:wf:route_frozen:t1:0"] == 12
    first_card = deliveries[0][3]
    first_session_id = deliveries[0][4]
    first_tool_scope = deliveries[0][5]
    first_expiry = persisted["delivery_scope_expiry:ord-9:wf:route_frozen:t1:0"]
    assert persisted["delivery_session_id:ord-9:wf:route_frozen:t1:0"] == first_session_id
    assert first_session_id.startswith("handoff-")
    assert first_tool_scope.startswith("mcs1.")
    assert all("mcs1." not in str(value) for value in persisted.values())

    persisted.pop("retry_not_before:ord-9:wf:route_frozen:t1:0")
    registry.revision = 13  # e.g. a human contractor_quote_set mutation
    profile, _old_template = runner.V3_ROUTES["route_frozen"]
    monkeypatch.setitem(
        runner.V3_ROUTES,
        "route_frozen",
        (profile, "CHANGED TEMPLATE {order_id} revision {revision}"),
    )
    runner._tick_locked(registry)

    assert deliveries[-1][:3] == ("ord-9:wf:route_frozen:t1:0", 12, 1)
    assert deliveries[-1][3] == first_card
    assert deliveries[-1][4] == first_session_id
    assert deliveries[-1][5] == first_tool_scope
    assert persisted["delivery_scope_expiry:ord-9:wf:route_frozen:t1:0"] == first_expiry


def test_tech_partial_write_retry_reuses_frozen_delivery_identity(monkeypatch) -> None:
    current = wf_state("ord-9", [("input_frozen", "t1")])
    current["workflow"] = {"status": "INPUT_FROZEN", "calculation_revision": 1}

    class Registry:
        revision = 12

        def all_states(self):
            return [current]

        def get(self, order_id):
            assert order_id == "ord-9"
            return self.revision, current

    registry = Registry()
    persisted: dict = {}
    deliveries: list[tuple[str, str, int, int]] = []
    monkeypatch.setattr(runner, "load_cursor", lambda: persisted.copy())
    monkeypatch.setattr(
        runner,
        "save_cursor",
        lambda cursor: (persisted.clear(), persisted.update(cursor)),
    )

    def partial_execute(job, revision, attempt_no=0):
        deliveries.append(
            (
                job["delivery_session_id"],
                job["delivery_tool_scope"],
                revision,
                attempt_no,
            )
        )
        current["workflow"]["status"] = "BOM_VALIDATED"
        registry.revision += 1
        return False

    monkeypatch.setattr(runner, "execute", partial_execute)

    runner._tick_locked(registry)
    first_session_id, first_tool_scope = deliveries[0][:2]
    persisted.pop("retry_not_before:ord-9:wf:input_frozen:t1:0")
    runner._tick_locked(registry)

    assert deliveries == [
        (first_session_id, first_tool_scope, 12, 0),
        (first_session_id, first_tool_scope, 12, 1),
    ]
    assert persisted["delivery_session_id:ord-9:wf:input_frozen:t1:0"] == first_session_id


def test_expired_delivery_scope_rotates_only_while_trigger_is_current(monkeypatch) -> None:
    current = wf_state("ord-9", [("input_frozen", "t1")])
    current["workflow"] = {"status": "BOM_VALIDATED", "calculation_revision": 1}
    cursor_key = "ord-9:wf:input_frozen:t1:0"

    class Registry:
        def all_states(self):
            return [current]

        def get(self, order_id):
            assert order_id == "ord-9"
            return 13, current

    persisted = {
        f"delivery_revision:{cursor_key}": 12,
        f"delivery_card:{cursor_key}": "old-card",
        f"delivery_scope_expiry:{cursor_key}": 999,
        f"delivery_session_id:{cursor_key}": "handoff-expired",
    }
    deliveries: list[tuple[str, str, str, int]] = []
    monkeypatch.setattr(runner.time, "time", lambda: 1_000)
    monkeypatch.setattr(runner, "load_cursor", lambda: persisted.copy())
    monkeypatch.setattr(
        runner,
        "save_cursor",
        lambda cursor: (persisted.clear(), persisted.update(cursor)),
    )
    monkeypatch.setattr(
        runner,
        "execute",
        lambda job, revision, attempt_no=0: deliveries.append(
            (
                job["delivery_session_id"],
                job["delivery_tool_scope"],
                job["delivery_card"],
                revision,
            )
        )
        or False,
    )

    runner._tick_locked(Registry())

    new_session, new_tool_scope, new_card, new_revision = deliveries[0]
    assert new_session.startswith("handoff-")
    assert new_session != "handoff-expired"
    assert new_tool_scope.startswith("mcs1.")
    assert new_card != "old-card"
    assert new_revision == 13
    assert persisted[f"delivery_scope_expiry:{cursor_key}"] == (
        1_000 + runner.CAPABILITY_TTL_SECONDS
    )


def test_time_costed_postcondition_requires_current_engine_pass_and_book() -> None:
    current = time_costed_state()
    job = runner.pending_handoffs([current], {})[0]
    assert runner.time_costed_postcondition(job, current) is False

    complete_time_costed_with_pass(current)
    assert runner.time_costed_postcondition(job, current) is True

    current["qa"]["1"]["mechanical"]["computed_by"] = "agent"
    assert runner.time_costed_postcondition(job, current) is False


def test_time_costed_postcondition_rejects_engine_pass_with_failed_check() -> None:
    current = time_costed_state()
    job = runner.pending_handoffs([current], {})[0]
    complete_time_costed_with_pass(current)
    current["qa"]["1"]["mechanical"]["checks"].append(
        {"code": "mismatch", "ok": False}
    )

    assert runner.time_costed_postcondition(job, current) is False


def test_time_costed_postcondition_accepts_correlated_engine_adjust() -> None:
    current = time_costed_state()
    job = runner.pending_handoffs([current], {})[0]
    current["workflow"] = {"status": "DETAILED_COSTING", "calculation_revision": 2}
    current["workflow_events"].extend(
        [
            {"event": "book_assembled", "at": "t7"},
            {"event": "qa_mechanical:ADJUST", "at": "t8"},
        ]
    )
    current["workflow_history"] = [
        {
            "calculation_revision": 1,
            "reason": "qa_mechanical_adjust",
            "book": {"calculation_revision": 1, "digest": "book"},
            "qa": {
                "mechanical": {
                    "verdict": "ADJUST",
                    "computed_by": "engine",
                    "checks": [{"code": "sum", "ok": False}],
                }
            },
        }
    ]

    assert runner.time_costed_postcondition(job, current) is True


def test_time_costed_postcondition_rejects_adjust_before_this_trigger() -> None:
    current = time_costed_state(at="old")
    current["workflow_events"].extend(
        [
            {"event": "qa_mechanical:ADJUST", "at": "old-adjust"},
            {"event": "time_costed", "at": "new"},
        ]
    )
    current["workflow"] = {"status": "DETAILED_COSTING", "calculation_revision": 2}
    current["workflow_history"] = [
        {
            "calculation_revision": 1,
            "reason": "qa_mechanical_adjust",
            "book": {"calculation_revision": 1},
            "qa": {
                "mechanical": {"verdict": "ADJUST", "computed_by": "engine"}
            },
        }
    ]
    current["workflow_history"][0]["qa"]["mechanical"]["checks"] = [
        {"code": "sum", "ok": False}
    ]
    old_key = "ord-9:wf:time_costed:old:0"
    job = runner.pending_handoffs([current], {old_key: "done"})[0]

    assert job["cursor_key"] == "ord-9:wf:time_costed:new:2"
    assert runner.time_costed_postcondition(job, current) is False


def test_time_costed_postcondition_is_fail_closed_for_malformed_receipts() -> None:
    current = time_costed_state()
    job = runner.pending_handoffs([current], {})[0]
    complete_time_costed_with_pass(current)
    current["qa"] = ["not", "a", "receipt-map"]

    assert runner.time_costed_postcondition(job, current) is False


def test_time_costed_arbitrary_200_does_not_advance_cursor(monkeypatch) -> None:
    current = time_costed_state()

    class Registry:
        def all_states(self):
            return [current]

        def get(self, _order_id):
            return 7, current

    persisted = {}
    monkeypatch.setattr(runner, "load_cursor", lambda: persisted.copy())
    monkeypatch.setattr(runner, "save_cursor", lambda value: (persisted.clear(), persisted.update(value)))
    monkeypatch.setattr(runner, "execute_book_machine", lambda *args, **kwargs: True)

    runner.tick(Registry())

    assert "ord-9:wf:time_costed:t6:0" not in persisted
    assert persisted["attempts:ord-9:wf:time_costed:t6:0"] == 1


def test_uncertain_chat_is_closed_when_postcondition_was_committed(monkeypatch) -> None:
    current = time_costed_state()

    class Registry:
        def all_states(self):
            return [current]

        def get(self, _order_id):
            return 7, current

    persisted = {}

    def execute(*_args, **_kwargs):
        complete_time_costed_with_pass(current)
        return False

    monkeypatch.setattr(runner, "load_cursor", lambda: persisted.copy())
    monkeypatch.setattr(runner, "save_cursor", lambda value: (persisted.clear(), persisted.update(value)))
    monkeypatch.setattr(runner, "execute_book_machine", execute)

    runner.tick(Registry())

    assert persisted["ord-9:wf:time_costed:t6:0"]
    assert "attempts:ord-9:wf:time_costed:t6:0" not in persisted


def test_restart_reconciles_postcondition_after_cursor_write_crash(monkeypatch) -> None:
    current = time_costed_state()

    class Registry:
        def all_states(self):
            return [current]

        def get(self, _order_id):
            return 8, current

    persisted = {}
    dispatches = []

    def execute(*_args, **_kwargs):
        dispatches.append("sent")
        complete_time_costed_with_pass(current)
        return True

    save_calls = 0

    def save_cursor(value):
        nonlocal save_calls
        save_calls += 1
        if save_calls == 1:
            raise OSError("simulated crash before durable cursor")
        persisted.clear()
        persisted.update(value)

    monkeypatch.setattr(runner, "load_cursor", lambda: persisted.copy())
    monkeypatch.setattr(runner, "save_cursor", save_cursor)
    monkeypatch.setattr(runner, "execute_book_machine", execute)

    with pytest.raises(OSError, match="simulated crash"):
        runner.tick(Registry())
    runner.tick(Registry())

    assert dispatches == ["sent"]
    assert persisted["ord-9:wf:time_costed:t6:0"]


def test_one_job_failure_does_not_block_next_job(monkeypatch) -> None:
    first = time_costed_state("ord-a", "a1")
    second = time_costed_state("ord-b", "b1")
    by_order = {"ord-a": first, "ord-b": second}

    class Registry:
        def all_states(self):
            return [first, second]

        def get(self, order_id):
            return 9, by_order[order_id]

    persisted = {}

    def execute(job, *_args, **_kwargs):
        if job["order_id"] == "ord-a":
            raise urllib.error.URLError("offline")
        complete_time_costed_with_pass(second)
        return True

    monkeypatch.setattr(runner, "load_cursor", lambda: persisted.copy())
    monkeypatch.setattr(runner, "save_cursor", lambda value: (persisted.clear(), persisted.update(value)))
    monkeypatch.setattr(runner, "execute_book_machine", execute)

    runner.tick(Registry())

    assert persisted["attempts:ord-a:wf:time_costed:a1:0"] == 1
    assert persisted["ord-b:wf:time_costed:b1:0"]


def test_missing_cursor_is_the_only_empty_cursor_case() -> None:
    assert runner.load_cursor() == {}


def test_corrupt_cursor_fails_closed() -> None:
    runner.STATE_DIR.mkdir(parents=True)
    (runner.STATE_DIR / "cursor.json").write_text("{broken", encoding="utf-8")

    with pytest.raises(runner.CursorStateError, match="invalid cursor"):
        runner.load_cursor()


def test_unreadable_cursor_prevents_all_dispatch(monkeypatch) -> None:
    original_read_text = runner.Path.read_text

    def deny_cursor(self, *args, **kwargs):
        if self.name == "cursor.json":
            raise PermissionError("denied")
        return original_read_text(self, *args, **kwargs)

    class Registry:
        def all_states(self):
            return [state("ord-1", [("blank", "approved", "t1")])]

    monkeypatch.setattr(runner.Path, "read_text", deny_cursor)
    monkeypatch.setattr(
        runner,
        "execute",
        lambda *args, **kwargs: pytest.fail("corrupt cursor must dispatch nothing"),
    )

    with pytest.raises(PermissionError, match="denied"):
        runner.tick(Registry())


def test_second_runner_cannot_enter_tick_while_lock_is_held(monkeypatch) -> None:
    class Registry:
        def all_states(self):
            return [state("ord-1", [("blank", "approved", "t1")])]

    monkeypatch.setattr(
        runner,
        "execute",
        lambda *args, **kwargs: pytest.fail("second runner must not execute the job"),
    )

    with runner.tick_lock() as acquired:
        assert acquired is True
        runner.tick(Registry())

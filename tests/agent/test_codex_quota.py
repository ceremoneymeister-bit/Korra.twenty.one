"""0.21.13: the Codex subscription quota reaches the owner's dashboard.

The backend reports how much of the subscription window is used — as
``x-codex-*`` headers and as a ``rate_limits`` object in the stream body.
These tests pin that both spellings produce the same snapshot, that the last
value survives a restart (it is a file, not process memory), and that the
Codex stream hooks record it without disturbing the turn.
"""

from __future__ import annotations

import json
import types

import pytest

from agent import rate_limit_tracker as rlt

RESETS_AT = 1790412544


@pytest.fixture(autouse=True)
def _fresh_memo():
    rlt._codex_quota_memo.clear()
    yield
    rlt._codex_quota_memo.clear()


def test_body_in_codex_cli_shape():
    snapshot = rlt.parse_codex_rate_limits_body(
        {"rate_limits": {"limit_id": "codex", "limit_name": None,
                         "primary": {"used_percent": 55.0, "window_minutes": 10080, "resets_at": RESETS_AT},
                         "secondary": None, "plan_type": "pro"}},
        now=1_790_000_000,
    )
    assert snapshot is not None
    assert snapshot.primary == rlt.CodexQuotaWindow(55.0, 10080, float(RESETS_AT))
    assert snapshot.secondary is None
    assert snapshot.plan_type == "pro" and snapshot.limit_id == "codex"


def test_body_in_app_server_camel_case_and_ms_timestamps():
    snapshot = rlt.parse_codex_rate_limits_body(
        {"rateLimits": {"limitId": "codex",
                        "primary": {"usedPercent": 12, "windowDurationMins": 300, "resetsAt": RESETS_AT * 1000},
                        "secondary": {"usedPercent": 40, "windowDurationMins": 10080, "resetsAt": RESETS_AT},
                        "planType": "plus"}},
        now=1_790_000_000,
    )
    assert snapshot.primary.used_percent == 12.0 and snapshot.primary.window_minutes == 300
    assert snapshot.primary.resets_at == float(RESETS_AT)
    assert snapshot.secondary.used_percent == 40.0
    assert snapshot.plan_type == "plus"


def test_headers_match_the_body_values():
    now = 1_790_000_000
    headers = {
        "X-Codex-Primary-Used-Percent": "55.0",
        "X-Codex-Primary-Window-Minutes": "10080",
        "X-Codex-Primary-Reset-At": str(RESETS_AT),
        "x-codex-secondary-used-percent": "7",
        "x-codex-secondary-window-minutes": "300",
        "x-codex-secondary-reset-after-seconds": "600",
        "content-type": "text/event-stream",
    }
    snapshot = rlt.parse_codex_rate_limit_headers(headers, now=now)
    body = rlt.parse_codex_rate_limits_body(
        {"rate_limits": {"primary": {"used_percent": 55.0, "window_minutes": 10080, "resets_at": RESETS_AT}}},
        now=now,
    )
    assert snapshot.primary == body.primary
    assert snapshot.secondary == rlt.CodexQuotaWindow(7.0, 300, now + 600.0)
    assert snapshot.source == "headers"


@pytest.mark.parametrize("payload", [
    None, {}, {"type": "response.output_text.delta", "delta": "hi"},
    {"rate_limits": {"primary": {"used_percent": "n/a"}}}, "text", 42,
])
def test_garbage_is_ignored(payload):
    assert rlt.parse_codex_rate_limits_body(payload) is None


def test_headers_without_codex_family_are_ignored():
    assert rlt.parse_codex_rate_limit_headers({"x-ratelimit-limit-requests": "5"}) is None
    assert rlt.parse_codex_rate_limit_headers({}) is None


def test_last_value_survives_a_restart(tmp_path):
    snapshot = rlt.parse_codex_rate_limit_headers(
        {"x-codex-primary-used-percent": "81.5", "x-codex-primary-window-minutes": "10080",
         "x-codex-primary-reset-at": str(RESETS_AT)},
        now=1_790_000_000,
    )
    assert rlt.record_codex_quota(snapshot, root=tmp_path) is True
    # A new process has an empty memo but reads the same file.
    rlt._codex_quota_memo.clear()
    stored = rlt.load_codex_quota(root=tmp_path)
    assert stored["primary"]["used_percent"] == 81.5
    assert stored["primary"]["resets_at"] == float(RESETS_AT)
    assert stored["captured_at"] == 1_790_000_000
    assert (tmp_path / "state" / rlt.CODEX_QUOTA_FILE).stat().st_mode & 0o777 == 0o600


def test_unchanged_value_is_not_rewritten_on_every_call(tmp_path):
    def snap(at, used="50"):
        return rlt.parse_codex_rate_limit_headers(
            {"x-codex-primary-used-percent": used, "x-codex-primary-reset-at": str(RESETS_AT)}, now=at)

    assert rlt.record_codex_quota(snap(1000.0), root=tmp_path) is True
    assert rlt.record_codex_quota(snap(1010.0), root=tmp_path) is False
    assert rlt.record_codex_quota(snap(1020.0, used="51"), root=tmp_path) is True
    assert rlt.record_codex_quota(snap(1020.0 + 301, used="51"), root=tmp_path) is True


def test_damaged_file_reads_as_no_value(tmp_path):
    path = tmp_path / "state" / rlt.CODEX_QUOTA_FILE
    path.parent.mkdir()
    path.write_text("{broken", encoding="utf-8")
    assert rlt.load_codex_quota(root=tmp_path) is None
    path.write_text(json.dumps({"version": 1, "primary": {"used_percent": "x"}}), encoding="utf-8")
    assert rlt.load_codex_quota(root=tmp_path) is None


def test_codex_stream_hooks_record_headers_and_body_events(tmp_path, monkeypatch):
    from agent import codex_runtime

    monkeypatch.setattr(rlt, "codex_quota_path", lambda root=None: tmp_path / "state" / rlt.CODEX_QUOTA_FILE)

    raw_stream = types.SimpleNamespace(response=types.SimpleNamespace(headers={
        "x-codex-primary-used-percent": "33", "x-codex-primary-window-minutes": "10080",
        "x-codex-primary-reset-at": str(RESETS_AT),
    }))
    codex_runtime._observe_codex_quota_headers(raw_stream)
    assert rlt.load_codex_quota()["primary"]["used_percent"] == 33.0

    codex_runtime._observe_codex_quota_event({
        "type": "codex.rate_limits",
        "rate_limits": {"primary": {"used_percent": 34.0, "window_minutes": 10080, "resets_at": RESETS_AT}},
    })
    assert rlt.load_codex_quota()["primary"]["used_percent"] == 34.0

    # Ordinary frames are not inspected and never raise.
    codex_runtime._observe_codex_quota_event({"type": "response.output_text.delta", "delta": "x"})
    codex_runtime._observe_codex_quota_event(object())
    codex_runtime._observe_codex_quota_headers(None)
    assert rlt.load_codex_quota()["primary"]["used_percent"] == 34.0


def test_app_server_notification_records_the_quota(tmp_path, monkeypatch):
    from agent import codex_runtime

    monkeypatch.setattr(rlt, "codex_quota_path", lambda root=None: tmp_path / "state" / rlt.CODEX_QUOTA_FILE)
    agent = types.SimpleNamespace()
    bridge = codex_runtime.make_codex_app_server_event_bridge(agent)
    bridge({"method": "account/rateLimits/updated", "params": {"rateLimits": {
        "limitId": "codex", "primary": {"usedPercent": 71, "windowDurationMins": 10080, "resetsAt": RESETS_AT},
    }}})
    assert rlt.load_codex_quota()["primary"]["used_percent"] == 71.0

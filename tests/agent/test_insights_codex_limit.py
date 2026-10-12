"""K21-306: «на что ушли N % лимита» — проценты окна у провайдера и ряд вызовов по дням."""
import time

import pytest

from agent.insights import InsightsEngine, codex_limit_section
from agent.rate_limit_tracker import CodexQuotaSnapshot, CodexQuotaWindow, record_codex_quota
from korra_state import SessionDB

DAY = 86400


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    return tmp_path


@pytest.fixture()
def db(tmp_path):
    session_db = SessionDB(db_path=tmp_path / "state.db")
    yield session_db
    session_db.close()


def call(db, monkeypatch, at, session, calls, tokens):
    with monkeypatch.context() as m:
        m.setattr(time, "time", lambda: at)
        db.update_token_counts(session, output_tokens=tokens, api_call_count=calls,
                               billing_provider="openai-codex")


def store_quota(now, used=36.0, minutes=10080, resets_in=3 * DAY):
    snapshot = CodexQuotaSnapshot(
        primary=CodexQuotaWindow(used, minutes, now + resets_in), captured_at=now, source="usage")
    assert record_codex_quota(snapshot, force=True)


def test_two_days_one_session_report_splits_by_day_not_by_session(db, monkeypatch):
    now = time.time()
    db.create_session("long", source="telegram", model="gpt-5")
    call(db, monkeypatch, now - DAY - 600, "long", 40, 4000)
    call(db, monkeypatch, now - 60, "long", 5, 500)
    store_quota(now)

    report = InsightsEngine(db).generate(days=7)

    assert report["overview"]["total_output_tokens"] == 4500
    daily = report["codex_limit"]["daily"]
    assert [d["calls"] for d in daily][-2:] == [40, 5]
    today = daily[-1]
    assert (today["calls"], today["output_tokens"]) == (5, 500)


def test_reports_show_provider_percent_and_say_counters_are_cumulative(db, monkeypatch):
    now = time.time()
    db.create_session("s", source="cli", model="gpt-5")
    call(db, monkeypatch, now - 60, "s", 3, 300)
    store_quota(now, used=36.0)
    engine = InsightsEngine(db)
    report = engine.generate(days=7)

    for text in (engine.format_terminal(report), engine.format_gateway(report)):
        assert "Лимит Codex у провайдера" in text
        assert "израсходовано 36 %" in text
        assert "окно 7 дн." in text
        assert "накопительные счётчики" in text
        assert "3 вызовов" in text


def test_empty_sessions_still_report_the_provider_limit(db):
    store_quota(time.time(), used=12.0, minutes=300, resets_in=3600)
    engine = InsightsEngine(db)
    report = engine.generate(days=1)
    assert report["empty"]
    assert "израсходовано 12 %" in engine.format_terminal(report)
    assert "окно 5 ч" in engine.format_gateway(report)


def test_expired_window_is_not_reported_as_still_used(db):
    now = time.time()
    store_quota(now - 2 * DAY, used=90.0, minutes=300, resets_in=3600)
    section = codex_limit_section(db._conn, 7, now=now)
    assert section["windows"] == ["окно 5 ч: окно обновилось после последнего замера, лимит снова полный"]
    assert not any("90" in line for line in section["windows"])


def test_without_codex_data_the_report_has_no_limit_block(db, monkeypatch):
    db.create_session("s", source="cli", model="claude")
    call_at = time.time() - 60
    with monkeypatch.context() as m:
        m.setattr(time, "time", lambda: call_at)
        db.update_token_counts("s", output_tokens=10, billing_provider="anthropic")
    engine = InsightsEngine(db)
    assert "Лимит Codex" not in engine.format_terminal(engine.generate(days=7))

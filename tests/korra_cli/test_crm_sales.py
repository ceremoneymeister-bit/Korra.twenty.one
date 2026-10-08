"""Card data: stuck by N days in each CRM, month over month, limits, cache and «данные от …»."""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from korra_cli import crm_connection as cc
from korra_cli import crm_readers as cr
from korra_cli import crm_sales as cs

from .crm_fakes import AMO_TOKEN, WEBHOOK, FakeAmo, FakeBitrix, amo_probe_handlers, bitrix_probe_handlers

TZ = ZoneInfo("Europe/Moscow")
NOW = datetime(2026, 10, 8, 12, 0, tzinfo=TZ)  # Thursday


def iso(text: str) -> str:
    return datetime.fromisoformat(text).replace(tzinfo=TZ).isoformat()


def ts(text: str) -> int:
    return int(datetime.fromisoformat(text).replace(tzinfo=TZ).timestamp())


@pytest.fixture(autouse=True)
def _isolated(no_real_network, monkeypatch):
    cr.reset_pace()
    cs.reset()
    monkeypatch.setattr(cs, "_spawn", lambda target: target())
    monkeypatch.setattr(cs, "_now", lambda tz: NOW)
    yield
    cr.reset_pace()
    cs.reset()


# ---------------------------------------------------------------- Bitrix24 scenario


def bitrix_handlers(**over):
    def deals(p):
        flt = p["filter"]
        if flt["STAGE_SEMANTIC_ID"] == "S":
            assert ">=CLOSEDATE" in flt
            rows = [
                ("1", "1000000", "2026-10-02T10:00:00", "5"),
                ("2", "400000", "2026-10-06T10:00:00", "6"),
                ("3", "700000", "2026-09-03T10:00:00", "5"),
                ("4", "100000", "2026-09-07T12:00:00", "6"),
                ("5", "900000", "2026-09-20T10:00:00", "5"),
                ("6", "50000", "2026-09-08T13:00:00", "6"),  # after the cut of the previous month
            ]
            return {
                "result": [{"ID": i, "OPPORTUNITY": a, "CLOSEDATE": iso(d), "ASSIGNED_BY_ID": m} for i, a, d, m in rows],
                "total": len(rows),
            }
        assert flt["STAGE_SEMANTIC_ID"] == "P"
        rows = [
            ("11", "Окна для Ромашки", "100000", "NEW", "5", "2026-10-08T06:00:00", "2026-10-07T10:00:00"),
            ("12", "Склад «Север»", "500000", "PROPOSAL", "5", "2026-08-01T10:00:00", "2026-09-20T10:00:00"),
            ("13", "Фасад", "300000", "PROPOSAL", "6", "2026-09-01T10:00:00", "2026-10-01T12:00:00"),
            ("14", "Двери", "200000", "PROPOSAL", "6", "2026-09-01T10:00:00", "2026-10-02T12:00:00"),
        ]
        return {
            "result": [
                {"ID": i, "TITLE": t, "OPPORTUNITY": a, "STAGE_ID": s, "ASSIGNED_BY_ID": m,
                 "DATE_CREATE": iso(c), "MOVED_TIME": iso(mv)}
                for i, t, a, s, m, c, mv in rows
            ],
            "total": len(rows),
        }

    base = bitrix_probe_handlers(
        **{
            "crm.deal.list": deals,
            "crm.status.list": lambda p: {
                "result": [
                    {"STATUS_ID": "C0:WON", "NAME": "Сделка успешна", "SORT": "60", "SEMANTICS": "S"},
                    {"STATUS_ID": "PROPOSAL", "NAME": "КП отправлено", "SORT": "20"},
                    {"STATUS_ID": "NEW", "NAME": "Новая", "SORT": "10"},
                    {"STATUS_ID": "LOSE", "NAME": "Провал", "SORT": "70", "SEMANTICS": "F"},
                ],
                "total": 4,
            },
            "crm.lead.list": lambda p: {
                "result": [
                    {"ID": "1", "DATE_CREATE": iso("2026-10-08T09:00:00")},
                    {"ID": "2", "DATE_CREATE": iso("2026-10-08T10:30:00")},
                    {"ID": "3", "DATE_CREATE": iso("2026-10-07T15:00:00")},
                    {"ID": "4", "DATE_CREATE": iso("2026-10-02T15:00:00")},
                ],
                "total": 4,
            },
            "tasks.task.list": lambda p: {
                "result": {
                    "tasks": [
                        {"id": "1", "responsibleId": "5", "ufCrmTask": ["D_12"]},
                        {"id": "2", "responsibleId": "6", "ufCrmTask": ["D_99"]},
                        {"id": "3", "responsibleId": "6", "ufCrmTask": []},
                    ]
                },
                "total": 3,
            },
            "user.get": lambda p: {
                "result": [
                    {"ID": "5", "NAME": "Анна", "LAST_NAME": "Миронова"},
                    {"ID": "6", "NAME": "Олег", "LAST_NAME": "Иванов"},
                ],
                "total": 2,
            },
        }
    )
    base.update(over)
    return base


@pytest.fixture
def portal(monkeypatch):
    state = SimpleNamespace(bitrix=FakeBitrix(bitrix_handlers()), amo=FakeAmo(amo_handlers()))

    def make(conn):
        if conn["type"] == cr.BITRIX:
            return cr.Bitrix24Reader(conn["webhook_url"], transport=state.bitrix, sleep=lambda s: None)
        return cr.AmoReader(conn["domain"], conn["token"], transport=state.amo, sleep=lambda s: None)

    monkeypatch.setattr(cc, "make_reader", make)
    return state


BITRIX_CONN = {"type": "bitrix24", "webhook_url": WEBHOOK, "settings": {"pipeline_id": "0", "stuck_days": 7}}
AMO_CONN = {"type": "amocrm", "domain": "acme.amocrm.ru", "token": AMO_TOKEN,
            "settings": {"pipeline_id": "900", "stuck_days": 7}}


def test_bitrix_stuck_is_counted_by_moved_time_and_n_days(portal):
    snap = cs.gather(BITRIX_CONN, now=NOW, tz=TZ)
    stuck = snap["stuck"]
    # 12: 18 days, 13: exactly 7 days (counts), 14: 6 days (does not)
    assert (stuck["count"], stuck["amount"], stuck["days"]) == (2, 800000, 7)
    assert [d["id"] for d in stuck["top"]] == ["12", "13"] and stuck["top"][0]["days"] == 18
    assert "crm.stagehistory.list" not in portal.bitrix.methods()


def test_bitrix_threshold_follows_the_setting(portal):
    snap = cs.gather({**BITRIX_CONN, "settings": {"pipeline_id": "0", "stuck_days": 3}}, now=NOW, tz=TZ)
    assert snap["stuck"]["count"] == 3
    snap = cs.gather({**BITRIX_CONN, "settings": {"pipeline_id": "0", "stuck_days": 30}}, now=NOW, tz=TZ)
    assert snap["stuck"]["count"] == 0


def test_month_over_month_compares_the_same_stretch(portal):
    won = cs.gather(BITRIX_CONN, now=NOW, tz=TZ)["won"]
    assert (won["amount"], won["count"]) == (1400000, 2)
    # Sep 1 .. Sep 8 12:00: 700000 + 100000; the Sep 8 13:00 deal and Sep 20 are later than that
    assert (won["prev_amount"], won["prev_count"], won["change_pct"]) == (800000, 2, 75)


def test_weeks_run_monday_to_sunday_over_six_weeks(portal):
    weeks = cs.gather(BITRIX_CONN, now=NOW, tz=TZ)["won"]["weeks"]
    assert [w["start"] for w in weeks] == [
        "2026-08-31", "2026-09-07", "2026-09-14", "2026-09-21", "2026-09-28", "2026-10-05",
    ]
    assert [w["amount"] for w in weeks] == [700000, 150000, 900000, 0, 1000000, 400000]


def test_new_leads_today_and_seven_day_series(portal):
    new = cs.gather(BITRIX_CONN, now=NOW, tz=TZ)["new_leads"]
    assert new["today"] == 2 and new["series"] == [1, 0, 0, 0, 0, 1, 2] and new["unsorted"] is None


def test_new_leads_fall_back_to_deals_when_leads_are_off(portal):
    portal.bitrix.handlers["crm.lead.list"] = lambda p: {"error": "ACCESS_DENIED", "error_description": "leads off"}
    base = portal.bitrix.handlers["crm.deal.list"]

    def deals(p):
        if "STAGE_SEMANTIC_ID" not in p["filter"]:
            assert p["filter"]["CATEGORY_ID"] == "0"
            return {"result": [{"ID": "1", "DATE_CREATE": iso("2026-10-08T09:00:00")}], "total": 1}
        return base(p)

    portal.bitrix.handlers["crm.deal.list"] = deals
    assert cs.gather(BITRIX_CONN, now=NOW, tz=TZ)["new_leads"]["today"] == 1


def test_overdue_tasks_count_only_tasks_linked_to_shown_deals(portal):
    overdue = cs.gather(BITRIX_CONN, now=NOW, tz=TZ)["overdue"]
    assert overdue == {"available": True, "tasks": 1, "managers": 1, "limited": False}
    stages = {s["id"]: s for s in cs.gather(BITRIX_CONN, now=NOW, tz=TZ)["river"]["stages"]}
    assert [d["late"] for d in stages["PROPOSAL"]["deals"]] == [True, False, False]


def test_card_works_without_task_permission(portal):
    portal.bitrix.handlers["tasks.task.list"] = lambda p: {"error": "ACCESS_DENIED"}
    snap = cs.gather(BITRIX_CONN, now=NOW, tz=TZ)
    assert snap["overdue"]["available"] is False and snap["overdue"]["tasks"] == 0
    assert snap["stuck"]["count"] == 2


def test_river_stages_in_order_with_links_and_flags(portal):
    river = cs.gather(BITRIX_CONN, now=NOW, tz=TZ)["river"]
    assert [s["name"] for s in river["stages"]] == ["Новая", "КП отправлено"]
    new, proposal = river["stages"]
    assert (new["count"], new["amount"], new["stuck"]) == (1, 100000, 0)
    assert (proposal["count"], proposal["amount"], proposal["stuck"]) == (3, 1000000, 2)
    assert new["deals"][0]["new"] is True and proposal["deals"][0]["new"] is False
    assert new["deals"][0]["url"] == "https://acme.bitrix24.ru/crm/deal/details/11/"
    assert river["busiest_stage"] == "КП отправлено"
    assert (river["deals_total"], river["truncated"], river["amount_total"]) == (4, False, 1100000)


def test_managers_overdue_stuck_won_and_leader(portal):
    people = {p["name"]: p for p in cs.gather(BITRIX_CONN, now=NOW, tz=TZ)["managers"]}
    anna, oleg = people["Анна Миронова"], people["Олег Иванов"]
    assert (anna["overdue"], anna["stuck"], anna["won_amount"], anna["leader"]) == (1, 1, 1000000, True)
    assert (oleg["overdue"], oleg["stuck"], oleg["won_amount"], oleg["leader"]) == (0, 1, 400000, False)
    assert anna["initials"] == "АМ"


def test_large_pipeline_is_cut_and_labelled(portal):
    def deals(p):
        if p["filter"]["STAGE_SEMANTIC_ID"] == "S":
            return {"result": [], "total": 0}
        start = int(p.get("start", 0))
        rows = [
            {"ID": str(start + i), "TITLE": "d", "OPPORTUNITY": "10", "STAGE_ID": "NEW", "ASSIGNED_BY_ID": "5",
             "DATE_CREATE": iso("2026-10-01T10:00:00"), "MOVED_TIME": iso("2026-10-01T10:00:00")}
            for i in range(50)
        ]
        return {"result": rows, "next": start + 50, "total": 900}

    portal.bitrix.handlers["crm.deal.list"] = deals
    river = cs.gather(BITRIX_CONN, now=NOW, tz=TZ)["river"]
    assert river["truncated"] is True and river["limit"] == 300
    assert (river["deals_total"], river["deals_loaded"]) == (900, 300)
    assert portal.bitrix.methods().count("crm.deal.list") == 6 + 1  # six pages of open deals, one of won


def test_other_pipeline_uses_its_own_stage_list(portal):
    seen = []

    def statuses(p):
        seen.append(p["filter"]["ENTITY_ID"])
        return {"result": [{"STATUS_ID": "C4:NEW", "NAME": "Заявка", "SORT": "10"}], "total": 1}

    portal.bitrix.handlers["crm.status.list"] = statuses
    snap = cs.gather({**BITRIX_CONN, "settings": {"pipeline_id": "4", "stuck_days": 7}}, now=NOW, tz=TZ)
    assert seen == ["DEAL_STAGE_4"] and snap["pipeline"] == {"id": "4", "name": "Партнёры"}


# ---------------------------------------------------------------- amoCRM scenario


def amo_handlers(**over):
    leads = [
        {"id": 101, "name": "Окна", "price": 100000, "status_id": 12, "responsible_user_id": 7,
         "created_at": ts("2026-09-01T10:00:00"), "updated_at": ts("2026-10-07T10:00:00")},
        {"id": 102, "name": "Склад", "price": 500000, "status_id": 12, "responsible_user_id": 8,
         "created_at": ts("2026-09-01T10:00:00"), "updated_at": ts("2026-09-15T10:00:00")},
        {"id": 103, "name": "Фасад", "price": 300000, "status_id": 11, "responsible_user_id": 8,
         "created_at": ts("2026-09-20T10:00:00"), "updated_at": ts("2026-10-05T10:00:00")},
        {"id": 104, "name": "Новая", "price": 20000, "status_id": 11, "responsible_user_id": 7,
         "created_at": ts("2026-10-08T08:00:00"), "updated_at": ts("2026-10-08T08:00:00")},
    ]

    def lead_list(q):
        if q.get("filter[statuses][0][status_id]") == ["142"]:
            assert q["filter[statuses][0][pipeline_id]"] == ["900"] and "filter[closed_at][from]" in q
            return {"_embedded": {"leads": [
                {"id": 1, "price": 1000000, "responsible_user_id": 7, "closed_at": ts("2026-10-02T10:00:00")},
                {"id": 2, "price": 700000, "responsible_user_id": 8, "closed_at": ts("2026-09-03T10:00:00")},
            ]}}
        if "filter[created_at][from]" in q:
            return {"_embedded": {"leads": [
                {"id": 104, "created_at": ts("2026-10-08T08:00:00")},
                {"id": 105, "created_at": ts("2026-10-08T09:00:00")},
                {"id": 106, "created_at": ts("2026-10-06T09:00:00")},
            ]}}
        assert q["filter[statuses][0][pipeline_id]"] == ["900"]
        statuses = {v[0] for k, v in q.items() if k.endswith("[status_id]")}
        assert statuses == {"11", "12"} and q["order[updated_at]"] == ["asc"]
        return {"_embedded": {"leads": leads}}

    def events(q):
        if "filter[entity_id][]" in q:
            return {"_embedded": {"events": [
                {"entity_id": 102, "created_at": ts("2026-09-10T00:00:00")},
                {"entity_id": 102, "created_at": ts("2026-09-02T00:00:00")},
            ]}}
        assert q["filter[type][]"] == ["lead_status_changed"]
        assert q["filter[created_at][from]"] == [str(ts("2026-10-01T12:00:00"))]
        return {"_embedded": {"events": [{"entity_id": 101, "created_at": ts("2026-10-07T10:00:00")}]}}

    def tasks(q):
        return {"_embedded": {"tasks": [
            {"id": 1, "entity_id": 102, "responsible_user_id": 8, "complete_till": ts("2026-10-03T10:00:00")},
            {"id": 2, "entity_id": 103, "responsible_user_id": 8, "complete_till": ts("2026-10-20T10:00:00")},
            {"id": 3, "entity_id": 101, "responsible_user_id": 7, "complete_till": ts("2026-10-25T10:00:00")},
        ]}}

    base = amo_probe_handlers(
        leads=lead_list,
        events=events,
        tasks=tasks,
        **{"leads/unsorted": lambda q: {"_total_items": 4, "_embedded": {"unsorted": []}}},
    )
    base.update(over)
    return base


def test_amo_stuck_is_no_stage_change_event_within_n_days(portal):
    snap = cs.gather(AMO_CONN, now=NOW, tz=TZ)
    stuck = snap["stuck"]
    # 101 changed stage in the window; 102 untouched since Sep 15; 103 only had a note;
    # 104 was created after the window started
    assert (stuck["count"], stuck["amount"], stuck["approx"]) == (2, 800000, False)
    top = {d["id"]: d for d in stuck["top"]}
    assert set(top) == {"102", "103"}
    assert top["102"]["days"] == 28  # last stage change Sep 10 (exact, from the event log)
    assert top["103"]["days"] == 18  # never moved since it was created on Sep 20
    assert top["102"]["url"] == "https://acme.amocrm.ru/leads/detail/102"


def test_amo_incomplete_event_log_counts_only_certain_deals(portal):
    def events(q):
        if "filter[entity_id][]" in q:
            return {"_embedded": {"events": []}}
        return {"_embedded": {"events": [{"entity_id": 999, "created_at": ts("2026-10-07T10:00:00")}] * 100}}

    portal.amo.handlers["events"] = events
    snap = cs.gather(AMO_CONN, now=NOW, tz=TZ)
    # 103 was updated inside the window and the log is longer than we read: not certain, so not counted
    assert snap["stuck"]["count"] == 1 and snap["stuck"]["approx"] is True
    assert sum(1 for c in portal.amo.calls if c[0] == "events" and "filter[entity_id][]" not in c[1]) == 8


def test_amo_won_month_over_month_and_leader(portal):
    snap = cs.gather(AMO_CONN, now=NOW, tz=TZ)
    assert (snap["won"]["amount"], snap["won"]["prev_amount"], snap["won"]["change_pct"]) == (1000000, 700000, 43)
    people = {p["name"]: p for p in snap["managers"]}
    assert people["Ирина"]["leader"] is True and people["Олег"]["overdue"] == 1 and people["Олег"]["stuck"] == 2


def test_amo_new_leads_and_unsorted(portal):
    new = cs.gather(AMO_CONN, now=NOW, tz=TZ)["new_leads"]
    assert new["today"] == 2 and new["series"][4] == 1 and new["unsorted"] == 4


def test_amo_overdue_tasks_stop_at_the_first_future_deadline(portal):
    overdue = cs.gather(AMO_CONN, now=NOW, tz=TZ)["overdue"]
    assert overdue == {"available": True, "tasks": 1, "managers": 1, "limited": False}


def test_amo_river_and_pipeline(portal):
    snap = cs.gather(AMO_CONN, now=NOW, tz=TZ)
    assert snap["source"] == "amocrm" and snap["source_label"] == "amoCRM"
    assert snap["pipeline"] == {"id": "900", "name": "Продажи"}
    assert [(s["name"], s["count"]) for s in snap["river"]["stages"]] == [("Заявка", 2), ("КП", 2)]


def test_amo_sends_only_get_reads(portal):
    cs.gather(AMO_CONN, now=NOW, tz=TZ)
    assert {c[0] for c in portal.amo.calls} <= {"leads", "leads/pipelines", "events", "tasks", "leads/unsorted", "users"}


# ---------------------------------------------------------------- section, cache, stale data


@pytest.fixture
def stored(portal, tmp_path):
    cc.save({"type": "bitrix24", "webhook_url": WEBHOOK}, root=tmp_path)
    portal.bitrix.calls.clear()
    return tmp_path


def test_not_connected_offers_agent_keys(monkeypatch, tmp_path):
    monkeypatch.setattr(cc, "candidates", lambda: [{"profile": "crm", "type": "bitrix24"}])
    out = cs.sales_section(root=tmp_path, tz=TZ)
    assert out == {"status": "not_connected", "candidates": [{"profile": "crm", "type": "bitrix24"}]}


def test_section_is_served_from_cache_for_five_minutes(portal, stored, monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(cs, "_clock", lambda: clock[0])
    first = cs.sales_section(root=stored, tz=TZ)
    assert first["status"] == "ok" and first["stale"] is False and first["source_label"] == "Битрикс24"
    calls = len(portal.bitrix.calls)
    clock[0] += 299
    assert cs.sales_section(root=stored, tz=TZ)["as_of"] == first["as_of"]
    assert len(portal.bitrix.calls) == calls
    clock[0] += 2
    cs.sales_section(root=stored, tz=TZ)
    assert len(portal.bitrix.calls) > calls


def test_failure_keeps_last_value_marked_with_its_time(portal, stored, monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(cs, "_clock", lambda: clock[0])
    good = cs.sales_section(root=stored, tz=TZ)
    portal.bitrix.handlers["crm.deal.list"] = lambda p: OSError("down")
    clock[0] += 400
    cs.sales_section(root=stored, tz=TZ)  # refresh fails in the background
    out = cs.sales_section(root=stored, tz=TZ)
    assert out["status"] == "ok" and out["stale"] is True and out["as_of"] == good["as_of"]
    assert out["error"]["code"] == "network" and out["won"] == good["won"]


def test_failure_is_not_retried_before_the_pause(portal, stored, monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(cs, "_clock", lambda: clock[0])
    portal.bitrix.handlers["profile"] = lambda p: OSError("down")
    portal.bitrix.handlers["crm.deal.list"] = lambda p: OSError("down")
    first = cs.sales_section(root=stored, tz=TZ)
    assert first["status"] == "error" and first["error"]["code"] == "network"
    calls = len(portal.bitrix.calls)
    clock[0] += 60
    cs.sales_section(root=stored, tz=TZ)
    assert len(portal.bitrix.calls) == calls
    clock[0] += 120
    portal.bitrix.handlers = bitrix_handlers()
    cs.sales_section(root=stored, tz=TZ)
    assert cs.sales_section(root=stored, tz=TZ)["status"] == "ok"


def test_rate_limit_waits_longer(portal, stored, monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(cs, "_clock", lambda: clock[0])
    portal.bitrix.handlers["crm.deal.list"] = lambda p: {"error": "QUERY_LIMIT_EXCEEDED"}
    assert cs.sales_section(root=stored, tz=TZ)["error"]["code"] == "rate_limited"
    cr.reset_pace()
    portal.bitrix.handlers = bitrix_handlers()
    calls = len(portal.bitrix.calls)
    clock[0] += 300
    cs.sales_section(root=stored, tz=TZ)
    assert len(portal.bitrix.calls) == calls


def test_replacing_the_key_starts_a_fresh_read(portal, stored):
    cs.sales_section(root=stored, tz=TZ)
    calls = len(portal.bitrix.calls)
    cc.update_settings({"stuck_days": 3}, root=stored)
    out = cs.sales_section(root=stored, tz=TZ)
    assert len(portal.bitrix.calls) > calls and out["stuck"]["days"] == 3 and out["stuck"]["count"] == 3


def test_rejected_key_stops_reading_until_rechecked(portal, stored):
    portal.bitrix.handlers["profile"] = lambda p: {"error": "INVALID_CREDENTIALS"}
    cc.recheck(root=stored)
    portal.bitrix.calls.clear()
    out = cs.sales_section(root=stored, tz=TZ)
    assert out["status"] == "error" and out["error"]["code"] == "bad_key"
    assert portal.bitrix.calls == []


def test_slow_first_read_reports_loading(portal, stored, monkeypatch):
    monkeypatch.setattr(cs, "_spawn", lambda target: None)
    out = cs.sales_section(root=stored, tz=TZ, wait=0)
    assert out["status"] == "loading" and out["connection"]["portal"] == "acme.bitrix24.ru"


def test_section_never_contains_the_secret(portal, stored):
    text = json.dumps(cs.sales_section(root=stored, tz=TZ), ensure_ascii=False)
    assert "abcdef1234567890" not in text and "/rest/17/" not in text


# ---------------------------------------------------------------- one builder, one time zone (F8)


PLUS7 = timezone(timedelta(hours=7))


def _spy_gather(monkeypatch):
    seen = []
    real = cs.gather

    def spy(conn, *, now=None, tz=None):
        seen.append(tz)
        return real(conn, now=now, tz=tz)

    monkeypatch.setattr(cs, "gather", spy)
    return seen


def test_caller_without_a_zone_gets_the_installation_zone(portal, stored, monkeypatch):
    monkeypatch.setattr(cs, "installation_tz", lambda: PLUS7)
    seen = _spy_gather(monkeypatch)
    assert cs.sales_section(root=stored)["status"] == "ok"
    assert seen == [PLUS7]


def test_agent_and_dashboard_share_one_zone_and_one_cache(portal, stored, monkeypatch):
    monkeypatch.setattr(cs, "installation_tz", lambda: PLUS7)
    seen = _spy_gather(monkeypatch)
    cs.sales_section(root=stored)  # the agent tool: no zone of its own
    cs.sales_section(root=stored, tz=PLUS7)  # the dashboard
    assert seen == [PLUS7]


def test_changed_zone_is_not_served_from_the_old_cache(portal, stored, monkeypatch):
    seen = _spy_gather(monkeypatch)
    cs.sales_section(root=stored, tz=TZ)
    cs.sales_section(root=stored, tz=PLUS7)
    assert seen == [TZ, PLUS7]


# ---------------------------------------------------------------- a manual check keeps the last figures (F9)


def test_manual_recheck_failure_keeps_the_last_figures(portal, stored):
    good = cs.sales_section(root=stored, tz=TZ)
    portal.bitrix.handlers["profile"] = lambda p: OSError("down")
    assert cc.recheck(root=stored)["ok"] is False
    out = cs.sales_section(root=stored, tz=TZ)
    assert out["status"] == "ok" and out["won"] == good["won"] and out["as_of"] == good["as_of"]
    assert out["stale"] is True and out["error"]["code"] == "network"


def test_successful_recheck_does_not_mark_data_stale(portal, stored):
    cs.sales_section(root=stored, tz=TZ)
    assert cc.recheck(root=stored)["ok"] is True
    out = cs.sales_section(root=stored, tz=TZ)
    assert out["status"] == "ok" and out["stale"] is False


def test_later_good_read_clears_an_earlier_failed_check(portal, stored, monkeypatch):
    clock = [time.time()]
    monkeypatch.setattr(cs, "_clock", lambda: clock[0])
    cs.sales_section(root=stored, tz=TZ)
    portal.bitrix.handlers["profile"] = lambda p: OSError("down")
    cc.recheck(root=stored)
    portal.bitrix.handlers = bitrix_handlers()
    clock[0] += 400
    cs.sales_section(root=stored, tz=TZ)  # the refresh succeeds
    out = cs.sales_section(root=stored, tz=TZ)
    assert out["stale"] is False and "error" not in out


def test_access_switch_does_not_trigger_a_new_read(portal, stored):
    cs.sales_section(root=stored, tz=TZ)
    calls = len(portal.bitrix.calls)
    cc.update_settings({"agents_access": False}, root=stored)
    cs.sales_section(root=stored, tz=TZ)
    assert len(portal.bitrix.calls) == calls  # the switch does not change the figures


# ---------------------------------------------------------------- amoCRM: how many open deals there really are (F5)


def _open_deals(portal, count):
    base = portal.amo.handlers["leads"]

    def leads(q):
        if "order[updated_at]" not in q:
            return base(q)
        size, page = int(q["limit"][0]), int(q["page"][0])
        rows = [
            {"id": 1000 + i, "name": f"d{i}", "price": 10, "status_id": 11, "responsible_user_id": 7,
             "created_at": ts("2026-09-01T10:00:00"), "updated_at": ts("2026-10-07T10:00:00")}
            for i in range(count)
        ]
        return {"_embedded": {"leads": rows[(page - 1) * size: page * size]}}

    portal.amo.handlers["leads"] = leads
    portal.amo.handlers["events"] = lambda q: {"_embedded": {"events": []}}


@pytest.mark.parametrize(
    ("count", "truncated", "total", "exact"),
    [
        (299, False, 299, True),
        (300, False, 300, True),
        (301, True, 301, True),
        (400, True, 400, True),
        (499, True, 499, True),
        (500, True, 500, True),
        (501, True, 501, True),
        (620, True, 620, True),
        (1499, True, 1499, True),
        (1500, True, 1500, False),
        (1700, True, 1500, False),
    ],
)
def test_amo_open_deal_total_is_exact_or_a_lower_bound(portal, count, truncated, total, exact):
    _open_deals(portal, count)
    river = cs.gather(AMO_CONN, now=NOW, tz=TZ)["river"]
    assert river["deals_loaded"] == min(count, 300)
    assert (river["truncated"], river["deals_total"], river["total_exact"]) == (truncated, total, exact)


def test_bitrix_total_comes_from_the_api_and_is_exact(portal):
    portal.bitrix.handlers["crm.deal.list"] = lambda p: (
        {"result": [], "total": 0}
        if p["filter"]["STAGE_SEMANTIC_ID"] == "S"
        else {"result": [{"ID": "1", "STAGE_ID": "NEW", "DATE_CREATE": iso("2026-10-01T10:00:00")}] * 50,
              "next": int(p.get("start", 0)) + 50, "total": 900}
    )
    river = cs.gather(BITRIX_CONN, now=NOW, tz=TZ)["river"]
    assert (river["deals_total"], river["total_exact"], river["truncated"]) == (900, True, True)

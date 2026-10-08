"""Data of the dashboard card «Продажи» and of the agent tool, from Битрикс24 or amoCRM.

Two collectors turn the CRM into one neutral :class:`Raw`; :func:`build_snapshot`
turns that into the card's section. The collectors differ in how a deal is
known to be *stuck* (no stage change for N days):

* **Битрикс24** — ``MOVED_TIME`` of the deal (the time of its last stage
  move), which ``crm.deal.list`` returns for every deal, so a whole pipeline
  costs a few requests. The creation date stands in when it is empty.
* **amoCRM** — a deal has no such field. The honest, cheap sign is the event
  log: a deal is stuck if it was created before the window and has no
  ``lead_status_changed`` event within the last N days. When the event log is
  longer than the pages we read, only deals not updated at all in the window
  are counted (a lower bound, flagged ``approx``).

Volume is bounded everywhere (pages and rows per list); when a list is cut the
section says so (``truncated``, ``limit``).
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, tzinfo
from pathlib import Path
from typing import Any, Callable, Optional

from korra_cli import crm_connection as cc
from korra_cli import crm_readers as cr
from korra_cli.crm_readers import AMOCRM, BITRIX, CrmError

logger = logging.getLogger(__name__)

TTL_SECONDS = 300.0
FIRST_WAIT_SECONDS = 6.0
RETRY_AFTER_FAILURE = 120.0
RETRY_AFTER_RATE_LIMIT = 600.0
OPEN_DEAL_LIMIT = 300
BITRIX_PAGE = 50
WON_PAGES = 10
LEAD_PAGES = 6
TASK_PAGES = 6
AMO_PAGE = 250
AMO_EVENT_PAGES = 8
WEEKS = 6


# ------------------------------------------------------------------ neutral data


@dataclass
class Deal:
    id: str
    title: str
    amount: float
    stage_id: str
    manager_id: str
    created: datetime
    idle_days: int
    stuck: bool


@dataclass
class Won:
    amount: float
    ts: datetime
    manager_id: str


@dataclass
class Raw:
    pipeline: dict
    pipelines: list
    stages: list
    deals: list
    deals_total: int
    deals_truncated: bool
    won: list
    won_truncated: bool
    new_leads: list
    unsorted: Optional[int]
    tasks_available: bool
    overdue: list
    overdue_truncated: bool
    names: dict = field(default_factory=dict)
    stuck_approx: bool = False
    deals_total_exact: bool = True


# ------------------------------------------------------------------ time helpers


def _midnight(moment: datetime) -> datetime:
    return moment.replace(hour=0, minute=0, second=0, microsecond=0)


def month_bounds(now: datetime) -> tuple[datetime, datetime, datetime]:
    """``(month_start, prev_month_start, prev_cut)``: the same stretch of the previous month."""
    start = _midnight(now).replace(day=1)
    prev_start = _midnight(start - timedelta(days=1)).replace(day=1)
    elapsed = now - start
    cut = min(prev_start + elapsed, start)
    return start, prev_start, cut


def week_starts(now: datetime) -> list[datetime]:
    monday = _midnight(now) - timedelta(days=now.weekday())
    return [monday - timedelta(weeks=WEEKS - 1 - i) for i in range(WEEKS)]


def won_window_start(now: datetime) -> datetime:
    return min(week_starts(now)[0], month_bounds(now)[1])


def _parse_dt(value: Any, tz: Optional[tzinfo]) -> Optional[datetime]:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return datetime.fromtimestamp(float(value), tz)
    if not isinstance(value, str) or not value:
        return None
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if moment.tzinfo is None:
        return moment.replace(tzinfo=tz) if tz else moment.astimezone()
    return moment.astimezone(tz) if tz else moment


def _money(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return number if number == number and abs(number) < 1e13 else 0.0


def _number(value: float) -> float | int:
    value = round(value, 2)
    return int(value) if value == int(value) else value


def _days(delta: timedelta) -> int:
    return max(0, int(delta.total_seconds() // 86400))


def _field(row: dict, *names: str) -> Any:
    for name in names:
        if name in row and row[name] not in (None, ""):
            return row[name]
    return None


# ------------------------------------------------------------------ Bitrix24 collector


def collect_bitrix(reader: cr.Bitrix24Reader, settings: dict, now: datetime, tz: Optional[tzinfo]) -> Raw:
    stuck_days = settings["stuck_days"]
    pipelines = cc.bitrix_pipelines(reader)
    wanted = str(settings.get("pipeline_id") or "")
    pipeline = next((p for p in pipelines if p["id"] == wanted), None) or pipelines[0]
    category = pipeline["id"]

    entity = "DEAL_STAGE" if category == "0" else f"DEAL_STAGE_{category}"
    statuses = reader.collect("crm.status.list", {"filter": {"ENTITY_ID": entity}}, max_pages=3)["items"]
    statuses = [s for s in statuses if isinstance(s, dict) and s.get("SEMANTICS") not in ("S", "F")]
    statuses.sort(key=lambda s: int(_money(s.get("SORT"))))
    stages = [{"id": str(s["STATUS_ID"]), "name": str(s.get("NAME") or "")} for s in statuses if s.get("STATUS_ID")]

    pages = (OPEN_DEAL_LIMIT + BITRIX_PAGE - 1) // BITRIX_PAGE
    opened = reader.collect(
        "crm.deal.list",
        {
            "filter": {"CATEGORY_ID": category, "STAGE_SEMANTIC_ID": "P"},
            "select": ["ID", "TITLE", "OPPORTUNITY", "STAGE_ID", "ASSIGNED_BY_ID", "DATE_CREATE", "MOVED_TIME"],
            "order": {"MOVED_TIME": "ASC"},
        },
        max_pages=pages,
    )
    deals = []
    for row in opened["items"][:OPEN_DEAL_LIMIT]:
        created = _parse_dt(row.get("DATE_CREATE"), tz) or now
        moved = _parse_dt(row.get("MOVED_TIME"), tz) or created
        idle = _days(now - moved)
        deals.append(
            Deal(
                id=str(row.get("ID")),
                title=str(row.get("TITLE") or ""),
                amount=_money(row.get("OPPORTUNITY")),
                stage_id=str(row.get("STAGE_ID") or ""),
                manager_id=str(row.get("ASSIGNED_BY_ID") or ""),
                created=created,
                idle_days=idle,
                stuck=idle >= stuck_days,
            )
        )

    since = won_window_start(now)
    won_rows = reader.collect(
        "crm.deal.list",
        {
            "filter": {
                "CATEGORY_ID": category,
                "STAGE_SEMANTIC_ID": "S",
                ">=CLOSEDATE": since.isoformat(timespec="seconds"),
            },
            "select": ["ID", "OPPORTUNITY", "CLOSEDATE", "ASSIGNED_BY_ID"],
            "order": {"CLOSEDATE": "DESC"},
        },
        max_pages=WON_PAGES,
    )
    won = []
    for row in won_rows["items"]:
        closed = _parse_dt(row.get("CLOSEDATE"), tz)
        if closed is not None:
            won.append(Won(_money(row.get("OPPORTUNITY")), closed, str(row.get("ASSIGNED_BY_ID") or "")))

    week_ago = (_midnight(now) - timedelta(days=6)).isoformat(timespec="seconds")
    new_leads = _bitrix_new(reader, category, week_ago, tz, now)

    tasks_available, overdue, overdue_cut = _bitrix_overdue(reader, {d.id for d in deals}, now)

    names = _bitrix_names(
        reader,
        {d.manager_id for d in deals} | {w.manager_id for w in won} | {o["manager_id"] for o in overdue},
    )
    return Raw(
        pipeline={"id": category, "name": pipeline["name"]},
        pipelines=[{"id": p["id"], "name": p["name"]} for p in pipelines],
        stages=stages,
        deals=deals,
        deals_total=int(opened["total"] if opened["total"] is not None else len(deals)),
        deals_total_exact=opened["total"] is not None or not opened["truncated"],
        deals_truncated=bool(opened["truncated"]) or len(opened["items"]) > OPEN_DEAL_LIMIT,
        won=won,
        won_truncated=bool(won_rows["truncated"]),
        new_leads=new_leads,
        unsorted=None,
        tasks_available=tasks_available,
        overdue=overdue,
        overdue_truncated=overdue_cut,
        names=names,
    )


def _bitrix_new(reader, category: str, since: str, tz, now) -> list[datetime]:
    """Creation moments of the last seven days: leads, or deals on portals without leads."""
    try:
        rows = reader.collect(
            "crm.lead.list", {"filter": {">=DATE_CREATE": since}, "select": ["ID", "DATE_CREATE"]}, max_pages=LEAD_PAGES
        )["items"]
    except CrmError as exc:
        if exc.code in {"network", "rate_limited", "bad_key", "budget", "plan_closed", "redirect", "limit"}:
            raise
        rows = reader.collect(
            "crm.deal.list",
            {"filter": {"CATEGORY_ID": category, ">=DATE_CREATE": since}, "select": ["ID", "DATE_CREATE"]},
            max_pages=LEAD_PAGES,
        )["items"]
    return [m for m in (_parse_dt(r.get("DATE_CREATE"), tz) for r in rows) if m is not None]


def _bitrix_overdue(reader, deal_ids: set[str], now: datetime) -> tuple[bool, list[dict], bool]:
    try:
        page = reader.collect(
            "tasks.task.list",
            {
                "filter": {"<DEADLINE": now.isoformat(timespec="seconds"), "REAL_STATUS": [1, 2, 3]},
                "select": ["ID", "RESPONSIBLE_ID", "DEADLINE", "UF_CRM_TASK"],
            },
            max_pages=TASK_PAGES,
        )
    except CrmError as exc:
        if exc.code != "forbidden":
            raise
        return False, [], False
    out = []
    for task in page["items"]:
        links = _field(task, "ufCrmTask", "UF_CRM_TASK") or []
        links = links if isinstance(links, list) else [links]
        linked = [str(x)[2:] for x in links if str(x).startswith("D_") and str(x)[2:] in deal_ids]
        if linked:
            out.append(
                {
                    "manager_id": str(_field(task, "responsibleId", "RESPONSIBLE_ID") or ""),
                    "deal_id": linked[0],
                }
            )
    return True, out, bool(page["truncated"])


def _bitrix_names(reader, ids: set[str]) -> dict[str, str]:
    ids = {i for i in ids if i}
    if not ids:
        return {}
    try:
        rows = reader.collect("user.get", {"filter": {"ID": sorted(ids)}}, max_pages=2)["items"]
    except CrmError as exc:
        if exc.code in {"network", "rate_limited", "bad_key", "budget"}:
            raise
        return {}
    return {
        str(r.get("ID")): " ".join(str(r.get(k) or "") for k in ("NAME", "LAST_NAME")).strip()
        for r in rows
        if isinstance(r, dict)
    }


# ------------------------------------------------------------------ amoCRM collector


def collect_amo(reader: cr.AmoReader, settings: dict, now: datetime, tz: Optional[tzinfo]) -> Raw:
    stuck_days = settings["stuck_days"]
    pipelines = cc.amo_pipelines(reader.get("leads/pipelines"))
    if not pipelines:
        raise CrmError("protocol")
    wanted = str(settings.get("pipeline_id") or "")
    pipeline = (
        next((p for p in pipelines if p["id"] == wanted), None)
        or next((p for p in pipelines if p["is_main"]), None)
        or pipelines[0]
    )
    pid = pipeline["id"]
    stages = [
        {"id": str(s["id"]), "name": s["name"]} for s in sorted(pipeline["statuses"], key=lambda s: s["sort"])
    ]

    open_query = cc.amo_open_filter(pipelines, only=pid) + [("order[updated_at]", "asc")]
    opened = reader.paged("leads", open_query, "leads", limit=OPEN_DEAL_LIMIT, page_size=AMO_PAGE, max_pages=2)
    rows = opened["items"]
    truncated = opened["truncated"]
    total, total_exact = opened["read"], True
    if not opened["complete"]:
        total, total_exact = _amo_open_total(reader, open_query, opened["read"])

    window = now - timedelta(days=stuck_days)
    changed, events_cut = _amo_changed_since(reader, window, tz)
    deals = []
    for row in rows:
        created = _parse_dt(row.get("created_at"), tz) or now
        updated = _parse_dt(row.get("updated_at"), tz) or created
        lead_id = row.get("id")
        quiet = updated < window or (not events_cut and lead_id not in changed)
        stuck = created < window and quiet
        deals.append(
            Deal(
                id=str(lead_id),
                title=str(row.get("name") or ""),
                amount=_money(row.get("price")),
                stage_id=str(row.get("status_id") or ""),
                manager_id=str(row.get("responsible_user_id") or ""),
                created=created,
                idle_days=max(stuck_days, _days(now - updated)) if stuck else _days(now - updated),
                stuck=stuck,
            )
        )
    _amo_exact_idle(reader, deals, tz, now)

    since = won_window_start(now)
    won_query = [
        ("filter[statuses][0][pipeline_id]", pid),
        ("filter[statuses][0][status_id]", 142),
        ("filter[closed_at][from]", int(since.timestamp())),
        ("order[closed_at]", "desc"),
    ]
    won_page = reader.paged("leads", won_query, "leads", limit=AMO_PAGE * 4, page_size=AMO_PAGE, max_pages=4)
    won = []
    for row in won_page["items"]:
        closed = _parse_dt(row.get("closed_at"), tz)
        if closed is not None:
            won.append(Won(_money(row.get("price")), closed, str(row.get("responsible_user_id") or "")))

    new_since = int((_midnight(now) - timedelta(days=6)).timestamp())
    new_page = reader.paged(
        "leads",
        [("filter[pipeline_id]", pid), ("filter[created_at][from]", new_since)],
        "leads",
        limit=AMO_PAGE * 4,
        page_size=AMO_PAGE,
        max_pages=4,
    )
    new_leads = [m for m in (_parse_dt(r.get("created_at"), tz) for r in new_page["items"]) if m is not None]

    unsorted = None
    try:
        data = reader.get("leads/unsorted", {"limit": 1})
        value = (data or {}).get("_total_items") if isinstance(data, dict) else None
        unsorted = int(value) if value is not None else None
    except CrmError as exc:
        if exc.code in {"network", "rate_limited", "bad_key", "budget"}:
            raise
    except (TypeError, ValueError):
        unsorted = None

    tasks_available, overdue, overdue_cut = _amo_overdue(reader, {d.id for d in deals}, now)

    users = (reader.get("users", {"limit": 250}) or {}).get("_embedded", {}).get("users") or []
    names = {str(u.get("id")): str(u.get("name") or "") for u in users if isinstance(u, dict)}

    return Raw(
        pipeline={"id": pid, "name": pipeline["name"]},
        pipelines=[{"id": p["id"], "name": p["name"]} for p in pipelines],
        stages=stages,
        deals=deals,
        deals_total=total,
        deals_total_exact=total_exact,
        deals_truncated=truncated,
        won=won,
        won_truncated=won_page["truncated"],
        new_leads=new_leads,
        unsorted=unsorted,
        tasks_available=tasks_available,
        overdue=overdue,
        overdue_truncated=overdue_cut,
        names=names,
        stuck_approx=events_cut,
    )


def _amo_open_total(reader: cr.AmoReader, query: list, read: int) -> tuple[int, bool]:
    """Open deals beyond the pages already read: count further pages within a small budget.

    Returns ``(count, exact)``; when the budget runs out first the count is a lower bound.
    """
    count = read
    for page in range(read // AMO_PAGE + 1, read // AMO_PAGE + 5):
        data = reader.get("leads", query + [("limit", AMO_PAGE), ("page", page)])
        batch = ((data or {}).get("_embedded") or {}).get("leads") or []
        count += len(batch)
        if len(batch) < AMO_PAGE:
            return count, True
    return count, False


def _amo_changed_since(reader: cr.AmoReader, window: datetime, tz) -> tuple[set, bool]:
    """Lead ids with a stage change since ``window``; second item: the log was longer than we read."""
    changed: set = set()
    base = [
        ("filter[type][]", "lead_status_changed"),
        ("filter[entity][]", "lead"),
        ("filter[created_at][from]", int(window.timestamp())),
    ]
    page_size = 100
    for page in range(1, AMO_EVENT_PAGES + 1):
        data = reader.get("events", base + [("limit", page_size), ("page", page)])
        batch = ((data or {}).get("_embedded") or {}).get("events") or []
        for event in batch:
            if isinstance(event, dict) and event.get("entity_id") is not None:
                changed.add(event["entity_id"])
        if len(batch) < page_size:
            return changed, False
    return changed, True


def _amo_exact_idle(reader: cr.AmoReader, deals: list[Deal], tz, now: datetime) -> None:
    """Exact days at the stage for the few longest-standing deals shown on the card."""
    top = sorted((d for d in deals if d.stuck), key=lambda d: -d.idle_days)[:5]
    if not top:
        return
    query = [("filter[type][]", "lead_status_changed"), ("filter[entity][]", "lead")]
    query += [("filter[entity_id][]", d.id) for d in top]
    data = reader.get("events", query + [("limit", 100)])
    latest: dict[str, datetime] = {}
    for event in ((data or {}).get("_embedded") or {}).get("events") or []:
        moment = _parse_dt(event.get("created_at"), tz)
        key = str(event.get("entity_id"))
        if moment is not None and (key not in latest or moment > latest[key]):
            latest[key] = moment
    for deal in top:
        since = latest.get(deal.id, deal.created)
        deal.idle_days = max(deal.idle_days if deal.id not in latest else 0, _days(now - since))


def _amo_overdue(reader: cr.AmoReader, deal_ids: set[str], now: datetime) -> tuple[bool, list[dict], bool]:
    now_ts = int(now.timestamp())
    out: list[dict] = []
    query = [("filter[is_completed]", 0), ("filter[entity_type]", "leads"), ("order[complete_till]", "asc")]
    try:
        for page in range(1, TASK_PAGES + 1):
            data = reader.get("tasks", query + [("limit", AMO_PAGE), ("page", page)])
            batch = ((data or {}).get("_embedded") or {}).get("tasks") or []
            for task in batch:
                if not isinstance(task, dict):
                    continue
                if (task.get("complete_till") or 0) >= now_ts:
                    return True, out, False
                if str(task.get("entity_id")) in deal_ids:
                    out.append(
                        {"manager_id": str(task.get("responsible_user_id") or ""), "deal_id": str(task["entity_id"])}
                    )
            if len(batch) < AMO_PAGE:
                return True, out, False
    except CrmError as exc:
        if exc.code != "forbidden":
            raise
        return False, [], False
    return True, out, True


# ------------------------------------------------------------------ snapshot


def _initials(name: str) -> str:
    words = [w for w in name.replace("-", " ").split() if w]
    return "".join(w[0] for w in words[:2]).upper() or "?"


def build_snapshot(
    raw: Raw,
    now: datetime,
    *,
    source: str,
    portal: str,
    stuck_days: int,
    deal_url: Callable[[Any], str],
) -> dict:
    """The card's section from neutral data. Pure: no network, no clock."""
    month_start, prev_start, prev_cut = month_bounds(now)
    cur = [w for w in raw.won if month_start <= w.ts <= now]
    prev = [w for w in raw.won if prev_start <= w.ts < prev_cut]
    cur_sum, prev_sum = sum(w.amount for w in cur), sum(w.amount for w in prev)
    change = round((cur_sum - prev_sum) / prev_sum * 100) if prev_sum > 0 else None

    weeks = []
    starts = week_starts(now)
    for i, start in enumerate(starts):
        end = starts[i + 1] if i + 1 < len(starts) else now + timedelta(days=1)
        items = [w for w in raw.won if start <= w.ts < end]
        weeks.append({"start": start.date().isoformat(), "amount": _number(sum(w.amount for w in items)), "count": len(items)})

    today = _midnight(now)
    series = [0] * 7
    for moment in raw.new_leads:
        offset = (today - _midnight(moment)).days
        if 0 <= offset < 7:
            series[6 - offset] += 1

    late_by_deal: dict[str, int] = {}
    late_by_manager: dict[str, int] = {}
    for item in raw.overdue:
        late_by_deal[item["deal_id"]] = late_by_deal.get(item["deal_id"], 0) + 1
        late_by_manager[item["manager_id"]] = late_by_manager.get(item["manager_id"], 0) + 1

    stage_names = {s["id"]: s["name"] for s in raw.stages}
    stuck_deals = [d for d in raw.deals if d.stuck]

    def view(d: Deal) -> dict:
        return {
            "id": d.id,
            "title": d.title[:90],
            "amount": _number(d.amount),
            "days": d.idle_days,
            "stuck": d.stuck,
            "late": d.id in late_by_deal,
            "new": _midnight(d.created) == today,
            "url": deal_url(d.id),
            "manager": raw.names.get(d.manager_id) or "",
            "stage": stage_names.get(d.stage_id, ""),
        }

    river_stages = []
    for stage in raw.stages:
        members = [d for d in raw.deals if d.stage_id == stage["id"]]
        river_stages.append(
            {
                "id": stage["id"],
                "name": stage["name"],
                "count": len(members),
                "amount": _number(sum(d.amount for d in members)),
                "stuck": sum(1 for d in members if d.stuck),
                "deals": [view(d) for d in sorted(members, key=lambda d: -d.amount)],
            }
        )
    busiest = max((s for s in river_stages if s["stuck"]), key=lambda s: s["stuck"], default=None)

    per_manager: dict[str, dict] = {}

    def manager(mid: str) -> dict:
        return per_manager.setdefault(
            mid,
            {"id": mid, "overdue": late_by_manager.get(mid, 0), "stuck": 0, "won_amount": 0.0, "won_count": 0},
        )

    for d in raw.deals:
        entry = manager(d.manager_id)
        entry["stuck"] += 1 if d.stuck else 0
    for mid in late_by_manager:
        manager(mid)
    for w in cur:
        entry = manager(w.manager_id)
        entry["won_amount"] += w.amount
        entry["won_count"] += 1
    per_manager.pop("", None)
    people = []
    for mid, entry in per_manager.items():
        name = raw.names.get(mid) or f"Менеджер {mid}"
        people.append(
            {
                "id": mid,
                "name": name,
                "initials": _initials(name),
                "overdue": entry["overdue"],
                "stuck": entry["stuck"],
                "won_amount": _number(entry["won_amount"]),
                "won_count": entry["won_count"],
                "leader": False,
            }
        )
    people.sort(key=lambda p: (-(p["overdue"] + p["stuck"]), -p["won_amount"], p["name"]))
    best = max(people, key=lambda p: p["won_amount"], default=None)
    if best and best["won_amount"] > 0:
        best["leader"] = True
    people = people[:8]

    top = sorted(stuck_deals, key=lambda d: (-d.idle_days, -d.amount))[:3]
    return {
        "source": source,
        "source_label": cr.SOURCE_LABELS[source],
        "portal": portal,
        "pipeline": raw.pipeline,
        "pipelines": raw.pipelines,
        "stuck_days": stuck_days,
        "won": {
            "amount": _number(cur_sum),
            "count": len(cur),
            "prev_amount": _number(prev_sum),
            "prev_count": len(prev),
            "change_pct": change,
            "weeks": weeks,
            "limited": raw.won_truncated,
        },
        "new_leads": {"today": series[6], "series": series, "unsorted": raw.unsorted},
        "stuck": {
            "count": len(stuck_deals),
            "amount": _number(sum(d.amount for d in stuck_deals)),
            "days": stuck_days,
            "approx": raw.stuck_approx,
            "top": [view(d) for d in top],
        },
        "overdue": {
            "available": raw.tasks_available,
            "tasks": len(raw.overdue),
            "managers": len(late_by_manager),
            "limited": raw.overdue_truncated,
        },
        "river": {
            "deals_total": raw.deals_total,
            "total_exact": raw.deals_total_exact,
            "deals_loaded": len(raw.deals),
            "amount_total": _number(sum(d.amount for d in raw.deals)),
            "truncated": raw.deals_truncated,
            "limit": OPEN_DEAL_LIMIT,
            "busiest_stage": busiest["name"] if busiest else None,
            "stages": river_stages,
        },
        "managers": people,
        "links": {"portal": f"https://{portal}/"},
    }


# ------------------------------------------------------------------ gathering


def installation_tz() -> Optional[tzinfo]:
    """The installation's configured zone; ``None`` falls back to the process zone."""
    try:
        from korra_time import get_timezone

        return get_timezone()
    except Exception:
        return None


def _zone_key(tz: Optional[tzinfo]) -> str:
    return str(getattr(tz, "key", None) or tz or "")


def _now(tz: Optional[tzinfo]) -> datetime:
    return datetime.now(tz) if tz else datetime.now().astimezone()


def gather(conn: dict, *, now: Optional[datetime] = None, tz: Optional[tzinfo] = None) -> dict:
    """Read the CRM and build the section. Raises :class:`CrmError`."""
    now = now or _now(tz)
    settings = cc.public(conn)["settings"]
    reader = cc.make_reader(conn)
    try:
        collect = collect_bitrix if conn["type"] == BITRIX else collect_amo
        raw = collect(reader, settings, now, tz)
        return build_snapshot(
            raw, now, source=conn["type"], portal=reader.portal, stuck_days=settings["stuck_days"], deal_url=reader.deal_url
        )
    finally:
        close = getattr(reader, "close", None)
        if close:
            close()


# ------------------------------------------------------------------ cache and background refresh


@dataclass
class _Entry:
    value: Optional[dict] = None
    at: float = 0.0
    error: Optional[dict] = None
    running: bool = False
    retry_at: float = 0.0
    done: threading.Event = field(default_factory=threading.Event)


_state: dict[Any, _Entry] = {}
_state_lock = threading.Lock()
_candidates_cache: dict[str, Any] = {"at": 0.0, "value": None}
CANDIDATES_TTL = 30.0


def reset() -> None:
    with _state_lock:
        _state.clear()
    _candidates_cache.update(at=0.0, value=None)


def _spawn(target: Callable[[], None]) -> None:
    threading.Thread(target=target, name="crm-sales-refresh", daemon=True).start()


def _clock() -> float:
    return time.time()


def _iso_ts(text: Optional[str]) -> float:
    try:
        return datetime.fromisoformat(text).timestamp() if text else 0.0
    except ValueError:
        return 0.0


def _stamp_text(ts: float) -> str:
    return datetime.fromtimestamp(ts).astimezone().isoformat(timespec="seconds")


def _refresh(entry: "_Entry", conn: dict, tz: Optional[tzinfo]) -> None:
    try:
        value = gather(conn, tz=tz)
        with _state_lock:
            entry.value, entry.at, entry.error, entry.retry_at = value, _clock(), None, 0.0
    except CrmError as exc:
        info = cr.describe_error(exc.code, conn["type"], cc.portal_of(conn))
        with _state_lock:
            entry.error = info
            entry.retry_at = _clock() + (RETRY_AFTER_RATE_LIMIT if exc.code == "rate_limited" else RETRY_AFTER_FAILURE)
        logger.info("crm sales refresh failed: %s", exc.code)
    except Exception:
        logger.exception("crm sales refresh crashed")
        with _state_lock:
            entry.error = cr.describe_error("protocol", conn["type"])
            entry.retry_at = _clock() + RETRY_AFTER_FAILURE
    finally:
        with _state_lock:
            entry.running = False
        entry.done.set()


def _render(conn: dict, entry: _Entry) -> dict:
    out: dict[str, Any] = {"connection": cc.public(conn)}
    if entry.value is not None:
        out.update(entry.value)
        out["status"] = "ok"
        out["as_of"] = _stamp_text(entry.at)
        out["stale"] = entry.error is not None
        if entry.error:
            out["error"] = entry.error
    elif entry.error is not None:
        out["status"] = "error"
        out["error"] = entry.error
    else:
        out["status"] = "loading"
    return out


def sales_section(*, root: Optional[Path] = None, tz: Optional[tzinfo] = None, wait: float = FIRST_WAIT_SECONDS) -> dict:
    """What ``/api/dashboard/state`` and the agent tool show. Never raises for CRM trouble.

    The installation's zone is applied here, so every caller gets the same «сегодня»
    and month boundary; ``tz`` only overrides it.
    """
    try:
        conn = cc.load(root)
    except cc.CrmConnectionError:
        return {"status": "error", "error": {"code": "storage", "title": "Хранилище недоступно",
                                                "message": "Не удалось прочитать подключение CRM.", "retry": False}}
    if conn is None:
        now = _clock()
        with _state_lock:
            if _candidates_cache["value"] is None or now - _candidates_cache["at"] > CANDIDATES_TTL:
                _candidates_cache.update(at=now, value=cc.candidates())
            candidates = list(_candidates_cache["value"])
        return {"status": "not_connected", "candidates": candidates}

    tz = tz or installation_tz()
    key = (str(root or ""), cc.identity(conn), _zone_key(tz))
    check = cc.public(conn)["last_check"]
    now = _clock()
    with _state_lock:
        for old in [k for k in _state if k[0] == key[0] and k != key]:
            del _state[old]
        entry = _state.setdefault(key, _Entry())
        blocked = check["code"] in {"bad_key", "plan_closed"} and entry.value is None
        fresh = entry.value is not None and now - entry.at < TTL_SECONDS
        start = not (fresh or entry.running or blocked or now < entry.retry_at)
        if start:
            entry.running = True
            entry.done.clear()
    with _state_lock:
        failed_after_read = (
            entry.value is not None and not check["ok"] and check["code"]
            and _iso_ts(check["at"]) >= int(entry.at)
        )
        if (blocked or failed_after_read) and entry.error is None:
            entry.error = cr.describe_error(check["code"], conn["type"], cc.portal_of(conn))
    if start:
        _spawn(lambda: _refresh(entry, conn, tz))
    if entry.value is None and entry.error is None and entry.running:
        entry.done.wait(wait)
    with _state_lock:
        return _render(conn, entry)

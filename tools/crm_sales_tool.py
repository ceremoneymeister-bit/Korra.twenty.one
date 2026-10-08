"""Read the owner's sales in Битрикс24 or amoCRM through the installation's connection.

Same contract as ``google_calendar``: the tool is offered to owner turns only,
and **every call** re-checks the principal (:mod:`gateway.principal`) and the
connection's «доступ агентам» switch. The key never reaches the model: the tool
returns figures and links, and when nothing is connected it points the owner to
the connection window instead of asking for a key in the chat. Read-only.
"""

from __future__ import annotations

import json
from typing import Any

from gateway.principal import current_principal
from korra_cli import crm_connection, crm_readers, crm_sales
from tools.registry import no_cache_check_fn, registry

CONNECT_LINK = "/?crm=connect"
MAX_STUCK = 25

_OWNER_ONLY = (
    "Продажи компании доступны только в личном разговоре владельца (кабинет Korra, компьютер владельца "
    "или личный чат владельца с ботом). Не пересказывайте и не угадывайте данные CRM здесь; "
    "скажите, что не можете их открыть."
)
_NO_KEY_IN_CHAT = "Никогда не просите ключ, вебхук или токен CRM в чате и не принимайте их из чата."
_NEXT = {
    "not_connected": (
        f"CRM не подключена. Скажите владельцу открыть окно подключения: {CONNECT_LINK} "
        f"(карточка «Продажи» на главной → «Подключить CRM»). {_NO_KEY_IN_CHAT}"
    ),
    "agents_off": (
        "Владелец не открыл CRM агентам. Он включает это в карточке «Продажи» → «⋯» → «Доступ агентам». "
        + _NO_KEY_IN_CHAT
    ),
    "bad_key": f"CRM отклонила ключ. Владелец заменяет его в окне {CONNECT_LINK}. {_NO_KEY_IN_CHAT}",
    "plan_closed": "Тариф Битрикс24 закрывает REST: нужна подписка «Маркетплейс». Скажите об этом владельцу.",
    "retry": "CRM сейчас не отвечает или просит подождать; предложите повторить чуть позже.",
}


def _error(code: str, message: str, step: str = "") -> str:
    payload: dict[str, Any] = {"ok": False, "error": code, "message": message}
    if step:
        payload["next_step"] = step
    return json.dumps(payload, ensure_ascii=False)


def _snapshot() -> dict | str:
    section = crm_sales.sales_section()
    if section.get("status") == "ok":
        return section
    error = section.get("error") or {}
    code = error.get("code")
    step = _NEXT.get(code, _NEXT["retry"])
    return _error(code or "unavailable", error.get("message") or "Данные CRM пока недоступны.", step)


def _freshness(section: dict) -> dict:
    """The same marks in every summary action: when the figures are from, and why they may be old."""
    out: dict[str, Any] = {"as_of": section.get("as_of"), "stale": bool(section.get("stale"))}
    if out["stale"]:
        error = section.get("error") or {}
        out["error"] = {"code": error.get("code"), "message": error.get("message")}
        out["note"] = "Свежие данные получить не удалось: это последние известные, «данные от as_of»."
    return out


def _overview(section: dict) -> dict:
    keep = ("source_label", "portal", "pipeline", "stuck_days", "won", "new_leads", "stuck", "overdue", "managers")
    out = {key: section[key] for key in keep if key in section}
    out.update(_freshness(section))
    river = section.get("river") or {}
    out["river"] = [
        {"stage": s["name"], "deals": s["count"], "amount": s["amount"], "stuck": s["stuck"]}
        for s in river.get("stages") or []
    ]
    if river.get("truncated"):
        out["limited"] = f"Показаны первые {river.get('deals_loaded')} сделок из {river.get('deals_total')}."
    return out


def _stuck(section: dict, limit: int) -> dict:
    rows = [
        {**deal, "stage": stage["name"]}
        for stage in (section.get("river") or {}).get("stages") or []
        for deal in stage["deals"]
        if deal.get("stuck")
    ]
    rows.sort(key=lambda d: -(d.get("days") or 0))
    out = {"stuck_days": section.get("stuck_days"), "count": (section.get("stuck") or {}).get("count"),
           "deals": rows[:limit], **_freshness(section)}
    if (section.get("stuck") or {}).get("approx"):
        out["note"] = "Журнал событий amoCRM обрезан: список может быть неполным."
    return out


def _deal(conn: dict, deal_id: str) -> dict:
    reader = crm_connection.make_reader(conn)
    try:
        if conn["type"] == crm_readers.BITRIX:
            data = reader.call("crm.deal.get", {"id": deal_id}).get("result") or {}
            deal = {"id": str(data.get("ID") or deal_id), "title": data.get("TITLE"),
                    "amount": data.get("OPPORTUNITY"), "stage_id": data.get("STAGE_ID"),
                    "manager_id": data.get("ASSIGNED_BY_ID"), "created": data.get("DATE_CREATE"),
                    "moved": data.get("MOVED_TIME"), "closed": data.get("CLOSED") == "Y"}
        else:
            data = reader.get(f"leads/{deal_id}")
            deal = {"id": str(data.get("id") or deal_id), "title": data.get("name"), "amount": data.get("price"),
                    "stage_id": data.get("status_id"), "manager_id": data.get("responsible_user_id"),
                    "created": data.get("created_at"), "updated": data.get("updated_at"),
                    "closed": data.get("closed_at") is not None}
    finally:
        close = getattr(reader, "close", None)
        if close:
            close()
    deal["url"] = reader.deal_url(deal["id"])
    return deal


def _handle(args: dict, **_kwargs) -> str:
    args = args or {}
    if not current_principal().owner:
        return _error("owner_only", _OWNER_ONLY)
    action = str(args.get("action") or "overview").strip().lower()
    if action not in {"overview", "stuck", "deal"}:
        return _error("invalid_request", "Неизвестное действие: overview, stuck или deal.")
    try:
        conn = crm_connection.load()
    except crm_connection.CrmConnectionError as exc:
        return _error(exc.code, str(exc))
    if conn is None:
        return _error("not_connected", "CRM не подключена.", _NEXT["not_connected"])
    if crm_connection.agent_access() is None:
        return _error("agents_off", "Владелец не открыл CRM агентам.", _NEXT["agents_off"])
    try:
        if action == "deal":
            deal_id = str(args.get("deal_id") or "").strip()
            if not deal_id.isdigit() or len(deal_id) > 12:
                return _error("invalid_request", "Нужен числовой deal_id.")
            return json.dumps({"ok": True, "deal": _deal(conn, deal_id)}, ensure_ascii=False)
        section = _snapshot()
        if isinstance(section, str):
            return section
        if action == "stuck":
            try:
                limit = max(1, min(int(args.get("limit") or 10), MAX_STUCK))
            except (TypeError, ValueError):
                limit = 10
            return json.dumps({"ok": True, **_stuck(section, limit)}, ensure_ascii=False)
        return json.dumps({"ok": True, **_overview(section)}, ensure_ascii=False)
    except crm_readers.CrmError as exc:
        info = crm_readers.describe_error(exc.code, conn["type"], crm_connection.portal_of(conn))
        step = _NEXT.get(exc.code, _NEXT["retry"] if info["retry"] else "")
        return _error(exc.code, info["message"], step)


@no_cache_check_fn
def _crm_available() -> bool:
    """Owner turns only; uncached because the answer depends on who is speaking."""
    return current_principal().owner


registry.register(
    name="crm_sales",
    toolset="crm_sales",
    schema={
        "name": "crm_sales",
        "description": (
            "Read the company's sales from the Битрикс24 or amoCRM the owner connected in Korra (read-only). "
            "action=overview (default): won this month vs the same stretch of the last one, new leads, stuck deals, "
            "overdue tasks, managers, stages. action=stuck: the longest-stuck deals with links. "
            "action=deal with deal_id: one deal. Figures may be from the last successful read (see as_of). "
            "If the CRM is not connected, send the owner to the connection window from next_step. "
            "NEVER ask for or accept a CRM key, webhook or token in the chat."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["overview", "stuck", "deal"]},
                "limit": {"type": "integer", "minimum": 1, "maximum": MAX_STUCK, "description": "stuck: how many deals."},
                "deal_id": {"type": "string", "description": "deal: numeric deal id."},
            },
            "additionalProperties": False,
        },
    },
    handler=_handle,
    check_fn=_crm_available,
    description="Owner's sales in Битрикс24 / amoCRM for all agents on this installation.",
    emoji="📈",
)

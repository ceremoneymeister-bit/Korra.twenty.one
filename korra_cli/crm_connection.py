"""The installation's CRM connection (Битрикс24 or amoCRM).

One connection per installation, owned by the owner and used by the dashboard
card and, on the owner's request, by any agent (``tools/crm_sales_tool.py``).
It lives in ``<root>/crm-connection/connection.json`` (directory 0700, file
0600), outside profiles and ``config.yaml``, so a profile backup or a shared
config never carries the key. Like the Google sharing, access for agents is a
switch on the connection («все агенты») and each turn is additionally checked
against :mod:`gateway.principal` by the tool.

The secret (webhook address or token) is read only by :func:`load` for the
reader factory; everything that leaves this module goes through
:func:`public`, a whitelist that has no place for it.
"""

from __future__ import annotations

import base64
import json
import os
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from korra_constants import get_default_hermes_root
from utils import atomic_json_write

from korra_cli import crm_readers as cr
from korra_cli.crm_readers import AMOCRM, BITRIX, CrmError

STORE_DIR = "crm-connection"
STORE_FILE = "connection.json"
VERSION = 1
DEFAULT_STUCK_DAYS = 7
MAX_STUCK_DAYS = 90
AMO_PROBE_PAGES = 4
_PROBE_DEAL_CAP = AMO_PROBE_PAGES * 250

_lock = threading.RLock()


class CrmConnectionError(RuntimeError):
    """Storage or request-shape trouble (not a CRM answer)."""

    def __init__(self, code: str, message: str, status_code: int = 422) -> None:
        self.code = code
        self.status_code = status_code
        super().__init__(message)


# ------------------------------------------------------------------ storage


def _path(root: Optional[Path] = None) -> Path:
    folder = Path(root or get_default_hermes_root()) / STORE_DIR
    if folder.is_symlink() or (folder / STORE_FILE).is_symlink():
        raise CrmConnectionError("unsafe_storage", "Хранилище подключения CRM небезопасно.", 409)
    return folder / STORE_FILE


def load(root: Optional[Path] = None) -> Optional[dict]:
    """The stored connection **with its secret**. Internal use only."""
    path = _path(root)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        raise CrmConnectionError("storage_error", "Не удалось прочитать подключение CRM.", 500) from exc
    if not isinstance(raw, dict) or raw.get("type") not in (BITRIX, AMOCRM):
        raise CrmConnectionError("storage_error", "Подключение CRM повреждено.", 500)
    if raw["type"] == BITRIX and not isinstance(raw.get("webhook_url"), str):
        raise CrmConnectionError("storage_error", "Подключение CRM повреждено.", 500)
    if raw["type"] == AMOCRM and not (isinstance(raw.get("domain"), str) and isinstance(raw.get("token"), str)):
        raise CrmConnectionError("storage_error", "Подключение CRM повреждено.", 500)
    return raw


def stamp(root: Optional[Path] = None) -> tuple[int, int]:
    """Changes whenever the connection file is rewritten; keys the card cache."""
    try:
        st = _path(root).stat()
    except (OSError, CrmConnectionError):
        return (0, 0)
    return (st.st_mtime_ns, st.st_size)


def _write(conn: dict, root: Optional[Path]) -> None:
    path = _path(root)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    atomic_json_write(path, conn, mode=0o600)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _settings(conn: dict) -> dict:
    raw = conn.get("settings") if isinstance(conn.get("settings"), dict) else {}
    days = raw.get("stuck_days")
    return {
        "pipeline_id": str(raw.get("pipeline_id") or ""),
        "stuck_days": days if isinstance(days, int) and 1 <= days <= MAX_STUCK_DAYS else DEFAULT_STUCK_DAYS,
        "agents_access": raw.get("agents_access") is not False,
    }


def portal_of(conn: dict) -> str:
    if conn["type"] == BITRIX:
        return cr.parse_bitrix_webhook(conn["webhook_url"])[0]
    return cr.parse_amo_domain(conn["domain"])


def public(conn: dict) -> dict:
    """Whitelisted view of a connection; there is no field here that can hold a secret."""
    account = conn.get("account") if isinstance(conn.get("account"), dict) else {}
    check = conn.get("last_check") if isinstance(conn.get("last_check"), dict) else {}
    try:
        portal = portal_of(conn)
    except CrmError:
        portal = ""
    return {
        "type": conn["type"],
        "source_label": cr.SOURCE_LABELS[conn["type"]],
        "portal": portal,
        "route": "socket" if conn.get("unix_socket") else "direct",
        "account": {
            "user": str(account.get("user") or "")[:120],
            "deals": account.get("deals") if isinstance(account.get("deals"), int) else None,
            "deals_capped": bool(account.get("deals_capped")),
            "managers": account.get("managers") if isinstance(account.get("managers"), int) else None,
            "tasks": account.get("tasks") is not False,
            "pipelines": [
                {"id": str(p.get("id")), "name": str(p.get("name") or "")[:120]}
                for p in (account.get("pipelines") or [])
                if isinstance(p, dict)
            ],
        },
        "settings": _settings(conn),
        "last_check": {
            "ok": check.get("ok") is True,
            "at": check.get("at") if isinstance(check.get("at"), str) else None,
            "code": check.get("code") if isinstance(check.get("code"), str) else None,
        },
        "saved_at": conn.get("saved_at") if isinstance(conn.get("saved_at"), str) else None,
    }


# ------------------------------------------------------------------ readers


def make_reader(conn: dict):
    """A reader for a stored (or just-typed) connection."""
    if conn["type"] == BITRIX:
        return cr.Bitrix24Reader(conn["webhook_url"])
    return cr.AmoReader(conn["domain"], conn["token"], unix_socket=conn.get("unix_socket") or "")


def failure(err: CrmError, conn_type: str, host: str = "", *, saved: bool = False) -> dict:
    info = cr.describe_error(err.code, conn_type, host)
    info["saved"] = saved
    return {"ok": False, "error": info}


# ------------------------------------------------------------------ probe


def bitrix_pipelines(reader: cr.Bitrix24Reader) -> list[dict]:
    try:
        result = reader.call("crm.category.list", {"entityTypeId": 2}).get("result")
        rows = result.get("categories") if isinstance(result, dict) else result
        pipelines = [{"id": str(r.get("id")), "name": r.get("name") or ""} for r in rows or [] if isinstance(r, dict)]
    except CrmError as exc:
        if exc.code in {"bad_key", "network", "rate_limited", "budget", "plan_closed", "redirect"}:
            raise
        pipelines = []
    if not pipelines:
        rows = reader.call("crm.dealcategory.list").get("result")
        pipelines = [{"id": str(r.get("ID")), "name": r.get("NAME") or ""} for r in rows or [] if isinstance(r, dict)]
    if not any(p["id"] == "0" for p in pipelines):
        pipelines.insert(0, {"id": "0", "name": "Общая"})
    return pipelines


def _probe_bitrix(reader: cr.Bitrix24Reader) -> dict:
    profile = reader.call("profile").get("result")
    profile = profile if isinstance(profile, dict) else {}
    user = " ".join(str(profile.get(k) or "") for k in ("NAME", "LAST_NAME")).strip()
    deals = reader.call("crm.deal.list", {"filter": {"STAGE_SEMANTIC_ID": "P"}, "select": ["ID"]})
    pipelines = bitrix_pipelines(reader)
    try:
        managers: Optional[int] = int(reader.call("user.get", {"filter": {"ACTIVE": "true"}}).get("total"))
    except (CrmError, TypeError, ValueError) as exc:
        if isinstance(exc, CrmError) and exc.code in {"network", "rate_limited", "bad_key", "plan_closed"}:
            raise
        managers = None
    tasks = True
    try:
        reader.call("tasks.task.list", {"select": ["ID"], "filter": {"REAL_STATUS": 2}})
    except CrmError as exc:
        if exc.code != "forbidden":
            raise
        tasks = False
    return {
        "user": user,
        "deals": int(deals.get("total") or 0),
        "deals_capped": False,
        "managers": managers,
        "tasks": tasks,
        "pipelines": pipelines,
    }


def amo_pipelines(data: Any) -> list[dict]:
    rows = ((data or {}).get("_embedded") or {}).get("pipelines") or []
    out = []
    for row in rows:
        if not isinstance(row, dict) or row.get("type") == 1:
            continue
        statuses = [
            {"id": s.get("id"), "name": s.get("name") or "", "sort": s.get("sort") or 0}
            for s in ((row.get("_embedded") or {}).get("statuses") or [])
            if isinstance(s, dict) and s.get("id") not in (142, 143) and s.get("type") != 1
        ]
        out.append(
            {
                "id": str(row.get("id")),
                "name": row.get("name") or "",
                "is_main": bool(row.get("is_main")),
                "sort": row.get("sort") or 0,
                "statuses": statuses,
            }
        )
    return out


def _jwt_subject(token: str) -> Optional[int]:
    try:
        body = token.split(".")[1]
        payload = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
        return int(payload.get("sub"))
    except (IndexError, ValueError, TypeError, AttributeError):
        return None


def _probe_amo(reader: cr.AmoReader, token: str) -> dict:
    account = reader.get("account") or {}
    pipelines = amo_pipelines(reader.get("leads/pipelines"))
    users = (reader.get("users", {"limit": 250}) or {}).get("_embedded", {}).get("users") or []
    me = _jwt_subject(token)
    user = next((u.get("name") for u in users if isinstance(u, dict) and u.get("id") == me), None)
    query = amo_open_filter(pipelines)
    deals = 0
    capped = False
    if query:
        page = reader.paged("leads", query, "leads", limit=_PROBE_DEAL_CAP, page_size=250, max_pages=AMO_PROBE_PAGES)
        deals, capped = len(page["items"]), page["truncated"]
    return {
        "user": user or str(account.get("name") or ""),
        "deals": deals,
        "deals_capped": capped,
        "managers": len(users),
        "tasks": True,
        "pipelines": [{"id": p["id"], "name": p["name"], "is_main": p["is_main"]} for p in pipelines],
        "account_name": str(account.get("name") or ""),
    }


def amo_open_filter(pipelines: list[dict], only: Optional[str] = None) -> list[tuple[str, Any]]:
    """Query pairs selecting the deals that are still open in the given pipelines."""
    pairs: list[tuple[str, Any]] = []
    i = 0
    for pipe in pipelines:
        if only is not None and pipe["id"] != only:
            continue
        for status in pipe.get("statuses") or []:
            pairs.append((f"filter[statuses][{i}][pipeline_id]", pipe["id"]))
            pairs.append((f"filter[statuses][{i}][status_id]", status["id"]))
            i += 1
    return pairs


def probe(conn: dict) -> dict:
    """Ask the CRM who we are and what is there. Raises :class:`CrmError`."""
    reader = make_reader(conn)
    try:
        if conn["type"] == BITRIX:
            found = _probe_bitrix(reader)
        else:
            found = _probe_amo(reader, conn["token"])
    finally:
        close = getattr(reader, "close", None)
        if close:
            close()
    return found


def _main_pipeline(found: dict) -> str:
    pipes = found.get("pipelines") or []
    main = next((p for p in pipes if p.get("is_main")), None) or (pipes[0] if pipes else None)
    return str(main["id"]) if main else ""


# ------------------------------------------------------------------ requests


def _text(value: Any, limit: int) -> str:
    return value.strip()[:limit] if isinstance(value, str) else ""


def _route(socket_path: str) -> str:
    if not socket_path:
        return ""
    if (
        not os.path.isabs(socket_path)
        or "\0" in socket_path
        or len(socket_path) > 240
        or os.path.normpath(socket_path) != socket_path
    ):
        raise CrmError("bad_url")
    return socket_path


def build(payload: Any, *, socket_path: str = "") -> dict:
    """A connection record from what the owner typed. Raises :class:`CrmError` for a bad address/key.

    The local socket route is never taken from ``payload`` (it comes from the HTTP
    API): only from an agent's ``.env`` being adopted or from the route already
    saved for the same account, passed in ``socket_path``.
    """
    if not isinstance(payload, dict):
        raise CrmConnectionError("invalid_request", "Не удалось разобрать запрос.")
    kind = payload.get("type")
    if kind == BITRIX:
        url = _text(payload.get("webhook_url"), 400)
        cr.parse_bitrix_webhook(url)
        return {"type": BITRIX, "webhook_url": url}
    if kind == AMOCRM:
        domain = cr.parse_amo_domain(_text(payload.get("domain"), 200))
        token = _text(payload.get("token"), 8192)
        if not token or re.search(r"\s", token):
            raise CrmError("bad_key")
        conn = {"type": AMOCRM, "domain": domain, "token": token}
        route = _route(socket_path)
        if route:
            conn["unix_socket"] = route
        return conn
    raise CrmConnectionError("invalid_request", "Выберите Битрикс24 или amoCRM.")


def _host_hint(payload: Any) -> str:
    try:
        return portal_of(build(payload))
    except Exception:
        return ""


def check(payload: Any, *, root: Optional[Path] = None) -> dict:
    """Check what was typed (or, with an empty payload, the saved connection). Saves nothing."""
    kind = payload.get("type") if isinstance(payload, dict) else None
    try:
        if not payload or not kind:
            return recheck(root=root)
        conn = build(payload)
        found = probe(conn)
    except CrmError as exc:
        return failure(exc, kind if kind in (BITRIX, AMOCRM) else BITRIX, _host_hint(payload))
    return {"ok": True, "found": _found_view(conn, found)}


def _found_view(conn: dict, found: dict) -> dict:
    return {
        "type": conn["type"],
        "source_label": cr.SOURCE_LABELS[conn["type"]],
        "portal": portal_of(conn),
        "user": found.get("user") or "",
        "deals": found.get("deals"),
        "deals_capped": bool(found.get("deals_capped")),
        "managers": found.get("managers"),
        "tasks": found.get("tasks") is not False,
        "pipelines": [{"id": str(p["id"]), "name": p["name"]} for p in found.get("pipelines") or []],
        "pipeline_id": _main_pipeline(found),
    }


def _account_record(found: dict) -> dict:
    return {
        key: found.get(key)
        for key in ("user", "deals", "deals_capped", "managers", "tasks", "pipelines", "account_name")
        if key in found
    }


def save(payload: Any, *, root: Optional[Path] = None, socket_path: str = "") -> dict:
    """Save a new or replacement connection.

    A key the CRM rejects is never kept. A network failure or a request limit
    is not the key's fault, so the connection is saved and checked again later.
    """
    try:
        conn = build(payload, socket_path=socket_path)
    except CrmError as exc:
        kind = payload.get("type") if isinstance(payload, dict) else None
        return failure(exc, kind if kind in (BITRIX, AMOCRM) else BITRIX, _host_hint(payload))
    kind = conn["type"]
    host = portal_of(conn)
    found: Optional[dict] = None
    warning: Optional[dict] = None
    try:
        found = probe(conn)
    except CrmError as exc:
        if exc.code not in {"network", "rate_limited", "budget", "limit"}:
            return failure(exc, kind, host)
        warning = failure(exc, kind, host, saved=True)["error"]
    with _lock:
        previous = load(root)
        keep = previous is not None and previous["type"] == kind and portal_of(previous) == host
        settings = _settings(previous) if keep else _settings({})
        requested = payload.get("settings") if isinstance(payload.get("settings"), dict) else {}
        settings = _merge_settings(settings, requested, found)
        if not settings["pipeline_id"] and found:
            settings["pipeline_id"] = _main_pipeline(found)
        record = dict(conn)
        record.update(
            version=VERSION,
            settings=settings,
            account=_account_record(found) if found else (previous or {}).get("account", {}) if keep else {},
            last_check={
                "ok": found is not None,
                "at": _now(),
                "code": None if found is not None else (warning or {}).get("code"),
            },
            saved_at=_now(),
        )
        _write(record, root)
    result = {"ok": True, "connection": public(record)}
    if warning:
        result["warning"] = warning
    return result


def _merge_settings(current: dict, patch: Any, found: Optional[dict]) -> dict:
    out = dict(current)
    if not isinstance(patch, dict):
        return out
    if "pipeline_id" in patch:
        pipeline = str(patch["pipeline_id"] or "").strip()
        known = {str(p["id"]) for p in (found or {}).get("pipelines") or []}
        if pipeline and not re.fullmatch(r"\d{1,12}", pipeline):
            raise CrmConnectionError("invalid_request", "Не удалось разобрать воронку.")
        if pipeline and known and pipeline not in known:
            raise CrmConnectionError("invalid_request", "Такой воронки нет в CRM.")
        out["pipeline_id"] = pipeline
    if "stuck_days" in patch:
        days = patch["stuck_days"]
        if isinstance(days, bool) or not isinstance(days, int) or not 1 <= days <= MAX_STUCK_DAYS:
            raise CrmConnectionError("invalid_request", f"«Застряла» — от 1 до {MAX_STUCK_DAYS} дней.")
        out["stuck_days"] = days
    if "agents_access" in patch:
        if not isinstance(patch["agents_access"], bool):
            raise CrmConnectionError("invalid_request", "Доступ агентам: да или нет.")
        out["agents_access"] = patch["agents_access"]
    return out


def update_settings(patch: Any, *, root: Optional[Path] = None) -> dict:
    with _lock:
        conn = load(root)
        if conn is None:
            raise CrmConnectionError("not_connected", "CRM не подключена.", 404)
        known = {"pipelines": (conn.get("account") or {}).get("pipelines") or []}
        conn["settings"] = _merge_settings(_settings(conn), patch, known)
        _write(conn, root)
        return public(conn)


def recheck(*, root: Optional[Path] = None) -> dict:
    """Check the saved connection against the CRM and record the outcome."""
    with _lock:
        conn = load(root)
    if conn is None:
        raise CrmConnectionError("not_connected", "CRM не подключена.", 404)
    host = portal_of(conn)
    try:
        found = probe(conn)
    except CrmError as exc:
        _record_check(root, conn, ok=False, code=exc.code)
        return failure(exc, conn["type"], host, saved=True)
    with _lock:
        fresh = load(root)
        if fresh is not None and portal_of(fresh) == host:
            fresh["account"] = _account_record(found)
            settings = _settings(fresh)
            if not settings["pipeline_id"] or settings["pipeline_id"] not in {str(p["id"]) for p in found["pipelines"]}:
                settings["pipeline_id"] = _main_pipeline(found)
            fresh["settings"] = settings
            fresh["last_check"] = {"ok": True, "at": _now(), "code": None}
            _write(fresh, root)
            view = {"ok": True, "found": _found_view(fresh, found), "connection": public(fresh)}
            return view
    return {"ok": True, "found": _found_view(conn, found)}


def _record_check(root: Optional[Path], conn: dict, *, ok: bool, code: Optional[str]) -> None:
    with _lock:
        fresh = load(root)
        if fresh is None or portal_of(fresh) != portal_of(conn):
            return
        fresh["last_check"] = {"ok": ok, "at": _now(), "code": code}
        _write(fresh, root)


def disconnect(*, root: Optional[Path] = None) -> dict:
    with _lock:
        path = _path(root)
        path.unlink(missing_ok=True)
        try:
            path.parent.rmdir()
        except OSError:
            pass
    return {"state": "not_connected"}


def status(*, root: Optional[Path] = None, with_candidates: bool = True) -> dict:
    conn = load(root)
    if conn is None:
        out: dict = {"state": "not_connected"}
        if with_candidates:
            out["candidates"] = candidates()
        return out
    return {"state": "connected", "connection": public(conn)}


def agent_access(root: Optional[Path] = None) -> Optional[dict]:
    """The stored connection if agents may use it, else ``None``."""
    conn = load(root)
    if conn is None or not _settings(conn)["agents_access"]:
        return None
    return conn


# ------------------------------------------------------------------ taking over an agent's key


def _read_env(path: Path) -> dict[str, str]:
    from korra_cli.config import _parse_env_value

    try:
        text = path.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return {}
    values: dict[str, str] = {}
    for line in text.replace("\r\n", "\n").split("\n"):
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[7:]
        key, _, value = line.partition("=")
        values[key.strip()] = _parse_env_value(value)
    return values


def _env_connection(values: dict[str, str]) -> list[dict]:
    found = []
    webhook = (values.get("BITRIX24_WEBHOOK_URL") or "").strip()
    if webhook:
        try:
            cr.parse_bitrix_webhook(webhook)
            found.append({"type": BITRIX, "webhook_url": webhook})
        except CrmError:
            pass
    domain = (values.get("AMOCRM_DOMAIN") or "").strip()
    token = (values.get("AMOCRM_LONG_TERM_TOKEN") or "").strip()
    if domain and token:
        entry = {"type": AMOCRM, "domain": domain, "token": token}
        socket_path = (values.get("AMOCRM_UNIX_SOCKET") or "").strip()
        try:
            build(entry, socket_path=socket_path)
            if socket_path:
                entry["unix_socket"] = socket_path
            found.append(entry)
        except CrmError:
            pass
    return found


def _agent_keys() -> list[tuple[Any, dict]]:
    from korra_cli import dashboard_state

    out = []
    for agent in dashboard_state.list_agents():
        for entry in _env_connection(_read_env(Path(agent.home) / ".env")):
            out.append((agent, entry))
    return out


def candidates() -> list[dict]:
    """Agent keys that could become the installation's connection. No secret in the answer."""
    seen = []
    for agent, entry in _agent_keys():
        try:
            portal = portal_of(entry)
        except CrmError:
            continue
        seen.append(
            {
                "profile": agent.profile,
                "label": agent.label,
                "type": entry["type"],
                "source_label": cr.SOURCE_LABELS[entry["type"]],
                "portal": portal,
            }
        )
    return seen


def adopt(profile: Any, kind: Any, *, root: Optional[Path] = None) -> dict:
    """Copy an agent's key into the installation connection. The agent's ``.env`` is not touched."""
    for agent, entry in _agent_keys():
        if agent.profile == profile and entry["type"] == kind:
            return save(entry, root=root, socket_path=entry.get("unix_socket") or "")
    raise CrmConnectionError("not_found", "У этого агента нет ключа этой CRM.", 404)

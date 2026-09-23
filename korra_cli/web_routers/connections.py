"""Owner's connected services and the dashboard calendar feed.

``GET /api/connections`` is the installation-wide, secret-free view behind the
«Подключённые сервисы» section of «Ключи и доступы»: which agent holds a Google grant, which agents borrow it and
what each of them can actually do with it.  Changes still go through the
existing profile-scoped routes (``/api/google-workspace/start|complete|
cancel|revoke`` and ``PUT /api/google-workspace/sharing``), so this module
adds no new way to mutate credentials.

``GET /api/dashboard/calendar`` feeds the dashboard «Календарь» card.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from korra_cli import dashboard_calendar
from korra_cli import icloud_calendar
from korra_cli import google_workspace as google
from korra_cli.web_deps import late


router = APIRouter()

_list_cron_jobs_sync = late("_list_cron_jobs_sync")

# Where the bundled Google Workspace skill lands in a profile.  Curated agents
# created from ready-made packages carry only their own skills, so for them
# Gmail/Drive/Sheets through the skill are not available even when the grant
# is shared.  The calendar tool does not depend on the skill.
_WORKSPACE_SKILL_PATHS = (
    Path("skills") / "productivity" / "google-workspace" / "SKILL.md",
    Path("skills") / "google-workspace" / "SKILL.md",
)


def _has_workspace_skill(home: Path) -> bool:
    try:
        return any((home / relative).is_file() for relative in _WORKSPACE_SKILL_PATHS)
    except OSError:
        return False


#: Chat surfaces whose toolset lists decide whether an agent has the calendar
#: tool: the cabinet chat and the messaging bots.
_CHAT_PLATFORMS = ("api_server", "telegram")


def _calendar_tool_enabled(home: Path) -> bool:
    """Whether the profile's own toolset lists include ``google_calendar``.

    A profile on the default lists has it; a restricted profile with an
    explicit list has it only when the list names it.
    """
    from korra_constants import reset_hermes_home_override, set_hermes_home_override

    token = set_hermes_home_override(str(home))
    try:
        from korra_cli.config import load_config_readonly
        from korra_cli.tools_config import _get_platform_tools

        config = load_config_readonly()
        return any("google_calendar" in _get_platform_tools(config, platform) for platform in _CHAT_PLATFORMS)
    except Exception:
        return False
    finally:
        reset_hermes_home_override(token)


def _connections_snapshot() -> dict[str, Any]:
    from korra_cli import profiles as profile_store

    snapshot = google.overview()
    app_ready = bool(snapshot["app"].get("configured"))
    for row in snapshot["profiles"]:
        home = google._profile_home_for_name(row["profile"])
        meta = profile_store.read_profile_meta(home)
        row["label"] = meta.get("display_name") or ""
        row["tools"] = {
            # The calendar tool needs the installation app and the profile's
            # toolset lists to include it; who may use it is decided per call.
            "calendar": app_ready and _calendar_tool_enabled(home),
            "workspace_skill": _has_workspace_skill(home),
        }
    return {"google": snapshot}


@router.get("/api/connections")
async def connections_overview():
    try:
        return await asyncio.to_thread(_connections_snapshot)
    except google.GoogleWorkspaceError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail={"code": exc.code, "message": str(exc)},
        ) from exc


@router.get("/api/dashboard/calendar")
async def dashboard_calendar_feed(refresh: bool = False):
    return await asyncio.to_thread(
        dashboard_calendar.build_feed,
        jobs_loader=lambda: _list_cron_jobs_sync("all"),
        refresh=refresh,
    )


class ICloudConnectBody(BaseModel):
    username: str
    app_password: str


def _icloud_call(callback, *args, **kwargs):
    try:
        return callback(*args, **kwargs)
    except icloud_calendar.ICloudCalendarError as exc:
        raise HTTPException(status_code=exc.status_code, detail={"code": exc.code, "message": str(exc)}) from exc


@router.get("/api/connections/icloud-calendar")
async def icloud_calendar_status():
    return await asyncio.to_thread(_icloud_call, icloud_calendar.status)


@router.post("/api/connections/icloud-calendar")
async def icloud_calendar_connect(body: ICloudConnectBody):
    return await asyncio.to_thread(_icloud_call, icloud_calendar.connect, body.username, body.app_password)


@router.delete("/api/connections/icloud-calendar")
async def icloud_calendar_disconnect():
    return await asyncio.to_thread(_icloud_call, icloud_calendar.disconnect)


@router.get("/api/dashboard/icloud-calendar")
async def icloud_dashboard_feed(refresh: bool = False):
    return await asyncio.to_thread(_icloud_call, icloud_calendar.dashboard_feed, refresh=refresh)

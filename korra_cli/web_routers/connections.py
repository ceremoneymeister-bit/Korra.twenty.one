"""Owner's connected services and the dashboard calendar feed.

``GET /api/connections`` is the installation-wide, secret-free view behind the
«Сервисы» screen: which agent holds a Google grant, which agents borrow it and
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

from korra_cli import dashboard_calendar
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


def _connections_snapshot() -> dict[str, Any]:
    from korra_cli import profiles as profile_store

    snapshot = google.overview()
    calendar_tool = bool(snapshot["app"].get("configured"))
    for row in snapshot["profiles"]:
        home = google._profile_home_for_name(row["profile"])
        meta = profile_store.read_profile_meta(home)
        row["label"] = meta.get("display_name") or ""
        row["tools"] = {
            # Native `google_calendar` tool: every agent has it wherever the
            # installation app is configured; the grant decides the answer.
            "calendar": calendar_tool,
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

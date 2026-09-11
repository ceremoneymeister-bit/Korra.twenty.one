"""Dashboard endpoints for profile-scoped Google Workspace OAuth."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, SecretStr

from korra_constants import get_process_hermes_home
from korra_cli import google_workspace as google


router = APIRouter()


class GoogleStartBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    services: list[str]


class GoogleCompleteBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    callback_url: SecretStr


class GoogleProfileBody(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _profile_home(profile: str | None) -> Path:
    requested = (profile or "").strip()
    if not requested or requested.lower() == "current":
        return get_process_hermes_home()
    # These routes sit behind the dashboard's machine-owner auth perimeter.
    # Only that authenticated management surface may choose a local profile;
    # agent/model tool input has no profile field at all.
    from korra_cli import profiles

    try:
        canonical = profiles.normalize_profile_name(requested)
        profiles.validate_profile_name(canonical)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not profiles.profile_exists(canonical):
        raise HTTPException(status_code=404, detail=f"Profile '{canonical}' does not exist.")
    return profiles.get_profile_dir(canonical)


def _translate(exc: google.GoogleWorkspaceError) -> HTTPException:
    return HTTPException(
        status_code=exc.status_code,
        detail={"code": exc.code, "message": str(exc)},
    )


@router.get("/api/google-workspace/status")
async def google_status(profile: Optional[str] = None):
    return await asyncio.to_thread(google.status, profile_home=_profile_home(profile))


@router.post("/api/google-workspace/start")
async def google_start(body: GoogleStartBody, profile: Optional[str] = None):
    try:
        return await asyncio.to_thread(
            google.start,
            ",".join(body.services),
            profile_home=_profile_home(profile),
        )
    except google.GoogleWorkspaceError as exc:
        raise _translate(exc) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/api/google-workspace/complete")
async def google_complete(body: GoogleCompleteBody, profile: Optional[str] = None):
    try:
        return await asyncio.to_thread(
            google.complete,
            body.callback_url.get_secret_value(),
            profile_home=_profile_home(profile),
        )
    except google.GoogleWorkspaceError as exc:
        raise _translate(exc) from exc


@router.post("/api/google-workspace/cancel")
async def google_cancel(body: GoogleProfileBody, profile: Optional[str] = None):
    try:
        return await asyncio.to_thread(google.cancel, profile_home=_profile_home(profile))
    except google.GoogleWorkspaceError as exc:
        raise _translate(exc) from exc


@router.post("/api/google-workspace/revoke")
async def google_revoke(body: GoogleProfileBody, profile: Optional[str] = None):
    try:
        return await asyncio.to_thread(google.revoke, profile_home=_profile_home(profile))
    except google.GoogleWorkspaceError as exc:
        raise _translate(exc) from exc


@router.post("/api/google-workspace/check/{service}")
async def google_check_service(
    service: str,
    body: GoogleProfileBody,
    profile: Optional[str] = None,
):
    try:
        return await asyncio.to_thread(
            google.check_service,
            service,
            profile_home=_profile_home(profile),
        )
    except google.GoogleWorkspaceError as exc:
        raise _translate(exc) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

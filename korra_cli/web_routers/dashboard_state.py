"""``GET /api/dashboard/state`` — live data for the personal dashboard cards.

One read-only request per refresh. The client cabinet already lets exactly
this path through its perimeter as GET-only, so the owner's board works the
same behind the cabinet as on a loopback panel. The data layer lives in
:mod:`korra_cli.dashboard_state`; this module only binds it to HTTP.
"""

import asyncio

from fastapi import APIRouter

router = APIRouter()


@router.get("/api/dashboard/state")
async def get_dashboard_state(period: str = "week"):
    """Attention, agents, metrics, day timeline, artifacts and Codex quota.

    ``period`` picks the metrics window (``week`` or ``month``); anything
    else falls back to the week instead of failing the whole board.
    """
    from korra_cli.dashboard_state import build_state

    return await asyncio.to_thread(build_state, period=period)


@router.post("/api/dashboard/codex-limit/reset")
async def reset_codex_limit():
    """Spend one banked Codex reset and return the limit as it is afterwards.

    Always answers 200 with ``ok``, ``status``, a Russian ``message`` and the
    new ``quota`` section: the card shows the message in either case.
    """
    from korra_cli.dashboard_state import reset_codex_limit as run

    return await asyncio.to_thread(run)

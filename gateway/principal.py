"""Who is on the other end of this agent turn, and may they use the owner's services?

A profile's connected services (Google Calendar, the owner's Kanban board)
are the owner's. Connecting a service in the cabinet is not consent to open
it to every visitor of that profile's public bot, so tools that reach them
check two independent things on every call:

1. **the grant** — the profile has an own or explicitly shared connection
   (enforced by the service layer, e.g. :mod:`korra_cli.google_workspace`);
2. **the principal** — the person (or job) this turn acts for, decided here
   from server-bound session state only. Model arguments never count.

Principal rules
---------------
* **Owner, live** — the owner's own machine or cabinet: CLI/TUI/desktop/ACP
  (no messaging platform bound) and the API server, which the dashboard chat
  reaches with the installation's ``API_SERVER_KEY``; or a messaging direct
  chat whose sender is named in ``gateway.credential_management.owners`` —
  once for the whole installation in the root ``config.yaml``, or in the
  profile's own config (the one owner mapping the gateway already uses for
  credential management; see :mod:`gateway.credential_management`).
* **Owner, delegated** (acts for the owner, nobody typing): a system turn in
  the owner's direct chat; a dispatcher-spawned Kanban worker (the board is
  the owner's and only owner surfaces can put work on it); a scheduled job
  created from an owner surface.
* **Not the owner**: any other messaging sender, every group/channel chat,
  content-triggered surfaces (webhook, e-mail, Home Assistant), scheduled jobs
  created by somebody else, and — fail closed — a context in a multi-session
  process that never learned who is speaking.

Services decide what each class may do: reads are allowed to the owner and to
owner-delegated background work; external changes (creating a calendar event)
only to the owner speaking live — never implicitly from background runs.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


#: Surfaces that are the owner's own machine or authenticated cabinet.
#: ``""`` is the CLI/TUI/desktop/ACP case: they bind a source, not a platform.
OWNER_SURFACES = frozenset({"", "api_server", "local", "cli", "tui", "desktop", "codex", "acp"})

#: Surfaces whose turns are triggered by content, not by a person talking.
CONTENT_SURFACES = frozenset({"webhook", "msgraph_webhook", "homeassistant", "email"})


@dataclass(frozen=True)
class Principal:
    kind: str
    """``owner`` | ``outsider`` | ``group`` | ``content`` | ``cron`` |
    ``kanban_worker`` | ``unknown``."""
    owner: bool
    """Acts for the verified owner (live or delegated)."""
    live: bool
    """The owner is speaking in this turn (not a background/system run)."""

    @property
    def cache_key(self) -> str:
        """Stable key for caches of anything that depends on the principal."""
        return f"{self.kind}:{int(self.owner)}:{int(self.live)}"

    @property
    def background(self) -> bool:
        return self.kind in {"cron", "kanban_worker"} or (self.owner and not self.live)


OWNER_LIVE = Principal("owner", owner=True, live=True)


def _session(name: str) -> str:
    from gateway.session_context import get_session_env

    return str(get_session_env(name, "") or "").strip().lower()


def current_principal() -> Principal:
    """Classify the current turn from server-bound session state."""
    from gateway.session_context import (
        background_owner,
        session_context_engaged,
        session_var_is_bound,
    )
    from korra_constants import korra_env

    if _session("KORRA_CRON_SESSION") == "1":
        return Principal("cron", owner=background_owner() is True, live=False)

    if korra_env("KORRA_KANBAN_TASK"):
        # A dispatcher-spawned worker (or its delegate child) runs a card on
        # the owner's board. Cron jobs fired inside a worker were caught above.
        return Principal("kanban_worker", owner=True, live=False)

    platform = _session("KORRA_SESSION_PLATFORM")
    if (
        not platform
        and session_context_engaged()
        and not session_var_is_bound("KORRA_SESSION_PLATFORM")
    ):
        # This process serves several sessions and this context never bound
        # one: we cannot tell who is speaking, so nobody is the owner.
        return Principal("unknown", owner=False, live=False)
    if platform in CONTENT_SURFACES:
        return Principal("content", owner=False, live=False)
    if platform in OWNER_SURFACES:
        return OWNER_LIVE

    owner = _session("KORRA_SESSION_OWNER")
    chat_type = _session("KORRA_SESSION_CHAT_TYPE")
    if chat_type and chat_type != "dm":
        return Principal("group", owner=False, live=True)
    if owner == "live":
        return OWNER_LIVE
    if owner == "delegated":
        return Principal("owner", owner=True, live=False)
    return Principal("outsider", owner=False, live=True)


def may_read_owner_services(principal: Principal | None = None) -> bool:
    """Reads of the owner's connected services: owner, live or delegated."""
    return (principal or current_principal()).owner


def may_change_owner_services(principal: Principal | None = None) -> bool:
    """External changes: only the owner speaking in a live conversation."""
    current = principal or current_principal()
    return current.owner and current.live


# ---------------------------------------------------------------------------
# Scheduled jobs
# ---------------------------------------------------------------------------


def origin_owner_verdict() -> bool:
    """Owner verdict to stamp on a job created in the current turn."""
    return current_principal().owner


def cron_job_acts_for_owner(job: Mapping[str, Any], config: Mapping[str, Any] | None = None) -> bool:
    """Whether a scheduled job acts for the owner when it runs unattended.

    Jobs created by the agent carry the creator's verdict in their origin.
    Jobs without an origin were created on the owner's machine or in the
    cabinet (CLI, dashboard). Older jobs with a messaging origin and no verdict
    count as the owner's only when their creator is a configured owner of the
    installation or of the profile.
    """
    origin = job.get("origin") if isinstance(job, Mapping) else None
    if not isinstance(origin, Mapping) or not origin.get("platform"):
        return True
    verdict = origin.get("owner")
    if isinstance(verdict, bool):
        return verdict
    platform = str(origin.get("platform") or "").strip().lower()
    if platform in OWNER_SURFACES:
        return True
    if platform in CONTENT_SURFACES:
        return False
    if config is None:
        try:
            from korra_cli.config import load_config

            config = load_config()
        except Exception:
            return False
    from gateway.credential_management import owner_matches

    return owner_matches(config, platform, str(origin.get("user_id") or ""))

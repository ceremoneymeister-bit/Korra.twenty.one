"""Durable per-person view of the agents screen on a phone.

Sibling of :mod:`korra_cli.dashboard_layout` and deliberately the same
contract: the record belongs to one verified person, the browser only claims a
revision, and the revision is server-owned and monotonic so the loser of a
phone-versus-laptop race is told instead of silently overwritten.

What the record holds:

* ``agents_mobile`` — how this person switches agents below the desktop
  breakpoint: ``"tabs"`` (a strip of avatars under the conversation line, the
  default) or ``"list"`` (a messenger-like list, one agent full screen).
  The desktop tab strip does not read it.
* ``pinned`` — agents this person keeps in the phone strip regardless of who
  needs attention. Profile names use the engine grammar; the main agent is
  stored under its server name ``default``.

The choice is personal on purpose. Putting it into the installation-wide
``dashboard.agent_tabs`` would be less code, but then an assistant who prefers
the list would switch the owner's phone to the list as well.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any, Iterable, Mapping

AGENTS_MOBILE_MODES: tuple[str, ...] = ("tabs", "list")
DEFAULT_AGENTS_MOBILE = "tabs"

#: More pins than the widest phone strip holds only push each other out; the
#: bound keeps a malformed browser from growing the record without limit.
MAX_PINNED = 12

#: Same grammar as ``_PROFILE_ID_RE`` in profiles.py and PROFILE_ID_RE in
#: web/src/lib/agent-tabs.ts: the name travels into URLs and paths.
_PROFILE_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")

#: Fields of the durable record. The API adds a derived ``scope`` on top.
RECORD_FIELDS: tuple[str, ...] = ("version", "revision", "agents_mobile", "pinned")


def _records(config: Mapping[str, Any]) -> dict[str, Any]:
    dashboard = config.get("dashboard")
    dashboard = dashboard if isinstance(dashboard, dict) else {}
    view = dashboard.get("view")
    view = view if isinstance(view, dict) else {}
    users = view.get("users")
    return users if isinstance(users, dict) else {}


def _mode(value: Any) -> str:
    return value if isinstance(value, str) and value in AGENTS_MOBILE_MODES else DEFAULT_AGENTS_MOBILE


def _pinned(value: Any) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    result: list[str] = []
    for raw in value:
        if not isinstance(raw, str) or not _PROFILE_RE.fullmatch(raw) or raw in result:
            continue
        result.append(raw)
        if len(result) >= MAX_PINNED:
            break
    return result


def _revision(value: Any) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        return 0
    return value


def preference(config: Mapping[str, Any], key: str) -> dict[str, Any]:
    """Return one person's normalized view plus its monotonic CAS revision."""

    raw = _records(config).get(key)
    raw = raw if isinstance(raw, dict) else {}
    return {
        "version": 1,
        "revision": _revision(raw.get("revision")),
        "agents_mobile": _mode(raw.get("agents_mobile")),
        "pinned": _pinned(raw.get("pinned")),
    }


def updated(
    before: Mapping[str, Any],
    *,
    agents_mobile: Any,
    pinned: Iterable[str] | None,
) -> dict[str, Any]:
    """Build the next normalized record; the revision never comes from a browser."""

    return {
        "version": 1,
        "revision": _revision(before.get("revision")) + 1,
        "agents_mobile": _mode(agents_mobile),
        "pinned": _pinned(list(pinned or [])),
    }


def record(value: Mapping[str, Any]) -> dict[str, Any]:
    """The durable part of an API payload, for read-back comparison."""

    return {field: value.get(field) for field in RECORD_FIELDS}


def store(config: dict[str, Any], key: str, value: Mapping[str, Any]) -> dict[str, Any]:
    """Write one person's record into *config*, leaving every other one intact.

    As with the dashboard board there is no eviction: growth is bounded by the
    people the installation's own auth lets in, and quietly deleting somebody's
    choice to make room would change their phone with no way to notice.
    """

    dashboard = config.get("dashboard")
    if not isinstance(dashboard, dict):
        dashboard = config["dashboard"] = {}
    view = dashboard.get("view")
    if not isinstance(view, dict):
        view = dashboard["view"] = {}
    users = view.get("users")
    if not isinstance(users, dict):
        users = view["users"] = {}
    users[key] = record(value)
    return config


def preserve_keys(key: str) -> set[tuple[str, ...]]:
    """Config paths that must survive ``save_config``'s default stripping."""

    base = ("dashboard", "view", "users", key)
    return {base + (field,) for field in RECORD_FIELDS}


def cache_scope(key: str) -> str:
    """Opaque per-person label for the browser's pre-paint cache.

    The browser keys its localStorage copy by it, so a second person signing in
    on the same browser never starts from the first person's screen. It is a
    hash of the storage key, which is itself a hash: nothing about the person
    can be read back from it.
    """

    return hashlib.sha256(f"korra-view\0{key}".encode()).hexdigest()[:16]


def payload(config: Mapping[str, Any], key: str) -> dict[str, Any]:
    """What the API and the page bootstrap hand to this person's browser."""

    return {**preference(config, key), "scope": cache_scope(key)}

"""Durable owner layout for the dashboard's agent tab strip.

The layout is shared between the owner's browsers.  Conversation selection and
scroll position deliberately do not live here: those are navigation state of a
particular device/tab, not installation preferences.
"""

from __future__ import annotations

import re
from typing import Any, Iterable


MAX_AGENT_TABS = 10
_PROFILE_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


def _profiles(value: Any, *, allow_main: bool) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for raw in value:
        if not isinstance(raw, str):
            continue
        profile = raw.strip()
        if profile == "":
            if not allow_main or profile in result:
                continue
        elif not _PROFILE_RE.fullmatch(profile) or profile in result:
            continue
        result.append(profile)
        if len(result) >= MAX_AGENT_TABS:
            break
    return result


def preference(config: dict[str, Any]) -> dict[str, Any]:
    """Return the normalized durable layout and its monotonic CAS revision."""

    dashboard = config.get("dashboard")
    dashboard = dashboard if isinstance(dashboard, dict) else {}
    raw = dashboard.get("agent_tabs")
    raw = raw if isinstance(raw, dict) else {}
    revision = raw.get("revision", 0)
    if not isinstance(revision, int) or isinstance(revision, bool) or revision < 0:
        revision = 0
    order = _profiles(raw.get("order"), allow_main=True)
    hidden = _profiles(raw.get("hidden"), allow_main=False)
    hidden_set = set(hidden)
    hidden = [profile for profile in order if profile and profile in hidden_set]
    return {
        "version": 1,
        "revision": revision,
        "initialized": raw.get("initialized") is True,
        "order": order,
        "hidden": hidden,
    }


def updated(
    before: dict[str, Any],
    *,
    order: Iterable[str],
    hidden: Iterable[str],
) -> dict[str, Any]:
    """Build the next normalized record without trusting browser clocks."""

    clean_order = _profiles(list(order), allow_main=True)
    if "" not in clean_order:
        clean_order.insert(0, "")
        clean_order = clean_order[:MAX_AGENT_TABS]
    clean_hidden = _profiles(list(hidden), allow_main=False)
    hidden_set = set(clean_hidden)
    clean_hidden = [profile for profile in clean_order if profile and profile in hidden_set]
    revision = before.get("revision", 0)
    if not isinstance(revision, int) or isinstance(revision, bool) or revision < 0:
        revision = 0
    return {
        "version": 1,
        "revision": revision + 1,
        "initialized": True,
        "order": clean_order,
        "hidden": clean_hidden,
    }

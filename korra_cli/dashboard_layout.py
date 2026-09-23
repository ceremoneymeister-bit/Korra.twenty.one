"""Durable per-user layout of the personal dashboard.

Sibling of :mod:`korra_cli.dashboard_agent_tabs`, deliberately a separate
contract: the agent tab strip is one installation-wide list of profiles, while
this record describes one person's board — which cards they keep, in which
order, and how large each tile is.

Two properties shape the storage shape:

* **The record is per user, and the user is never taken from the request
  body.** A browser may only claim a revision; who it belongs to is resolved
  from the verified session on the server (see ``_dashboard_layout_key`` in
  ``web_server``). Writing one person's board must not move another's, so each
  identity owns its own subtree and a write rewrites only that subtree.
* **The revision is server-owned and monotonic.** Two browsers editing the
  same board is normal (phone and laptop); the loser of a race must be told,
  not silently overwritten, so the caller can re-read the winner.

Widget geometry uses one modular grid for every card: S 1×1, M 2×1, L 2×2.
``attention`` is pinned above the grid — it is the "what needs you" strip, not
a tile — so it carries neither a size nor a position.
"""

from __future__ import annotations

import hashlib
from typing import Any, Iterable, Mapping


#: Catalog order. This is also the default board order, so a restored card
#: returns to its own place instead of to the end of the grid.
WIDGET_IDS: tuple[str, ...] = (
    "attention",
    "agents",
    "metrics",
    "upcoming-tasks",
    "recent-results",
    # Встречи из подключённого Google-календаря, задачи с датой и запуски
    # агентов на неделю (0.21.13). Новый в каталоге — у тех, кто уже
    # настраивал доску, встаёт последним, а не пропадает.
    "calendar",
)

#: Cards that live outside the tile grid: no size, no reordering.
PINNED_WIDGET_IDS: tuple[str, ...] = ("attention",)

#: Tiles the owner can resize and reorder.
TILE_WIDGET_IDS: tuple[str, ...] = tuple(
    widget for widget in WIDGET_IDS if widget not in PINNED_WIDGET_IDS
)

#: S 1×1, M 2×1, L 2×2 — one geometry for every tile.
WIDGET_SIZES: tuple[str, ...] = ("s", "m", "l")

DEFAULT_SIZE = "m"

#: Storage key of the single-owner (loopback / session-token) dashboard. The
#: installation has no verified human identity there: one machine, one owner.
LOCAL_USER_KEY = "local"


def storage_key(user_id: Any = None, provider: Any = None) -> str:
    """Return the config key for a verified identity, or the single-owner key.

    The caller passes what the *server* verified, never what a browser sent.
    Identities are hashed so that config.yaml — which operators read, copy and
    attach to support tickets — never accumulates e-mail addresses.
    """

    identity = user_id.strip() if isinstance(user_id, str) else ""
    if not identity:
        return LOCAL_USER_KEY
    issuer = provider.strip() if isinstance(provider, str) else ""
    digest = hashlib.sha256(f"{issuer}\0{identity}".encode()).hexdigest()
    return f"u:{digest[:24]}"


def default_sizes() -> dict[str, str]:
    return {widget: DEFAULT_SIZE for widget in TILE_WIDGET_IDS}


def _widgets(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for raw in value:
        if isinstance(raw, str) and raw in WIDGET_IDS and raw not in result:
            result.append(raw)
    return result


def _order(value: Any) -> list[str]:
    """Stored order first, then every catalog card the record never mentioned.

    A card added to the catalog in a later release must appear for people who
    already personalized their board, rather than vanish because their stored
    order predates it.
    """

    stored = _widgets(value)
    return stored + [widget for widget in WIDGET_IDS if widget not in stored]


def _sizes(value: Any) -> dict[str, str]:
    sizes = default_sizes()
    if isinstance(value, Mapping):
        for widget in TILE_WIDGET_IDS:
            choice = value.get(widget)
            if isinstance(choice, str) and choice in WIDGET_SIZES:
                sizes[widget] = choice
    return sizes


def _revision(value: Any) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        return 0
    return value


def _records(config: Mapping[str, Any]) -> dict[str, Any]:
    dashboard = config.get("dashboard")
    dashboard = dashboard if isinstance(dashboard, dict) else {}
    layout = dashboard.get("layout")
    layout = layout if isinstance(layout, dict) else {}
    users = layout.get("users")
    return users if isinstance(users, dict) else {}


def preference(config: Mapping[str, Any], key: str) -> dict[str, Any]:
    """Return one person's normalized board plus its monotonic CAS revision."""

    raw = _records(config).get(key)
    raw = raw if isinstance(raw, dict) else {}
    order = _order(raw.get("order"))
    hidden_set = set(_widgets(raw.get("hidden")))
    return {
        "version": 1,
        "revision": _revision(raw.get("revision")),
        "initialized": raw.get("initialized") is True,
        "order": order,
        "hidden": [widget for widget in order if widget in hidden_set],
        "sizes": _sizes(raw.get("sizes")),
    }


def updated(
    before: Mapping[str, Any],
    *,
    order: Iterable[str],
    hidden: Iterable[str],
    sizes: Any,
) -> dict[str, Any]:
    """Build the next normalized record without trusting the browser's clock."""

    clean_order = _order(list(order))
    # Pinned cards are not part of the tile flow, so their stored position is
    # meaningless; keep them at the catalog's own place instead of wherever a
    # browser happened to serialize them.
    tiles = [widget for widget in clean_order if widget not in PINNED_WIDGET_IDS]
    arranged: list[str] = []
    tile_flow = iter(tiles)
    for widget in WIDGET_IDS:
        if widget in PINNED_WIDGET_IDS:
            arranged.append(widget)
        else:
            arranged.append(next(tile_flow))
    hidden_set = set(_widgets(list(hidden)))
    return {
        "version": 1,
        "revision": _revision(before.get("revision")) + 1,
        "initialized": True,
        "order": arranged,
        "hidden": [widget for widget in arranged if widget in hidden_set],
        "sizes": _sizes(sizes),
    }


def store(config: dict[str, Any], key: str, record: Mapping[str, Any]) -> dict[str, Any]:
    """Write one person's record into *config*, leaving every other one intact.

    There is deliberately no eviction of older records. Growth is bounded by
    the people the installation's own auth lets in, each record is a handful of
    short lists, and the alternative — quietly deleting somebody's saved board
    to make room — costs a person their personalization with no warning and no
    way to notice. Arranging your dashboard must never rearrange another's.

    Returns the mutated config so the caller can hand it straight to
    ``save_config``.
    """

    dashboard = config.get("dashboard")
    if not isinstance(dashboard, dict):
        dashboard = config["dashboard"] = {}
    layout = dashboard.get("layout")
    if not isinstance(layout, dict):
        layout = dashboard["layout"] = {}
    users = layout.get("users")
    if not isinstance(users, dict):
        users = layout["users"] = {}
    users[key] = dict(record)
    return config


def preserve_keys(key: str) -> set[tuple[str, ...]]:
    """Config paths that must survive ``save_config``'s default stripping."""

    base = ("dashboard", "layout", "users", key)
    return {
        base + ("version",),
        base + ("revision",),
        base + ("initialized",),
        base + ("order",),
        base + ("hidden",),
        base + ("sizes",),
    }

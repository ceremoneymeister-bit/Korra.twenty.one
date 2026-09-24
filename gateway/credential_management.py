"""Fail-closed owner mapping for chat-triggered credential management.

The owner is a person, the same for every agent of an installation (Dmitry's
decision of 24.09.2026). The mapping has two levels with one meaning:

* **installation** — ``gateway.credential_management.owners`` in the
  installation root's ``config.yaml`` (:func:`korra_constants.
  get_default_hermes_root`: ``/opt/data`` in the container, which is also the
  default profile's home). The operator writes it once; it counts in the
  direct chat of every profile's bot.
* **profile** — the same key in the active profile's config. Still honored,
  exactly as before.

A sender is an owner when either level names this exact platform principal.
An absent or empty root list leaves the profile-only behavior unchanged.
Both files are read on every decision through the mtime-keyed config caches,
so an edit applies to the next message without restarting the gateway.
"""

from __future__ import annotations

import logging
from typing import Any, Mapping

logger = logging.getLogger(__name__)

#: Default for ``installation=``: read the installation root's config.
FROM_INSTALLATION_ROOT: Any = object()


def configured_owners(config: Mapping[str, Any] | None, platform: str) -> frozenset[str]:
    """Exact owner ids one config names for ``platform``; anything malformed is empty."""
    if not isinstance(config, Mapping):
        return frozenset()
    clean_platform = str(platform or "").strip().lower()
    if not clean_platform:
        return frozenset()
    gateway = config.get("gateway")
    if not isinstance(gateway, Mapping):
        return frozenset()
    credential_management = gateway.get("credential_management")
    if not isinstance(credential_management, Mapping):
        return frozenset()
    owners = credential_management.get("owners")
    if not isinstance(owners, Mapping):
        return frozenset()
    configured = owners.get(clean_platform)
    if not isinstance(configured, list):
        return frozenset()
    return frozenset(
        str(item).strip()
        for item in configured
        if isinstance(item, (str, int))
        and not isinstance(item, bool)
        and str(item).strip()
        and str(item).strip() != "*"
    )


def installation_owner_config() -> Mapping[str, Any]:
    """The installation root's config as written, for the owner decision only.

    Read through the canonical raw reader under a temporary home override, so
    it shares the (mtime_ns, size)-keyed cache and never creates directories.
    Any failure reads as "no installation owners" (fail closed to the
    profile's own list).
    """
    try:
        from korra_cli.config import read_raw_config_readonly
        from korra_constants import (
            get_default_hermes_root,
            reset_hermes_home_override,
            set_hermes_home_override,
        )

        token = set_hermes_home_override(str(get_default_hermes_root()))
        try:
            config = read_raw_config_readonly()
        finally:
            reset_hermes_home_override(token)
    except Exception:
        logger.debug("installation owner config unavailable", exc_info=True)
        return {}
    return config if isinstance(config, Mapping) else {}


def installation_owners(platform: str) -> frozenset[str]:
    """Owner ids the installation root names for ``platform``."""
    return configured_owners(installation_owner_config(), platform)


def owner_matches(
    config: Mapping[str, Any] | None,
    platform: str,
    user_id: str,
    *,
    installation: Mapping[str, Any] | None = FROM_INSTALLATION_ROOT,
) -> bool:
    """Return whether one exact platform principal is a configured owner.

    General gateway authorization, pairing, roles, and allow-all settings are
    deliberately unrelated to this narrower capability. ``config`` is the
    active profile's config; ``installation`` defaults to the installation
    root's config (pass a mapping to decide without disk access). Both default
    to empty.
    """
    clean_platform = str(platform or "").strip().lower()
    clean_user_id = str(user_id or "").strip()
    if not clean_platform or not clean_user_id:
        return False
    if clean_user_id in configured_owners(config, clean_platform):
        return True
    if installation is FROM_INSTALLATION_ROOT:
        installation = installation_owner_config()
    return clean_user_id in configured_owners(installation, clean_platform)


def owner_principal(
    config: Mapping[str, Any] | None,
    *,
    platform: str,
    user_id: str,
    chat_type: str,
    internal: bool,
    installation: Mapping[str, Any] | None = FROM_INSTALLATION_ROOT,
) -> str:
    """Who a messaging turn acts for, from the same owner mapping.

    ``"live"`` — a configured owner (installation or profile level) wrote this
    message in a direct chat; ``"delegated"`` — a system turn (background
    completion, board notification) inside that owner's direct chat; ``""`` —
    anyone else, including the owner speaking in a group, where others read
    the answer.
    """
    if str(chat_type or "").strip().lower() != "dm":
        return ""
    if not owner_matches(config, platform, user_id, installation=installation):
        return ""
    return "delegated" if internal else "live"

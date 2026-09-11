"""Fail-closed owner mapping for chat-triggered credential management."""

from __future__ import annotations

from typing import Any, Mapping


def owner_matches(config: Mapping[str, Any] | None, platform: str, user_id: str) -> bool:
    """Return whether one exact platform principal is a configured owner.

    General gateway authorization, pairing, roles, and allow-all settings are
    deliberately unrelated to this narrower capability.  The mapping belongs
    to the active profile's config and defaults to empty.
    """
    if not isinstance(config, Mapping):
        return False
    clean_platform = str(platform or "").strip().lower()
    clean_user_id = str(user_id or "").strip()
    if not clean_platform or not clean_user_id:
        return False
    gateway = config.get("gateway")
    if not isinstance(gateway, Mapping):
        return False
    credential_management = gateway.get("credential_management")
    if not isinstance(credential_management, Mapping):
        return False
    owners = credential_management.get("owners")
    if not isinstance(owners, Mapping):
        return False
    configured = owners.get(clean_platform)
    if not isinstance(configured, list):
        return False
    allowed = {
        str(item).strip()
        for item in configured
        if isinstance(item, (str, int))
        and not isinstance(item, bool)
        and str(item).strip()
        and str(item).strip() != "*"
    }
    return clean_user_id in allowed

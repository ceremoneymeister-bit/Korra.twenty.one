"""Canonical least-privilege Google Workspace OAuth scope contract."""

from __future__ import annotations


SERVICE_SCOPES: dict[str, tuple[str, ...]] = {
    "email": ("https://www.googleapis.com/auth/gmail.modify",),
    "calendar": ("https://www.googleapis.com/auth/calendar.events",),
    "drive": ("https://www.googleapis.com/auth/drive",),
    "contacts": ("https://www.googleapis.com/auth/contacts.readonly",),
    "sheets": ("https://www.googleapis.com/auth/spreadsheets",),
    "docs": ("https://www.googleapis.com/auth/documents",),
}
MINIMUM_SCOPES = tuple(
    scope for service_scopes in SERVICE_SCOPES.values() for scope in service_scopes
)
LEGACY_ALL_SCOPES = (
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/contacts.readonly",
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/documents",
)
# Recognized scopes issued by older Ceremoneymeister deployments.  They remain
# usable only for the capabilities they actually grant while the owner is
# guided through a new, tracked consent flow.
LEGACY_COMPAT_SCOPES = frozenset(
    {
        "https://mail.google.com/",
        "https://www.googleapis.com/auth/contacts",
    }
)
# Google can append identity scopes to an OAuth grant.  They do not turn an old
# broad Workspace token into a safe least-privilege token; they only let us
# identify the deployed 8/9/10-scope legacy family and explain the migration.
GOOGLE_IDENTITY_SCOPES = frozenset(
    {
        "openid",
        "email",
        "profile",
        "https://www.googleapis.com/auth/userinfo.email",
        "https://www.googleapis.com/auth/userinfo.profile",
    }
)
KNOWN_SCOPES = (
    frozenset(MINIMUM_SCOPES)
    | frozenset(LEGACY_ALL_SCOPES)
    | LEGACY_COMPAT_SCOPES
    | GOOGLE_IDENTITY_SCOPES
)
TOKEN_SERVICES_KEY = "korra_services"
TOKEN_REQUESTED_SCOPES_KEY = "korra_requested_scopes"
API_SERVICE_NAMES = {
    "gmail": "email",
    "calendar": "calendar",
    "drive": "drive",
    "people": "contacts",
    "sheets": "sheets",
    "docs": "docs",
}


def parse_services(raw: str | None) -> tuple[str, ...]:
    """Parse a comma-separated service allowlist into canonical order."""
    if raw is None or not raw.strip():
        raise ValueError("services requires a non-empty comma-separated service list")
    parts = [part.strip().lower() for part in raw.split(",")]
    if any(not part for part in parts):
        raise ValueError("services contains an empty service name")
    if len(parts) != len(set(parts)):
        raise ValueError("services contains a duplicate service name")
    if "all" in parts:
        if len(parts) != 1:
            raise ValueError("'all' cannot be combined with individual services")
        return tuple(SERVICE_SCOPES)
    unknown = sorted(set(parts) - set(SERVICE_SCOPES))
    if unknown:
        raise ValueError(
            "unknown Google Workspace service(s): "
            f"{', '.join(unknown)}; choose from {', '.join(SERVICE_SCOPES)} or all"
        )
    return tuple(name for name in SERVICE_SCOPES if name in parts)


def scopes_for_services(services: tuple[str, ...]) -> list[str]:
    if not services:
        raise ValueError("at least one Google Workspace service is required")
    if services == ("all",):
        return list(MINIMUM_SCOPES)
    if "all" in services:
        raise ValueError("'all' cannot be combined with individual services")
    unknown = sorted(set(services) - set(SERVICE_SCOPES))
    if unknown:
        raise ValueError(f"unknown Google Workspace service(s): {', '.join(unknown)}")
    return [scope for name in services for scope in SERVICE_SCOPES[name]]


def granted_scopes_from_payload(payload: dict) -> list[str]:
    raw = payload.get("scopes") or payload.get("scope")
    if isinstance(raw, str):
        return [scope for scope in raw.split() if scope]
    if isinstance(raw, list) and all(isinstance(scope, str) for scope in raw):
        return [scope for scope in raw if scope]
    return []


def tracked_scope_contract(payload: dict) -> tuple[tuple[str, ...], list[str]] | None:
    raw_services = payload.get(TOKEN_SERVICES_KEY)
    raw_scopes = payload.get(TOKEN_REQUESTED_SCOPES_KEY)
    if raw_services is None and raw_scopes is None:
        return None
    if not isinstance(raw_services, list) or not raw_services:
        raise ValueError(f"{TOKEN_SERVICES_KEY} must be a non-empty list")
    if not all(isinstance(name, str) and name for name in raw_services):
        raise ValueError(f"{TOKEN_SERVICES_KEY} contains an invalid service")
    services = parse_services(",".join(raw_services))
    expected_scopes = scopes_for_services(services)
    if raw_scopes != expected_scopes:
        raise ValueError(f"{TOKEN_REQUESTED_SCOPES_KEY} does not match selected services")
    return services, expected_scopes


def _legacy_services(granted: set[str]) -> tuple[str, ...]:
    """Derive the bounded service set from recognized, actually granted scopes."""
    if granted - KNOWN_SCOPES:
        return ()
    service_grants = {
        "email": {
            "https://mail.google.com/",
            "https://www.googleapis.com/auth/gmail.readonly",
            "https://www.googleapis.com/auth/gmail.send",
            "https://www.googleapis.com/auth/gmail.modify",
        },
        "calendar": {
            "https://www.googleapis.com/auth/calendar",
            "https://www.googleapis.com/auth/calendar.events",
        },
        "drive": {"https://www.googleapis.com/auth/drive"},
        "contacts": {
            "https://www.googleapis.com/auth/contacts",
            "https://www.googleapis.com/auth/contacts.readonly",
        },
        "sheets": {"https://www.googleapis.com/auth/spreadsheets"},
        "docs": {"https://www.googleapis.com/auth/documents"},
    }
    return tuple(
        service for service in SERVICE_SCOPES if granted & service_grants[service]
    )


def legacy_scope_inventory(payload: dict) -> dict[str, object]:
    """Classify an untracked token and expose only recognized capabilities."""
    granted = set(granted_scopes_from_payload(payload))
    unknown = sorted(granted - KNOWN_SCOPES)
    workspace = granted - GOOGLE_IDENTITY_SCOPES
    if not granted:
        kind = "unrecorded"
    elif unknown:
        kind = "unknown"
    elif frozenset(LEGACY_ALL_SCOPES).issubset(workspace):
        kind = "legacy_broad"
    else:
        kind = "legacy_untracked"
    return {
        "kind": kind,
        "scope_count": len(granted),
        "unknown_scope_count": len(unknown),
        "usable_services": list(_legacy_services(granted)),
        "compatible": bool(granted) and not unknown and bool(_legacy_services(granted)),
        "requires_reauthorization": True,
    }


def validate_scope_contract(payload: dict) -> tuple[tuple[str, ...], list[str]]:
    """Validate a tracked grant or bound a recognized legacy grant to reality.

    Legacy grants never acquire the selected-service metadata and never gain a
    scope.  They can keep serving only APIs implied by their recorded scopes
    until explicit reconnect consent replaces them.
    """
    tracked = tracked_scope_contract(payload)
    if tracked is None:
        inventory = legacy_scope_inventory(payload)
        services = tuple(inventory["usable_services"])
        if inventory["compatible"] and services:
            return services, granted_scopes_from_payload(payload)
        raise ValueError(
            "legacy Google token cannot be bounded to recognized services "
            f"({inventory['kind']}, {inventory['scope_count']} recorded scopes); "
            "revoke and reconnect with selected services"
        )
    services, expected = tracked
    granted = granted_scopes_from_payload(payload)
    missing, extra = scope_difference(granted, expected)
    if missing or extra:
        details = []
        if missing:
            details.append(f"missing {len(missing)}")
        if extra:
            details.append(f"unexpected {len(extra)}")
        raise ValueError("token scopes do not match selected services (" + ", ".join(details) + ")")
    return services, expected


def require_selected_service(payload: dict, api_name: str) -> None:
    try:
        service = API_SERVICE_NAMES[api_name]
    except KeyError:
        raise ValueError(f"unknown Google Workspace API service: {api_name}") from None
    services, _ = validate_scope_contract(payload)
    if services != ("all",) and service not in services:
        raise ValueError(
            f"service '{service}' was not selected during OAuth setup; "
            "revoke before changing services"
        )


def scope_difference(actual: list[str], expected: list[str]) -> tuple[list[str], list[str]]:
    return sorted(set(expected) - set(actual)), sorted(set(actual) - set(expected))

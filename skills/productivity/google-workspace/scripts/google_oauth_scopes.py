"""Canonical Google Workspace service-to-scope contract."""

from __future__ import annotations


SERVICE_SCOPES = {
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
KNOWN_SCOPES = frozenset(MINIMUM_SCOPES) | frozenset(LEGACY_ALL_SCOPES)
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
        raise ValueError("--services requires a non-empty comma-separated service list")

    parts = [part.strip().lower() for part in raw.split(",")]
    if any(not part for part in parts):
        raise ValueError("--services contains an empty service name")
    if len(parts) != len(set(parts)):
        raise ValueError("--services contains a duplicate service name")
    if "all" in parts:
        if len(parts) != 1:
            raise ValueError("'all' cannot be combined with individual services")
        return ("all",)

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
        return list(LEGACY_ALL_SCOPES)
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
    """Return and validate Korra's persisted least-privilege token contract."""
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


def validate_scope_contract(payload: dict) -> tuple[tuple[str, ...] | None, list[str]]:
    """Validate tracked tokens exactly; retain bounded support for legacy tokens."""
    tracked = tracked_scope_contract(payload)
    granted = granted_scopes_from_payload(payload)
    if tracked is None:
        if not granted:
            raise ValueError("legacy token has no recorded scopes; re-authentication is required")
        unknown = sorted(set(granted) - KNOWN_SCOPES)
        if unknown:
            raise ValueError(f"legacy token contains unknown scope(s): {', '.join(unknown)}")
        return None, granted

    services, expected = tracked
    missing = sorted(set(expected) - set(granted))
    extra = sorted(set(granted) - set(expected))
    if missing or extra:
        details = []
        if missing:
            details.append(f"missing: {', '.join(missing)}")
        if extra:
            details.append(f"unexpected: {', '.join(extra)}")
        raise ValueError("token scopes do not match selected services (" + "; ".join(details) + ")")
    return services, expected


def require_selected_service(payload: dict, api_name: str) -> None:
    """Reject API dispatch outside a tracked service allowlist."""
    try:
        service = API_SERVICE_NAMES[api_name]
    except KeyError:
        raise ValueError(f"unknown Google Workspace API service: {api_name}") from None

    services, _ = validate_scope_contract(payload)
    if services is None or services == ("all",):
        return
    if service not in services:
        raise ValueError(
            f"service '{service}' was not selected during OAuth setup; "
            "run setup.py --revoke before changing services"
        )


def scope_difference(actual: list[str], expected: list[str]) -> tuple[list[str], list[str]]:
    return (
        sorted(set(expected) - set(actual)),
        sorted(set(actual) - set(expected)),
    )

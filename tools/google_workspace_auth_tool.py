"""Owner-facing Google Workspace connection management tool."""

from __future__ import annotations

import json

from korra_constants import get_hermes_home
from korra_cli import google_workspace as google
from tools.registry import registry


def _owner_context_problem() -> str | None:
    """Reject unattended and content-triggered sessions.

    The gateway has already admitted an interactive sender through its
    per-platform owner allowlist.  This second boundary prevents cron,
    webhook, and unidentified multiplex turns from managing credentials.
    """
    try:
        from gateway.session_context import get_session_env
    except Exception:
        return "Google authorization requires a verified owner context."

    try:
        if get_session_env("KORRA_CRON_SESSION", ""):
            return "Google authorization cannot be managed from a scheduled session."
        if get_session_env("KORRA_CREDENTIAL_MANAGEMENT_AUTHORIZED", "") != "1":
            return "Google authorization requires a verified owner context."
        platform = get_session_env("KORRA_SESSION_PLATFORM", "").strip().lower()
        if not platform or platform in {
            "api_server",
            "cli",
            "desktop",
            "email",
            "homeassistant",
            "local",
            "msgraph_webhook",
            "tui",
            "webhook",
        }:
            return "Google authorization requires an interactive owner session."
        if not get_session_env("KORRA_SESSION_USER_ID", "").strip():
            return "Google authorization requires an identified owner."
    except Exception:
        return "Google authorization requires a verified owner context."
    return None


def _handle(args: dict, **_kwargs) -> str:
    problem = _owner_context_problem()
    if problem:
        return json.dumps({"ok": False, "error": "owner_required", "message": problem})
    action = str(args.get("action") or "status").strip().lower()
    home = get_hermes_home()
    try:
        if action == "status":
            result = google.status(profile_home=home)
        elif action == "start":
            services = args.get("services")
            if not isinstance(services, list) or not services:
                raise GoogleWorkspaceErrorCompat("services_required", "Select at least one Google service.")
            result = google.start(",".join(str(item) for item in services), profile_home=home)
        elif action == "cancel":
            result = google.cancel(profile_home=home)
        else:
            raise GoogleWorkspaceErrorCompat("action_invalid", f"Unsupported action: {action}")
        return json.dumps({"ok": True, **result})
    except (google.GoogleWorkspaceError, ValueError, GoogleWorkspaceErrorCompat) as exc:
        return json.dumps(
            {
                "ok": False,
                "error": getattr(exc, "code", "invalid_request"),
                "message": str(exc),
            }
        )


class GoogleWorkspaceErrorCompat(ValueError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


registry.register(
    name="google_workspace_auth",
    toolset="google_workspace",
    schema={
        "name": "google_workspace_auth",
        "description": (
            "Start, inspect, or cancel this agent profile's Google Workspace connection. "
            "Connection completion happens in the owner's Keys settings; never ask for or accept a callback URL, code, token, or app secret in chat."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["start", "status", "cancel"],
                },
                "services": {
                    "type": "array",
                    "items": {
                        "type": "string",
                        "enum": list(google.SERVICE_SCOPES),
                    },
                    "uniqueItems": True,
                    "description": "Required only for start.",
                },
            },
            "required": ["action"],
            "additionalProperties": False,
        },
    },
    handler=_handle,
    description="Manage the current profile's Google Workspace authorization.",
    emoji="🔐",
)

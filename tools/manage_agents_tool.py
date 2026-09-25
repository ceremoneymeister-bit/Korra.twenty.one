"""Let the main agent improve the installation's agents at the owner's request.

The owner should not need the settings screens to make an agent better: they
ask the main agent, which reads the other agent and edits its role, memory,
materials, name or description through :mod:`korra_cli.profile_editor`
(journaled, undoable).

Profiles are not isolated on disk (``agent/file_safety.py``), so this tool is
not a new privilege: it is the reviewed path with an owner check, a scan of
the new role, conflict detection and undo, instead of raw file writes.

Who gets it — decided per call from server-bound state, never from arguments:

* the main agent (profile ``default``) with ``agent.manage_profiles: main``;
* the owner speaking live (cabinet, own computer, or the owner's direct chat);
* not a delegate_task child, a scheduled job, a Kanban worker or a one-shot
  ``chat -q`` run (bot-chat delivery, agent-to-agent messages).
"""

from __future__ import annotations

import json

from tools.registry import no_cache_check_fn, registry, tool_error

_ACTIONS = ("list", "show", "update_role", "memory", "add_material", "remove_material",
            "rename", "describe", "history", "undo")

_OWNER_ONLY = (
    "Changing agents is available only to the main agent when the owner asks "
    "in their own conversation (the Korra cabinet, the owner's computer, or "
    "the owner's direct chat). Tell the owner to ask there."
)


def _setting() -> str:
    try:
        from korra_cli.config import cfg_get, load_config

        return str(cfg_get(load_config(), "agent", "manage_profiles", default="main")).strip().lower()
    except Exception:
        return "off"


def _refusal() -> str | None:
    """Why this turn may not change agents, or None when it may."""
    try:
        from agent.delegation_context import is_delegated_child_context
        from gateway.principal import current_principal
        from gateway.session_context import get_session_env
        from korra_cli.profiles import get_active_profile_name
        from utils import is_truthy_value
    except Exception:
        return _OWNER_ONLY
    if _setting() != "main":
        return "Changing agents from chat is turned off in this installation (agent.manage_profiles)."
    try:
        if get_active_profile_name() != "default":
            return "Only the main agent changes other agents. Ask the owner to use the main agent."
        if is_delegated_child_context():
            return _OWNER_ONLY
        if is_truthy_value(get_session_env("KORRA_SINGLE_QUERY_SESSION", "")):
            return _OWNER_ONLY
        principal = current_principal()
    except Exception:
        return _OWNER_ONLY
    if principal.kind != "owner" or not (principal.owner and principal.live):
        return _OWNER_ONLY
    return None


@no_cache_check_fn
def _available() -> bool:
    return _refusal() is None


def _text(args: dict, key: str) -> str:
    value = args.get(key)
    return value if isinstance(value, str) else ""


def _dispatch(args: dict) -> dict:
    from korra_cli import profile_editor as editor

    action = _text(args, "action")
    agent = _text(args, "agent")
    reason = _text(args, "reason")
    if action == "list":
        return editor.list_agents()
    if action == "show":
        return editor.show_agent(agent)
    if action == "history":
        return editor.history(agent, int(args.get("limit") or 20))
    if action == "update_role":
        content = args.get("content") if isinstance(args.get("content"), str) else None
        return editor.update_role(agent, content=content, old_text=_text(args, "old_text"),
                                  new_text=_text(args, "new_text"), version=_text(args, "version"), reason=reason)
    if action == "memory":
        return editor.change_memory(agent, action=_text(args, "memory_action"), target=_text(args, "target") or "memory",
                                    content=_text(args, "content"), old_text=_text(args, "old_text"), reason=reason)
    if action == "add_material":
        return editor.add_material(agent, title=_text(args, "title"), text=_text(args, "content"),
                                   url=_text(args, "url"), reason=reason)
    if action == "remove_material":
        return editor.remove_material(agent, material=_text(args, "material"), reason=reason)
    if action == "rename":
        return editor.rename(agent, value=_text(args, "value"), reason=reason)
    if action == "describe":
        return editor.describe(agent, value=_text(args, "value"), reason=reason)
    if action == "undo":
        return editor.undo(_text(args, "change_id"), reason=reason)
    raise editor.ProfileEditError("action: " + ", ".join(_ACTIONS))


def _handle(args: dict, **_kwargs) -> str:
    from korra_cli.profile_editor import ProfileEditConflict

    refusal = _refusal()
    if refusal:
        return tool_error(refusal)
    try:
        result = _dispatch(args or {})
    except ProfileEditConflict as exc:
        return json.dumps({"ok": False, "error": "conflict", "message": str(exc)}, ensure_ascii=False)
    except FileNotFoundError as exc:
        return json.dumps({"ok": False, "error": "not_found", "message": str(exc)}, ensure_ascii=False)
    except (ValueError, TypeError) as exc:
        return json.dumps({"ok": False, "error": "invalid_request", "message": str(exc)}, ensure_ascii=False)
    except OSError:
        return json.dumps({"ok": False, "error": "io_error",
                           "message": "Не удалось прочитать или сохранить данные агента. Повторите позже."}, ensure_ascii=False)
    return json.dumps(result, ensure_ascii=False)


_DESCRIPTION = (
    "Improve the installation's other agents (and yourself) when the owner asks: read an agent, "
    "edit its role (SOUL), memory, reference materials, display name or description; list and undo changes. "
    "Workflow: `show` the agent first; make the smallest change that does what the owner asked; keep the "
    "role's existing structure, language and rules; add nothing the owner did not ask for. For small edits "
    "use update_role with old_text/new_text copied exactly from `role`; pass `version` from show. For "
    "'all agents' requests, apply to each agent separately. Memory entries are short facts (add/replace/"
    "remove with the exact entry text); long texts go to materials. After changing, tell the owner in plain "
    "words what changed, that it applies to the agent's new chats, and that you can undo it. Model, tools, "
    "keys, channels, schedules and permissions are not changed here — point the owner to Settings."
)

registry.register(
    name="manage_agents",
    toolset="agent_profiles",
    schema={
        "name": "manage_agents",
        "description": _DESCRIPTION,
        "parameters": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": list(_ACTIONS)},
                "agent": {"type": "string", "description": "Agent id or its display name; required except for list, history (optional) and undo."},
                "content": {"type": "string", "description": "update_role: the whole new role; memory: the entry text; add_material: the material text."},
                "old_text": {"type": "string", "description": "update_role: exact fragment to replace; memory replace/remove: the exact current entry."},
                "new_text": {"type": "string", "description": "update_role: replacement for old_text."},
                "version": {"type": "string", "description": "role_version from show; protects against overwriting a newer role."},
                "memory_action": {"type": "string", "enum": ["add", "replace", "remove"]},
                "target": {"type": "string", "enum": ["memory", "user"], "description": "memory: agent notes (memory) or facts about the owner (user)."},
                "title": {"type": "string", "description": "add_material: short title."},
                "url": {"type": "string", "description": "add_material: a link instead of or with text."},
                "material": {"type": "string", "description": "remove_material: the material id from show."},
                "value": {"type": "string", "description": "rename: new display name; describe: new description."},
                "change_id": {"type": "string", "description": "undo: change id from a result or history."},
                "reason": {"type": "string", "description": "The owner's request in a few words, kept in the change history."},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100},
            },
            "required": ["action"],
            "additionalProperties": False,
        },
    },
    handler=_handle,
    check_fn=_available,
    description="The main agent improves the installation's agents for the owner.",
    emoji="🛠️",
)

"""OpenAI native web search — the Codex Responses server-side ``web_search`` built-in.

Never searches by itself. On the Codex Responses transport (provider ``openai-codex``)
the client ``web_search`` function is swapped for ``{"type": "web_search"}`` and the
model searches server-side, on the ChatGPT/Codex subscription (no API key, no pay-per-use).
See ``agent/transports/codex.py``. The provider exists so ``web.search_backend`` has a
real name to point at and so ``search()`` fails honestly when a transport cannot host the
built-in (the keyless ring then rescues that call).
"""

from __future__ import annotations

import json
from typing import Any, Dict

from agent.web_search_provider import WebSearchProvider

_UNSUPPORTED_MSG = (
    "openai-native is OpenAI's server-side web_search for provider openai-codex; "
    "it cannot run as a client-side search on this transport."
)


def _dget(obj: Any, key: str) -> Any:
    return obj.get(key) if isinstance(obj, dict) else None


def has_codex_credentials() -> bool:
    """Cheap probe: openai-codex OAuth tokens in ``auth.json`` (no refresh, no network)."""
    try:
        from korra_constants import get_hermes_home

        auth_path = get_hermes_home() / "auth.json"
        if not auth_path.exists():
            return False
        store = json.loads(auth_path.read_text(encoding="utf-8-sig"))
        tokens = _dget(_dget(_dget(store, "providers"), "openai-codex"), "tokens")
        if str(_dget(tokens, "access_token") or "").strip():
            return True
        entries = _dget(_dget(store, "credential_pool"), "openai-codex")
        return isinstance(entries, list) and any(
            isinstance(e, dict) and str(e.get("access_token", "") or "").strip() for e in entries
        )
    except Exception:  # noqa: BLE001 — availability must never raise
        return False


class OpenAINativeWebSearchProvider(WebSearchProvider):
    @property
    def name(self) -> str:
        return "openai-native"

    @property
    def display_name(self) -> str:
        return "OpenAI Native Web Search (Codex)"

    def is_available(self) -> bool:
        return has_codex_credentials()

    def supports_search(self) -> bool:
        return True

    def supports_extract(self) -> bool:
        return False

    def search(self, query: str, limit: int = 5) -> Dict[str, Any]:
        return {"success": False, "error": _UNSUPPORTED_MSG}

    def get_setup_schema(self) -> Dict[str, Any]:
        return {
            "name": "OpenAI Native Web Search (Codex)",
            "badge": "subscription · search only",
            "tag": "Server-side search on the ChatGPT/Codex subscription — only for provider openai-codex",
            "env_vars": [],
        }

"""Calculator dashboard boundaries, independent of the fleet/admin cabinet.

The calculator has immutable agent roles. Its dashboard can operate orders,
enterprise packs and inbox files; runtime administration belongs to deployment.
These gates also cover direct API and WebSocket calls, not just navigation.
"""

import re

from starlette.responses import JSONResponse
from fastapi import HTTPException

from korra_constants import korra_env


def calculator_mode() -> bool:
    return korra_env("KORRA_UI_MODE", "").strip().lower() == "calc"


def _allows_mutation(path: str) -> bool:
    if path == "/api/calc" or path.startswith("/api/calc/"):
        return True
    if path in {
        "/api/env", "/api/env/reveal", "/api/model/set",
        "/api/dashboard/theme", "/api/dashboard/font",
        "/api/chat/completions", "/api/chat/upload", "/api/chat/approval",
        "/api/files/upload", "/api/files/upload-stream", "/api/files/mkdir",
        "/api/files/text", "/api/files/rename", "/api/files/trash",
        "/api/files/trash/restore", "/api/files/trash/purge",
    }:
        return True
    if path.startswith(("/api/providers/oauth/", "/api/providers/custom-endpoints")):
        return True
    if path == "/api/providers/validate":
        return True
    # Browser chat delivery/cancel is scoped to the chat, not agent setup.
    return path.startswith("/api/chat/messages/")


class CalculatorBoundaryMiddleware:
    """Block administrative mutation and shell-capable WS in calculator mode."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if calculator_mode():
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 1008})
                return
            if scope["type"] == "http":
                path = scope.get("path", "")
                method = scope.get("method", "GET")
                if (
                    path == "/api/config/raw"
                    or path.startswith("/api/")
                    and method not in {"GET", "HEAD", "OPTIONS"}
                    and not _allows_mutation(path)
                ):
                    response = JSONResponse(
                        {"detail": "Настройки расчётчика управляются при развёртывании"},
                        status_code=403,
                    )
                    await response(scope, receive, send)
                    return
        await self.app(scope, receive, send)


_CREDENTIAL_FIELD = re.compile(
    r"(?:^|_)(?:key|token|secret|password|passwd|authorization|cookie|bearer)$",
    re.IGNORECASE,
)


def redact_runtime_config(value):
    """Copy config for the calculator UI without resolved runtime credentials.

    Env values and HTTP headers are transport configuration, never editable
    model settings. Mask every value there, including custom key names. Other
    nested credential fields (auxiliary/custom model providers) are also masked.
    Do not mutate the effective config object shared with the runtime.
    """
    if isinstance(value, dict):
        result = {}
        for key, child in value.items():
            normalized = str(key).replace("-", "_")
            if normalized.lower() in {"env", "headers"} and isinstance(child, dict):
                result[key] = {name: "***" if entry else "" for name, entry in child.items()}
            elif _CREDENTIAL_FIELD.search(normalized) and not normalized.lower().endswith("_env"):
                result[key] = "***" if child else ""
            else:
                result[key] = redact_runtime_config(child)
        return result
    if isinstance(value, list):
        return [redact_runtime_config(item) for item in value]
    return value


PROVIDER_CREDENTIAL_KEYS = frozenset({
    # Deliberately explicit: provider catalogs may contain shared aliases
    # such as GH_TOKEN/GITHUB_TOKEN that also authorize repository access.
    # A catalog label is presentation metadata, never an authorization rule.
    "AI_GATEWAY_API_KEY",
    "ALIBABA_CODING_PLAN_API_KEY",
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_TOKEN",
    "ARCEEAI_API_KEY",
    "AZURE_FOUNDRY_API_KEY",
    "CLAUDE_CODE_OAUTH_TOKEN",
    "COPILOT_GITHUB_TOKEN",
    "DASHSCOPE_API_KEY",
    "DEEPINFRA_API_KEY",
    "DEEPSEEK_API_KEY",
    "FIREWORKS_API_KEY",
    "GEMINI_API_KEY",
    "GLM_API_KEY",
    "GMI_API_KEY",
    "GOOGLE_API_KEY",
    "HF_TOKEN",
    "KILOCODE_API_KEY",
    "KIMI_API_KEY",
    "KIMI_CN_API_KEY",
    "KIMI_CODING_API_KEY",
    "LM_API_KEY",
    "MINIMAX_API_KEY",
    "MINIMAX_CN_API_KEY",
    "NVIDIA_API_KEY",
    "NOVITA_API_KEY",
    "OLLAMA_API_KEY",
    "OPENCODE_GO_API_KEY",
    "OPENCODE_ZEN_API_KEY",
    "OPENAI_API_KEY",
    "OPENROUTER_API_KEY",
    "STEPFUN_API_KEY",
    "TOKENHUB_API_KEY",
    "UPSTAGE_API_KEY",
    "XAI_API_KEY",
    "XIAOMI_API_KEY",
    "ZAI_API_KEY",
    "Z_AI_API_KEY",
})

def require_calculator_credential_key(key: str) -> None:
    if calculator_mode() and key not in PROVIDER_CREDENTIAL_KEYS:
        raise HTTPException(status_code=403, detail="Доступны только ключи провайдеров моделей")

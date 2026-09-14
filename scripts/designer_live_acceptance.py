#!/usr/bin/env python3
"""One live presentation result from an installed Designer in a disposable container.

Explicit opt-in only: KORRA_DESIGNER_LIVE=subscription-only, plus a fresh --out.
Input on stdin: {"auth_mode": "chatgpt", "access_token": "..."}. Never pass
refresh tokens, API keys, user DATA, or a host home into this container. Its /tmp
must be tmpfs; only the result directory is persisted. Source is read-only.
This supplements, not replaces, UI and exact release artifact acceptance.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import time

from designer_preflight import ENGINE, FIXTURES, dependencies, install_template, native_terminal_probe


def subscription_token(payload: dict) -> str:
    """Require an unexpired access-only ChatGPT login, never API billing."""
    if set(payload) != {"auth_mode", "access_token"} or payload.get("auth_mode") != "chatgpt":
        raise ValueError("Expected access-only ChatGPT subscription credentials")
    token = payload.get("access_token")
    if not isinstance(token, str) or len(token.split(".")) != 3:
        raise ValueError("Expected an OAuth access token, not an API key")
    encoded = token.split(".")[1]
    claims = json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
    if float(claims.get("exp", 0)) < time.time() + 1200:
        raise ValueError("Subscription access expires too soon; sign in normally before the test")
    return token


def validate_output_scope(output: Path) -> None:
    """Catch a mis-mounted stand before using any subscription quota."""
    if str(ENGINE) not in sys.path:
        sys.path.insert(0, str(ENGINE))
    from agent.file_safety import get_safe_write_roots
    safe_roots = get_safe_write_roots()
    if safe_roots and not any(output.resolve().is_relative_to(Path(root)) for root in safe_roots):
        raise ValueError("Test output is outside the image's write-safe root; fix the mount, not the product guard")


def export_visible_response(value):
    """Keep the visible transcript/metrics, not private provider reasoning."""
    private = {"last_reasoning", "reasoning_content", "reasoning",
               "codex_reasoning_items", "encrypted_content", "thinking",
               "pre_transform_response"}
    if isinstance(value, dict):
        return {key: export_visible_response(item) for key, item in value.items() if key not in private}
    if isinstance(value, list):
        return [export_visible_response(item) for item in value
                if not (isinstance(item, dict) and item.get("type") in {"reasoning", "thinking", "redacted_thinking"})]
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--model", default="gpt-5.6-sol")
    args = parser.parse_args()
    if os.environ.get("KORRA_DESIGNER_LIVE") != "subscription-only" or not Path("/.dockerenv").exists():
        parser.error("Run only in an explicitly enabled disposable subscription test container")
    validate_output_scope(args.out)
    token = subscription_token(json.load(sys.stdin))
    missing = [key for key, available in dependencies().items() if not available]
    if missing:
        raise RuntimeError(f"Offline preflight required; missing: {missing}")
    output = args.out.resolve()
    output.mkdir(parents=True, exist_ok=False)
    workspace = output / "workspace"
    workspace.mkdir()
    home = Path(tempfile.mkdtemp(prefix="designer-live-", dir="/tmp"))
    os.environ.update(HERMES_HOME=str(home), KORRA_HOME=str(home),
                      HERMES_DASHBOARD_SESSION_TOKEN="designer-offline-preflight",
                      TERMINAL_ENV="local", TERMINAL_CWD=str(workspace))
    # The test has no API-billed provider, fallback, external MCP or messaging.
    for name in ("OPENAI_API_KEY", "OPENROUTER_API_KEY", "ANTHROPIC_API_KEY",
                 "KORRA_CODEX_BASE_URL", "HERMES_CODEX_BASE_URL"):
        os.environ.pop(name, None)
    sys.path.insert(0, str(ENGINE))
    import yaml

    report = {"kind": "designer_live_presentation", "provider": "openai-codex",
              "model": args.model, "auth_mode": "chatgpt", "billing": "subscription-only",
              "image_generation": "DISABLED", "other_cases": "NOT_RUN",
              "visual_acceptance": "PENDING", "status": "RUNNING"}
    started = time.monotonic()

    def save_json(path: Path, value: object) -> None:
        text = json.dumps(value, ensure_ascii=False, indent=2, default=str)
        path.write_text(text.replace(token, "[REDACTED]"), encoding="utf-8")

    try:
        report["installation"] = install_template(home, model=args.model)
        profile = home / "profiles/designer"
        report["native_terminal"] = native_terminal_probe(profile)
        # Access-only credential pool is a native runtime path. There is no
        # refresh token to rotate or invalidate the operator's original login.
        auth = {"version": 1, "active_provider": "openai-codex", "providers": {},
                "credential_pool": {"openai-codex": [{
                    "id": "designer-acceptance", "label": "Subscription acceptance",
                    "auth_type": "oauth", "priority": 0, "source": "manual:acceptance",
                    "access_token": token, "base_url": "https://chatgpt.com/backend-api/codex",
                }]}}
        auth_path = profile / "auth.json"
        auth_path.write_text(json.dumps(auth), encoding="utf-8")
        auth_path.chmod(0o600)
        config_path = profile / "config.yaml"
        config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        config.update({"fallback_model": [], "mcp_servers": {},
                       "terminal": {"env": "local", "cwd": str(workspace)},
                       "compression": {"enabled": False},
                       "auxiliary": {"vision": {"provider": "openai-codex", "model": args.model}}})
        config_path.write_text(yaml.safe_dump(config, allow_unicode=True), encoding="utf-8")
        os.environ.update(HERMES_HOME=str(profile), KORRA_HOME=str(profile))
        os.chdir(workspace)
        from korra_constants import set_hermes_home_override
        set_hermes_home_override(str(profile))
        from korra_cli.runtime_provider import resolve_runtime_provider
        runtime = resolve_runtime_provider(requested="openai-codex", target_model=args.model)
        if runtime["provider"] != "openai-codex" or runtime["base_url"] != "https://chatgpt.com/backend-api/codex":
            raise RuntimeError("Refusing a non-subscription runtime")
        if runtime["api_key"] != token:
            raise RuntimeError("Unexpected runtime credentials")
        from run_agent import AIAgent

        tool_events = []

        def progress(*event, **details):
            # Tool names/status only in console; complete conversation is
            # retained separately, scrubbed of the sole test access token.
            status = str(event[0]) if event else "tool"
            name = str(event[1]) if len(event) > 1 else "unknown"
            entry = {"event": status, "name": name}
            if "is_error" in details:
                entry["is_error"] = bool(details["is_error"])
            tool_events.append(entry)
            print(json.dumps(entry), flush=True)

        agent = AIAgent(
            provider="openai-codex", model=args.model, api_key=runtime["api_key"],
            base_url=runtime["base_url"], api_mode="codex_responses",
            enabled_toolsets=["terminal", "file", "skills", "vision", "todo"],
            disabled_toolsets=["image_gen", "web", "browser", "cronjob", "delegation"],
            load_soul_identity=True, skip_context_files=True, skip_memory=True,
            skip_background_review=True, fallback_model=[], quiet_mode=True,
            max_iterations=40, max_tokens=12000, run_budget_seconds=900,
            tool_progress_callback=progress,
        )
        report["tools"] = sorted(agent.valid_tool_names)
        forbidden = {"image_generate", "delegate_task", "cronjob", "send_message"}
        if forbidden.intersection(agent.valid_tool_names):
            raise RuntimeError("Unexpected enabled tool outside the single presentation case")
        report["soul_sha256"] = hashlib.sha256((profile / "SOUL.md").read_bytes()).hexdigest()
        report["skill_sha256"] = hashlib.sha256((profile / "skills/visual-design/SKILL.md").read_bytes()).hexdigest()
        prompt = json.loads((FIXTURES / "tasks.json").read_text(encoding="utf-8"))["presentation"]["prompt"]
        prompt += "\n\n" + (FIXTURES / "brief.md").read_text(encoding="utf-8")
        prompt += f"\n\nРабочая папка и итоговые файлы: {workspace}. Это первый вариант, после него жди оценки пользователя."
        (output / "request.md").write_text(prompt, encoding="utf-8")
        save_json(output / "report.json", report)
        result = agent.run_conversation(prompt)
        save_json(output / "response.json", export_visible_response(result))
        (output / "answer.md").write_text(str(result.get("final_response") or "").replace(token, "[REDACTED]"), encoding="utf-8")
        report["status"] = "RETURNED"  # Not PASS: review actual artifacts first.
        report["tool_events"] = tool_events
        report["model_requests"] = result.get("api_calls")
        report["runtime_error"] = result.get("error")
        report["files"] = sorted(str(path.relative_to(workspace)) for path in workspace.rglob("*") if path.is_file())
    except Exception as exc:
        report["status"] = "FAIL"
        report["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        report["elapsed_seconds"] = round(time.monotonic() - started, 2)
        save_json(output / "report.json", report)
        print(json.dumps(report, ensure_ascii=False, default=str).replace(token, "[REDACTED]"), flush=True)
    return 0 if report["status"] == "RETURNED" else 1


if __name__ == "__main__":
    raise SystemExit(main())

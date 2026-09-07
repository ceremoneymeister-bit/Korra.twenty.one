#!/usr/bin/env python3
"""Create a fresh local pilot. Never copy provider credentials or live sessions."""
import argparse
import json
import os
from pathlib import Path
import secrets

import yaml


ROLES = {
    "default": ("front", "Приём заказов"),
    "raschet-route": ("tech", "Технолог"),
    "raschet-blank": ("supply", "Материалы и заготовка"),
    "raschet-time": ("norm", "Нормировщик"),
}
SCOPED = ("order_get", "workflow_status", "bom_upsert", "route_variants_propose",
          "route_freeze", "blank_drivers_set", "supply_confirm", "time_norms_set")


def write(path, content, mode=0o640):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    path.chmod(mode)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--prompts", type=Path, required=True)
    args = parser.parse_args()
    root = args.root.resolve()
    data = root / "data"
    if (data / "config.yaml").exists():
        raise SystemExit("Existing pilot config: bootstrap refuses to overwrite it")
    if not (root / "rates/_active.json").is_file():
        raise SystemExit("Copy a verified rates pack into ROOT/rates before bootstrap")
    for name in ROLES:
        source = args.prompts / "SOUL.md" if name == "default" else args.prompts / "profiles" / name / "SOUL.md"
        if not source.is_file():
            raise SystemExit(f"Missing role prompt: {source}")
    api_key, scope_secret = secrets.token_hex(32), secrets.token_hex(32)
    runtime = f"API_SERVER_KEY={api_key}\nAPI_SERVER_PORT=8661\nMETAL_CALC_SCOPE_SECRET={scope_secret}\n"
    write(root / "runtime.env", runtime, 0o600)
    write(data / ".env", runtime, 0o600)
    env = {
        "METAL_CALC_ORDERS_ROOT": "/opt/data/orders",
        "METAL_CALC_RATES_ROOT": "/etc/metal-calc/rates",
        "METAL_CALC_DELIVERY_ROOT": "/opt/data/delivery",
        "METAL_CALC_CACHE_ROOT": "/opt/data/cache/documents",
        "METAL_CALC_IMAGE_DIGEST": "${METAL_CALC_IMAGE_DIGEST}",
        "METAL_CALC_SCOPE_SECRET": "${METAL_CALC_SCOPE_SECRET}",
    }
    server = {"command": "/opt/metal-calc/bin/metal-calc-mcp", "args": [],
              "env": env, "timeout": 180, "connect_timeout": 30,
              "supports_parallel_tool_calls": False,
              "tools": {"resources": False, "prompts": False}}
    policy = {
        "trust": {"preset": "standard"},
        "agent": {"disabled_toolsets": ["terminal", "code_execution", "delegation", "cronjob",
                  "file", "web", "search", "browser", "image_gen", "computer_use", "kanban",
                  "context_engine", "project"]},
        "platform_toolsets": {"api_server": ["metal_calc"], "telegram": ["metal_calc", "clarify"]},
        "mcp_servers": {"metal_calc": server},
        "tools": {"tool_search": {"enabled": "off"}},
        "skills": {"inline_shell": False, "creation_nudge_interval": 0, "write_approval": True},
        "curator": {"enabled": False},
        "memory": {"memory_enabled": False, "user_profile_enabled": False},
    }
    write(root / "policy/config.yaml", yaml.safe_dump(policy, allow_unicode=True, sort_keys=False))
    pilot_note = (
        "# Локальный пилот расчётчика Korra 21\n\n"
        "Текущий канал — кабинет оператора. Telegram в этом пилоте не подключён. "
        "Загрузка папки в Файлах заказов создаёт сохранённый черновик. "
        "Серверное чтение S0–S1 хранит наблюдения; состав и входы расчёта подтверждает сотрудник. "
        "Используй только доступные инструменты и проверенные источники. "
        "Пока действуют прежние данные предприятия; окончательные цены требуют "
        "подтверждённых правил и проверки человеком.\n\n"
    )
    for name, (role, label) in ROLES.items():
        home = data if name == "default" else data / "profiles" / name
        cfg = {
            "model": {"provider": "openai-codex", "default": "gpt-5.6-sol"},
            "agent": {"max_turns": 60, "reasoning_effort": "high", "max_output_tokens": 8192},
            "streaming": {"enabled": True},
            "api_server": {"enabled": True},
            "platforms": {"telegram": {"enabled": False}, "api_server": {"enabled": True}},
            "mcp_servers": {"metal_calc": {"env": {"METAL_CALC_ROLE": role, "METAL_CALC_PROFILE": name}}},
        }
        if role != "front":
            cfg["mcp_servers"]["metal_calc"]["context_arguments"] = {
                tool: {"hermes_session_context": "request_scope"} for tool in SCOPED
            }
        else:
            cfg["gateway"] = {
                "multiplex_profiles": True,
                "multiplex_profile_allowlist": [profile for profile in ROLES if profile != "default"],
            }
        write(home / "config.yaml", yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False))
        write(home / "profile.yaml", yaml.safe_dump({"display_name": label, "description": label}, allow_unicode=True))
        # Only the default gateway owns the port. Named profiles are served
        # through /p/<profile>/ by its multiplex runtime.
        state = "running" if name == "default" else "stopped"
        write(home / "gateway_state.json", json.dumps({"gateway_state": state}) + "\n")
        if name != "default":
            write(home / ".env", f"API_SERVER_PORT=8661\nAPI_SERVER_KEY={api_key}\n", 0o600)
        source = args.prompts / "SOUL.md" if name == "default" else args.prompts / "profiles" / name / "SOUL.md"
        if role == "front":
            source = Path(__file__).with_name("front-soul.md")
        write(home / "SOUL.md", pilot_note + source.read_text())
    for rel in ("client/inbox", "client/artifacts", "cache/documents", "orders", "delivery", "handoff"):
        (data / rel).mkdir(parents=True, exist_ok=True)
    for tree in (data, root / "rates", root / "policy"):
        for path in [tree, *tree.rglob("*")]:
            if path.is_symlink():
                raise SystemExit(f"Unexpected symlink in pilot: {path}")
            os.chown(path, 10000, 10000)
            if path.is_dir():
                path.chmod(0o750)
    print("Fresh pilot prepared; no provider credentials or Telegram tokens copied.")


if __name__ == "__main__":
    main()

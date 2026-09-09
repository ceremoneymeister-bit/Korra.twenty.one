"""The dashboard's existing two palettes, shared with its pre-paint bootstrap."""
from __future__ import annotations
import hashlib
import json
import re
from pathlib import Path
from typing import Any

_DATA = json.loads((Path(__file__).parent / "data/dashboard-themes.json").read_text())
THEMES = _DATA["themes"]


def normalize_theme(value: Any) -> str:
    name = value.strip().lower() if isinstance(value, str) else ""
    return "dark" if name == "dark" or name in _DATA["legacy_dark"] else "light"


def preference(config: dict, installation_id: str | None, owner: str, base_path: str = "") -> dict:
    dashboard = config.get("dashboard")
    dashboard = dashboard if isinstance(dashboard, dict) else {}
    raw = dashboard.get("theme")
    evening_raw = dashboard.get("evening_prompt")
    evening_raw = evening_raw if isinstance(evening_raw, dict) else {}
    until = evening_raw.get("snooze_until", 0)
    # Bound before any float conversion: YAML integers can exceed float range.
    # Year 9999 is safely representable in JS milliseconds; NaN/inf also fail.
    until = until if isinstance(until, (int, float)) and not isinstance(until, bool) and 0 <= until <= 253402300799 else 0
    evening = {"disabled": evening_raw["disabled"] if isinstance(evening_raw.get("disabled"), bool) else normalize_theme(raw) == "dark", "snooze_until": until}
    # An operator's config edit invalidates caches without updating our field.
    revision = hashlib.sha256(json.dumps(
        [raw, dashboard.get("theme_revision"), evening], sort_keys=True, default=str,
    ).encode()).hexdigest()[:24]
    return {
        "version": 1, "theme": normalize_theme(raw), "known": True,
        "installation_id": installation_id,
        "owner": hashlib.sha256(owner.encode()).hexdigest()[:16],
        "base_path": base_path, "revision": revision, "evening": evening,
    }


def bootstrap_css(value: dict) -> str:
    theme = THEMES[normalize_theme(value.get("theme"))]
    variables = {}
    for name, layer in theme["palette"].items():
        if not isinstance(layer, dict):
            continue
        variables[f"--{name}-base"] = layer["hex"]
        variables[f"--{name}-alpha"] = str(layer["alpha"])
        variables[f"--{name}"] = f'color-mix(in srgb, {layer["hex"]} {round(layer["alpha"] * 100)}%, transparent)'
    def kebab(name):
        return re.sub(r"[A-Z]", lambda m: "-" + m[0].lower(), name)
    variables.update({"--" + kebab(k): v for k, v in theme["colorOverrides"].items()})
    variables.update({"--neo-" + kebab(k): v for k, v in theme["neumorphism"].items()})
    variables.update({
        "--theme-font-sans": theme["typography"]["fontSans"],
        "--theme-font-display": theme["typography"]["fontSans"],
        "--theme-font-mono": theme["typography"]["fontMono"],
        "--theme-base-size": theme["typography"]["baseSize"],
        "--theme-terminal-background": theme["terminalBackground"],
        "--theme-terminal-foreground": theme["terminalForeground"],
        "--series-input-token": theme["seriesColors"]["inputTokenAccent"],
        "--series-output-token": theme["seriesColors"]["outputTokenAccent"],
    })
    # Trusted shipped tokens, never CSS from configuration or browser cache.
    css = "".join(f"{key}:{val};" for key, val in variables.items())
    return (
        '<style id="hermes-theme-bootstrap">:root{' + css
        + f'color-scheme:{normalize_theme(value.get("theme"))};'
        + "}html,body{background-color:var(--background-base);color:var(--midground-base);"
        + "font-family:var(--theme-font-sans);font-size:var(--theme-base-size);}</style>"
    )

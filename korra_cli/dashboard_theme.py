"""The dashboard's palettes, shared with its pre-paint bootstrap."""
from __future__ import annotations
import colorsys
import copy
import math
import hashlib
import json
import re
from pathlib import Path
from typing import Any

_DATA = json.loads((Path(__file__).parent / "data/dashboard-themes.json").read_text(encoding="utf-8"))
THEMES = _DATA["themes"]
DEFAULT_COLOR = "#5275d9"


def normalize_color(value: Any) -> str:
    return value.lower() if isinstance(value, str) and re.fullmatch(r"#[0-9a-fA-F]{6}", value) else DEFAULT_COLOR


def color_theme(value: str) -> dict:
    """Same bounded HSL palette as web/src/themes/color.ts, for first paint."""
    def rgb(color):
        return [int(color[i:i + 2], 16) / 255 for i in (1, 3, 5)]

    def hex_color(channels):
        return "#" + "".join(f"{math.floor(c * 255 + .500000001):02x}" for c in channels)

    def mix(a, b, weight):
        return hex_color([x * (1 - weight) + y * weight for x, y in zip(rgb(a), rgb(b))])

    def luminance(color):
        linear = [c / 12.92 if c <= .04045 else ((c + .055) / 1.055) ** 2.4 for c in rgb(color)]
        return sum(c * w for c, w in zip(linear, (.2126, .7152, .0722)))

    def contrast(a, b):
        x, y = luminance(a), luminance(b)
        return (max(x, y) + .05) / (min(x, y) + .05)

    h, raw_l, saturation = colorsys.rgb_to_hls(*rgb(normalize_color(value)))
    lightness = max(.35, min(.6, raw_l))
    accent = hex_color(colorsys.hls_to_rgb(h, lightness, saturation))
    def gradient_end(lightness):
        return mix(hex_color(colorsys.hls_to_rgb((h + .05) % 1, lightness, saturation)), "#ffffff", .89)

    while min(contrast(accent, mix(accent, "#ffffff", .88)), contrast(accent, gradient_end(lightness))) < 3:
        lightness -= .005
        accent = hex_color(colorsys.hls_to_rgb(h, lightness, saturation))
    end = gradient_end(lightness)
    line_l = lightness
    accent_line = accent
    while min(contrast(accent_line, mix(accent, "#ffffff", .88)), contrast(accent_line, end)) < 4.5:
        line_l -= .005
        accent_line = hex_color(colorsys.hls_to_rgb(h, line_l, saturation))
    background = mix(accent, "#ffffff", .94)
    surface = mix(accent, "#ffffff", .88)
    shadow = mix(surface, accent, .22)
    foreground = "#000000" if contrast(accent, "#000000") >= contrast(accent, "#ffffff") else "#ffffff"
    theme = copy.deepcopy(THEMES["light"])
    theme.update(name="color", label="Цвет", description="Любой цвет с мягким градиентом")
    theme["palette"]["background"]["hex"] = background
    theme["neumorphism"].update(background=background, surface=surface, shadow=shadow,
        highlight=mix(surface, "#ffffff", .8), textSecondary="#505050", accent=accent,
        accentLine=accent_line, accentForeground=foreground)
    theme["colorOverrides"].update(card=surface, popover=surface, secondary=surface, muted=surface,
        primary=accent, accent=accent, primaryForeground=foreground, accentForeground=foreground,
        mutedForeground="#505050", border=shadow, input=shadow, ring=accent)
    theme["assets"] = {"bg": f"linear-gradient(135deg, {background}, {end})"}
    theme["terminalBackground"] = surface
    theme["seriesColors"] = {"inputTokenAccent": accent, "outputTokenAccent": "#505050"}
    return theme


def normalize_theme(value: Any) -> str:
    name = value.strip().lower() if isinstance(value, str) else ""
    return "color" if name == "color" else "dark" if name == "dark" or name in _DATA["legacy_dark"] else "light"


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
    revision_data = [raw, dashboard.get("theme_revision"), evening]
    if "theme_color" in dashboard:
        revision_data.append(dashboard["theme_color"])
    revision = hashlib.sha256(json.dumps(
        revision_data, sort_keys=True, default=str,
    ).encode()).hexdigest()[:24]
    return {
        "version": 1, "theme": normalize_theme(raw), "known": True,
        "installation_id": installation_id,
        "owner": hashlib.sha256(owner.encode()).hexdigest()[:16],
        "base_path": base_path, "revision": revision, "evening": evening,
        **({"color": normalize_color(dashboard.get("theme_color"))}
           if normalize_theme(raw) == "color" or "theme_color" in dashboard else {}),
    }


def bootstrap_css(value: dict) -> str:
    theme_name = normalize_theme(value.get("theme"))
    theme = color_theme(value.get("color")) if theme_name == "color" else THEMES[theme_name]
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
    if theme_name == "color":
        variables["--theme-asset-bg"] = theme["assets"]["bg"]
    # Shipped tokens or derived from a strictly validated hex color.
    css = "".join(f"{key}:{val};" for key, val in variables.items())
    return (
        '<style id="hermes-theme-bootstrap">:root{' + css
        + f'color-scheme:{"dark" if theme_name == "dark" else "light"};'
        + "}html,body{background-color:var(--background-base);color:var(--midground-base);"
        + "font-family:var(--theme-font-sans);font-size:var(--theme-base-size);"
        + "background-image:var(--theme-asset-bg,none);}</style>"
    )

"""The dashboard's palettes, shared with its pre-paint bootstrap."""
from __future__ import annotations
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


def _rgb(color):
    return [int(color[i:i + 2], 16) / 255 for i in (1, 3, 5)]


def _luminance(color):
    return sum((c / 12.92 if c <= .04045 else ((c + .055) / 1.055) ** 2.4) * w
               for c, w in zip(_rgb(color), (.2126, .7152, .0722)))


def color_scheme(value: Any, scheme: Any = None) -> str:
    """Infer only legacy choices; an explicit scheme does not depend on the hue."""
    if scheme in ("light", "dark"):
        return scheme
    return "dark" if _luminance(normalize_color(value)) < .35 else "light"


def _chroma_hue(color):
    r, g, b = [c / 12.92 if c <= .04045 else ((c + .055) / 1.055) ** 2.4 for c in _rgb(color)]
    l = (.4122214708*r + .5363325363*g + .0514459929*b) ** (1/3)
    m = (.2119034982*r + .6806995451*g + .1073969566*b) ** (1/3)
    s = (.0883024619*r + .2817188376*g + .6299787005*b) ** (1/3)
    a = 1.9779984951*l - 2.428592205*m + .4505937099*s
    bb = .0259040371*l + .7827717662*m - .808675766*s
    return math.hypot(a, bb), math.atan2(bb, a)


def _tone(lightness, chroma, hue):
    for _ in range(81):
        a, b = chroma * math.cos(hue), chroma * math.sin(hue)
        l = (lightness + .3963377774*a + .2158037573*b) ** 3
        m = (lightness - .1055613458*a - .0638541728*b) ** 3
        s = (lightness - .0894841775*a - 1.291485548*b) ** 3
        channels = [4.0767416621*l - 3.3077115913*m + .2309699292*s,
                    -1.2684380046*l + 2.6097574011*m - .3413193965*s,
                    -.0041960863*l - .7034186147*m + 1.707614701*s]
        if all(0 <= c <= 1 for c in channels):
            break
        chroma *= .94
    channels = [12.92*c if c <= .0031308 else 1.055*c ** (1/2.4) - .055 for c in channels]
    return "#" + "".join(f"{math.floor(max(0, min(1, c)) * 255 + .500000001):02x}" for c in channels)


def color_theme(value: str, scheme: str | None = None) -> dict:
    """Same OKLCH roles as web/src/themes/color.ts, for the server's first paint."""
    color = normalize_color(value)
    dark = color_scheme(color, scheme) == "dark"
    c, h = _chroma_hue(color)
    tint = min(c, .042)
    accent_chroma = 0 if c < .003 else min(.17, max(.07, c * 1.6))

    def role(light, night, amount):
        return _tone(night if dark else light, tint * amount, h)

    def on(bg):
        lum = _luminance(bg)
        return "#000000" if (lum + .05) / .05 >= 1.05 / (lum + .05) else "#ffffff"

    background, surface = role(.92, .19, .55), role(.955, .265, .45)
    accent = _tone(.79 if dark else .44, accent_chroma, h)
    primary, secondary = _tone(.95 if dark else .255, .012, h), _tone(.77 if dark else .445, .018, h)
    shadow, highlight = role(.78, .16, .45), role(.99, .32, .2)
    rail, field = role(.89, .215, .72), role(.91, .225, .48)
    elevated, selected = role(.978, .31, .25), role(.855, .355, .9)
    destructive = "#ffb4b9" if dark else "#9f2433"
    theme = copy.deepcopy(THEMES["dark" if dark else "light"])
    theme.update(name="color", label="Цвет", description="Любой цвет с мягким градиентом")
    theme["palette"].update(background={"hex": background, "alpha": 1},
        midground={"hex": primary, "alpha": 1}, foreground={"hex": highlight, "alpha": 0})
    theme["neumorphism"].update(background=background, surface=surface, shadow=shadow,
        highlight=highlight, textPrimary=primary, textSecondary=secondary, accent=accent,
        accentLine=accent, accentForeground=on(accent), rail=rail, field=field, elevated=elevated, selected=selected)
    theme["colorOverrides"].update(card=surface, cardForeground=primary,
        popover=elevated, popoverForeground=primary, secondary=selected, secondaryForeground=primary, muted=field,
        primary=accent, accent=accent, primaryForeground=on(accent), accentForeground=on(accent),
        mutedForeground=secondary, border=shadow, input=shadow, ring=accent,
        destructive=destructive, destructiveForeground=on(destructive),
        success="#ace4bc" if dark else "#20583f", warning="#efd69b" if dark else "#6e4909")
    theme["assets"] = {"bg": f"linear-gradient(135deg, {background}, {role(.93, .205, .55)})"}
    theme["terminalBackground"] = surface
    theme["terminalForeground"] = primary
    theme["swatchColors"] = [background, primary, accent]
    theme["seriesColors"] = {"inputTokenAccent": accent, "outputTokenAccent": secondary}
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
    if "theme_color_scheme" in dashboard:
        revision_data.append(dashboard["theme_color_scheme"])
    revision = hashlib.sha256(json.dumps(
        revision_data, sort_keys=True, default=str,
    ).encode()).hexdigest()[:24]
    return {
        "version": 1, "theme": normalize_theme(raw), "known": True,
        "installation_id": installation_id,
        "owner": hashlib.sha256(owner.encode()).hexdigest()[:16],
        "base_path": base_path, "revision": revision, "evening": evening,
        **({"color": normalize_color(dashboard.get("theme_color")),
            "color_scheme": color_scheme(dashboard.get("theme_color"), dashboard.get("theme_color_scheme"))}
           if normalize_theme(raw) == "color" or "theme_color" in dashboard else {}),
    }


def bootstrap_css(value: dict) -> str:
    theme_name = normalize_theme(value.get("theme"))
    theme = color_theme(value.get("color"), value.get("color_scheme")) if theme_name == "color" else copy.deepcopy(THEMES[theme_name])
    neo = theme["neumorphism"]
    for key in ("rail", "field", "elevated", "selected"):
        neo.setdefault(key, neo["background"] if key == "rail" else neo["surface"])
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
    background_rgb = [int(theme["palette"]["background"]["hex"][i:i + 2], 16) / 255 for i in (1, 3, 5)]
    lightness = sum((c / 12.92 if c <= .04045 else ((c + .055) / 1.055) ** 2.4) * w
                    for c, w in zip(background_rgb, (.2126, .7152, .0722)))
    # Shipped tokens or derived from a strictly validated hex color.
    css = "".join(f"{key}:{val};" for key, val in variables.items())
    return (
        '<style id="hermes-theme-bootstrap">:root{' + css
        + f'color-scheme:{"dark" if lightness < .35 else "light"};'
        + "}html,body{background-color:var(--background-base);color:var(--midground-base);"
        + "font-family:var(--theme-font-sans);font-size:var(--theme-base-size);"
        + "background-image:var(--theme-asset-bg,none);}</style>"
    )

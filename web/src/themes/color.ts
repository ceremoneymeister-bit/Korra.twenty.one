import type { DashboardTheme } from "./types";

/** Color math shared in behavior with korra_cli.dashboard_theme (pre-paint). */
export const DEFAULT_COLOR = "#5275d9";
export const THEME_COLORS = [
  ["Тёмно-синий", "#182c54"], ["Синий", "#2456b8"],
  ["Тёмно-зелёный", "#164c3b"], ["Бордовый", "#65243e"],
  ["Фиолетовый", "#49336b"], ["Розовый", "#b83e73"],
  ["Бледно-жёлтый", "#f4e7b2"], ["Мятный", "#c6e4d5"],
  ["Лавандовый", "#ded5f0"], ["Персиковый", "#f2d3bd"],
];
export function validColor(value: unknown): value is string {
  return typeof value === "string" && /^#[0-9a-f]{6}$/i.test(value);
}
const rgb = (hex: string) => [1, 3, 5].map(i => parseInt(hex.slice(i, i + 2), 16) / 255);
// Stabilize half-channel rounding across JS and the Python pre-paint path.
const hex = (channels: number[]) => "#" + channels.map(c => Math.floor(c * 255 + .500000001).toString(16).padStart(2, "0")).join("");
function mix(a: string, b: string, weight: number): string {
  const other = rgb(b);
  return hex(rgb(a).map((c, i) => c * (1 - weight) + other[i] * weight));
}
export function luminance(color: string): number {
  const linear = rgb(color).map(c => c <= .04045 ? c / 12.92 : ((c + .055) / 1.055) ** 2.4);
  return linear[0] * .2126 + linear[1] * .7152 + linear[2] * .0722;
}
export function contrast(a: string, b: string): number {
  const x = luminance(a), y = luminance(b);
  return (Math.max(x, y) + .05) / (Math.min(x, y) + .05);
}
export function themeColorScheme(theme: DashboardTheme): "dark" | "light" {
  return luminance(theme.palette.background.hex) < .35 ? "dark" : "light";
}

export function colorTokens(value: string) {
  const color = validColor(value) ? value.toLowerCase() : DEFAULT_COLOR;
  const dark = luminance(color) < .35;
  const ink = dark ? "#ffffff" : "#000000";
  // Retain the chosen color. Only move luminance enough to leave room for
  // readable secondary text, raised surfaces and both neumorphic shadows.
  let surface = color;
  const low = dark ? .012 : .5, high = dark ? .095 : .85;
  while (luminance(surface) < low) surface = mix(surface, "#ffffff", .02);
  while (luminance(surface) > high) surface = mix(surface, "#000000", .02);
  const background = mix(surface, dark ? "#000000" : "#ffffff", .045);
  const end = mix(surface, dark ? "#000000" : "#ffffff", .015);
  const surfaces = [surface, background, end];
  const readable = (start: string) => {
    for (let step = 0; step <= 100; step++) {
      const candidate = mix(start, ink, step / 100);
      if (surfaces.every(bg => contrast(candidate, bg) >= 4.5)) return candidate;
    }
    return ink;
  };
  const accent = readable(mix(surface, ink, .55));
  // Cards and agent tabs also use subtle accent washes on hover/selection.
  surfaces.push(mix(surface, accent, .22));
  const textPrimary = readable(mix(surface, ink, .94));
  const textSecondary = readable(mix(surface, ink, .68));
  const destructive = readable(dark ? "#ff6b74" : "#b42318");
  return {
    dark, background, surface, accent,
    shadow: mix(surface, "#000000", dark ? .3 : .16),
    highlight: mix(surface, "#ffffff", dark ? .065 : .6),
    textPrimary, textSecondary,
    accentLine: readable(accent),
    accentForeground: contrast(accent, "#000000") >= contrast(accent, "#ffffff") ? "#000000" : "#ffffff",
    destructive,
    destructiveForeground: contrast(destructive, "#000000") >= contrast(destructive, "#ffffff") ? "#000000" : "#ffffff",
    success: readable(dark ? "#9ede01" : "#047857"),
    warning: readable(dark ? "#f6c453" : "#8a5200"),
    gradient: `linear-gradient(135deg, ${background}, ${end})`,
  };
}

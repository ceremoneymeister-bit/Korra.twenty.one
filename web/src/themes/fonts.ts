/**
 * Curated UI-font catalog for the dashboard font override.
 *
 * The font override is an independent layer that sits ON TOP of the active
 * theme: a theme still ships its own `typography.fontSans` default, but a
 * user can pick any font here and it persists across theme switches. Picking
 * "Theme default" clears the override and returns to whatever the active
 * theme specifies.
 *
 * Why a curated catalog instead of a free-text font name + URL box: the
 * `fontUrl` is injected into the page as a `<link rel="stylesheet">`, so
 * accepting an arbitrary user-supplied URL would be a self-XSS / SSRF-ish
 * footgun in the dashboard. A vetted catalog keeps the injected origins
 * fixed (self-hosted files + system stacks) while still giving real choice. The
 * matching allow-list on the backend (`_FONT_CHOICES` in web_server.py)
 * rejects any id not defined here.
 *
 * Keep `FONT_CHOICES` in sync with `_FONT_CHOICES` in
 * `hermes_cli/web_server.py` — the ids must match exactly.
 */

/** System stacks reused from presets so "System" choices need no webfont. */
const SYSTEM_SANS =
  'system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif';
const SYSTEM_MONO =
  'ui-monospace, "SF Mono", "Cascadia Mono", Menlo, Consolas, monospace';
const SYSTEM_SERIF =
  'Georgia, Cambria, "Times New Roman", Times, serif';

export type FontCategory = "sans" | "serif" | "mono";

export interface FontChoice {
  /** Stable id persisted in config / localStorage. */
  id: string;
  /** Human-readable label shown in the picker. */
  label: string;
  /** Rough grouping for the picker. */
  category: FontCategory;
  /** CSS font-family stack applied to `--theme-font-sans` (+ display). */
  stack: string;
  /** Optional vetted stylesheet URL. Prefer self-hosted @font-face entries. */
  fontUrl?: string;
}

/** Sentinel id meaning "no override — use the active theme's font". */
export const THEME_DEFAULT_FONT_ID = "theme";

/**
 * The curated set. Order is the display order in the picker (grouped by
 * category in the UI). `stack` always ends in a system fallback so a font
 * that fails to load still renders something sane.
 */
export const FONT_CHOICES: FontChoice[] = [
  // ── System (no webfont fetch) ──────────────────────────────────────────
  { id: "system-sans", label: "Системный без засечек", category: "sans", stack: SYSTEM_SANS },
  { id: "system-serif", label: "Системный с засечками", category: "serif", stack: SYSTEM_SERIF },
  { id: "system-mono", label: "Системный моноширинный", category: "mono", stack: SYSTEM_MONO },

  // ── Self-hosted: deterministic under the production CSP ───────────────
  {
    id: "onest",
    label: "Onest",
    category: "sans",
    stack: `"Onest", ${SYSTEM_SANS}`,
  },

  // JetBrains Mono is already bundled for the terminal and covers Cyrillic.
  {
    id: "jetbrains-mono",
    label: "JetBrains Mono",
    category: "mono",
    stack: `"JetBrains Mono", ${SYSTEM_MONO}`,
  },
];

const FONT_BY_ID: Record<string, FontChoice> = Object.fromEntries(
  FONT_CHOICES.map((f) => [f.id, f]),
);

/** Look up a font choice by id. Returns undefined for the theme-default
 *  sentinel and for any unknown id. */
export function getFontChoice(id: string | null | undefined): FontChoice | undefined {
  if (!id || id === THEME_DEFAULT_FONT_ID) return undefined;
  return FONT_BY_ID[id];
}

/** Whether an id refers to a real catalog font (vs. theme-default/unknown). */
export function isOverrideFont(id: string | null | undefined): boolean {
  return getFontChoice(id) !== undefined;
}

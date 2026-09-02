import type { DashboardTheme, ThemeLayout, ThemeTypography } from "./types";

/** The two palette ids supported by the Korra dashboard. */
export type BuiltinThemeName = "light" | "dark";

/** Dominant lime sampled from the Korra 21 logo. */
export const BRAND_LIME = "#9BE424";

const SYSTEM_SANS =
  'system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif';
const SYSTEM_MONO =
  'ui-monospace, "SF Mono", "Cascadia Mono", Menlo, Consolas, monospace';

const DEFAULT_TYPOGRAPHY: ThemeTypography = {
  fontSans: `"Onest", ${SYSTEM_SANS}`,
  fontMono: SYSTEM_MONO,
  baseSize: "15px",
  lineHeight: "1.55",
  letterSpacing: "0",
};

const DEFAULT_LAYOUT: ThemeLayout = {
  radius: "0.625rem",
  density: "comfortable",
};

/**
 * Neutral light palette. The logo lime is darkened for filled controls so
 * white text reaches WCAG AA contrast; decorative accents keep the original
 * #9BE424 brand color.
 */
export const lightTheme: DashboardTheme = {
  name: "light",
  label: "Светлая",
  description: "Светлый нейтральный интерфейс",
  palette: {
    background: { hex: "#F7F7F5", alpha: 1 },
    midground: { hex: "#202124", alpha: 1 },
    foreground: { hex: "#FFFFFF", alpha: 0 },
    warmGlow: "rgba(155, 228, 36, 0.16)",
    noiseOpacity: 0,
  },
  typography: DEFAULT_TYPOGRAPHY,
  layout: DEFAULT_LAYOUT,
  terminalBackground: "#FFFFFF",
  terminalForeground: "#202124",
  seriesColors: {
    inputTokenAccent: "#4F7900",
    outputTokenAccent: "#7C3AED",
  },
  colorOverrides: {
    card: "#FFFFFF",
    cardForeground: "#202124",
    popover: "#FFFFFF",
    popoverForeground: "#202124",
    primary: "#4F7900",
    primaryForeground: "#FFFFFF",
    secondary: "#EFEFEC",
    secondaryForeground: "#202124",
    muted: "#E7E7E3",
    mutedForeground: "#667085",
    accent: "#EAF7D3",
    accentForeground: "#304A00",
    destructive: "#B42318",
    destructiveForeground: "#FFFFFF",
    success: "#047857",
    warning: "#8A5200",
    border: "#CFD1CC",
    input: "#B8BBB4",
    ring: "#4F7900",
  },
  swatchColors: ["#F7F7F5", "#4F7900", BRAND_LIME],
};

/**
 * Deep ink-purple palette. Surfaces stay on the base hue and rise by roughly
 * five to ten lightness points; the unmodified logo lime is the accent.
 */
export const darkTheme: DashboardTheme = {
  name: "dark",
  label: "Тёмная",
  description: "Глубокий фиолетовый интерфейс",
  palette: {
    background: { hex: "#150B29", alpha: 1 },
    midground: { hex: "#F4EFFA", alpha: 1 },
    foreground: { hex: "#FFFFFF", alpha: 0 },
    warmGlow: "rgba(155, 228, 36, 0.18)",
    noiseOpacity: 0,
  },
  typography: DEFAULT_TYPOGRAPHY,
  layout: DEFAULT_LAYOUT,
  terminalBackground: "#150B29",
  terminalForeground: "#F4EFFA",
  seriesColors: {
    inputTokenAccent: "#9BE424",
    outputTokenAccent: "#C6A7FF",
  },
  colorOverrides: {
    card: "#20123A",
    cardForeground: "#F4EFFA",
    popover: "#20123A",
    popoverForeground: "#F4EFFA",
    primary: BRAND_LIME,
    primaryForeground: "#150B29",
    secondary: "#25173F",
    secondaryForeground: "#F4EFFA",
    muted: "#271B41",
    mutedForeground: "#B9ACC9",
    accent: "#324719",
    accentForeground: "#E3F8C0",
    destructive: "#FF6B74",
    destructiveForeground: "#150B29",
    success: "#83D95B",
    warning: "#F6C453",
    border: "#493568",
    input: "#493568",
    ring: BRAND_LIME,
  },
  swatchColors: ["#150B29", "#20123A", BRAND_LIME],
};

/** The default is deliberately the light palette. */
export const defaultTheme = lightTheme;

export const BUILTIN_THEMES: Record<BuiltinThemeName, DashboardTheme> = {
  light: lightTheme,
  dark: darkTheme,
};

/** Legacy dark presets which should retain their visual mode after upgrade. */
const LEGACY_DARK_THEMES = new Set([
  "default",
  "default-large",
  "large",
  "hermes-teal",
  "midnight",
  "ember",
  "mono",
  "cyberpunk",
  "rose",
]);

/**
 * Collapse persisted server/local theme names to the supported pair.
 * Known dark presets stay dark; former light presets and unknown/custom
 * values use the new safe default.
 */
export function migrateThemeName(name: string | null | undefined): BuiltinThemeName {
  const normalized = name?.trim().toLowerCase();
  if (normalized === "dark" || LEGACY_DARK_THEMES.has(normalized ?? "")) {
    return "dark";
  }
  return "light";
}

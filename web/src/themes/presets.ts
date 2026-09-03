import type { DashboardTheme, ThemeLayout, ThemeTypography } from "./types";
import {
  BRAND_LIME,
  darkNeumorphism,
  lightNeumorphism,
} from "./neumorphism";

/** The two palette ids supported by the Korra dashboard. */
export type BuiltinThemeName = "light" | "dark";

/** Фирменный лайм — доминирующий цвет знака «21» (точная выборка из
 *  LOGO/k21: 34 267 пикселей #9EDE01). Раньше здесь стоял приблизительный
 *  #9BE424, а в светлой теме он ещё и затемнялся до болотного #4F7900 ради
 *  белого текста на кнопке (1.63:1 — нечитаемо). Правильный размен: лайм
 *  оставляем фирменным, текст на нём делаем тёмным (11.61:1). */
export { BRAND_LIME } from "./neumorphism";

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
  radius: "1rem",
  density: "comfortable",
};

/** Owner-approved neutral light palette. */
export const lightTheme: DashboardTheme = {
  name: "light",
  label: "Светлая",
  description: "Светлый нейтральный интерфейс",
  palette: {
    background: { hex: lightNeumorphism.background, alpha: 1 },
    midground: { hex: lightNeumorphism.textPrimary, alpha: 1 },
    foreground: { hex: lightNeumorphism.highlight, alpha: 0 },
    warmGlow: "transparent",
    noiseOpacity: 0,
  },
  typography: DEFAULT_TYPOGRAPHY,
  layout: DEFAULT_LAYOUT,
  neumorphism: lightNeumorphism,
  terminalBackground: lightNeumorphism.surface,
  terminalForeground: lightNeumorphism.textPrimary,
  seriesColors: {
    inputTokenAccent: lightNeumorphism.accentLine,
    outputTokenAccent: lightNeumorphism.textSecondary,
  },
  colorOverrides: {
    card: lightNeumorphism.surface,
    cardForeground: lightNeumorphism.textPrimary,
    popover: lightNeumorphism.surface,
    popoverForeground: lightNeumorphism.textPrimary,
    primary: BRAND_LIME,
    primaryForeground: lightNeumorphism.accentForeground,
    secondary: lightNeumorphism.surface,
    secondaryForeground: lightNeumorphism.textPrimary,
    // Одобренное превью: приглушённый фон на шаг темнее поверхности.
    muted: "#DCDCDC",
    mutedForeground: lightNeumorphism.textSecondary,
    accent: BRAND_LIME,
    accentForeground: lightNeumorphism.accentForeground,
    destructive: "#B42318",
    destructiveForeground: "#FFFFFF",
    success: "#047857",
    warning: "#8A5200",
    border: lightNeumorphism.shadow,
    input: lightNeumorphism.shadow,
    ring: lightNeumorphism.accentLine,
  },
  swatchColors: [lightNeumorphism.background, lightNeumorphism.textPrimary, BRAND_LIME],
};

/** Owner-approved neutral dark palette. */
export const darkTheme: DashboardTheme = {
  name: "dark",
  label: "Тёмная",
  description: "Тёмный нейтральный интерфейс",
  palette: {
    background: { hex: darkNeumorphism.background, alpha: 1 },
    midground: { hex: darkNeumorphism.textPrimary, alpha: 1 },
    foreground: { hex: darkNeumorphism.highlight, alpha: 0 },
    warmGlow: "transparent",
    noiseOpacity: 0,
  },
  typography: DEFAULT_TYPOGRAPHY,
  layout: DEFAULT_LAYOUT,
  neumorphism: darkNeumorphism,
  terminalBackground: darkNeumorphism.surface,
  terminalForeground: darkNeumorphism.textPrimary,
  seriesColors: {
    inputTokenAccent: darkNeumorphism.accentLine,
    outputTokenAccent: darkNeumorphism.textSecondary,
  },
  colorOverrides: {
    card: darkNeumorphism.surface,
    cardForeground: darkNeumorphism.textPrimary,
    popover: darkNeumorphism.surface,
    popoverForeground: darkNeumorphism.textPrimary,
    primary: BRAND_LIME,
    primaryForeground: darkNeumorphism.accentForeground,
    secondary: darkNeumorphism.surface,
    secondaryForeground: darkNeumorphism.textPrimary,
    muted: "#2B2B2B",
    mutedForeground: darkNeumorphism.textSecondary,
    accent: BRAND_LIME,
    accentForeground: darkNeumorphism.accentForeground,
    destructive: "#FF6B74",
    destructiveForeground: darkNeumorphism.surface,
    success: "#83D95B",
    warning: "#F6C453",
    border: darkNeumorphism.shadow,
    input: darkNeumorphism.shadow,
    ring: darkNeumorphism.accentLine,
  },
  swatchColors: [darkNeumorphism.background, darkNeumorphism.textPrimary, BRAND_LIME],
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

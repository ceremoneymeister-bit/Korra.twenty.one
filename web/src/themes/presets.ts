import type { DashboardTheme } from "./types";
import { colorTokens, DEFAULT_COLOR } from "./color";
import data from "../../../korra_cli/data/dashboard-themes.json";

/** The owner-approved palettes are also consumed by the server bootstrap. */
export type BuiltinThemeName = "light" | "dark" | "color";
export { BRAND_LIME } from "./neumorphism";
export const lightTheme = data.themes.light as DashboardTheme;
export const darkTheme = data.themes.dark as DashboardTheme;
export const defaultTheme = lightTheme;
export const BUILTIN_THEMES: Record<BuiltinThemeName, DashboardTheme> = {
  light: lightTheme, dark: darkTheme, color: colorTheme(DEFAULT_COLOR),
};

/** Legacy default is not evidence of an explicit dark choice. */
export function migrateThemeName(name: unknown): BuiltinThemeName {
  const value = typeof name === "string" ? name.trim().toLowerCase() : "";
  return value === "color" ? "color" : value === "dark" || data.legacy_dark.includes(value) ? "dark" : "light";
}

/** Derive every surface from the chosen color, using the existing preset shape. */
export function colorTheme(color: string): DashboardTheme {
  const { dark, gradient, destructive, destructiveForeground, success, warning, ...neo } = colorTokens(color);
  const base = dark ? darkTheme : lightTheme;
  return {
    ...base, name: "color", label: "Цвет", description: "Любой цвет с мягким градиентом",
    palette: { ...base.palette, background: { hex: neo.background, alpha: 1 },
      midground: { hex: neo.textPrimary, alpha: 1 }, foreground: { hex: neo.highlight, alpha: 0 } },
    neumorphism: neo,
    assets: { bg: gradient },
    terminalBackground: neo.surface, terminalForeground: neo.textPrimary,
    swatchColors: [neo.background, neo.textPrimary, neo.accent],
    seriesColors: { inputTokenAccent: neo.accent, outputTokenAccent: neo.textSecondary },
    colorOverrides: {
      ...base.colorOverrides,
      cardForeground: neo.textPrimary, popoverForeground: neo.textPrimary, secondaryForeground: neo.textPrimary,
      destructive, destructiveForeground, success, warning,
      card: neo.surface, popover: neo.surface, secondary: neo.surface, muted: neo.surface,
      primary: neo.accent, accent: neo.accent,
      primaryForeground: neo.accentForeground, accentForeground: neo.accentForeground,
      mutedForeground: neo.textSecondary, border: neo.shadow, input: neo.shadow, ring: neo.accentLine,
    },
  };
}

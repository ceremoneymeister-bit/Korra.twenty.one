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

/** A light preset with a derived palette; no separate theme storage or schema. */
export function colorTheme(color: string): DashboardTheme {
  const { gradient, ...neo } = colorTokens(color);
  return {
    ...lightTheme, name: "color", label: "Цвет", description: "Любой цвет с мягким градиентом",
    palette: { ...lightTheme.palette, background: { hex: neo.background, alpha: 1 } },
    neumorphism: neo,
    assets: { bg: gradient },
    terminalBackground: neo.surface,
    seriesColors: { inputTokenAccent: neo.accent, outputTokenAccent: neo.textSecondary },
    colorOverrides: {
      ...lightTheme.colorOverrides,
      card: neo.surface, popover: neo.surface, secondary: neo.surface, muted: neo.surface,
      primary: neo.accent, accent: neo.accent,
      primaryForeground: neo.accentForeground, accentForeground: neo.accentForeground,
      mutedForeground: neo.textSecondary, border: neo.shadow, input: neo.shadow, ring: neo.accent,
    },
  };
}

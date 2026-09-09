import type { DashboardTheme } from "./types";
import data from "../../../korra_cli/data/dashboard-themes.json";

/** The owner-approved palettes are also consumed by the server bootstrap. */
export type BuiltinThemeName = "light" | "dark";
export { BRAND_LIME } from "./neumorphism";
export const lightTheme = data.themes.light as DashboardTheme;
export const darkTheme = data.themes.dark as DashboardTheme;
export const defaultTheme = lightTheme;
export const BUILTIN_THEMES: Record<BuiltinThemeName, DashboardTheme> = {
  light: lightTheme, dark: darkTheme,
};

/** Legacy default is not evidence of an explicit dark choice. */
export function migrateThemeName(name: unknown): BuiltinThemeName {
  const value = typeof name === "string" ? name.trim().toLowerCase() : "";
  return value === "dark" || data.legacy_dark.includes(value) ? "dark" : "light";
}

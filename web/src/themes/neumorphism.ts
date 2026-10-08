import type { ThemeNeumorphism } from "./types";

import data from "../../../korra_cli/data/dashboard-themes.json";

export const BRAND_LIME = data.themes.light.neumorphism.accent;
export const lightNeumorphism: ThemeNeumorphism = data.themes.light.neumorphism;
export const darkNeumorphism: ThemeNeumorphism = data.themes.dark.neumorphism;

const TOKEN_TO_VAR: Record<keyof ThemeNeumorphism, `--neo-${string}`> = {
  background: "--neo-background",
  surface: "--neo-surface",
  rail: "--neo-rail",
  field: "--neo-field",
  elevated: "--neo-elevated",
  selected: "--neo-selected",
  shadow: "--neo-shadow",
  highlight: "--neo-highlight",
  textPrimary: "--neo-text-primary",
  textSecondary: "--neo-text-secondary",
  accent: "--neo-accent",
  accentLine: "--neo-accent-line",
  accentForeground: "--neo-accent-foreground",
};

export const NEUMORPHISM_CSS_VARS = Object.values(TOKEN_TO_VAR);

export function neumorphismVars(
  tokens: ThemeNeumorphism | undefined,
): Record<string, string> {
  if (!tokens) return {};
  return Object.fromEntries(
    Object.entries({ rail: tokens.background, field: tokens.surface, elevated: tokens.surface, selected: tokens.surface, ...tokens }).map(([key, value]) => [
      TOKEN_TO_VAR[key as keyof ThemeNeumorphism],
      value,
    ]),
  );
}

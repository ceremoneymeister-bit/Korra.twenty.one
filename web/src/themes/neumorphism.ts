import type { ThemeNeumorphism } from "./types";

export const BRAND_LIME = "#9EDE01";

/** Owner-approved palette (canon: projects/Korra 21/UI_PALETTE.md).
 *  Do not derive or visually tune these values. */
export const lightNeumorphism: ThemeNeumorphism = {
  background: "#e8e8e8",
  surface: "#e0e0e0",
  shadow: "#bebebe",
  highlight: "#ffffff",
  textPrimary: "#1f1f1f",
  textSecondary: "#5c5c5c",
  accent: BRAND_LIME,
  /* Владелец: никакого затемнённого «болотного» лайма. Лайм живёт только
   * заливками; тонкие метки и текст в роли акцента на светлом — чернила. */
  accentLine: "#1f1f1f",
  accentForeground: "#1f1f1f",
};

export const darkNeumorphism: ThemeNeumorphism = {
  background: "#212121",
  surface: "#212121",
  shadow: "#191919",
  highlight: "#3c3c3c",
  textPrimary: "#e8e8e8",
  textSecondary: "#9a9a9a",
  accent: BRAND_LIME,
  accentLine: BRAND_LIME,
  accentForeground: "#1f1f1f",
};

const TOKEN_TO_VAR: Record<keyof ThemeNeumorphism, `--neo-${string}`> = {
  background: "--neo-background",
  surface: "--neo-surface",
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
    Object.entries(tokens).map(([key, value]) => [
      TOKEN_TO_VAR[key as keyof ThemeNeumorphism],
      value,
    ]),
  );
}

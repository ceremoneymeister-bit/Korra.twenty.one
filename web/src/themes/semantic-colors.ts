import type { ThemeColorOverrides } from "./types";

/** Map a color override to the runtime indirection consumed by index.css.
 *
 * Tailwind's `@theme inline` substitutes `--color-*` at build time. Writing
 * those variables from React therefore cannot change an already compiled
 * utility. The plain semantic variables below deliberately survive in the
 * generated CSS (`bg-primary` -> `var(--primary, ...)`) and remain themeable
 * at runtime. */
const OVERRIDE_KEY_TO_VAR: Record<keyof ThemeColorOverrides, string> = {
  card: "--card",
  cardForeground: "--card-foreground",
  popover: "--popover",
  popoverForeground: "--popover-foreground",
  primary: "--primary",
  primaryForeground: "--primary-foreground",
  secondary: "--secondary",
  secondaryForeground: "--secondary-foreground",
  muted: "--muted",
  mutedForeground: "--muted-foreground",
  accent: "--accent",
  accentForeground: "--accent-foreground",
  destructive: "--destructive",
  destructiveForeground: "--destructive-foreground",
  success: "--success",
  warning: "--warning",
  border: "--border",
  input: "--input",
  ring: "--ring",
};

/** Keys cleared before every theme switch so an omitted optional override
 * cannot retain the previous theme's value. Both current built-ins provide
 * the complete set; the optional shape is kept for compatible custom themes. */
export const COLOR_OVERRIDE_CSS_VARS = Object.values(OVERRIDE_KEY_TO_VAR);

export function colorOverrideVars(
  overrides: ThemeColorOverrides | undefined,
): Record<string, string> {
  if (!overrides) return {};
  const out: Record<string, string> = {};
  for (const [key, value] of Object.entries(overrides)) {
    if (!value) continue;
    const cssVar = OVERRIDE_KEY_TO_VAR[key as keyof ThemeColorOverrides];
    if (cssVar) out[cssVar] = value;
  }
  return out;
}

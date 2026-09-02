import { describe, expect, it } from "vitest";

import {
  COLOR_OVERRIDE_CSS_VARS,
  colorOverrideVars,
} from "./semantic-colors";
import type { ThemeColorOverrides } from "./types";

describe("semantic theme color wiring", () => {
  it("maps every declared override to a distinct runtime variable", () => {
    const overrides = {
      card: "value-card",
      cardForeground: "value-card-foreground",
      popover: "value-popover",
      popoverForeground: "value-popover-foreground",
      primary: "value-primary",
      primaryForeground: "value-primary-foreground",
      secondary: "value-secondary",
      secondaryForeground: "value-secondary-foreground",
      muted: "value-muted",
      mutedForeground: "value-muted-foreground",
      accent: "value-accent",
      accentForeground: "value-accent-foreground",
      destructive: "value-destructive",
      destructiveForeground: "value-destructive-foreground",
      success: "value-success",
      warning: "value-warning",
      border: "value-border",
      input: "value-input",
      ring: "value-ring",
    } satisfies Required<ThemeColorOverrides>;

    const mapped = colorOverrideVars(overrides);

    expect(mapped).toEqual({
      "--card": "value-card",
      "--card-foreground": "value-card-foreground",
      "--popover": "value-popover",
      "--popover-foreground": "value-popover-foreground",
      "--primary": "value-primary",
      "--primary-foreground": "value-primary-foreground",
      "--secondary": "value-secondary",
      "--secondary-foreground": "value-secondary-foreground",
      "--muted": "value-muted",
      "--muted-foreground": "value-muted-foreground",
      "--accent": "value-accent",
      "--accent-foreground": "value-accent-foreground",
      "--destructive": "value-destructive",
      "--destructive-foreground": "value-destructive-foreground",
      "--success": "value-success",
      "--warning": "value-warning",
      "--border": "value-border",
      "--input": "value-input",
      "--ring": "value-ring",
    });
    expect(new Set(COLOR_OVERRIDE_CSS_VARS)).toEqual(
      new Set(Object.keys(mapped)),
    );
    expect(Object.keys(mapped).every((key) => !key.startsWith("--color-"))).toBe(
      true,
    );
  });

  it("omits absent and empty optional overrides", () => {
    expect(colorOverrideVars(undefined)).toEqual({});
    expect(colorOverrideVars({ primary: "", ring: "value-ring" })).toEqual({
      "--ring": "value-ring",
    });
  });
});

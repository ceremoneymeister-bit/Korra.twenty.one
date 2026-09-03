import { describe, expect, it } from "vitest";

import {
  BRAND_LIME,
  darkNeumorphism,
  lightNeumorphism,
  neumorphismVars,
} from "./neumorphism";

describe("Korra neumorphism tokens", () => {
  it("maps both palettes through one complete CSS-variable contract", () => {
    const light = neumorphismVars(lightNeumorphism);
    const dark = neumorphismVars(darkNeumorphism);

    expect(Object.keys(light)).toEqual(Object.keys(dark));
    expect(light).toEqual({
      "--neo-background": "#e8e8e8",
      "--neo-surface": "#e0e0e0",
      "--neo-shadow": "#bebebe",
      "--neo-highlight": "#ffffff",
      "--neo-text-primary": "#1f1f1f",
      "--neo-text-secondary": "#5c5c5c",
      "--neo-accent": BRAND_LIME,
      "--neo-accent-line": "#567a00",
      "--neo-accent-foreground": "#1f1f1f",
    });
    expect(dark["--neo-accent"]).toBe(BRAND_LIME);
    expect(dark["--neo-accent-line"]).toBe(BRAND_LIME);
  });

  it("does not emit partial values for absent optional custom themes", () => {
    expect(neumorphismVars(undefined)).toEqual({});
  });
});

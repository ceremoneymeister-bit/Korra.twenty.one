import { describe, expect, it } from "vitest";

import {
  BRAND_LIME,
  BUILTIN_THEMES,
  darkTheme,
  defaultTheme,
  lightTheme,
  migrateThemeName,
} from "./presets";

function relativeLuminance(hex: string): number {
  const channels = hex
    .slice(1)
    .match(/.{2}/g)!
    .map((part) => Number.parseInt(part, 16) / 255)
    .map((channel) =>
      channel <= 0.04045
        ? channel / 12.92
        : ((channel + 0.055) / 1.055) ** 2.4,
    );
  return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2];
}

function contrast(first: string, second: string): number {
  const firstLuminance = relativeLuminance(first);
  const secondLuminance = relativeLuminance(second);
  const lighter = Math.max(firstLuminance, secondLuminance);
  const darker = Math.min(firstLuminance, secondLuminance);
  return (lighter + 0.05) / (darker + 0.05);
}

function hueAndLightness(hex: string): { hue: number; lightness: number } {
  const [red, green, blue] = hex
    .slice(1)
    .match(/.{2}/g)!
    .map((part) => Number.parseInt(part, 16) / 255);
  const max = Math.max(red, green, blue);
  const min = Math.min(red, green, blue);
  const delta = max - min;
  let hue = 0;
  if (delta > 0) {
    if (max === red) hue = 60 * (((green - blue) / delta) % 6);
    if (max === green) hue = 60 * ((blue - red) / delta + 2);
    if (max === blue) hue = 60 * ((red - green) / delta + 4);
  }
  return {
    hue: hue < 0 ? hue + 360 : hue,
    lightness: (max + min) / 2,
  };
}

describe("built-in dashboard themes", () => {
  it("exposes exactly the light and dark palettes and defaults to light", () => {
    expect(Object.keys(BUILTIN_THEMES)).toEqual(["light", "dark"]);
    expect(defaultTheme).toBe(lightTheme);
    expect(lightTheme.label).toBe("Светлая");
    expect(darkTheme.label).toBe("Тёмная");
  });

  it("uses the required ink-purple dark canvas and Korra lime accent", () => {
    expect(darkTheme.palette.background.hex.toLowerCase()).toBe("#150b29");
    expect(darkTheme.colorOverrides?.primary).toBe(BRAND_LIME);
    expect(darkTheme.colorOverrides?.ring).toBe(BRAND_LIME);
  });

  it("keeps dark cards on the canvas hue and 4–8% lighter", () => {
    const canvas = hueAndLightness(darkTheme.palette.background.hex);
    const card = hueAndLightness(darkTheme.colorOverrides!.card!);
    expect(Math.abs(card.hue - canvas.hue)).toBeLessThan(3);
    expect(card.lightness - canvas.lightness).toBeGreaterThanOrEqual(0.04);
    expect(card.lightness - canvas.lightness).toBeLessThanOrEqual(0.08);
  });

  it("keeps filled primary controls AA-readable in both palettes", () => {
    expect(
      contrast(
        lightTheme.colorOverrides!.primary!,
        lightTheme.colorOverrides!.primaryForeground!,
      ),
    ).toBeGreaterThanOrEqual(4.5);
    expect(
      contrast(
        darkTheme.colorOverrides!.primary!,
        darkTheme.colorOverrides!.primaryForeground!,
      ),
    ).toBeGreaterThanOrEqual(4.5);
  });
});

describe("migrateThemeName", () => {
  it.each([
    "default",
    "default-large",
    "large",
    "hermes-teal",
    "midnight",
    "ember",
    "mono",
    "cyberpunk",
    "rose",
    "dark",
  ])("maps the former dark preset %s to dark", (name) => {
    expect(migrateThemeName(name)).toBe("dark");
  });

  it.each(["light", "nous-blue", "lens-5i", "custom-theme", "", undefined])(
    "maps former light or unknown preset %s to light",
    (name) => {
      expect(migrateThemeName(name)).toBe("light");
    },
  );
});

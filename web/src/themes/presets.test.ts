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

describe("built-in dashboard themes", () => {
  it("exposes exactly the light and dark palettes and defaults to light", () => {
    expect(Object.keys(BUILTIN_THEMES)).toEqual(["light", "dark"]);
    expect(defaultTheme).toBe(lightTheme);
    expect(lightTheme.label).toBe("Светлая");
    expect(darkTheme.label).toBe("Тёмная");
  });

  it("uses the owner-approved neutral tokens without the retired purple", () => {
    expect(lightTheme.neumorphism).toEqual({
      background: "#e8e8e8",
      surface: "#e0e0e0",
      shadow: "#bebebe",
      highlight: "#ffffff",
      textPrimary: "#1f1f1f",
      textSecondary: "#5c5c5c",
      accent: BRAND_LIME,
      accentLine: "#1f1f1f",
      accentForeground: "#1f1f1f",
    });
    expect(darkTheme.neumorphism).toEqual({
      background: "#212121",
      surface: "#212121",
      shadow: "#191919",
      highlight: "#3c3c3c",
      textPrimary: "#e8e8e8",
      textSecondary: "#9a9a9a",
      accent: BRAND_LIME,
      accentLine: BRAND_LIME,
      accentForeground: "#1f1f1f",
    });
    expect(JSON.stringify(BUILTIN_THEMES).toLowerCase()).not.toContain("#150b29");
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

  it("keeps text and thin accents readable against each surface", () => {
    for (const theme of [lightTheme, darkTheme]) {
      const neo = theme.neumorphism!;
      expect(contrast(neo.textPrimary, neo.surface)).toBeGreaterThanOrEqual(7);
      expect(contrast(neo.textSecondary, neo.surface)).toBeGreaterThanOrEqual(4.5);
      expect(contrast(neo.accentLine, neo.surface)).toBeGreaterThanOrEqual(3);
      expect(contrast(neo.accent, neo.accentForeground)).toBeGreaterThanOrEqual(7);
    }
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

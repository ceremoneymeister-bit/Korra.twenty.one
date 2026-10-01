import { expect, it } from "vitest";
import { colorTokens, contrast, luminance } from "./color";
import { colorTheme, lightTheme } from "./presets";

it("keeps text readable and accents distinct across the RGB cube, including extremes", () => {
  for (let r = 0; r <= 255; r += 17) for (let g = 0; g <= 255; g += 17) for (let b = 0; b <= 255; b += 17) {
    const color = "#" + [r, g, b].map(c => c.toString(16).padStart(2, "0")).join("");
    const t = colorTokens(color);
    for (const surface of [t.surface, t.background, ...t.gradient.match(/#[0-9a-f]{6}/g)!]) {
      expect(contrast(t.textPrimary, surface), color).toBeGreaterThanOrEqual(4.5);
      expect(contrast(t.textSecondary, surface), color).toBeGreaterThanOrEqual(4.5);
      expect(contrast(t.accentLine, surface), color).toBeGreaterThanOrEqual(4.5);
      expect(contrast(t.accent, surface), color).toBeGreaterThanOrEqual(3);
    }
    expect(contrast(t.accentForeground, t.accent), color).toBeGreaterThanOrEqual(4.5);
    expect(luminance(t.shadow)).toBeLessThan(luminance(t.surface));
    expect(luminance(t.highlight)).toBeGreaterThan(luminance(t.surface));
  }
});
it("moves very light and very dark choices into a visible range", () => {
  for (const color of ["#ffffff", "#ffffee", "#000000", "#000011"]) {
    const t = colorTokens(color);
    expect(t.accent).not.toBe(color);
    expect(luminance(t.accent)).toBeGreaterThan(.03);
    expect(luminance(t.accent)).toBeLessThan(.3);
  }
  expect(colorTokens("#5275d9").accent).toBe("#5275d9");
});
it("derives colored shadows and a gradient without mutating the light preset", () => {
  const before = structuredClone(lightTheme);
  const blue = colorTheme("#5275d9"), pink = colorTheme("#d95791");
  expect(blue.neumorphism?.shadow).not.toBe(pink.neumorphism?.shadow);
  expect(blue.assets?.bg).toMatch(/^linear-gradient/);
  expect(blue.colorOverrides?.primary).toBe(blue.neumorphism?.accent);
  expect(lightTheme).toEqual(before);
});

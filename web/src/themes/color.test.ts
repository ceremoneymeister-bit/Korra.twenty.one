import { expect, it } from "vitest";
import { colorTokens, contrast, luminance, THEME_COLORS, themeColorScheme } from "./color";
import { colorTheme, lightTheme, darkTheme } from "./presets";

function assertReadable(color: string) {
  const t = colorTokens(color), theme = colorTheme(color);
  const surfaces = [t.surface, t.background, ...t.gradient.match(/#[0-9a-f]{6}/g)!];
  for (const surface of surfaces) {
    for (const text of [t.textPrimary, t.textSecondary, t.accentLine, t.success, t.warning, t.destructive]) {
      expect(contrast(text, surface), `${color}: ${text} on ${surface}`).toBeGreaterThanOrEqual(4.5);
    }
    expect(contrast(t.accent, surface), color).toBeGreaterThanOrEqual(3);
  }
  const selected = "#" + [1, 3, 5].map(i => Math.round(
    parseInt(t.surface.slice(i, i + 2), 16) * .78 + parseInt(t.accent.slice(i, i + 2), 16) * .22,
  ).toString(16).padStart(2, "0")).join("");
  expect(contrast(t.textPrimary, selected), color + " selected").toBeGreaterThanOrEqual(4.5);
  expect(contrast(t.textSecondary, selected), color + " selected").toBeGreaterThanOrEqual(4.5);
  expect(contrast(t.accentForeground, t.accent), color).toBeGreaterThanOrEqual(4.5);
  expect(contrast(t.destructiveForeground, t.destructive), color).toBeGreaterThanOrEqual(4.5);
  expect(luminance(t.shadow)).toBeLessThan(luminance(t.surface));
  expect(luminance(t.highlight)).toBeGreaterThan(luminance(t.surface));
  expect(themeColorScheme(theme)).toBe(t.dark ? "dark" : "light");
  expect(theme.palette.midground.hex).toBe(t.textPrimary);
  expect(theme.terminalForeground).toBe(t.textPrimary);
  for (const [surface, foreground] of [["card", "cardForeground"], ["popover", "popoverForeground"], ["secondary", "secondaryForeground"], ["muted", "mutedForeground"]] as const) {
    expect(contrast(theme.colorOverrides![foreground]!, theme.colorOverrides![surface]!)).toBeGreaterThanOrEqual(4.5);
    expect(theme.colorOverrides![surface]).toBe(t.surface);
  }
}

it.each([...THEME_COLORS, ...["#000000", "#ffffff", "#ffff00", "#0000ff"].map(c => [c, c])])(
  "keeps the complete %s palette readable", (_label, color) => assertReadable(color),
);
it("keeps text readable and accents distinct across the RGB cube", () => {
  for (let r = 0; r <= 255; r += 17) for (let g = 0; g <= 255; g += 17) for (let b = 0; b <= 255; b += 17) {
    assertReadable("#" + [r, g, b].map(c => c.toString(16).padStart(2, "0")).join(""));
  }
}, 30000);
it("uses the selected color for surfaces, with light text on dark choices and vice versa", () => {
  for (const color of ["#182c54", "#164c3b", "#65243e", "#f4e7b2"]) {
    const t = colorTokens(color);
    expect(t.surface).toBe(color);
    expect(t.dark ? luminance(t.textPrimary) > luminance(t.surface) : luminance(t.textPrimary) < luminance(t.surface)).toBe(true);
    expect(t.background).not.toBe(t.surface);
  }
});
it("derives colored shadows without mutating either neutral preset", () => {
  const before = structuredClone([lightTheme, darkTheme]);
  const blue = colorTheme("#2456b8"), pink = colorTheme("#b83e73");
  expect(blue.neumorphism?.shadow).not.toBe(pink.neumorphism?.shadow);
  expect(blue.assets?.bg).toMatch(/^linear-gradient/);
  expect(blue.colorOverrides?.primary).toBe(blue.neumorphism?.accent);
  expect([lightTheme, darkTheme]).toEqual(before);
});

import { expect, it } from "vitest";
import { colorTokens, contrast, luminance, THEME_COLORS, themeColorScheme, colorToHSV, hsvToColor, type ColorScheme } from "./color";
import { colorTheme, lightTheme, darkTheme } from "./presets";

function assertReadable(color: string, scheme: ColorScheme) {
  const t = colorTokens(color, scheme), theme = colorTheme(color, scheme);
  const surfaces = [t.surface, t.background, t.rail, t.field, t.elevated, t.selected, ...t.gradient.match(/#[0-9a-f]{6}/g)!];
  for (const surface of surfaces) {
    for (const text of [t.textPrimary, t.textSecondary, t.accentLine, t.success, t.warning, t.destructive]) {
      expect(contrast(text, surface), `${color}/${scheme}: ${text} on ${surface}`).toBeGreaterThanOrEqual(4.5);
    }
  }
  expect(contrast(t.accentForeground, t.accent)).toBeGreaterThanOrEqual(4.5);
  expect(contrast(t.destructiveForeground, t.destructive)).toBeGreaterThanOrEqual(4.5);
  expect(contrast(t.textPrimary, t.textSecondary)).toBeGreaterThan(1.5);
  expect(luminance(t.shadow)).toBeLessThan(luminance(t.surface));
  expect(luminance(t.highlight)).toBeGreaterThan(luminance(t.surface));
  expect(themeColorScheme(theme)).toBe(scheme);
  expect(theme.colorOverrides?.card).not.toBe(theme.colorOverrides?.popover);
  expect(theme.neumorphism?.field).not.toBe(theme.neumorphism?.surface);
  expect(theme.palette.midground.hex).toBe(t.textPrimary);
}
it.each([...THEME_COLORS, ...["#000000", "#ffffff", "#ffff00", "#0000ff"].map(c => [c, c])])(
  "keeps all %s roles readable in both schemes", (_label, color) => {
    assertReadable(color, "light"); assertReadable(color, "dark");
  },
);
it("keeps roles readable across the RGB cube in both schemes", () => {
  for (let r = 0; r <= 255; r += 17) for (let g = 0; g <= 255; g += 17) for (let b = 0; b <= 255; b += 17) {
    const color = "#" + [r,g,b].map(c => c.toString(16).padStart(2,"0")).join("");
    assertReadable(color, "light"); assertReadable(color, "dark");
  }
}, 30000);
it("keeps an explicit scheme when hue or brightness crosses the old threshold", () => {
  for (const scheme of ["light", "dark"] as const) {
    const a = colorTokens("#9f9f9f", scheme), b = colorTokens("#a0a0a0", scheme);
    expect(a.dark).toBe(b.dark);
    expect(Math.abs(luminance(a.surface) - luminance(b.surface))).toBeLessThan(.01);
    expect(colorTokens("#ff0000", scheme).dark).toBe(colorTokens("#00ff00", scheme).dark);
  }
});
it("retains the legacy scheme without modifying neutral presets", () => {
  expect(colorTokens("#182c54").dark).toBe(true);
  expect(colorTokens("#ded5f0").dark).toBe(false);
  expect(lightTheme.neumorphism?.background).toBe("#e8e8e8");
  expect(darkTheme.neumorphism?.background).toBe("#212121");
});
it.each(["#000000", "#ffffff", "#5275d9", "#ded5f0", "#00ff00", "#808080"])("round trips picker color %s", color => {
  expect(hsvToColor(colorToHSV(color))).toBe(color);
});
it("retains the hue of neutral colors while moving the picker", () => {
  expect(colorToHSV("#ffffff", 170).h).toBe(170);
});

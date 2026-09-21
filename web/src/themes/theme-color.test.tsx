// @vitest-environment jsdom
import { beforeEach, describe, expect, it } from "vitest";

import { BUILTIN_THEMES } from "./presets";
import { DEFAULT_THEME_COLOR, applyThemeColorMeta, resolveThemeColor } from "./theme-color";

const themeColor = () =>
  document.querySelector<HTMLMetaElement>('meta[name="theme-color"]')?.getAttribute("content");

describe("цвет системного хрома", () => {
  beforeEach(() => {
    document.querySelectorAll('meta[name="theme-color"]').forEach((meta) => meta.remove());
    document.documentElement.removeAttribute("style");
  });

  it("берёт непрозрачный холст темы", () => {
    expect(resolveThemeColor(BUILTIN_THEMES.light)).toBe("#e8e8e8");
    expect(resolveThemeColor(BUILTIN_THEMES.dark)).toBe("#212121");
  });

  it("не отдаёт системному хрому вычисляемое или полупрозрачное значение", () => {
    const palette = BUILTIN_THEMES.light.palette;
    expect(
      resolveThemeColor({
        neumorphism: { ...BUILTIN_THEMES.light.neumorphism!, background: "color-mix(in srgb, #fff 50%, transparent)" },
        palette,
      }),
    ).toBe(palette.background.hex);
    expect(resolveThemeColor({ palette: { ...palette, background: { hex: "var(--x)", alpha: 1 } } })).toBe(
      DEFAULT_THEME_COLOR,
    );
    expect(resolveThemeColor(null)).toBe(DEFAULT_THEME_COLOR);
  });

  it("создаёт тег, если его нет, и потом переписывает тот же самый", () => {
    applyThemeColorMeta("#123456");
    applyThemeColorMeta("#654321");
    expect(document.querySelectorAll('meta[name="theme-color"]')).toHaveLength(1);
    expect(themeColor()).toBe("#654321");
  });
});

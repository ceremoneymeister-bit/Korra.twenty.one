import { readFileSync, readdirSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import {
  COMPONENT_SURFACE_FALLBACK,
  componentSurfaceBackground,
} from "./component-surface";

const srcDirectory = fileURLToPath(new URL("..", import.meta.url));

/** Все .ts/.tsx исходники панели. */
function sources(directory: string): { name: string; source: string }[] {
  return readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    const path = `${directory}/${entry.name}`;
    if (entry.isDirectory()) return entry.name === "vendor" ? [] : sources(path);
    if (!/\.tsx?$/.test(entry.name) || entry.name.endsWith(".test.ts") || entry.name.endsWith(".test.tsx")) return [];
    return [{ name: path.slice(srcDirectory.length), source: readFileSync(path, "utf8") }];
  });
}

describe("фон каркасных поверхностей", () => {
  it("даёт переопределению темы выиграть, а без него — непрозрачный холст", () => {
    expect(componentSurfaceBackground("sidebar")).toBe(
      `var(--component-sidebar-background, ${COMPONENT_SURFACE_FALLBACK})`,
    );
    expect(componentSurfaceBackground("header")).toBe(
      `var(--component-header-background, ${COMPONENT_SURFACE_FALLBACK})`,
    );
    // Запас — цепочка до конкретного цвета: иначе объявление снова станет
    // невалидным на момент вычисления и фон схлопнется в transparent.
    expect(COMPONENT_SURFACE_FALLBACK).toMatch(/#[0-9a-f]{6}\)*$/i);
    expect(COMPONENT_SURFACE_FALLBACK).toContain("--neo-background");
    expect(COMPONENT_SURFACE_FALLBACK).toContain("--background-base");
  });

  it("в панели не осталось фона компонента без запасного значения", () => {
    // Регрессия прозрачного выдвижного меню: `var(--component-*-background)`
    // без запаса вычисляется в `unset`, то есть в `transparent`, и перебивает
    // непрозрачный класс рядом.
    const bare = sources(srcDirectory)
      // Сам модуль запаса описывает дефект словами — это его документация.
      .filter(({ name }) => name !== "/themes/component-surface.ts")
      .flatMap(({ name, source }) =>
      source
        .split("\n")
        .map((line, index) => ({ name, line, number: index + 1 }))
        .filter(({ line }) => /var\(\s*--component-[a-z]+-background\s*\)/.test(line))
        .map(({ name: file, number, line }) => `${file}:${number}: ${line.trim()}`),
    );
    expect(bare).toEqual([]);
  });
});

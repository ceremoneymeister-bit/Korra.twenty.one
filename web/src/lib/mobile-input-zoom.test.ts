import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

const indexCss = readFileSync(fileURLToPath(new URL("../index.css", import.meta.url)), "utf8");
const indexHtml = readFileSync(fileURLToPath(new URL("../../index.html", import.meta.url)), "utf8");
const viewport = /<meta\s+[^>]*name="viewport"[\s\S]*?>/.exec(indexHtml)?.[0] ?? "";

/** Тело @media-блока по его условию. */
function mediaBlock(condition: string): string {
  const start = indexCss.indexOf(`@media ${condition}`);
  if (start < 0) return "";
  let depth = 0;
  for (let i = indexCss.indexOf("{", start); i < indexCss.length; i += 1) {
    if (indexCss[i] === "{") depth += 1;
    if (indexCss[i] === "}") {
      depth -= 1;
      if (depth === 0) return indexCss.slice(start, i + 1);
    }
  }
  return "";
}

describe("iOS Safari не масштабирует страницу под фокус в поле", () => {
  // Правило владельца: «при вводе текста и при активации поля ввода
  // происходит автоматический зум и это всё портит» (21.09).
  const block = mediaBlock("(max-width: 1023.98px), (pointer: coarse)");

  it("поля на телефоне и планшете набираются не мельче 16 px", () => {
    expect(block).not.toBe("");
    expect(block).toMatch(/font-size:\s*max\(16px,\s*1rem\)\s*!important/);
    for (const selector of ["input", "textarea", "select"]) {
      expect(block).toMatch(new RegExp(`(^|[\\s,])${selector}(?![\\w-])`, "m"));
    }
  });

  it("покрывает и iPad в портрете, и iPad в альбомной ориентации", () => {
    // `lg` Tailwind — 1024; до него продуктовая оболочка мобильная (768/820).
    // Ровно 1024 CSS px — альбомный iPad: его ловит условие про палец.
    expect(indexCss).toContain("@media (max-width: 1023.98px), (pointer: coarse)");
  });

  it("галочки, переключатели и скрытое поле терминала под правило не попадают", () => {
    expect(block).toContain('input:not([type=\'checkbox\']):not([type=\'radio\'])');
    expect(block).toContain("textarea:not(.xterm-helper-textarea)");
  });

  it("увеличение страницы у человека не забрано", () => {
    expect(viewport).toContain("viewport-fit=cover");
    expect(viewport).not.toMatch(/user-scalable\s*=\s*(no|0)/);
    expect(viewport).not.toMatch(/maximum-scale/);
  });
});

describe("холст документа и системный хром", () => {
  it("html и body красятся одним значением с запасом", () => {
    const canvas = /background:\s*var\(--neo-background,\s*var\(--background-base,\s*#e8e8e8\)\)/g;
    expect(indexCss.match(canvas)?.length).toBeGreaterThanOrEqual(2);
  });

  it("документ открывается с meta theme-color — до первого кадра React", () => {
    expect(indexHtml).toMatch(/<meta\s+name="theme-color"\s+content="#e8e8e8"/);
    expect(indexHtml).toContain("var(--neo-background,var(--background-base,#e8e8e8))");
  });
});

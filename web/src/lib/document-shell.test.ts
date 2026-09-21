import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

const indexHtml = readFileSync(fileURLToPath(new URL("../../index.html", import.meta.url)), "utf8");

/**
 * Оболочка документа принадлежит сразу двум пакетам 0.21.12: мобильному
 * (окраска системного хрома Safari до первого кадра React) и теме (точное
 * название вкладки, выбранное владельцем). Оба обещания живут в соседних
 * строках `index.html`, поэтому разрешение конфликта «целиком одной стороной»
 * молча теряет одно из них. Тест держит их вместе.
 */
describe("оболочка документа", () => {
  it("вкладка браузера называется точно «Korra - Кабинет»", () => {
    expect(/<title>([\s\S]*?)<\/title>/.exec(indexHtml)?.[1]).toBe("Korra - Кабинет");
  });

  it("вместе с названием сохраняется стартовая окраска системного хрома", () => {
    expect(indexHtml).toMatch(/<meta\s+name="theme-color"\s+content="#e8e8e8"/);
    expect(indexHtml).toContain("var(--neo-background,var(--background-base,#e8e8e8))");
  });
});

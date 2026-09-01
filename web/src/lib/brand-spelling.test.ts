import { readFileSync, readdirSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

const pagesDirectory = fileURLToPath(new URL("../pages", import.meta.url));
const forbidden = /(?<![\p{L}\p{N}_])(?:Кора|Коры|Коре|Кору|Корой|Корою|Cora|Kora|КОРA)(?![\p{L}\p{N}_])/u;

describe("Korra brand spelling", () => {
  const pageSources = readdirSync(pagesDirectory)
    .filter((name) => name.endsWith(".ts") || name.endsWith(".tsx"))
    .map((name) => ({
      name,
      source: readFileSync(`${pagesDirectory}/${name}`, "utf8"),
    }));

  it("uses the two-r spelling in every owner-facing page", () => {
    const violations = pageSources.flatMap(({ name, source }) =>
      source
        .split("\n")
        .map((line, index) => ({ name, line, number: index + 1 }))
        .filter(({ line }) => forbidden.test(line))
        .map(({ name: file, number, line }) => `${file}:${number}: ${line.trim()}`),
    );

    expect(violations).toEqual([]);
  });

  it.each(["Кора", "Коры", "КОРA", "Cora", "Kora"])(
    "detects the forbidden spelling %s",
    (sample) => {
      expect(forbidden.test(sample)).toBe(true);
    },
  );

  // Проверка склонений: правило «две р» ловит опечатку, но не ловит
  // неправильный падеж — «с Коррой», а не «с Корра». Список привязан к
  // экранам, которые в этой сборке действительно есть: фразы «Пульта»
  // Екатерины сюда не входят, иначе тест требовал бы чужих страниц.
  it("keeps critical visible phrases correctly inflected", () => {
    const combined = pageSources.map(({ source }) => source).join("\n");
    expect(combined).toContain("Разговор с Коррой");
    expect(combined).toContain("Как здесь работать");
    expect(combined).toContain("Корре");
  });
});

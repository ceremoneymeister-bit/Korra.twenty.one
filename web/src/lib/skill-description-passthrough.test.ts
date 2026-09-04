import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

/**
 * Описание навыка — это его содержимое, а не подпись интерфейса.
 *
 * `russianInterfaceText` существует, чтобы английская проза бэкенда не текла
 * в русский интерфейс, и подменяет её запасной строкой. Применённая к
 * `description` из `SKILL.md`, она стирала единственную строку, по которой
 * видно, что навык умеет: в каталоге из 54 карточек 53 писали «Описание
 * отсутствует.» — при том, что `/api/skills` отдавал описания полностью.
 *
 * Английское описание навыка показываем как есть; заглушка остаётся только
 * для действительно пустого описания.
 */

const SKILLS_PAGE = join(__dirname, "..", "pages", "SkillsPage.tsx");

describe("описания навыков", () => {
  it("не проходят через подменяющий фильтр интерфейса", () => {
    // Схлопнутые пробелы ловят и однострочный, и перенесённый вызов.
    const source = readFileSync(SKILLS_PAGE, "utf8").replace(/\s+/g, " ");
    expect(source).not.toMatch(
      /russianInterfaceText\(\s*(skill|result)\.description/,
    );
  });

  it("страница всё же читает описание навыка", () => {
    const source = readFileSync(SKILLS_PAGE, "utf8");
    expect(source).toMatch(/skill\.description/);
  });
});

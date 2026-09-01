import { describe, expect, it } from "vitest";

import { isTableDelimiter, splitTableRow } from "./markdown-tables";

describe("таблицы в Markdown", () => {
  it("разбивает строку на ячейки", () => {
    expect(splitTableRow("| Часть | Что делает |")).toEqual(["Часть", "Что делает"]);
  });

  it("работает без внешних палок", () => {
    expect(splitTableRow("Часть | Что делает")).toEqual(["Часть", "Что делает"]);
  });

  it("не режет по экранированной палке", () => {
    expect(splitTableRow("| a \\| b | c |")).toEqual(["a | b", "c"]);
  });

  it("узнаёт строку-разделитель", () => {
    expect(isTableDelimiter("|---|---|")).toBe(true);
    expect(isTableDelimiter("| :--- | ---: |")).toBe(true);
    expect(isTableDelimiter("|:-:|")).toBe(true);
  });

  it("не принимает за разделитель обычный текст", () => {
    expect(isTableDelimiter("| Часть | Что делает |")).toBe(false);
    expect(isTableDelimiter("текст без палок")).toBe(false);
    expect(isTableDelimiter("---")).toBe(false);
  });

  it("не принимает за разделитель строку с содержимым", () => {
    expect(isTableDelimiter("| --- | итого |")).toBe(false);
  });
});

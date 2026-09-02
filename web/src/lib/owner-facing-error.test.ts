import { describe, expect, it } from "vitest";

import { ownerFacingError } from "./owner-facing-error";

describe("ownerFacingError", () => {
  it("localizes browser network failures", () => {
    expect(ownerFacingError(new TypeError("Failed to fetch"))).toContain("связаться с сервером");
  });

  it("preserves a Russian backend detail", () => {
    expect(ownerFacingError(new Error('409: {"detail":"Материал уже изменился"}')))
      .toBe("Материал уже изменился");
  });

  it("does not expose English server internals", () => {
    expect(ownerFacingError(new Error("500: sqlite database is locked")))
      .toBe("Сервис временно недоступен. Повторите через минуту.");
  });

  it("does not expose mixed Russian and English internals", () => {
    expect(ownerFacingError(new Error("Ошибка: Connection failed"), "Сбой"))
      .toBe("Сбой");
  });
});

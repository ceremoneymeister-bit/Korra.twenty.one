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
      .toBe("Сервис временно недоступен. Попробуйте позже. Если ошибка повторяется, обратитесь в поддержку.");
  });

  it("does not expose mixed Russian and English internals", () => {
    expect(ownerFacingError(new Error("Ошибка: Connection failed"), "Сбой"))
      .toBe("Сбой");
  });
});


describe("specific recovery advice", () => {
  it("quota wins over HTTP 429 without inventing a reset time or asking for credits", () => {
    const text = ownerFacingError('429: {"error":{"code":"insufficient_quota","message":"quota exceeded"}}');
    expect(text).toContain("объём работы");
    expect(text).not.toMatch(/минут|кредит|пополн|API|провайдер/i);
  });
  it("distinguishes a short request limit from quota and an expired login", () => {
    expect(ownerFacingError('429: too many requests')).toContain("временно");
    expect(ownerFacingError('401: login required')).toContain("Вход в кабинет");
    expect(ownerFacingError('401: {"error":{"message":"OAuth token has expired"}}')).toContain("аккаунт модели");
  });
  it("does not expose a backend payload or claim a model limit for a generic server outage", () => {
    const text = ownerFacingError('502: <html>private backend failed</html>');
    expect(text).toContain("Сервис временно недоступен");
    expect(text).not.toMatch(/private|лимит|подписк/);
  });
});

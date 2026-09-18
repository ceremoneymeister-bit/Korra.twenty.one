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
    expect(text).toContain("Лимит использования модели исчерпан");
    expect(text).not.toMatch(/минут|кредит|пополн|API|провайдер/i);
  });
  it("shows a provider reset time when the usage-limit response includes one", () => {
    const text = ownerFacingError(JSON.stringify({
      error: {
        type: "usage_limit_reached",
        message: "You hit your usage limit.",
        resets_at: "2026-09-19T10:30:00Z",
      },
    }));
    expect(text).toContain("Лимит использования модели исчерпан");
    expect(text).toContain("2026");
    expect(text).toContain("10:30");
    expect(text).not.toContain("когда он обновится");
  });
  it("uses structured rate-limit reason even after the server localized its message", () => {
    const text = ownerFacingError(JSON.stringify({
      error: {
        reason: "rate_limit",
        message: "Модель временно не принимает запросы: достигнут лимит.",
        resets_at: 1_800_000_000,
      },
    }));
    expect(text).toContain("Можно продолжить после");
    expect(text).not.toContain("пока неизвестно");
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

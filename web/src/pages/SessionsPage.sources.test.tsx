// @vitest-environment jsdom

/**
 * Разговор, начатый в самой панели, обязан лежать в «Чатах».
 *
 * Движок метит его источником `dashboard` (заголовок `X-Korra-Session-Source`
 * от прокси чата → `KORRA_SESSION_SOURCE` → строка сессии). До этого он
 * приезжал как `api_server`, а `api_server` — источник автоматизации: клиент
 * открывал историю своих же разговоров и видел «Сессий пока нет».
 */

import { describe, expect, it, vi } from "vitest";

// KorraLoader оборачивает `window.matchMedia` прямо при импорте модуля, а в
// jsdom его нет — ставим заглушку до того, как подтянется страница.
vi.hoisted(() => {
  if (typeof window !== "undefined" && !window.matchMedia) {
    window.matchMedia = ((query: string) => ({
      addEventListener: () => {},
      addListener: () => {},
      dispatchEvent: () => false,
      matches: false,
      media: query,
      onchange: null,
      removeEventListener: () => {},
      removeListener: () => {},
    })) as unknown as typeof window.matchMedia;
  }
});

// Орбита рисует себя через requestAnimationFrame и canvas — в jsdom это шум.
vi.mock("thinking-orbs", () => ({
  ThinkingOrb: ({ state }: { state: string }) => (
    <span data-orb={state} data-testid="orb" />
  ),
}));

import { isAutomationSource, sourceLabel } from "./SessionsPage";

describe("таксономия источников истории", () => {
  it("разговор из панели не считается автоматизацией", () => {
    expect(isAutomationSource("dashboard")).toBe(false);
  });

  it("вызовы сторонних клиентов остаются автоматизацией", () => {
    expect(isAutomationSource("api_server")).toBe(true);
    expect(isAutomationSource("cron")).toBe(true);
    expect(isAutomationSource("webhook")).toBe(true);
  });

  it("каналы людей остаются чатами", () => {
    expect(isAutomationSource("telegram")).toBe(false);
    expect(isAutomationSource("cli")).toBe(false);
  });

  it("служебный ход обслуживания не попадает в «Чаты»", () => {
    // Сервер и так не отдаёт этот класс в списках разговоров; вкладка «Чаты»
    // держит ту же таксономию, чтобы строка не всплыла и при явном выборе.
    expect(isAutomationSource("maintenance")).toBe(true);
  });

  it("источник панели подписан по-русски, а не слагом движка", () => {
    expect(sourceLabel("dashboard")).toBe("Панель");
    expect(sourceLabel("hermes_browser")).toBe("Панель");
  });

  it("служебный класс подписан по-русски", () => {
    expect(sourceLabel("maintenance")).toBe("Обслуживание");
  });
});

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

import { isAutomationSource, sessionSourceQuery, sourceLabel } from "./SessionsPage";

describe("таксономия источников истории", () => {
  it("разговор из панели не считается автоматизацией", () => {
    expect(isAutomationSource("dashboard")).toBe(false);
  });

  it("продолжение результата человеком остаётся в чатах", () => {
    expect(isAutomationSource("cron_discussion")).toBe(false);
    expect(sourceLabel("cron_discussion")).toBe("Обсуждение задачи");
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

describe("фильтр «Истории» по выбранным источникам (0.21.15, ревью Astra §2.2)", () => {
  const all = ["dashboard", "telegram", "kanban", "cron"];

  it("несколько выбранных источников уходят явным набором, а не исключением остальных", () => {
    expect(sessionSourceQuery(["dashboard", "kanban"], "all", all)).toEqual({ sources: ["dashboard", "kanban"] });
  });

  it("один выбранный — своим источником", () => {
    expect(sessionSourceQuery(["kanban"], "all", all)).toEqual({ source: "kanban" });
  });

  it("запуски канбана — автоматизация, и раздел автоматизаций просит их явно", () => {
    expect(isAutomationSource("kanban")).toBe(true);
    expect(sourceLabel("kanban")).toBe("Канбан");
    const query = sessionSourceQuery(null, "automation", all);
    expect(query.sources).toEqual(expect.arrayContaining(["cron", "kanban", "maintenance"]));
  });

  it("«Все» явно запрашивает и разговоры, и автоматизации (второе чистое ревью Astra, P2-3)", () => {
    const query = sessionSourceQuery(null, "all", all);
    expect(query.sources).toEqual(expect.arrayContaining(["dashboard", "telegram", "cron", "kanban"]));
    expect(query.excludeSources).toBeUndefined();
  });

  it("раздел чатов по-прежнему исключает автоматизации", () => {
    expect(sessionSourceQuery(null, "chats", all).excludeSources).toEqual(expect.arrayContaining(["cron", "kanban"]));
  });
});

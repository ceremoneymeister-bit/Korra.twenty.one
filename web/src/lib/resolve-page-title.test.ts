import { describe, expect, it } from "vitest";
import { resolvePageTitle } from "./resolve-page-title";
import type { Translations } from "@/i18n/types";

// Minimal translations stub — only the fields resolvePageTitle touches.
const t = {
  app: {
    webUi: "Web UI",
    nav: {
      analytics: "Analytics",
      chat: "Chat",
      config: "Config",
      cron: "Cron",
      documentation: "Documentation",
      keys: "Keys",
      logs: "Logs",
      models: "Models",
      profiles: "Profiles",
      plugins: "Plugins",
      sessions: "Sessions",
      skills: "Skills",
    },
  },
} as unknown as Translations;

describe("resolvePageTitle", () => {
  it("называет мастер и раздел достижений даже со старым манифестом", () => {
    expect(resolvePageTitle("/profiles/new/", t, [])).toBe("Создать агента");
    expect(resolvePageTitle("/achievements", t, [{ path: "/achievements", label: "Achievements" }])).toBe("Достижения");
    // Плагин мог остаться с прежней подписью — заголовок всё равно продуктовый.
    expect(resolvePageTitle("/achievements", t, [{ path: "/achievements", label: "Польза от агентов" }])).toBe("Достижения");
  });
  it("uses i18n nav keys for translated routes", () => {
    expect(resolvePageTitle("/sessions", t, [])).toBe("Sessions");
    expect(resolvePageTitle("/env", t, [])).toBe("Keys");
  });

  it("renders initialisms and literal labels correctly", () => {
    // Regression: the naive capitalize fallback produced "Mcp".
    expect(resolvePageTitle("/mcp", t, [])).toBe("MCP");
    expect(resolvePageTitle("/system", t, [])).toBe("Система");
    expect(resolvePageTitle("/channels", t, [])).toBe("Каналы");
    expect(resolvePageTitle("/webhooks", t, [])).toBe("Вебхуки");
    expect(resolvePageTitle("/pairing", t, [])).toBe("Подключения");
    expect(resolvePageTitle("/files", t, [])).toBe("Файлы");
  });

  it("называет дашборд одинаково в продукте и по прямой ссылке в панели", () => {
    // В панели пункта меню нет, но заголовок у открытого экрана быть обязан.
    expect(resolvePageTitle("/dashboard", t, [])).toBe("Дашборд");
    (globalThis as { window?: unknown }).window = { __KORRA_UI_MODE__: "fleet" };
    expect(resolvePageTitle("/dashboard", t, [])).toBe("Дашборд");
    delete (globalThis as { window?: unknown }).window;
  });

  it("uses the configured fleet label before the admin fallback", () => {
    (globalThis as { window?: unknown }).window = { __KORRA_UI_MODE__: "fleet" };
    expect(resolvePageTitle("/agents", t, [])).toBe("Агенты");
    // «История» переехала в «Служебное»; заголовок обязан совпасть с пунктом.
    expect(resolvePageTitle("/sessions", t, [])).toBe("История");
    delete (globalThis as { window?: unknown }).window;
  });

  it("prefers plugin tab labels", () => {
    expect(
      resolvePageTitle("/notes", t, [{ path: "/notes", label: "Заметки" }]),
    ).toBe("Заметки");
    // Короткое имя плагина — не проза, его не подменяем: заголовок обязан
    // совпадать с пунктом меню, по которому на экран пришли.
    expect(
      resolvePageTitle("/notes", t, [{ path: "/notes", label: "Kanban" }]),
    ).toBe("Kanban");
  });

  it("names the shipped kanban board without a plugin tab", () => {
    expect(resolvePageTitle("/kanban", t, [])).toBe("Канбан-доска");
  });

  it("falls back to the generic plugin title for English prose labels", () => {
    expect(
      resolvePageTitle("/notes", t, [
        { path: "/notes", label: "Notes and long form drafts" },
      ]),
    ).toBe("Плагин Korra");
  });

  it("объясняет неизвестный маршрут без заглушки в заголовке", () => {
    expect(resolvePageTitle("/whatever", t, [])).toBe("Раздел не найден");
  });

  it("treats root as sessions and trailing slashes as equivalent", () => {
    expect(resolvePageTitle("/", t, [])).toBe("Sessions");
    expect(resolvePageTitle("/mcp/", t, [])).toBe("MCP");
  });
});

import { describe, expect, it } from "vitest";

import {
  productHomePath,
  selectProductNav,
  selectProductSettingsNav,
  selectServiceNav,
  type NavEntry,
} from "./product-nav";

const ADMIN_NAV: NavEntry[] = [
  { path: "/chat", labelKey: "chat", label: "Chat" },
  { path: "/agents", label: "Agents" },
  { path: "/orders", label: "Orders" },
  { path: "/rates", label: "Rates" },
  { path: "/sessions", labelKey: "sessions", label: "Sessions" },
  { path: "/files", label: "Files" },
  { path: "/models", labelKey: "models", label: "Models" },
  { path: "/logs", labelKey: "logs", label: "Logs" },
  { path: "/cron", labelKey: "cron", label: "Cron" },
  { path: "/help", label: "Help" },
  { path: "/skills", labelKey: "skills", label: "Skills" },
  { path: "/plugins", labelKey: "plugins", label: "Plugins" },
  { path: "/mcp", label: "MCP" },
  { path: "/channels", label: "Channels" },
  { path: "/webhooks", label: "Webhooks" },
  { path: "/pairing", label: "Pairing" },
  { path: "/profiles", labelKey: "profiles", label: "Profiles" },
  { path: "/config", labelKey: "config", label: "Config" },
  { path: "/env", labelKey: "keys", label: "Keys" },
  { path: "/system", label: "System" },
  { path: "/docs", labelKey: "documentation", label: "Documentation" },
];

describe("selectProductNav", () => {
  it("связывает расчётчиков, заказы и данные в одном рабочем меню", () => {
    const nav = selectProductNav(ADMIN_NAV, "calc");
    expect(nav.map(({ path }) => path)).toEqual([
      "/agents", "/orders", "/rates", "/files", "/sessions", "/help",
    ]);
    expect(nav.map(({ label }) => label)).toEqual([
      "Расчётчики", "Заказы", "Данные", "Файлы", "История", "Помощь",
    ]);
    expect(nav.every(({ labelKey }) => labelKey === undefined)).toBe(true);
  });
  it("убирает отдельный чат и оставляет четыре рабочих экрана", () => {
    expect(selectProductNav(ADMIN_NAV, "fleet")).toEqual([
      { path: "/agents", label: "Агенты", labelKey: undefined },
      { path: "/files", label: "Файлы", labelKey: undefined },
      { path: "/sessions", label: "История", labelKey: undefined },
      { path: "/cron", label: "Задачи", labelKey: undefined },
    ]);
  });
});

describe("secondary navigation", () => {
  it("держит справку расчётчика в основном меню и скрывает служебные настройки", () => {
    expect(selectProductSettingsNav(ADMIN_NAV, "calc").map(({ path }) => path)).toEqual([
      "/env", "/models",
    ]);
    expect(selectServiceNav(ADMIN_NAV, "calc")).toEqual([]);
  });
  it("формирует настройки отдельно от служебных экранов", () => {
    expect(selectProductSettingsNav(ADMIN_NAV).map((item) => item.path)).toEqual([
      "/env",
      "/models",
      "/logs",
      "/help",
    ]);
    expect(selectServiceNav(ADMIN_NAV).map((item) => item.path)).toEqual([
      "/skills",
      "/plugins",
      "/mcp",
      "/channels",
      "/webhooks",
      "/pairing",
      "/profiles",
      "/config",
      "/system",
    ]);
  });
});

describe("productHomePath", () => {
  it("делает вкладку агентов домашним экраном fleet", () => {
    expect(productHomePath("fleet")).toBe("/agents");
    expect(productHomePath("calc")).toBe("/agents");
    expect(productHomePath(null)).toBe("/sessions");
  });
});

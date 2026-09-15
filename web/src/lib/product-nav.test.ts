import { describe, expect, it } from "vitest";

import {
  productHomePath,
  selectProductNav,
  selectProductSettingsNav,
  selectProductSidebar,
  selectServiceNav,
  type NavEntry,
} from "./product-nav";

const ADMIN_NAV: NavEntry[] = [
  { path: "/chat", labelKey: "chat", label: "Chat" },
  { path: "/agents", label: "Agents" },
  { path: "/sessions", labelKey: "sessions", label: "Sessions" },
  { path: "/files", label: "Files" },
  { path: "/models", labelKey: "models", label: "Models" },
  { path: "/logs", labelKey: "logs", label: "Logs" },
  { path: "/cron", labelKey: "cron", label: "Cron" },
  { path: "/help", label: "Help" },
  { path: "/updates", label: "Updates" },
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

/** Вкладки плагинов контура так, как их собирает App из манифестов. */
const PLUGIN_NAV: NavEntry[] = [
  { path: "/kanban", label: "Канбан-доска" },
  { path: "/achievements", label: "Достижения" },
  { path: "/notes", label: "Заметки" },
];

describe("selectProductNav", () => {
  it("убирает отдельный чат и оставляет три рабочих экрана", () => {
    expect(selectProductNav(ADMIN_NAV, "fleet")).toEqual([
      { path: "/agents", label: "Агенты", labelKey: undefined },
      { path: "/files", label: "Файлы", labelKey: undefined },
      { path: "/cron", label: "Задачи", labelKey: undefined },
    ]);
  });
});

describe("secondary navigation", () => {
  it("формирует настройки отдельно от служебных экранов", () => {
    expect(selectProductSettingsNav(ADMIN_NAV).map((item) => item.path)).toEqual([
      "/env",
      "/models",
      "/updates",
      "/logs",
      "/help",
    ]);
    // Подпись продукта, а не админской панели: пункт называется по-русски.
    expect(
      selectProductSettingsNav(ADMIN_NAV).find((item) => item.path === "/updates")?.label,
    ).toBe("Обновления");
    expect(selectServiceNav(ADMIN_NAV).map((item) => item.path)).toEqual([
      "/sessions",
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

  it("ставит вкладку плагина на её место в группе, а незнакомую — в конец", () => {
    expect(selectServiceNav(ADMIN_NAV, PLUGIN_NAV).map((item) => item.path)).toEqual([
      "/sessions",
      "/achievements",
      "/skills",
      "/plugins",
      "/mcp",
      "/channels",
      "/webhooks",
      "/pairing",
      "/profiles",
      "/config",
      "/system",
      // Канбан остаётся в главном списке, поэтому здесь его нет.
      "/notes",
    ]);
  });
});

describe("selectProductSidebar", () => {
  const groups = selectProductSidebar(ADMIN_NAV, PLUGIN_NAV, "fleet");

  it("убирает историю и достижения из главного меню, оставляя канбан под задачами", () => {
    expect(groups.main.map((item) => item.path)).toEqual([
      "/agents",
      "/files",
      "/cron",
      "/kanban",
    ]);
  });

  it("показывает перенесённые пункты в «Служебном» ровно один раз и под нужными именами", () => {
    const service = groups.service;
    expect(service.filter((item) => item.path === "/sessions")).toHaveLength(1);
    expect(service.filter((item) => item.path === "/achievements")).toHaveLength(1);
    expect(service.find((item) => item.path === "/sessions")?.label).toBe("История");
    expect(service.find((item) => item.path === "/achievements")?.label).toBe(
      "Достижения",
    );
    // Подпись админской панели не должна подменять продуктовую переводом.
    expect(service.find((item) => item.path === "/sessions")?.labelKey).toBeUndefined();
  });

  it("не повторяет ни один пункт между группами сайдбара", () => {
    const paths = [...groups.main, ...groups.settings, ...groups.service].map(
      (item) => item.path,
    );
    expect(new Set(paths).size).toBe(paths.length);
  });

  it("не создаёт мёртвый пункт, когда плагина в контуре нет", () => {
    const withoutPlugins = selectProductSidebar(ADMIN_NAV, [], "fleet");
    const paths = [
      ...withoutPlugins.main,
      ...withoutPlugins.settings,
      ...withoutPlugins.service,
    ].map((item) => item.path);
    expect(paths).not.toContain("/achievements");
    expect(paths).not.toContain("/kanban");
    expect(paths).toContain("/sessions");
  });
});

describe("productHomePath", () => {
  it("делает вкладку агентов домашним экраном fleet", () => {
    expect(productHomePath("fleet")).toBe("/agents");
    expect(productHomePath(null)).toBe("/sessions");
  });
});

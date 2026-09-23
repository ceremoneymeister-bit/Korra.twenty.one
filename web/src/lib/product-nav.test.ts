import { describe, expect, it } from "vitest";

import {
  productHomePath,
  resolveOpenGroup,
  selectProductNav,
  selectProductSettingsNav,
  selectProductSidebar,
  selectServiceNav,
  stripProductOnlyNav,
  type NavEntry,
} from "./product-nav";

// Список встроенных пунктов так, как его собирает App: «Дашборд» лежит в нём
// вместе с админскими экранами, а из меню панели его убирает
// stripProductOnlyNav.
const ADMIN_NAV: NavEntry[] = [
  { path: "/chat", labelKey: "chat", label: "Chat" },
  { path: "/dashboard", label: "Дашборд" },
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
  it("убирает отдельный чат и оставляет четыре рабочих экрана", () => {
    expect(selectProductNav(ADMIN_NAV, "fleet")).toEqual([
      { path: "/dashboard", label: "Дашборд", labelKey: undefined },
      { path: "/agents", label: "Агенты", labelKey: undefined },
      { path: "/files", label: "Файлы", labelKey: undefined },
      { path: "/cron", label: "Задачи", labelKey: undefined },
    ]);
  });

  it("ставит дашборд первым пунктом, прямо над агентами", () => {
    const paths = selectProductNav(ADMIN_NAV, "fleet").map((item) => item.path);
    expect(paths[0]).toBe("/dashboard");
    expect(paths[1]).toBe("/agents");
  });
});

describe("stripProductOnlyNav", () => {
  it("прячет дашборд от административной панели, не трогая остальное меню", () => {
    const admin = stripProductOnlyNav(ADMIN_NAV, null);
    expect(admin.map((item) => item.path)).not.toContain("/dashboard");
    expect(admin.map((item) => item.path)).toEqual(
      ADMIN_NAV.filter((item) => item.path !== "/dashboard").map(
        (item) => item.path,
      ),
    );
  });

  it("оставляет продуктовые экраны продукту", () => {
    expect(
      stripProductOnlyNav(ADMIN_NAV, "fleet").map((item) => item.path),
    ).toContain("/dashboard");
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
      "/dashboard",
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

describe("resolveOpenGroup", () => {
  const groups = selectProductSidebar(ADMIN_NAV, PLUGIN_NAV, "fleet");

  it("раскрывает группу текущего экрана — в том числе после прямого перехода и F5", () => {
    // Свежее состояние без выбора пользователя = прямой заход или обновление.
    expect(resolveOpenGroup("/sessions", groups, null)).toBe("service");
    expect(resolveOpenGroup("/achievements", groups, null)).toBe("service");
    expect(resolveOpenGroup("/env", groups, null)).toBe("settings");
    // Вложенный экран открывает группу своего пункта.
    expect(resolveOpenGroup("/help/tasks", groups, null)).toBe("settings");
    expect(resolveOpenGroup("/profiles/new", groups, null)).toBe("service");
  });

  it("держит группы закрытыми на экранах главного списка", () => {
    expect(resolveOpenGroup("/dashboard", groups, null)).toBeNull();
    expect(resolveOpenGroup("/agents", groups, null)).toBeNull();
    expect(resolveOpenGroup("/kanban", groups, null)).toBeNull();
    // До загрузки манифестов групп ещё нет — и открывать нечего.
    expect(resolveOpenGroup("/sessions", null, null)).toBeNull();
  });

  it("не открывает заново группу, свёрнутую вручную на этом же экране", () => {
    const collapsed = { path: "/sessions", group: null };
    expect(resolveOpenGroup("/sessions", groups, collapsed)).toBeNull();
    // Выбор пользователя важнее авто-раскрытия и на соседней группе.
    expect(
      resolveOpenGroup("/sessions", groups, { path: "/sessions", group: "settings" }),
    ).toBe("settings");
  });

  it("снова раскрывает группу при переходе на другой её пункт", () => {
    const collapsed = { path: "/sessions", group: null };
    expect(resolveOpenGroup("/achievements", groups, collapsed)).toBe("service");
    expect(resolveOpenGroup("/skills", groups, collapsed)).toBe("service");
    // Ручное раскрытие на прежнем экране не тянется на экран другой группы.
    expect(
      resolveOpenGroup("/env", groups, { path: "/agents", group: "service" }),
    ).toBe("settings");
  });
});

describe("productHomePath", () => {
  it("делает дашборд стартовым экраном продукта, не трогая админскую панель", () => {
    expect(productHomePath("fleet")).toBe("/dashboard");
    expect(productHomePath(null)).toBe("/sessions");
  });
});

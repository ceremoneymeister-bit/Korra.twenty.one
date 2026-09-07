import { describe, expect, it } from "vitest";

import {
  agentSettingsHref,
  buildAgentTabs,
  MAIN_AGENT_TAB,
  MAX_AGENT_TABS,
  sameAgentTabs,
} from "./agent-tabs";

const MAIN = { profile: "", label: "Корра" };

function profile(
  name: string,
  extra: Record<string, unknown> = {},
): Record<string, unknown> {
  return {
    name,
    path: `/opt/data/profiles/${name}`,
    is_default: false,
    model: null,
    provider: null,
    has_env: false,
    skill_count: 0,
    gateway_running: false,
    description: "",
    description_auto: false,
    display_name: "",
    distribution_name: null,
    distribution_version: null,
    distribution_source: null,
    has_alias: false,
    ...extra,
  };
}

describe("buildAgentTabs", () => {
  it("без профилей и при мусоре вместо списка остаётся одна главная вкладка", () => {
    for (const input of [[], null, undefined, "default", 42, {}]) {
      expect(buildAgentTabs(input)).toEqual([MAIN]);
    }
  });

  it("главный агент всегда первый и носит имя из профиля default, второй раз не добавляется", () => {
    const tabs = buildAgentTabs([
      profile("zebra"),
      profile("default", { is_default: true, display_name: "Главный" }),
      profile("alpha"),
    ]);
    expect(tabs[0]).toEqual({ profile: "", label: "Главный" });
    expect(tabs.map((tab) => tab.profile)).toEqual(["", "zebra", "alpha"]);
    expect(tabs.filter((tab) => tab.profile === "")).toHaveLength(1);
  });

  it("главный агент без своего имени подписан «Корра», с именем — им", () => {
    // Живой случай 07.09.2026: у клиентки главного агента зовут «Зара» — так
    // его отдаёт /api/profiles, а вкладка называлась «Корра» жёстко в коде.
    expect(buildAgentTabs([profile("alpha")])[0]).toEqual(MAIN);
    expect(
      buildAgentTabs([profile("default", { is_default: true, display_name: "  Зара " })])[0],
    ).toEqual({ profile: "", label: "Зара" });
    // Пустое имя не затирает подпись по умолчанию.
    expect(
      buildAgentTabs([profile("default", { is_default: true, display_name: "   " })])[0],
    ).toEqual(MAIN);
  });

  it("подпись — имя, заданное владельцем, иначе имя профиля; описание уходит в подсказку", () => {
    const tabs = buildAgentTabs([
      profile("buhgalter", {
        display_name: "  Бухгалтер  ",
        description: " Считает и сверяет первичку ",
      }),
      profile("raschet"),
    ]);
    expect(tabs[1]).toEqual({
      profile: "buhgalter",
      label: "Бухгалтер",
      description: "Считает и сверяет первичку",
    });
    expect(tabs[2]).toEqual({ profile: "raschet", label: "raschet" });
    expect("description" in tabs[2]).toBe(false);
  });

  it("порядок именованных профилей — как отдал сервер", () => {
    const names = ["m", "a", "z", "k"];
    const tabs = buildAgentTabs(names.map((name) => profile(name)));
    expect(tabs.slice(1).map((tab) => tab.profile)).toEqual(names);
  });

  it("не больше десяти вкладок вместе с главной, лишние отбрасываются с конца", () => {
    const many = Array.from({ length: 14 }, (_, index) =>
      profile(`agent-${String(index).padStart(2, "0")}`),
    );
    const tabs = buildAgentTabs(many);
    expect(tabs).toHaveLength(MAX_AGENT_TABS);
    expect(tabs[0]).toEqual(MAIN);
    expect(tabs[MAX_AGENT_TABS - 1].profile).toBe("agent-08");
  });

  it("дубли схлопываются, первая запись выигрывает", () => {
    const tabs = buildAgentTabs([
      profile("dup", { display_name: "Первый" }),
      profile("dup", { display_name: "Второй" }),
      profile(" dup "),
    ]);
    expect(tabs).toEqual([MAIN, { profile: "dup", label: "Первый" }]);
  });

  it("имена вне грамматики движка и битые записи пропускаются без исключений", () => {
    const tabs = buildAgentTabs([
      profile("Bad Name"),
      profile("-leading-dash"),
      profile("dot.ted"),
      profile("Кириллица"),
      profile(""),
      profile("a".repeat(65)),
      null,
      "string-instead-of-object",
      {},
      { name: 17 },
      { name: "ok-one", display_name: 5, description: ["x"] },
    ]);
    expect(tabs).toEqual([MAIN, { profile: "ok-one", label: "ok-one" }]);
  });

  it("остановленный профиль, профиль без бота и без модели — всё равно вкладки", () => {
    const tabs = buildAgentTabs([
      profile("stopped", { gateway_running: false }),
      profile("no-env", { has_env: false, model: null, provider: null }),
      profile("running", { gateway_running: true, has_env: true }),
    ]);
    expect(tabs.map((tab) => tab.profile)).toEqual([
      "",
      "stopped",
      "no-env",
      "running",
    ]);
  });

  it("каждый вызов отдаёт свою копию главной вкладки", () => {
    const tabs = buildAgentTabs([]);
    expect(tabs[0]).not.toBe(MAIN_AGENT_TAB);
    expect(tabs[0]).toEqual(MAIN_AGENT_TAB);
  });
});

describe("sameAgentTabs", () => {
  it("сравнивает состав по профилю, подписи и описанию", () => {
    const a = buildAgentTabs([profile("x", { description: "d" })]);
    expect(sameAgentTabs(a, buildAgentTabs([profile("x", { description: "d" })]))).toBe(true);
    expect(sameAgentTabs(a, buildAgentTabs([profile("x")]))).toBe(false);
    expect(sameAgentTabs(a, buildAgentTabs([profile("x", { display_name: "X" })]))).toBe(false);
    expect(sameAgentTabs(a, buildAgentTabs([]))).toBe(false);
    expect(sameAgentTabs(a, buildAgentTabs([profile("y", { description: "d" })]))).toBe(false);
  });
});

describe("agentSettingsHref", () => {
  it("ведёт к роли или модели именно этого агента", () => {
    expect(agentSettingsHref("calculator", "role")).toBe(
      "/profiles?agent=calculator&edit=role",
    );
    expect(agentSettingsHref("calculator", "model")).toBe(
      "/profiles?agent=calculator&edit=model",
    );
  });

  it("главная вкладка — профиль панели, в настройках он default", () => {
    expect(agentSettingsHref(MAIN_AGENT_TAB.profile, "role")).toBe(
      "/profiles?agent=default&edit=role",
    );
  });

  it("навыки и расписание открываются в своих разделах с явным профилем", () => {
    expect(agentSettingsHref("calculator", "skills")).toBe(
      "/skills?profile=calculator",
    );
    expect(agentSettingsHref("calculator", "schedule")).toBe(
      "/cron?profile=calculator",
    );
    expect(agentSettingsHref(MAIN_AGENT_TAB.profile, "skills")).toBe(
      "/skills?profile=default",
    );
  });
});

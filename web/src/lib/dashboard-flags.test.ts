import { afterEach, describe, expect, it } from "vitest";

import {
  getAgentTabs,
  isProductUiMode,
  productUiMode,
} from "./dashboard-flags";

function setUiMode(mode: string | undefined) {
  (globalThis as { window?: unknown }).window = { __KORRA_UI_MODE__: mode };
}

afterEach(() => {
  delete (globalThis as { window?: unknown }).window;
});

describe("productUiMode", () => {
  it("узнаёт основной флотовый интерфейс", () => {
    setUiMode("fleet");
    expect(productUiMode()).toBe("fleet");
    expect(isProductUiMode()).toBe(true);
  });

  it("любое другое значение означает административную панель", () => {
    for (const mode of ["admin", "", "operator", undefined]) {
      setUiMode(mode);
      expect(productUiMode()).toBeNull();
      expect(isProductUiMode()).toBe(false);
    }
  });
});

describe("getAgentTabs", () => {
  it("валидирует, дедуплицирует и ограничивает вкладки десятью", () => {
    (globalThis as { window?: unknown }).window = {
      __KORRA_AGENT_TABS__: [
        ...Array.from({ length: 12 }, (_, index) => ({
          profile: `agent-${index}`,
          label: `Агент ${index}`,
        })),
        { profile: "agent-0", label: "Дубль" },
        { profile: "", label: "Пустой" },
      ],
    };
    const tabs = getAgentTabs();
    expect(tabs).toHaveLength(10);
    expect(tabs[0]).toEqual({ profile: "agent-0", label: "Агент 0" });
  });

  it("без конфигурации оставляет один основной чат", () => {
    setUiMode("fleet");
    expect(getAgentTabs()).toEqual([{ profile: "", label: "Корра" }]);
  });
});

// @vitest-environment jsdom

import { act, useEffect } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useAgentTabs, type UseAgentTabsReturn } from "./useAgentTabs";

const getProfiles = vi.fn();
const getAgentTabs = vi.fn();
const setAgentTabs = vi.fn();
const updateProfileDisplayName = vi.fn();

vi.mock("@/lib/api", () => ({
  api: {
    getProfiles: () => getProfiles(),
    getAgentTabs: () => getAgentTabs(),
    setAgentTabs: (layout: { revision: number; order: string[]; hidden: string[] }) =>
      setAgentTabs(layout),
    updateProfileDisplayName: (name: string, displayName: string) =>
      updateProfileDisplayName(name, displayName),
  },
}));

let container: HTMLDivElement;
let root: Root;
let current: UseAgentTabsReturn;
let serverLayout: {
  version: 1;
  revision: number;
  initialized: boolean;
  order: string[];
  hidden: string[];
};

function Probe({ onValue }: { onValue: (value: UseAgentTabsReturn) => void }) {
  const value = useAgentTabs();
  useEffect(() => onValue(value), [onValue, value]);
  return null;
}

async function mount() {
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  await act(async () => {
    root.render(
      <Probe
        onValue={(value) => {
          current = value;
        }}
      />,
    );
  });
}

beforeEach(() => {
  window.localStorage.clear();
  getProfiles.mockReset();
  getAgentTabs.mockReset();
  setAgentTabs.mockReset();
  serverLayout = {
    version: 1,
    revision: 0,
    initialized: false,
    order: [],
    hidden: [],
  };
  getAgentTabs.mockImplementation(async () => ({ ...serverLayout }));
  setAgentTabs.mockImplementation(async (layout) => {
    serverLayout = {
      version: 1,
      revision: serverLayout.revision + 1,
      initialized: true,
      order: [...layout.order],
      hidden: [...layout.hidden],
    };
    return { ...serverLayout };
  });
  updateProfileDisplayName.mockReset();
  updateProfileDisplayName.mockResolvedValue({
    ok: true,
    display_name: "",
  });
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
});

describe("useAgentTabs", () => {
  it("после ответа сервера показывает главную вкладку и реальные профили", async () => {
    getProfiles.mockResolvedValue({
      profiles: [
        { name: "default", is_default: true, display_name: "", description: "" },
        { name: "raschet", is_default: false, display_name: "Расчётчик", description: "" },
      ],
    });
    await mount();
    await act(async () => {});
    expect(current.tabs).toEqual([
      { profile: "", label: "Корра" },
      { profile: "raschet", label: "Расчётчик" },
    ]);
  });

  it("берёт подпись из display_name, а без него оставляет name", async () => {
    getProfiles.mockResolvedValue({
      profiles: [
        {
          name: "analyst",
          is_default: false,
          display_name: "  Аналитик  ",
        },
        { name: "writer", is_default: false, display_name: "" },
      ],
    });
    await mount();
    await act(async () => {});

    expect(current.tabs).toEqual([
      { profile: "", label: "Корра" },
      { profile: "analyst", label: "Аналитик" },
      { profile: "writer", label: "writer" },
    ]);
  });

  it("сохраняет display_name и обновляет подпись без перемонтирования вкладки", async () => {
    getProfiles.mockResolvedValue({
      profiles: [{ name: "writer", is_default: false }],
    });
    await mount();
    await act(async () => {});

    await act(async () => {
      await current.updateDisplayName("writer", "  Редактор  ");
    });

    expect(updateProfileDisplayName).toHaveBeenCalledWith("writer", "Редактор");
    expect(current.tabs).toEqual([
      { profile: "", label: "Корра" },
      { profile: "writer", label: "Редактор" },
    ]);
  });

  it("ошибка сети не роняет экран: остаётся главная вкладка", async () => {
    getProfiles.mockRejectedValue(new Error("502"));
    await mount();
    await act(async () => {});
    expect(current.tabs).toEqual([{ profile: "", label: "Корра" }]);
  });

  it("сбой layout endpoint не прячет реальные профили", async () => {
    getProfiles.mockResolvedValue({
      profiles: [{ name: "designer", is_default: false, display_name: "Дизайнер" }],
    });
    getAgentTabs.mockRejectedValue(new Error("layout unavailable"));
    await mount();
    await act(async () => {});
    expect(current.tabs.map((tab) => tab.profile)).toEqual(["", "designer"]);
  });

  it("битый ответ без списка — тоже только главная вкладка", async () => {
    getProfiles.mockResolvedValue({ profiles: "not-a-list" });
    await mount();
    await act(async () => {});
    expect(current.tabs).toEqual([{ profile: "", label: "Корра" }]);
  });

  it("refresh подхватывает новый профиль, а при том же составе не меняет ссылку", async () => {
    getProfiles.mockResolvedValue({ profiles: [] });
    await mount();
    await act(async () => {});
    const before = current.tabs;

    await act(async () => {
      await current.refresh();
    });
    expect(current.tabs).toBe(before);

    getProfiles.mockResolvedValue({
      profiles: [{ name: "novyj", is_default: false }],
    });
    await act(async () => {
      await current.refresh();
    });
    expect(current.tabs.map((tab) => tab.profile)).toEqual(["", "novyj"]);
  });

  it("сбой при обновлении сохраняет прошлый состав", async () => {
    getProfiles.mockResolvedValue({
      profiles: [{ name: "keep", is_default: false }],
    });
    await mount();
    await act(async () => {});
    expect(current.tabs.map((tab) => tab.profile)).toEqual(["", "keep"]);

    getProfiles.mockRejectedValue(new Error("timeout"));
    await act(async () => {
      await current.refresh();
    });
    expect(current.tabs.map((tab) => tab.profile)).toEqual(["", "keep"]);
  });

  it("восстанавливает порядок, позволяет двигать главную вкладку и добавляет новый профиль в конец", async () => {
    window.localStorage.setItem(
      "korra.agentTabs.order",
      JSON.stringify(["writer", "", "analyst"]),
    );
    getProfiles.mockResolvedValue({
      profiles: [
        { name: "analyst", is_default: false },
        { name: "writer", is_default: false },
        { name: "newcomer", is_default: false },
      ],
    });
    await mount();
    await act(async () => {});

    expect(current.tabs.map((tab) => tab.profile)).toEqual([
      "writer",
      "",
      "analyst",
      "newcomer",
    ]);

    await act(async () => current.moveTab("", "left"));
    expect(current.tabs.map((tab) => tab.profile)).toEqual([
      "",
      "writer",
      "analyst",
      "newcomer",
    ]);
    expect(setAgentTabs).toHaveBeenLastCalledWith({
      revision: 1,
      order: ["", "writer", "analyst", "newcomer"],
      hidden: [],
    });
    expect(window.localStorage.getItem("korra.agentTabs.order")).toBeNull();
  });

  it("скрывает вкладку, не скрывает Корру и возвращает профиль", async () => {
    getProfiles.mockResolvedValue({
      profiles: [
        { name: "writer", is_default: false, display_name: "Редактор" },
      ],
    });
    await mount();
    await act(async () => {});

    await act(async () => current.hideTab("writer"));
    expect(current.tabs.map((tab) => tab.profile)).toEqual([""]);
    expect(current.hiddenTabs.map((tab) => tab.profile)).toEqual(["writer"]);
    expect(setAgentTabs).toHaveBeenLastCalledWith({
      revision: 1,
      order: ["", "writer"],
      hidden: ["writer"],
    });

    await act(async () => current.hideTab(""));
    expect(current.tabs.map((tab) => tab.profile)).toEqual([""]);

    await act(async () => current.showTab("writer"));
    expect(current.tabs.map((tab) => tab.profile)).toEqual(["", "writer"]);
    expect(current.hiddenTabs).toEqual([]);
  });

  it("вычищает удалённый профиль из порядка и скрытых вкладок", async () => {
    window.localStorage.setItem(
      "korra.agentTabs.order",
      JSON.stringify(["ghost", "", "keep"]),
    );
    window.localStorage.setItem(
      "korra.agentTabs.hidden",
      JSON.stringify(["ghost"]),
    );
    getProfiles.mockResolvedValue({
      profiles: [{ name: "keep", is_default: false }],
    });
    await mount();
    await act(async () => {});

    expect(current.tabs.map((tab) => tab.profile)).toEqual(["", "keep"]);
    expect(current.hiddenTabs).toEqual([]);
    expect(setAgentTabs).toHaveBeenCalledWith({
      revision: 0,
      order: ["", "keep"],
      hidden: [],
    });
    expect(window.localStorage.getItem("korra.agentTabs.order")).toBeNull();
    expect(window.localStorage.getItem("korra.agentTabs.hidden")).toBeNull();
  });

  it("при конфликте revision принимает серверный порядок, а не часы браузера", async () => {
    serverLayout = {
      version: 1,
      revision: 7,
      initialized: true,
      order: ["", "writer", "analyst"],
      hidden: [],
    };
    getProfiles.mockResolvedValue({
      profiles: [
        { name: "writer", is_default: false },
        { name: "analyst", is_default: false },
      ],
    });
    await mount();
    await act(async () => {});
    setAgentTabs.mockRejectedValueOnce(new Error("409"));
    serverLayout = { ...serverLayout, revision: 8, order: ["analyst", "", "writer"] };

    await act(async () => {
      current.moveTab("writer", "left");
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(current.tabs.map((tab) => tab.profile)).toEqual(["analyst", "", "writer"]);
  });

  it("переставляет вкладку pointer/touch-операцией через общий server layout", async () => {
    serverLayout = {
      version: 1,
      revision: 3,
      initialized: true,
      order: ["", "writer", "analyst"],
      hidden: [],
    };
    getProfiles.mockResolvedValue({
      profiles: [
        { name: "writer", is_default: false },
        { name: "analyst", is_default: false },
      ],
    });
    await mount();
    await act(async () => {});
    await act(async () => current.reorderTab("analyst", "writer"));

    expect(current.tabs.map((tab) => tab.profile)).toEqual(["", "analyst", "writer"]);
    expect(setAgentTabs).toHaveBeenLastCalledWith({
      revision: 3,
      order: ["", "analyst", "writer"],
      hidden: [],
    });
  });
});

// @vitest-environment jsdom

import { act, useEffect } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useAgentTabs, type UseAgentTabsReturn } from "./useAgentTabs";

const getProfiles = vi.fn();
const updateProfileDisplayName = vi.fn();

vi.mock("@/lib/api", () => ({
  api: {
    getProfiles: () => getProfiles(),
    updateProfileDisplayName: (name: string, displayName: string) =>
      updateProfileDisplayName(name, displayName),
  },
}));

let container: HTMLDivElement;
let root: Root;
let current: UseAgentTabsReturn;

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
    expect(
      JSON.parse(window.localStorage.getItem("korra.agentTabs.order")!),
    ).toEqual(["", "writer", "analyst", "newcomer"]);
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
    expect(
      JSON.parse(window.localStorage.getItem("korra.agentTabs.hidden")!),
    ).toEqual(["writer"]);

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
    expect(
      JSON.parse(window.localStorage.getItem("korra.agentTabs.order")!),
    ).toEqual(["", "keep"]);
    expect(
      JSON.parse(window.localStorage.getItem("korra.agentTabs.hidden")!),
    ).toEqual([]);
  });
});

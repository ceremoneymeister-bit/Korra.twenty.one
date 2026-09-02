// @vitest-environment jsdom

import { act, useEffect } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useAgentTabs, type UseAgentTabsReturn } from "./useAgentTabs";

const getProfiles = vi.fn();

vi.mock("@/lib/api", () => ({
  api: {
    getProfiles: () => getProfiles(),
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
  getProfiles.mockReset();
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
});

// @vitest-environment jsdom

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const apiMocks = vi.hoisted(() => ({
  getDashboardView: vi.fn(),
  setDashboardView: vi.fn(),
}));

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return { ...actual, api: { ...actual.api, ...apiMocks } };
});

import { ApiError, type DashboardViewPreference } from "@/lib/api";
import {
  $agentsView,
  chooseAgentsMobileMode,
  initialAgentsView,
  loadAgentsView,
  resetAgentsViewForTests,
  togglePinnedAgent,
  viewCacheKey,
} from "@/lib/agents-view";

function pref(extra: Partial<DashboardViewPreference> = {}): DashboardViewPreference {
  return { version: 1, revision: 3, agents_mobile: "tabs", pinned: [], scope: "0123456789abcdef", ...extra };
}

async function settle() {
  for (let index = 0; index < 8; index += 1) await Promise.resolve();
  await new Promise((resolve) => setTimeout(resolve, 0));
}

beforeEach(() => {
  localStorage.clear();
  delete window.__KORRA_VIEW_PREF__;
  apiMocks.getDashboardView.mockReset();
  apiMocks.setDashboardView.mockReset();
  resetAgentsViewForTests();
});

afterEach(() => {
  delete window.__KORRA_VIEW_PREF__;
});

describe("личный вид экрана агентов", () => {
  it("первый кадр берёт выбор из страницы, без неё — из кэша, иначе вкладки", () => {
    expect(initialAgentsView()).toMatchObject({ mode: "tabs", revision: null });

    localStorage.setItem(viewCacheKey(), JSON.stringify(pref({ agents_mobile: "list", revision: 9 })));
    // Кэш — только чтобы не мелькнул чужой вид; ревизию он не подтверждает.
    expect(initialAgentsView()).toMatchObject({ mode: "list", revision: null });

    window.__KORRA_VIEW_PREF__ = pref({ agents_mobile: "tabs", revision: 12, pinned: ["default", "designer"] });
    expect(initialAgentsView()).toMatchObject({ mode: "tabs", revision: 12, pinned: ["", "designer"] });

    // Мусор в странице или кэше не принимается.
    window.__KORRA_VIEW_PREF__ = { version: 1, revision: 1, agents_mobile: "carousel", pinned: [], scope: "x" };
    localStorage.setItem(viewCacheKey(), "{broken");
    expect(initialAgentsView()).toMatchObject({ mode: "tabs", revision: null });
  });

  it("выбор меняет экран сразу и уходит на сервер с его ревизией", async () => {
    apiMocks.getDashboardView.mockResolvedValue(pref());
    apiMocks.setDashboardView.mockImplementation(async (body) => pref({ ...body, revision: 4 }));
    await loadAgentsView();
    expect($agentsView.get().revision).toBe(3);

    chooseAgentsMobileMode("list");
    expect($agentsView.get()).toMatchObject({ mode: "list", status: "saving" });
    await settle();

    expect(apiMocks.setDashboardView).toHaveBeenCalledWith({ revision: 3, agents_mobile: "list", pinned: [] });
    expect($agentsView.get()).toMatchObject({ mode: "list", revision: 4, status: "saved" });
    // Следующий заход покажет список без мелькания вкладок.
    expect(JSON.parse(localStorage.getItem(viewCacheKey()) ?? "null")).toMatchObject({ agents_mobile: "list", revision: 4 });
  });

  it("поздний ответ на прежнюю запись не отменяет последнее нажатие", async () => {
    apiMocks.getDashboardView.mockResolvedValue(pref());
    let answerFirst: (value: DashboardViewPreference) => void = () => {};
    apiMocks.setDashboardView
      .mockImplementationOnce(() => new Promise((resolve) => { answerFirst = resolve; }))
      .mockImplementationOnce(async (body) => pref({ ...body, revision: 5 }));
    await loadAgentsView();

    chooseAgentsMobileMode("list");
    await Promise.resolve();
    chooseAgentsMobileMode("tabs");
    answerFirst(pref({ agents_mobile: "list", revision: 4 }));
    await settle();

    expect(apiMocks.setDashboardView).toHaveBeenLastCalledWith({ revision: 4, agents_mobile: "tabs", pinned: [] });
    expect($agentsView.get()).toMatchObject({ mode: "tabs", revision: 5, status: "saved" });
  });

  it("закреплённый главный агент уходит на сервер как default", async () => {
    apiMocks.getDashboardView.mockResolvedValue(pref());
    apiMocks.setDashboardView.mockImplementation(async (body) => pref({ ...body, revision: 4 }));
    await loadAgentsView();
    togglePinnedAgent("");
    await settle();
    expect(apiMocks.setDashboardView).toHaveBeenCalledWith({ revision: 3, agents_mobile: "tabs", pinned: ["default"] });
    expect($agentsView.get().pinned).toEqual([""]);
  });

  it("другое устройство успело раньше — показан его выбор и сказано словами", async () => {
    apiMocks.getDashboardView.mockResolvedValue(pref());
    const winner = pref({ revision: 5, agents_mobile: "tabs", pinned: ["lawyer"] });
    apiMocks.setDashboardView.mockRejectedValue(
      new ApiError(409, "409: conflict", { detail: "Вид уже изменили", preference: winner }),
    );
    await loadAgentsView();
    chooseAgentsMobileMode("list");
    await settle();

    expect($agentsView.get()).toMatchObject({ mode: "tabs", revision: 5, pinned: ["lawyer"], status: "conflict" });
    expect($agentsView.get().message).toContain("другом устройстве");
  });

  it("панель не отвечает — выбор действует здесь, и об этом сказано", async () => {
    apiMocks.getDashboardView.mockRejectedValue(new Error("offline"));
    chooseAgentsMobileMode("list");
    await settle();
    expect(apiMocks.setDashboardView).not.toHaveBeenCalled();
    expect($agentsView.get()).toMatchObject({ mode: "list", status: "error" });
    expect($agentsView.get().message).toContain("не сохранён");
  });
});

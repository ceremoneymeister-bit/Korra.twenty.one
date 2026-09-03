// @vitest-environment jsdom

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter, useNavigate } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ProfileScopeChip } from "@/components/ProfileScopeChip";
import { ProfileProvider } from "@/contexts/ProfileProvider";
import { useProfileScope } from "@/contexts/useProfileScope";
import type { ProfileInfo } from "@/lib/api";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT =
  true;

const apiMocks = vi.hoisted(() => ({
  getActiveProfile: vi.fn(),
  getProfiles: vi.fn(),
  setManagementProfile: vi.fn(),
}));

vi.mock("@/lib/api", () => ({
  api: {
    getActiveProfile: apiMocks.getActiveProfile,
    getProfiles: apiMocks.getProfiles,
  },
  setManagementProfile: apiMocks.setManagementProfile,
}));

vi.mock("@/i18n", () => ({
  useI18n: () => ({
    t: { app: { profileScopeLabel: "{name}" } },
  }),
}));

const profiles = [
  { name: "default", display_name: "Основной" },
  { name: "research", display_name: "Исследователь" },
  { name: "writer", display_name: "" },
] as ProfileInfo[];

const profileScopeStorageKey = (route: string) =>
  `korra.profileScope.${route}`;

let container: HTMLDivElement;
let root: Root;

async function render(ui: React.ReactNode) {
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  await act(async () => root.render(ui));
  await act(async () => {
    await Promise.resolve();
  });
}

function click(element: Element | null) {
  if (!element) throw new Error("Expected element to exist");
  element.dispatchEvent(new MouseEvent("click", { bubbles: true }));
}

function ScopeHarness() {
  const navigate = useNavigate();
  const { profile, currentProfile } = useProfileScope();
  return (
    <>
      <span data-testid="scope">{profile || currentProfile}</span>
      <button type="button" onClick={() => navigate("/config")}>Конфигурация</button>
      <button type="button" onClick={() => navigate("/env")}>Ключи</button>
      <button type="button" onClick={() => navigate("/files")}>Файлы</button>
    </>
  );
}

beforeEach(() => {
  localStorage.clear();
  apiMocks.getProfiles.mockResolvedValue({ profiles });
  apiMocks.getActiveProfile.mockResolvedValue({
    active: "default",
    current: "default",
  });
  apiMocks.setManagementProfile.mockClear();
  Object.defineProperty(window, "matchMedia", {
    configurable: true,
    value: vi.fn(() => ({ matches: false })),
  });
  Element.prototype.scrollIntoView = vi.fn();
});

afterEach(async () => {
  await act(async () => root?.unmount());
  container?.remove();
});

describe("ProfileScopeChip", () => {
  it("shows profile display names and stores a selection for its route", async () => {
    await render(
      <MemoryRouter initialEntries={["/config"]}>
        <ProfileProvider>
          <ProfileScopeChip />
        </ProfileProvider>
      </MemoryRouter>,
    );

    const trigger = container.querySelector('[role="combobox"]');
    expect(trigger?.textContent).toContain("Основной");

    await act(async () => click(trigger));
    const options = [...document.body.querySelectorAll('[role="option"]')];
    expect(options.map((option) => option.textContent)).toEqual([
      "Основной",
      "Исследователь",
      "writer",
    ]);

    await act(async () => {
      click(options[1]);
    });

    expect(localStorage.getItem(profileScopeStorageKey("/config"))).toBe(
      "research",
    );
    expect(apiMocks.setManagementProfile).toHaveBeenLastCalledWith("research");
  });

  it("applies each section's saved profile and clears scope elsewhere", async () => {
    localStorage.setItem(profileScopeStorageKey("/config"), "research");
    localStorage.setItem(profileScopeStorageKey("/env"), "writer");

    await render(
      <MemoryRouter initialEntries={["/config"]}>
        <ProfileProvider>
          <ScopeHarness />
        </ProfileProvider>
      </MemoryRouter>,
    );

    expect(container.querySelector('[data-testid="scope"]')?.textContent).toBe(
      "research",
    );
    expect(apiMocks.setManagementProfile).toHaveBeenLastCalledWith("research");

    await act(async () => click([...container.querySelectorAll("button")][1]));
    expect(container.querySelector('[data-testid="scope"]')?.textContent).toBe(
      "writer",
    );
    expect(apiMocks.setManagementProfile).toHaveBeenLastCalledWith("writer");

    await act(async () => click([...container.querySelectorAll("button")][2]));
    expect(container.querySelector('[data-testid="scope"]')?.textContent).toBe(
      "default",
    );
    expect(apiMocks.setManagementProfile).toHaveBeenLastCalledWith("");
  });

  it("stays hidden when only one profile exists", async () => {
    apiMocks.getProfiles.mockResolvedValue({ profiles: [profiles[0]] });

    await render(
      <MemoryRouter initialEntries={["/skills"]}>
        <ProfileProvider>
          <ProfileScopeChip />
        </ProfileProvider>
      </MemoryRouter>,
    );

    expect(container.querySelector('[role="combobox"]')).toBeNull();
  });
});

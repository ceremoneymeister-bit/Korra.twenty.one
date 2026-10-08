// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router";
import { beforeEach, afterEach, expect, it, vi } from "vitest";
const mocks = vi.hoisted(() => ({ allowed: false, pin: false, getSkills: vi.fn(), getToolsets: vi.fn(), toggleSkill: vi.fn(), setSkillAutoLoad: vi.fn(),
  setEnd: vi.fn(), setAfterTitle: vi.fn() }));
vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return { ...actual, api: { ...actual.api, getSkills: mocks.getSkills, getToolsets: mocks.getToolsets, toggleSkill: mocks.toggleSkill, setSkillAutoLoad: mocks.setSkillAutoLoad } };
});
vi.mock("@/hooks/useCabinetSession", () => ({ useCabinetSession: () => ({ canManageSkills: mocks.allowed, canPinSkills: mocks.pin,
  canBrowseSkillsHub: mocks.allowed, canConfigureToolsets: mocks.allowed }) }));
vi.mock("@/contexts/usePageHeader", () => ({ usePageHeader: () => ({ setEnd: mocks.setEnd, setAfterTitle: mocks.setAfterTitle }) }));
vi.mock("@/plugins", () => ({ PluginSlot: () => null }));
vi.mock("@/components/KorraLoader", () => ({ KorraLoader: () => <span>Загрузка</span> }));
import SkillsPage from "./SkillsPage";
let host: HTMLDivElement;
let root: Root;
beforeEach(() => {
  vi.clearAllMocks();
  mocks.allowed = false;
  mocks.pin = false;
  mocks.getSkills.mockResolvedValue([{ name: "visual-design", description: "Визуальный дизайн", enabled: true }]);
  mocks.getToolsets.mockResolvedValue([{ name: "image_gen", label: "Генерация", description: "Изображения", enabled: true, configured: false, tools: ["image_generate"] }]);
  host = document.createElement("div"); document.body.append(host); root = createRoot(host);
});
afterEach(async () => { await act(async () => root.unmount()); host.remove(); });
async function render() { await act(async () => root.render(<MemoryRouter><SkillsPage /></MemoryRouter>)); }
it("shows installed skills but does not offer blocked editing or toggles", async () => {
  await render();
  expect(host.textContent).toContain("visual-design");
  expect(host.textContent).toContain("администратору установки");
  const toggle = host.querySelector<HTMLButtonElement>('[role="switch"]')!;
  expect(toggle.disabled).toBe(true);
  expect(host.querySelector('button[title]')).toBeNull();
  await act(async () => toggle.click());
  expect(mocks.toggleSkill).not.toHaveBeenCalled();
});
it("keeps editing and working toggles when the cabinet permits them", async () => {
  mocks.allowed = true;
  await render();
  const toggle = host.querySelector<HTMLButtonElement>('[role="switch"]')!;
  expect(toggle.disabled).toBe(false);
  expect(host.querySelector('button[title]')).not.toBeNull();
  await act(async () => toggle.click());
  expect(mocks.toggleSkill).toHaveBeenCalledWith("visual-design", false, undefined);
});
it("pins a skill to every chat through the auto-load switch", async () => {
  mocks.allowed = true;
  mocks.setSkillAutoLoad.mockResolvedValue({ ok: true, name: "visual-design", auto_load: true });
  await render();
  const pin = host.querySelector<HTMLButtonElement>('[aria-label="visual-design: в каждом чате"]')!;
  expect(pin.getAttribute("aria-checked")).toBe("false");
  await act(async () => pin.click());
  expect(mocks.setSkillAutoLoad).toHaveBeenCalledWith("visual-design", true, undefined);
  expect(host.querySelector('[aria-label="visual-design: в каждом чате"]')!.getAttribute("aria-checked")).toBe("true");
});
it("does not offer the auto-load switch without permission to manage skills", async () => {
  await render();
  expect(host.querySelector('[aria-label="visual-design: в каждом чате"]')).toBeNull();
});
it("lets a client pin skills to every chat while editing, toggling and creating stay hidden", async () => {
  mocks.pin = true;
  mocks.setSkillAutoLoad.mockResolvedValue({ ok: true, name: "visual-design", auto_load: true });
  await render();
  const enable = host.querySelector<HTMLButtonElement>('[aria-label="visual-design: включён"]')!;
  expect(enable.disabled).toBe(true);
  expect(host.querySelector('button[title="Edit SKILL.md"]')).toBeNull();
  expect(host.textContent).not.toContain("New skill");
  expect(host.textContent).toContain("закрепить в каждом чате");
  const pin = host.querySelector<HTMLButtonElement>('[aria-label="visual-design: в каждом чате"]')!;
  expect(pin.disabled).toBe(false);
  await act(async () => pin.click());
  expect(mocks.setSkillAutoLoad).toHaveBeenCalledWith("visual-design", true, undefined);
  expect(host.querySelector('[aria-label="visual-design: в каждом чате"]')!.getAttribute("aria-checked")).toBe("true");
  await act(async () => enable.click());
  expect(mocks.toggleSkill).not.toHaveBeenCalled();
});

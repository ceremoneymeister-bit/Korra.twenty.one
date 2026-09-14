// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router";
import { beforeEach, afterEach, expect, it, vi } from "vitest";
const mocks = vi.hoisted(() => ({ allowed: false, getSkills: vi.fn(), getToolsets: vi.fn(), toggleSkill: vi.fn(),
  setEnd: vi.fn(), setAfterTitle: vi.fn() }));
vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return { ...actual, api: { ...actual.api, getSkills: mocks.getSkills, getToolsets: mocks.getToolsets, toggleSkill: mocks.toggleSkill } };
});
vi.mock("@/hooks/useCabinetSession", () => ({ useCabinetSession: () => ({ canManageSkills: mocks.allowed,
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

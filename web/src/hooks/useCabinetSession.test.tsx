// @vitest-environment jsdom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, expect, it, vi } from "vitest";
const state = vi.hoisted(() => ({ base: "/c/probe", fetch: vi.fn() }));
vi.mock("@/lib/api", () => ({ get HERMES_BASE_PATH() { return state.base; }, fetchJSON: state.fetch }));
import { useCabinetSession } from "./useCabinetSession";

afterEach(() => { state.base = "/c/probe"; vi.clearAllMocks(); });
async function probe(response: unknown) {
  state.fetch.mockResolvedValue(response);
  const host = document.createElement("div");
  const root = createRoot(host);
  function Probe() { return <output>{JSON.stringify(useCabinetSession())}</output>; }
  await act(async () => root.render(<Probe />));
  const result = JSON.parse(host.textContent!);
  await act(async () => root.unmount());
  return result;
}

it("fails closed on missing or non-boolean capabilities even when mode says admin", async () => {
  const result = await probe({ kind: "cabinet", mode: "admin", capabilities: { skills_manage: "true", files_manage: 1 } });
  expect(result.restrictedFiles).toBe(true);
  expect(result.canManageSkills).toBe(false);
  expect(result.canConfigureToolsets).toBe(false);
});
it("uses declared per-surface capabilities independently of the role label", async () => {
  const result = await probe({ kind: "cabinet", mode: "client", capabilities: { skills_manage: true, toolsets_config: true } });
  expect(result.canManageSkills).toBe(true);
  expect(result.canConfigureToolsets).toBe(true);
  expect(result.restrictedFiles).toBe(true);
});
it("keeps standalone controls and does not probe a nonexistent cabinet", async () => {
  state.base = "";
  const result = await probe({});
  expect(result.canManageSkills).toBe(true);
  expect(result.restrictedFiles).toBe(false);
  expect(state.fetch).not.toHaveBeenCalled();
});

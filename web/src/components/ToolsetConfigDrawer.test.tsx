// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import { ToolsetConfigDrawer } from "./ToolsetConfigDrawer";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const api = vi.hoisted(() => ({
  getToolsetConfig: vi.fn(), selectToolsetProvider: vi.fn(), toggleToolset: vi.fn(),
  saveToolsetEnv: vi.fn(), runToolsetPostSetup: vi.fn(), getActionStatus: vi.fn(),
}));
vi.mock("@/lib/api", () => ({ api }));

const provider = (over: Record<string, unknown>) => ({
  name: "Local Browser", badge: "", tag: "", env_vars: [], post_setup: "agent_browser",
  requires_nous_auth: false, is_active: false, status: "ready", ...over,
});
const CONFIG = {
  name: "browser", has_category: true, active_provider: "Local Browser",
  providers: [
    provider({ name: "Local Browser", is_active: true }),
    provider({ name: "Camofox", post_setup: "camofox" }),
    provider({ name: "Lightpanda", post_setup: "lightpanda", status: "needs_setup" }),
    provider({ name: "Browserbase", post_setup: "browserbase", status: "needs_keys",
      env_vars: [{ key: "BROWSERBASE_API_KEY", prompt: "key", url: null, default: null, is_set: false }] }),
    provider({ name: "Nous Subscription (Browser Use cloud)", requires_nous_auth: true, status: "needs_auth" }),
  ],
};
const TOOLSET = { name: "browser", label: "Автоматизация браузера", description: "", enabled: true, configured: true, tools: [], platform: "cli", platform_label: "CLI" };

let root: Root;
let host: HTMLDivElement;
beforeEach(() => {
  for (const method of Object.values(api)) method.mockReset();
  api.getToolsetConfig.mockResolvedValue(CONFIG);
  api.selectToolsetProvider.mockResolvedValue({ ok: true });
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});
afterEach(async () => {
  await act(async () => root.unmount());
  host.remove();
  document.body.innerHTML = "";
});
async function open(browserChoiceOnly: boolean) {
  await act(async () => root.render(
    <ToolsetConfigDrawer toolset={TOOLSET as never} profile="p1" browserChoiceOnly={browserChoiceOnly} onClose={() => {}} onChanged={() => {}} />,
  ));
}
const text = () => document.body.textContent ?? "";
const buttons = (label: string) => [...document.body.querySelectorAll("button")].filter((b) => b.textContent?.trim() === label);

it("browser-choice mode offers only ready browsers with no keys, setup, or on/off switch (K21-370)", async () => {
  await open(true);
  expect(text()).toContain("Local Browser");
  expect(text()).toContain("Camofox");
  expect(text()).not.toContain("Lightpanda");
  expect(text()).not.toContain("Browserbase");
  expect(text()).not.toContain("Nous Subscription");
  expect(document.body.querySelector("input")).toBeNull();
  expect(document.body.querySelector('[role="switch"]')).toBeNull();
  expect(buttons("Сохранить ключи")).toHaveLength(0);
  expect(text()).not.toContain("agent_browser");
  expect(text()).not.toContain("camofox)");
  await act(async () => buttons("Выбрать")[0].click());
  expect(api.selectToolsetProvider).toHaveBeenCalledWith("browser", "Camofox", "p1");
  expect(api.saveToolsetEnv).not.toHaveBeenCalled();
  expect(api.runToolsetPostSetup).not.toHaveBeenCalled();
  expect(api.toggleToolset).not.toHaveBeenCalled();
});
it("full mode keeps keys, setup and the switch for the administrator", async () => {
  await open(false);
  expect(text()).toContain("Lightpanda");
  expect(text()).toContain("Browserbase");
  expect(document.body.querySelector('input[type="password"]')).not.toBeNull();
  expect(document.body.querySelector('[role="switch"]')).not.toBeNull();
  expect(text()).toContain("agent_browser");
});

// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { VoiceForm } from "./AgentVoicePage";
const api = vi.hoisted(() => ({ get: vi.fn(), save: vi.fn(), voices: vi.fn(), speak: vi.fn(), catalog: vi.fn() }));
vi.mock("@/lib/agent-voice", async (original) => ({ ...await original<typeof import("@/lib/agent-voice")>(), agentVoiceApi: api }));
const settings = { enabled: false, provider: "elevenlabs", voice: "warm", model: "eleven_multilingual_v2", base_url: "", speed: 1, web_mode: "manual", telegram_mode: "off", has_key: false };
let host: HTMLDivElement, root: Root;
beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  host = document.createElement("div"); document.body.append(host); root = createRoot(host);
  for (const fn of Object.values(api)) fn.mockReset();
  api.get.mockResolvedValue(settings);
  api.catalog.mockResolvedValue({ voices: [{ id: "navigator", name: "Навигатор", description: "Разговорный голос", model: "fish-audio/s2.1-pro", default_speed: 1.05, sample_url: "/api/voices/navigator/sample" }] });
  api.save.mockImplementation(async (_profile, value) => ({ ...value, has_key: true }));
  api.speak.mockResolvedValue({ clips: ["data:audio/mpeg;base64,c2FtcGxl"] });
});
afterEach(async () => { await act(async () => root.unmount()); host.remove(); });

it("показывает образец встроенного голоса до ввода ключа", async () => {
  api.get.mockResolvedValue({ ...settings, provider: "openrouter_fish", voice: "navigator", model: "fish-audio/s2.1-pro", speed: 1.05 });
  await act(async () => root.render(<MemoryRouter><VoiceForm profile="navigator" name="Навигатор" /></MemoryRouter>));
  expect(host.textContent).toContain("OpenRouter API-ключ");
  expect(host.textContent).toContain("Образец можно послушать без ключа");
  expect((host.querySelector('audio[aria-label="Образец голоса Навигатор"]') as HTMLAudioElement).getAttribute("src")).toBe("/api/voices/navigator/sample");
  expect(api.speak).not.toHaveBeenCalled();
});
const button = (name: string) => Array.from(host.querySelectorAll("button")).find(item => item.textContent?.includes(name))!;
it("сохраняет ключ только указанному агенту; проба доступна после сохранения", async () => {
  await act(async () => root.render(<MemoryRouter><VoiceForm profile="psychologist" name="Психолог" /></MemoryRouter>));
  expect(api.get).toHaveBeenCalledWith("psychologist");
  expect(button("Проба голоса").disabled).toBe(true);
  await act(async () => (host.querySelector('input[type="checkbox"]') as HTMLInputElement).click());
  await act(async () => {
    const input = host.querySelector("#voice-key") as HTMLInputElement;
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(input, "owner-key");
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
  expect(button("Проба голоса").disabled).toBe(true);
  await act(async () => button("Сохранить").click());
  expect(api.save).toHaveBeenCalledWith("psychologist", expect.objectContaining({ enabled: true }), "owner-key", false);
  expect((host.querySelector("#voice-key") as HTMLInputElement).value).toBe("");
  expect(api.speak).not.toHaveBeenCalled();
  await act(async () => button("Проба голоса").click());
  expect(api.speak).toHaveBeenCalledWith("psychologist", expect.any(String));
  expect(host.querySelector("audio")).not.toBeNull();
});

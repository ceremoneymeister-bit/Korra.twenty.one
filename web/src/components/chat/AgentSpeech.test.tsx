// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AgentSpeech } from "./AgentSpeech";
import type { AgentVoiceSettings } from "@/lib/agent-voice";
const speak = vi.hoisted(() => vi.fn());
vi.mock("@/lib/agent-voice", async (original) => ({ ...await original<typeof import("@/lib/agent-voice")>(), agentVoiceApi: { speak } }));
const settings: AgentVoiceSettings = { enabled: true, provider: "compatible", voice: "warm", model: "tts", base_url: "http://localhost/v1", speed: 1, web_mode: "auto", telegram_mode: "off", has_key: false };
let host: HTMLDivElement, root: Root;
beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  host = document.createElement("div"); document.body.append(host); root = createRoot(host);
  speak.mockReset().mockResolvedValue({ clips: ["data:audio/mpeg;base64,c2FtcGxl"] });
  vi.spyOn(HTMLMediaElement.prototype, "play").mockResolvedValue();
  vi.spyOn(HTMLMediaElement.prototype, "pause").mockImplementation(() => {});
});
afterEach(async () => { await act(async () => root.unmount()); host.remove(); vi.restoreAllMocks(); });
async function render(streaming = false, active = true, enabled = true) {
  await act(async () => { root.render(<AgentSpeech profile="psychologist" text="Я вас слушаю" streaming={streaming} active={active} settings={{ ...settings, enabled }} />); });
}
describe("Голос ответа", () => {
  it("не озвучивает историю при открытии и не вызывает сервис при выключении", async () => {
    await render(); expect(speak).not.toHaveBeenCalled();
    await render(true, true, false); await render(false, true, false);
    expect(speak).not.toHaveBeenCalled(); expect(host.querySelector("button")).toBeNull();
  });
  it("озвучивает новый ответ один раз для выбранного профиля", async () => {
    await render(true); await render(false); await render(false);
    expect(speak).toHaveBeenCalledExactlyOnceWith("psychologist", "Я вас слушаю");
    expect(host.querySelector("audio")?.getAttribute("src")).toContain("data:audio/");
    await render(false, false);
    expect(HTMLMediaElement.prototype.pause).toHaveBeenCalled();
  });
  it("не озвучивает ответ скрытой вкладки при возврате к ней", async () => {
    await render(true, false); await render(false, false); await render(false, true);
    expect(speak).not.toHaveBeenCalled();
  });
  it("блокирует повторное нажатие во время генерации, даёт повторить после ошибки", async () => {
    let reject!: (reason: Error) => void;
    speak.mockReturnValueOnce(new Promise((_resolve, failed) => { reject = failed; }));
    await render();
    await act(async () => { host.querySelector("button")!.click(); host.querySelector("button")!.click(); });
    expect(speak).toHaveBeenCalledTimes(1);
    await act(async () => reject(new Error("offline")));
    expect(host.textContent).toContain("текст ответа сохранён");
    await act(async () => host.querySelector("button")!.click());
    expect(speak).toHaveBeenCalledTimes(2);
  });
});

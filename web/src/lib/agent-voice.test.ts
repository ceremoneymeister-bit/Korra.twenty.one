// @vitest-environment node
import { expect, it, vi } from "vitest";
import { agentVoiceApi, releaseSpeechClips } from "./agent-voice";
const fetchJSON = vi.hoisted(() => vi.fn());
vi.mock("@/lib/api", () => ({ fetchJSON }));

it("returns playable blob URLs allowed by cabinet CSP, then releases their bytes", async () => {
  fetchJSON.mockResolvedValue({ clips: ["data:audio/mpeg;base64,SUQzZml4dHVyZQ=="] });
  const result = await agentVoiceApi.speak("listener", "Привет");
  expect(fetchJSON).toHaveBeenCalledWith("/api/profiles/listener/voice/speak", expect.any(Object));
  expect(result.clips[0]).toMatch(/^blob:/);
  const response = await fetch(result.clips[0]);
  expect(response.headers.get("content-type")).toBe("audio/mpeg");
  expect(await response.text()).toBe("ID3fixture");
  releaseSpeechClips(result.clips);
  await expect(fetch(result.clips[0])).rejects.toThrow();
});

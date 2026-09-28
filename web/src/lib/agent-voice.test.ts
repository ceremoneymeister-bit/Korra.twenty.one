// @vitest-environment node
import { expect, it, vi } from "vitest";
import { agentVoiceApi, releaseSpeechClips } from "./agent-voice";
const fetchJSON = vi.hoisted(() => vi.fn());
const authedFetch = vi.hoisted(() => vi.fn());
vi.mock("@/lib/api", () => ({ fetchJSON, authedFetch }));

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

it("образец голоса идёт авторизованным запросом панели и становится Blob URL (ревью Astra §2.4)", async () => {
  authedFetch.mockResolvedValueOnce(new Response(new TextEncoder().encode("ID3sample"), { headers: { "content-type": "audio/mpeg" } }));
  const url = await agentVoiceApi.sample("/api/voices/boss/sample");
  // authedFetch сам ставит base path кабинета и заголовок сессии.
  expect(authedFetch).toHaveBeenCalledWith("/api/voices/boss/sample", undefined);
  expect(url).toMatch(/^blob:/);
  expect(await (await fetch(url)).text()).toBe("ID3sample");
  URL.revokeObjectURL(url);
  authedFetch.mockResolvedValueOnce(new Response("", { status: 404 }));
  await expect(agentVoiceApi.sample("/api/voices/boss/sample")).rejects.toThrow("404");
});

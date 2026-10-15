// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { transcribeRecording } from "./api";

const DEADLINE_MS = 15 * 60_000;
const recording = () => new Blob(["звук"], { type: "audio/webm" });

class Reader {
  static latest: Reader;
  result = "data:audio/webm;base64,AAAA";
  onload: (() => void) | null = null;
  readAsDataURL() { Reader.latest = this; this.onload?.(); }
}

beforeEach(() => {
  vi.useFakeTimers();
  vi.stubGlobal("FileReader", Reader);
});
afterEach(() => {
  vi.restoreAllMocks();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

it.each(["upload", "json", "error body"])("зависшее %s ограничено целиком, соединение отменено", async stage => {
  const forever = () => new Promise<never>(() => {});
  let signal: AbortSignal | undefined;
  vi.stubGlobal("fetch", vi.fn(async (_url, init) => {
    signal = init.signal;
    if (stage === "upload") return forever();
    return stage === "json"
      ? { ok: true, json: forever }
      : { ok: false, text: forever };
  }));
  const result = transcribeRecording(recording()).catch(error => error);
  await vi.advanceTimersByTimeAsync(DEADLINE_MS);
  expect(signal?.aborted).toBe(true);
  expect(await result).toBeInstanceOf(Error);
  expect((await result).message).toContain("15 минут");
  expect(vi.getTimerCount()).toBe(0);
});

it("даёт штатному долгому распознаванию закончиться и убирает таймер", async () => {
  let respond!: (response: Response) => void;
  let signal: AbortSignal | undefined;
  vi.stubGlobal("fetch", vi.fn((_url, init) => {
    signal = init.signal;
    return new Promise<Response>(resolve => { respond = resolve; });
  }));
  const result = transcribeRecording(recording());
  await vi.advanceTimersByTimeAsync(12 * 60_000);
  expect(signal?.aborted).toBe(false);
  respond(Response.json({ transcript: "длинный ответ", audio_seconds: 600 }));
  await expect(result).resolves.toEqual({ text: "длинный ответ", audioSeconds: 600 });
  expect(vi.getTimerCount()).toBe(0);
  await vi.advanceTimersByTimeAsync(DEADLINE_MS);
  expect(signal?.aborted).toBe(false);
});

it("сетевая ошибка освобождает таймер", async () => {
  vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));
  await expect(transcribeRecording(recording())).rejects.toThrow("Failed to fetch");
  expect(vi.getTimerCount()).toBe(0);
});

it("позднее чтение аудио после таймаута не отправляет зависший запрос", async () => {
  vi.spyOn(Reader.prototype, "readAsDataURL").mockImplementation(function (this: Reader) {
    Reader.latest = this;
  });
  const fetcher = vi.fn();
  vi.stubGlobal("fetch", fetcher);
  const result = transcribeRecording(recording()).catch(error => error);
  await vi.advanceTimersByTimeAsync(DEADLINE_MS);
  expect(await result).toBeInstanceOf(Error);
  Reader.latest.onload?.();
  await Promise.resolve();
  expect(fetcher).not.toHaveBeenCalled();
  expect(vi.getTimerCount()).toBe(0);
});

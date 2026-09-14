// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { authedFetch } from "@/lib/api";
import {
  UploadError,
  cancelUpload,
  completeUpload,
  createUpload,
  isRetryableStatus,
  uploadPart,
  uploadStatus,
  type UploadManifest,
} from "./upload-session";

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return { ...actual, authedFetch: vi.fn(), withBasePath: (url: string) => url };
});

const fetchMock = vi.mocked(authedFetch);

function reply(status: number, body: unknown = {}): Response {
  return new Response(typeof body === "string" ? body : JSON.stringify(body), { status });
}

const manifest: UploadManifest = {
  upload_id: "0f1e2d3c-4b5a-4968-8778-695a4b3c2d1e",
  origin: "chat",
  files: [{ path: "IMG_2581.jpg", size: 12 }],
};

class FakeXhr {
  static last: FakeXhr | null = null;
  status = 0;
  responseText = "";
  withCredentials = false;
  readonly headers: Record<string, string> = {};
  readonly upload = { onprogress: null as ((event: { loaded: number }) => void) | null };
  onload: (() => void) | null = null;
  onerror: (() => void) | null = null;
  ontimeout: (() => void) | null = null;
  onabort: (() => void) | null = null;
  url = "";
  sent: Blob | null = null;

  constructor() {
    FakeXhr.last = this;
  }
  open(_method: string, url: string) {
    this.url = url;
  }
  setRequestHeader(name: string, value: string) {
    this.headers[name] = value;
  }
  send(body: Blob) {
    this.sent = body;
  }
  abort() {
    this.onabort?.();
  }
  finish(status: number, body: unknown = {}) {
    this.status = status;
    this.responseText = typeof body === "string" ? body : JSON.stringify(body);
    this.onload?.();
  }
}

beforeEach(() => {
  fetchMock.mockReset();
  FakeXhr.last = null;
  vi.stubGlobal("XMLHttpRequest", FakeXhr);
  window.__HERMES_SESSION_TOKEN__ = "tok";
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("классификация ответов", () => {
  it("повторяет только то, что проходит само", () => {
    for (const status of [0, 408, 425, 429, 500, 502, 503, 504]) {
      expect(isRetryableStatus(status, "")).toBe(true);
    }
    // Так кабинет за nginx закрывает поток, не дойдя до панели.
    expect(isRetryableStatus(400, "")).toBe(true);
    expect(isRetryableStatus(400, "Файлы .sh загружать нельзя")).toBe(false);
    for (const status of [403, 404, 410, 411, 413, 507]) {
      expect(isRetryableStatus(status, "Нет места")).toBe(false);
    }
  });

  it("достаёт русский текст, смещение и список недостающих файлов", async () => {
    fetchMock.mockResolvedValueOnce(reply(507, { detail: "На диске контура не хватает места" }));
    await expect(createUpload(manifest)).rejects.toMatchObject({
      status: 507,
      detail: "На диске контура не хватает места",
      retryable: false,
    });

    fetchMock.mockResolvedValueOnce(reply(409, { detail: "Загружены не все файлы", missing: [3, 7] }));
    const failure: unknown = await completeUpload(manifest.upload_id).catch((cause) => cause);
    expect(failure).toBeInstanceOf(UploadError);
    expect((failure as UploadError).missing).toEqual([3, 7]);
  });
});

describe("маршруты сессии", () => {
  it("создаёт сессию, читает статус, завершает и отменяет", async () => {
    // Тело Response читается один раз — на каждый вызов новый объект.
    fetchMock.mockImplementation(async () =>
      reply(201, { upload_id: manifest.upload_id, received: [] }));
    await createUpload(manifest);
    expect(fetchMock.mock.calls[0][0]).toBe("/api/uploads");
    expect(JSON.parse(String(fetchMock.mock.calls[0][1]?.body))).toEqual(manifest);

    await uploadStatus(manifest.upload_id);
    expect(fetchMock.mock.calls[1][0]).toBe(`/api/uploads/${manifest.upload_id}`);

    await completeUpload(manifest.upload_id, [2]);
    expect(fetchMock.mock.calls[2][0]).toBe(`/api/uploads/${manifest.upload_id}/complete`);
    expect(JSON.parse(String(fetchMock.mock.calls[2][1]?.body))).toEqual({ exclude: [2] });

    fetchMock.mockImplementation(async () => new Response(null, { status: 204 }));
    await cancelUpload(manifest.upload_id);
    expect(fetchMock.mock.calls[3][1]?.method).toBe("DELETE");
  });
});

describe("часть через XHR", () => {
  it("шлёт смещение, заголовок сессии и отдаёт прогресс", async () => {
    const chunk = new Blob(["0123456789"]);
    const seen: number[] = [];
    const promise = uploadPart("abc", 3, 4096, chunk, (loaded) => seen.push(loaded));
    const xhr = FakeXhr.last!;
    expect(xhr.url).toBe("/api/uploads/abc/files/3?offset=4096");
    expect(xhr.headers["X-Hermes-Session-Token"]).toBe("tok");
    expect(xhr.withCredentials).toBe(true);
    xhr.upload.onprogress?.({ loaded: 512 });
    xhr.finish(200, { index: 3, bytes: 4106, complete: true });
    await expect(promise).resolves.toEqual({ index: 3, bytes: 4106, complete: true });
    expect(seen).toEqual([512]);
  });

  it("превращает обрыв соединения в повторяемую ошибку, а 409 — в смещение", async () => {
    const dropped = uploadPart("abc", 0, 0, new Blob(["x"]));
    FakeXhr.last!.onerror?.();
    await expect(dropped).rejects.toMatchObject({ status: 0, retryable: true });

    const resync = uploadPart("abc", 0, 0, new Blob(["x"]));
    FakeXhr.last!.finish(409, { detail: "Часть пришла не по порядку", bytes: 8192 });
    await expect(resync).rejects.toMatchObject({ status: 409, bytes: 8192 });
  });

  it("отменяется по сигналу и до отправки, и в полёте", async () => {
    const aborted = new AbortController();
    aborted.abort();
    await expect(uploadPart("abc", 0, 0, new Blob(["x"]), undefined, aborted.signal))
      .rejects.toMatchObject({ name: "AbortError" });

    const controller = new AbortController();
    const flying = uploadPart("abc", 0, 0, new Blob(["x"]), undefined, controller.signal);
    controller.abort();
    await expect(flying).rejects.toMatchObject({ name: "AbortError" });
  });
});

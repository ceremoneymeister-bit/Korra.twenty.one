// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { api, fetchJSON, setManagementProfile, transcribeAudio } from "./api";

const reloadMocks = vi.hoisted(() => ({
  attemptDashboardTokenReloadOnce: vi.fn(() => false),
  clearDashboardTokenReloadAttempt: vi.fn(),
}));

vi.mock("./dashboard-auth-reload", () => ({
  attemptDashboardTokenReloadOnce: reloadMocks.attemptDashboardTokenReloadOnce,
  clearDashboardTokenReloadAttempt: reloadMocks.clearDashboardTokenReloadAttempt,
}));

const SESSION_HEADER = "X-Hermes-Session-Token";

beforeEach(() => {
  reloadMocks.attemptDashboardTokenReloadOnce.mockReset();
  reloadMocks.attemptDashboardTokenReloadOnce.mockReturnValue(false);
  reloadMocks.clearDashboardTokenReloadAttempt.mockReset();

  Object.defineProperty(window, "__HERMES_SESSION_TOKEN__", {
    configurable: true,
    value: "stale-token",
    writable: true,
  });
  Object.defineProperty(window, "__HERMES_AUTH_REQUIRED__", {
    configurable: true,
    value: false,
    writable: true,
  });
});

afterEach(() => {
  setManagementProfile("");
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

function jsonFetchMock(body: unknown = { ok: true }) {
  return vi.fn<typeof fetch>(
    async () =>
      new Response(JSON.stringify(body), {
        headers: { "Content-Type": "application/json" },
        status: 200,
      }),
  );
}

describe("fetchJSON", () => {
  it("tries the one-shot reload path for loopback 401s", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({
        clone: () => ({
          json: async () => ({}),
        }),
        ok: false,
        status: 401,
        statusText: "Unauthorized",
        text: async () => "Unauthorized",
      })),
    );
    reloadMocks.attemptDashboardTokenReloadOnce.mockReturnValue(true);

    const pending = fetchJSON("/api/status");
    await expect(Promise.race([pending, Promise.resolve("pending")])).resolves.toBe(
      "pending",
    );

    expect(reloadMocks.attemptDashboardTokenReloadOnce).toHaveBeenCalledTimes(1);
    expect(reloadMocks.clearDashboardTokenReloadAttempt).not.toHaveBeenCalled();
  });

  it("clears the reload latch after a successful response", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({
        json: async () => ({ ok: true }),
        ok: true,
        status: 200,
      })),
    );

    await expect(fetchJSON("/api/status")).resolves.toEqual({ ok: true });

    expect(reloadMocks.clearDashboardTokenReloadAttempt).toHaveBeenCalledTimes(1);
  });

  it("does not expose English backend errors", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        new Response('{"detail":"database connection failed"}', {
          status: 500,
          headers: { "Content-Type": "application/json" },
        }),
      ),
    );

    await expect(fetchJSON("/api/status")).rejects.toThrow(
      "500: Сервис временно недоступен. Повторите через минуту.",
    );
  });

  it("preserves a Russian backend detail", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        new Response('{"detail":"Сессия уже завершена"}', {
          status: 409,
          headers: { "Content-Type": "application/json" },
        }),
      ),
    );

    await expect(fetchJSON("/api/status")).rejects.toThrow(
      "409: Сессия уже завершена",
    );
  });
});

describe("api.getModelOptions", () => {
  it("requests a live model refresh when asked", async () => {
    vi.stubGlobal("window", {});

    const fetchMock = jsonFetchMock({ providers: [] });
    vi.stubGlobal("fetch", fetchMock);

    await api.getModelOptions({ refresh: true });

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/model/options?refresh=1&include_unconfigured=1",
      expect.objectContaining({ credentials: "include" }),
    );
  });

  it("keeps explicit profile scoping when refreshing", async () => {
    vi.stubGlobal("window", {});

    const fetchMock = jsonFetchMock({ providers: [] });
    vi.stubGlobal("fetch", fetchMock);

    await api.getModelOptions({ profile: "default", refresh: true });

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/model/options?profile=default&refresh=1&include_unconfigured=1",
      expect.objectContaining({ credentials: "include" }),
    );
  });
});

describe("api OAuth helpers", () => {
  it("starts OAuth login in gated mode without requiring an injected session token", async () => {
    vi.stubGlobal("window", { __HERMES_AUTH_REQUIRED__: true });
    const fetchMock = jsonFetchMock({
      flow: "device_code",
      session_id: "oauth-session",
    });
    vi.stubGlobal("fetch", fetchMock);

    await api.startOAuthLogin("openai-codex");

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/providers/oauth/openai-codex/start",
      expect.objectContaining({
        body: "{}",
        credentials: "include",
        method: "POST",
      }),
    );
    const headers = fetchMock.mock.calls[0][1]?.headers as Headers;
    expect(headers.get("Content-Type")).toBe("application/json");
    expect(headers.has(SESSION_HEADER)).toBe(false);
  });

  it("still sends the injected session token for OAuth login in loopback mode", async () => {
    vi.stubGlobal("window", { __HERMES_SESSION_TOKEN__: "loopback-token" });
    const fetchMock = jsonFetchMock({
      flow: "device_code",
      session_id: "oauth-session",
    });
    vi.stubGlobal("fetch", fetchMock);

    await api.startOAuthLogin("openai-codex");

    const headers = fetchMock.mock.calls[0][1]?.headers as Headers;
    expect(headers.get(SESSION_HEADER)).toBe("loopback-token");
  });

  it("runs provider auth mutations in gated mode via cookie auth", async () => {
    vi.stubGlobal("window", { __HERMES_AUTH_REQUIRED__: true });
    const fetchMock = jsonFetchMock({ ok: true });
    vi.stubGlobal("fetch", fetchMock);

    await api.disconnectOAuthProvider("anthropic");
    await api.submitOAuthCode("anthropic", "oauth-session", "code-123");
    await api.cancelOAuthSession("oauth-session");
    await api.revealEnvVar("OPENAI_API_KEY");

    for (const call of fetchMock.mock.calls) {
      const init = call[1] as RequestInit;
      expect(init.credentials).toBe("include");
      expect((init.headers as Headers).has(SESSION_HEADER)).toBe(false);
    }
  });

  it("keeps every OAuth operation on the selected management profile", async () => {
    vi.stubGlobal("window", {});
    const fetchMock = jsonFetchMock({
      flow: "device_code",
      session_id: "oauth-session",
    });
    vi.stubGlobal("fetch", fetchMock);
    setManagementProfile("worker");

    await api.getOAuthProviders();
    await api.disconnectOAuthProvider("anthropic");
    await api.startOAuthLogin("openai-codex");
    await api.submitOAuthCode("anthropic", "oauth-session", "code-123");
    await api.pollOAuthSession("anthropic", "oauth-session");
    await api.cancelOAuthSession("oauth-session");

    expect(fetchMock.mock.calls.map(([url]) => url)).toEqual([
      "/api/providers/oauth?profile=worker",
      "/api/providers/oauth/anthropic?profile=worker",
      "/api/providers/oauth/openai-codex/start?profile=worker",
      "/api/providers/oauth/anthropic/submit?profile=worker",
      "/api/providers/oauth/anthropic/poll/oauth-session?profile=worker",
      "/api/providers/oauth/sessions/oauth-session?profile=worker",
    ]);
  });
});

describe("api Google Workspace helpers", () => {
  it("keeps every connection operation on the selected management profile", async () => {
    vi.stubGlobal("window", {});
    const fetchMock = jsonFetchMock({ status: "revoked", remote_revoked: false });
    vi.stubGlobal("fetch", fetchMock);
    setManagementProfile("writer");

    await api.getGoogleWorkspaceStatus();
    await api.startGoogleWorkspace(["drive"]);
    await api.completeGoogleWorkspace("http://localhost/?state=s&code=c");
    await api.cancelGoogleWorkspace();
    const revokeResult = await api.revokeGoogleWorkspace();
    await api.checkGoogleWorkspaceService("drive");

    expect(fetchMock.mock.calls.map(([url]) => url)).toEqual([
      "/api/google-workspace/status?profile=writer",
      "/api/google-workspace/start?profile=writer",
      "/api/google-workspace/complete?profile=writer",
      "/api/google-workspace/cancel?profile=writer",
      "/api/google-workspace/revoke?profile=writer",
      "/api/google-workspace/check/drive?profile=writer",
    ]);
    expect(revokeResult).toEqual({ status: "revoked", remote_revoked: false });
  });
});

describe("transcribeAudio", () => {
  const recording = () => new Blob(["звук"], { type: "audio/webm;codecs=opus" });

  function transcribeFetchMock(body: unknown, status = 200) {
    return vi.fn<typeof fetch>(
      async () =>
        new Response(JSON.stringify(body), {
          headers: { "Content-Type": "application/json" },
          status,
        }),
    );
  }

  it("шлёт base64 в JSON, а не multipart, и адресует запись профилю", async () => {
    const fetchMock = transcribeFetchMock({ ok: true, transcript: "смета" });
    vi.stubGlobal("fetch", fetchMock);

    await expect(
      transcribeAudio(recording(), "audio/webm;codecs=opus", "raschet"),
    ).resolves.toBe("смета");

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/audio/transcribe?profile=raschet");
    expect(init?.method).toBe("POST");
    const body = JSON.parse(String(init?.body)) as {
      data_url: string;
      mime_type: string;
    };
    expect(body.data_url.startsWith("data:audio/webm")).toBe(true);
    expect(body.data_url).toContain(";base64,");
    expect(body.mime_type).toBe("audio/webm;codecs=opus");
  });

  it("тишину отдаёт пустой строкой, а не отказом", async () => {
    vi.stubGlobal(
      "fetch",
      transcribeFetchMock({ ok: true, transcript: "", provider: "deepgram" }),
    );
    await expect(transcribeAudio(recording())).resolves.toBe("");
  });

  it("ненастроенный STT переводит на человеческий и называет ключ", async () => {
    vi.stubGlobal(
      "fetch",
      transcribeFetchMock(
        {
          detail:
            "No STT provider available. Install faster-whisper for free local " +
            "transcription, set GROQ_API_KEY for free Groq Whisper.",
        },
        400,
      ),
    );

    await expect(transcribeAudio(recording())).rejects.toThrowError(
      "Не получилось распознать речь. Повторите или напишите текстом; если распознавание не настроено, добавьте ключ Deepgram в разделе «Ключи».",
    );
  });

  it("незарегистрированный плагин deepgram читается так же", async () => {
    vi.stubGlobal(
      "fetch",
      transcribeFetchMock(
        {
          detail:
            "stt.provider='deepgram' is set but no built-in, command, or plugin " +
            "provider registered that name.",
        },
        400,
      ),
    );

    await expect(transcribeAudio(recording())).rejects.toThrowError(
      "Не получилось распознать речь. Повторите или напишите текстом; если распознавание не настроено, добавьте ключ Deepgram в разделе «Ключи».",
    );
  });

  it("отказ движка не утекает английским текстом", async () => {
    vi.stubGlobal(
      "fetch",
      transcribeFetchMock({ detail: "Transcription failed: boom" }, 500),
    );

    await expect(transcribeAudio(recording())).rejects.toThrowError(
      "Сервис временно недоступен. Повторите через минуту.",
    );
  });

  it("пустую запись до сервера не тащит", async () => {
    const fetchMock = transcribeFetchMock({ ok: true, transcript: "" });
    vi.stubGlobal("fetch", fetchMock);

    await expect(
      transcribeAudio(new Blob([], { type: "audio/webm" })),
    ).rejects.toThrowError("Запись пустая — микрофон ничего не услышал.");
    expect(fetchMock).not.toHaveBeenCalled();
  });
});

// @vitest-environment jsdom

// Контракт клиента задач против маршрутов движка
// (`korra_cli/web_routers/cron.py`). Раздел «Задачи» уже был написан под
// придуманный owner-контракт: тело с `expected_revision` и
// `POST /api/cron/jobs/{id}/archive`, которого в движке нет. Ни один такой
// вызов не долетал до сети, а `?profile=` не передавался вовсе — задачи
// чужого профиля были недостижимы. Тесты держат вызовы на реальных
// маршрутах.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { api } from "./api";

interface Call {
  url: string;
  method: string;
  body: string | null;
}

let calls: Call[];

function lastCall(): Call {
  expect(calls.length).toBeGreaterThan(0);
  return calls[calls.length - 1];
}

/** Путь запроса без базового префикса панели и без хоста. */
function path(call: Call): string {
  return call.url.replace(/^https?:\/\/[^/]+/, "");
}

beforeEach(() => {
  calls = [];
  vi.stubGlobal(
    "fetch",
    vi.fn((url: string, init?: RequestInit) => {
      calls.push({
        url: String(url),
        method: init?.method ?? "GET",
        body: typeof init?.body === "string" ? init.body : null,
      });
      return Promise.resolve(
        new Response("{}", {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      );
    }),
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("клиент задач говорит на языке движка", () => {
  it("список запрашивается с профилем", async () => {
    await api.getCronJobs("secretary");
    expect(path(lastCall())).toBe("/api/cron/jobs?profile=secretary");
    expect(lastCall().method).toBe("GET");
  });

  it("пауза — POST .../pause?profile= без тела", async () => {
    await api.pauseCronJob("job-1", "secretary");
    const call = lastCall();
    expect(path(call)).toBe("/api/cron/jobs/job-1/pause?profile=secretary");
    expect(call.method).toBe("POST");
    expect(call.body).toBeNull();
  });

  it("снятие с паузы — POST .../resume?profile= без тела и без подтверждения", async () => {
    await api.resumeCronJob("job-1", "secretary");
    const call = lastCall();
    expect(path(call)).toBe("/api/cron/jobs/job-1/resume?profile=secretary");
    expect(call.method).toBe("POST");
    expect(call.body).toBeNull();
  });

  it("ручной запуск — POST .../trigger?profile=", async () => {
    await api.triggerCronJob("job-1", "secretary");
    const call = lastCall();
    expect(path(call)).toBe("/api/cron/jobs/job-1/trigger?profile=secretary");
    expect(call.method).toBe("POST");
  });

  it("удаление — DELETE /api/cron/jobs/{id}?profile=, архива у движка нет", async () => {
    await api.deleteCronJob("job-1", "secretary");
    const call = lastCall();
    expect(path(call)).toBe("/api/cron/jobs/job-1?profile=secretary");
    expect(call.method).toBe("DELETE");
    expect(calls.some((c) => c.url.includes("/archive"))).toBe(false);
  });

  it("правка — PUT с обёрткой updates, как ждёт CronJobUpdate", async () => {
    await api.updateCronJob("job-1", { schedule: "0 9 * * *" }, "secretary");
    const call = lastCall();
    expect(path(call)).toBe("/api/cron/jobs/job-1?profile=secretary");
    expect(call.method).toBe("PUT");
    expect(JSON.parse(call.body ?? "{}")).toEqual({
      updates: { schedule: "0 9 * * *" },
    });
  });

  it("создание — POST /api/cron/jobs?profile=", async () => {
    await api.createCronJob({ prompt: "отчёт", schedule: "30m" }, "secretary");
    const call = lastCall();
    expect(path(call)).toBe("/api/cron/jobs?profile=secretary");
    expect(call.method).toBe("POST");
  });

  it("ни один вызов задач не шлёт expected_revision", async () => {
    await api.pauseCronJob("job-1", "default");
    await api.resumeCronJob("job-1", "default");
    await api.updateCronJob("job-1", { prompt: "x" }, "default");
    await api.deleteCronJob("job-1", "default");
    expect(calls.every((c) => !(c.body ?? "").includes("expected_revision"))).toBe(
      true,
    );
  });

  it("параллельного owner-набора без профиля больше нет", () => {
    const surface = api as unknown as Record<string, unknown>;
    for (const name of [
      "getOwnerCronJobs",
      "createOwnerCronJob",
      "updateOwnerCronJob",
      "pauseOwnerCronJob",
      "resumeOwnerCronJob",
      "archiveOwnerCronJob",
      "getOwnerCronDeliveryTargets",
    ]) {
      expect(surface[name]).toBeUndefined();
    }
  });
});

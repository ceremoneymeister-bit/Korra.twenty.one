// @vitest-environment jsdom
import React, { act } from "react";
import * as router from "react-router";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

let root: Root;
let host: HTMLDivElement;
const boards = vi.fn();
const jobs = vi.fn();
const profiles = vi.fn();

async function renderPage() {
  let Page: React.ComponentType | undefined;
  Object.assign(window, {
    __HERMES_PLUGIN_SDK__: { React, router, fetchJSON: boards, api: { getCronJobs: jobs, getProfiles: profiles } },
    __HERMES_PLUGINS__: { registerSlot: vi.fn(), register: (_name: string, component: React.ComponentType) => { Page = component; } },
  });
  // @ts-expect-error Плагин поставляется как исполняемый браузерный JavaScript.
  await import("../../../plugins/hermes-achievements/dashboard/dist/index.js");
  const Component = Page!;
  await act(async () => { root.render(<router.MemoryRouter><Component /></router.MemoryRouter>); });
}

beforeEach(() => {
  vi.resetModules();
  boards.mockReset().mockResolvedValue({ boards: [] });
  jobs.mockReset().mockResolvedValue([]);
  profiles.mockReset().mockResolvedValue({ profiles: [{ name: "default", is_default: true }] });
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});
afterEach(async () => { await act(async () => root.unmount()); host.remove(); });

describe("Польза от агентов", () => {
  it("показывает первый шаг без выдуманных результатов и ведёт к действию", async () => {
    await renderPage();
    expect(host.textContent).toContain("Работа вашей команды");
    expect(host.querySelector('.kb-benefit-next a')?.getAttribute("href")).toBe("/kanban");
    expect(host.textContent).not.toMatch(/Hermes|Copper|Olympian/);
  });
  it("связывает этапы с реальными карточками, успешными запусками и собственными агентами", async () => {
    boards.mockResolvedValue({ boards: [{ slug: "sales", name: "Продажи", total: 3, counts: { done: 2, review: 1 } }] });
    jobs.mockResolvedValue([
      { enabled: true, last_status: "ok", last_run_at: "2026-09-05" },
      { enabled: true, last_status: "error", last_run_at: "2026-09-05" },
      { enabled: true },
    ]);
    profiles.mockResolvedValue({ profiles: [{ is_default: true }, { name: "sales", is_default: false }] });
    await renderPage();
    expect(host.textContent).not.toContain("Освоено");
    expect(host.querySelector('[data-milestone="routine"] .kb-benefit-metric strong')?.textContent).toBe("1");
    expect(host.querySelector('[data-milestone="team"] .kb-benefit-metric strong')?.textContent).toBe("1");
    expect(host.textContent).toContain("Сейчас нужно ваше внимание");
  });
  it("при ошибке источника сохраняет известные результаты и не подменяет неизвестное нулём", async () => {
    boards.mockRejectedValue(new Error("503: private traceback"));
    profiles.mockResolvedValue({ profiles: [{ name: "sales", is_default: false }] });
    await renderPage();
    expect(host.textContent).toContain("Часть данных сейчас недоступна");
    expect(host.querySelector('[data-milestone="task"]')?.textContent).toContain("Нет данных");
    expect(host.querySelector('[data-milestone="team"]')?.textContent).toContain("Есть данные");
    expect(host.textContent).not.toContain("traceback");
  });
  it("не выдаёт приостановленные расписания за работающие и ведёт к нужной доске", async () => {
    boards.mockResolvedValue({ boards: [{ slug: "purchases", name: "Закупки", total: 2, counts: { blocked: 1, review: 1 } }] });
    jobs.mockResolvedValue([
      { enabled: false, last_status: "ok", last_run_at: "2026-09-05" },
      { enabled: false, last_status: "error", last_fire_error: "Старая ошибка" },
      { enabled: true, last_status: "ok", last_run_at: "2026-09-06" },
    ]);
    await renderPage();
    expect(host.querySelector('[data-milestone="routine"] .kb-benefit-metric strong')?.textContent).toBe("1");
    expect(host.textContent).toContain("Приостановлено расписаний: 2");
    expect(host.textContent).not.toContain("Расписаний с ошибкой");
    expect(Array.from(host.querySelectorAll("a")).map(a => [a.getAttribute("href"), a.textContent])).toContainEqual(["/kanban?board=purchases&view=attention", "Закупки: нужно решение — 1, на проверке — 1. Открыть →"]);
    expect(host.querySelector('[data-milestone="result"] a')?.getAttribute("href")).toBe("/kanban?view=done");
  });
});

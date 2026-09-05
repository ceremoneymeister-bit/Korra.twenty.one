// @vitest-environment jsdom
import React, { act } from "react";
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
    __HERMES_PLUGIN_SDK__: { React, fetchJSON: boards, api: { getCronJobs: jobs, getProfiles: profiles } },
    __HERMES_PLUGINS__: { register: (_name: string, component: React.ComponentType) => { Page = component; } },
  });
  // @ts-expect-error Плагин поставляется как исполняемый браузерный JavaScript.
  await import("../../../plugins/hermes-achievements/dashboard/dist/index.js");
  await act(async () => { root.render(React.createElement(Page!)); });
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
    expect(host.textContent).toContain("Освоено 0 из 4");
    expect(host.querySelector('.kb-benefit-next a')?.getAttribute("href")).toBe("/kanban");
    expect(host.textContent).not.toMatch(/Hermes|Copper|Olympian/);
  });
  it("связывает этапы с реальными карточками, успешными запусками и собственными агентами", async () => {
    boards.mockResolvedValue({ boards: [{ total: 3, counts: { done: 2, review: 1 } }] });
    jobs.mockResolvedValue([
      { last_status: "ok", last_run_at: "2026-09-05" },
      { last_status: "error", last_run_at: "2026-09-05" },
      { enabled: true },
    ]);
    profiles.mockResolvedValue({ profiles: [{ is_default: true }, { name: "sales", is_default: false }] });
    await renderPage();
    expect(host.textContent).toContain("Освоено 4 из 4");
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
    expect(host.querySelector('[data-milestone="team"]')?.textContent).toContain("Освоено");
    expect(host.textContent).not.toContain("traceback");
  });
});

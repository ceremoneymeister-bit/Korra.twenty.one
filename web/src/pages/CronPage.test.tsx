// @vitest-environment jsdom

// Раздел «Задачи» в режиме клиента (`KORRA_UI_MODE=fleet`). До правки он был
// написан под несуществующий owner-контракт: каждое действие сперва требовало
// `job.revision`, которого движок не отдаёт, и падало с «Обновите страницу и
// повторите», не дойдя до сети. Правка и удаление были заперты за
// неработающей паузой, ручного запуска не было вовсе, а список грузился без
// профиля. Тесты держат раздел на реальных маршрутах движка.

import { act, useState, type ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { PageHeaderContext } from "@/contexts/page-header-context";
import { ProfileContext } from "@/contexts/profile-context";
import type { CronJob } from "@/lib/api";

// KorraLoader оборачивает `window.matchMedia` прямо при импорте модуля, а в
// jsdom его нет — ставим заглушку до того, как подтянется CronPage.
vi.hoisted(() => {
  if (typeof window !== "undefined" && !window.matchMedia) {
    window.matchMedia = ((query: string) => ({
      addEventListener: () => {},
      addListener: () => {},
      dispatchEvent: () => false,
      matches: false,
      media: query,
      onchange: null,
      removeEventListener: () => {},
      removeListener: () => {},
    })) as unknown as typeof window.matchMedia;
  }
});

const apiMocks = vi.hoisted(() => ({
  getCronJobs: vi.fn(),
  getCronDeliveryTargets: vi.fn(),
  createCronJob: vi.fn(),
  updateCronJob: vi.fn(),
  pauseCronJob: vi.fn(),
  resumeCronJob: vi.fn(),
  triggerCronJob: vi.fn(),
  deleteCronJob: vi.fn(),
  getProfiles: vi.fn(),
  getSkills: vi.fn(),
  getToolsets: vi.fn(),
  getModelOptions: vi.fn(),
}));

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return { ...actual, api: { ...actual.api, ...apiMocks } };
});

import CronPage from "./CronPage";

let container: HTMLDivElement;
let root: Root;

/** Задача в том виде, в каком её отдаёт `GET /api/cron/jobs`: без `revision`
 *  и без `archived_at` — этих полей у движка нет. */
function job(overrides: Partial<CronJob> = {}): CronJob {
  return {
    id: "job-1",
    profile: "secretary",
    name: "Утренний отчёт",
    prompt: "Собери сводку за вчера",
    schedule: { kind: "interval", expr: "30m", display: "каждые 30 минут" },
    enabled: true,
    state: "scheduled",
    deliver: "local",
    last_run_at: null,
    next_run_at: null,
    ...overrides,
  };
}

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT =
  true;

/** Кнопка «Добавить задачу» живёт в шапке страницы — отдаём её через тот же
 *  контекст, что и приложение, и рисуем рядом. */
function PageHeaderHost({ children }: { children: ReactNode }) {
  const [end, setEnd] = useState<ReactNode>(null);
  return (
    <PageHeaderContext.Provider
      value={{ setAfterTitle: () => {}, setEnd, setTitle: () => {} }}
    >
      <div data-testid="page-header-end">{end}</div>
      {children}
    </PageHeaderContext.Provider>
  );
}

async function flush() {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
}

/** Отрисовать страницу в выбранном профиле и дождаться списка. */
async function renderPage(profile = "secretary") {
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  await act(async () =>
    root.render(
      <ProfileContext.Provider
        value={{
          profile,
          currentProfile: "default",
          profiles: [],
          setProfile: () => {},
          refreshProfiles: async () => {},
        }}
      >
        <PageHeaderHost>
          <CronPage />
        </PageHeaderHost>
      </ProfileContext.Provider>,
    ),
  );
  await flush();
}

function actionButton(label: string, scope: ParentNode = container) {
  return Array.from(scope.querySelectorAll("button")).find(
    (button) => button.getAttribute("aria-label") === label,
  );
}

function findButton(text: string, scope: ParentNode = container) {
  return Array.from(scope.querySelectorAll("button")).find((button) =>
    button.textContent?.includes(text),
  );
}

async function click(element: Element | undefined) {
  expect(element).toBeTruthy();
  await act(async () => {
    (element as HTMLElement).click();
  });
  await flush();
}

async function enterText(field: HTMLElement, value: string) {
  const proto =
    field instanceof HTMLTextAreaElement
      ? HTMLTextAreaElement.prototype
      : HTMLInputElement.prototype;
  const setter = Object.getOwnPropertyDescriptor(proto, "value")?.set;
  await act(async () => {
    setter?.call(field, value);
    field.dispatchEvent(new Event("input", { bubbles: true }));
  });
}

function toastText(): string {
  return document.body.textContent ?? "";
}

beforeEach(() => {
  window.__KORRA_UI_MODE__ = "fleet";
  vi.stubGlobal(
    "matchMedia",
    vi.fn((query: string) => ({
      addEventListener: vi.fn(),
      dispatchEvent: vi.fn(),
      matches: false,
      media: query,
      onchange: null,
      removeEventListener: vi.fn(),
    })),
  );
  Object.defineProperty(HTMLElement.prototype, "scrollIntoView", {
    configurable: true,
    value: vi.fn(),
  });
  apiMocks.getCronJobs.mockResolvedValue([job()]);
  apiMocks.getCronDeliveryTargets.mockResolvedValue({
    targets: [
      { id: "local", name: "Локально", home_target_set: true, home_env_var: null },
    ],
  });
  apiMocks.getProfiles.mockResolvedValue({ profiles: [] });
  apiMocks.getSkills.mockResolvedValue([]);
  apiMocks.getToolsets.mockResolvedValue([]);
  apiMocks.getModelOptions.mockResolvedValue(null);
  apiMocks.createCronJob.mockResolvedValue(job());
  apiMocks.updateCronJob.mockResolvedValue(job());
  apiMocks.pauseCronJob.mockResolvedValue(job({ state: "paused", enabled: false }));
  apiMocks.resumeCronJob.mockResolvedValue(job());
  apiMocks.triggerCronJob.mockResolvedValue(job());
  apiMocks.deleteCronJob.mockResolvedValue({ ok: true });
});

afterEach(async () => {
  await act(async () => root?.unmount());
  container?.remove();
  vi.unstubAllGlobals();
  vi.clearAllMocks();
  delete window.__KORRA_UI_MODE__;
});

describe("CronPage в режиме клиента", () => {
  it("запрашивает задачи выбранного профиля, а не всех сразу", async () => {
    await renderPage("secretary");
    expect(apiMocks.getCronJobs).toHaveBeenCalledWith("secretary");
  });

  it("пауза доходит до движка и не требует ревизии", async () => {
    await renderPage();
    await click(actionButton("Пауза"));

    expect(apiMocks.pauseCronJob).toHaveBeenCalledWith("job-1", "secretary");
    expect(toastText()).not.toContain("Обновите страницу");
    // Список перечитывается после действия: первый раз при монтировании,
    // второй — по факту паузы.
    expect(apiMocks.getCronJobs).toHaveBeenCalledTimes(2);
  });

  it("снимает с паузы без ввода названия задачи", async () => {
    apiMocks.getCronJobs.mockResolvedValue([
      job({ state: "paused", enabled: false }),
    ]);
    await renderPage();
    await click(actionButton("Возобновить"));

    expect(apiMocks.resumeCronJob).toHaveBeenCalledWith("job-1", "secretary");
    expect(toastText()).not.toContain("Название задачи");
  });

  it("даёт запустить задачу вручную", async () => {
    await renderPage();
    const trigger = actionButton("Запустить сейчас");
    expect(trigger).toBeTruthy();
    await click(trigger);

    expect(apiMocks.triggerCronJob).toHaveBeenCalledWith("job-1", "secretary");
  });

  it("правка и удаление не заперты за паузой", async () => {
    await renderPage();
    expect(actionButton("Изменить задачу")?.disabled).toBe(false);
    expect(actionButton("Удалить задачу")?.disabled).toBe(false);
  });

  it("сохраняет правку через PUT с профилем задачи", async () => {
    await renderPage();
    await click(actionButton("Изменить задачу"));

    const dialog = document.querySelector<HTMLElement>('[role="dialog"]');
    expect(dialog).toBeTruthy();
    await enterText(
      dialog!.querySelector<HTMLInputElement>("#edit-cron-name")!,
      "Вечерний отчёт",
    );
    await click(findButton("Сохранить", dialog!));

    expect(apiMocks.updateCronJob).toHaveBeenCalledTimes(1);
    const [id, payload, profile] = apiMocks.updateCronJob.mock.calls[0];
    expect(id).toBe("job-1");
    expect(profile).toBe("secretary");
    expect(payload).toMatchObject({ name: "Вечерний отчёт" });
  });

  it("удаляет задачу через DELETE и предупреждает, что это навсегда", async () => {
    await renderPage();
    await click(actionButton("Удалить задачу"));

    expect(document.body.textContent).toContain("Отменить это нельзя");
    const dialog = document.querySelector<HTMLElement>('[role="alertdialog"]')
      ?? document.querySelector<HTMLElement>('[role="dialog"]');
    await click(findButton("Удалить", dialog ?? document.body));

    expect(apiMocks.deleteCronJob).toHaveBeenCalledWith("job-1", "secretary");
  });

  it("после создания не обещает черновик — движок включает задачу сразу", async () => {
    await renderPage();
    await click(
      findButton(
        "Добавить задачу",
        document.querySelector('[data-testid="page-header-end"]')!,
      ),
    );

    const dialog = document.querySelector<HTMLElement>('[role="dialog"]')!;
    await enterText(
      dialog.querySelector<HTMLInputElement>("#cron-name")!,
      "Ночная сводка",
    );
    await enterText(
      dialog.querySelector<HTMLTextAreaElement>("#cron-prompt")!,
      "Собери сводку",
    );
    await click(findButton("Добавить в расписание", dialog));

    expect(apiMocks.createCronJob).toHaveBeenCalledTimes(1);
    expect(apiMocks.createCronJob.mock.calls[0][1]).toBe("secretary");
    expect(toastText()).toContain("будет запускаться по расписанию");
    expect(toastText()).not.toContain("Черновик");
  });
});

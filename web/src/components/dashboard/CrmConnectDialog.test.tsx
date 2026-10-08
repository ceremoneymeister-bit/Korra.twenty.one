// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { CrmConnectDialog } from "@/components/dashboard/CrmConnectDialog";
import {
  crmConnectionFixture,
  dashboardStateFixture,
  FIXTURE_NOW,
  salesFixture,
} from "@/components/dashboard/dashboard-state.fixture";
import { $crmDialog, closeCrmDialog, openCrmDialog, type DashboardSales } from "@/lib/crm";
import { $dashboardState, $dashboardStatus, refreshDashboardState } from "@/lib/dashboard-state";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const SECRET = "https://acme.bitrix24.ru/rest/12/SECRETSECRET123/";
const FOUND = {
  type: "bitrix24",
  source_label: "Битрикс24",
  portal: "acme.bitrix24.ru",
  user: "Анна Петрова",
  deals: 128,
  deals_capped: false,
  managers: 6,
  tasks: true,
  pipelines: [
    { id: "0", name: "Продажи" },
    { id: "2", name: "Партнёры" },
  ],
  pipeline_id: "0",
};

let root: Root | null = null;
let container: HTMLDivElement;
let sales: DashboardSales;
let reply: Record<string, { status: number; body: unknown }>;
const calls: { method: string; path: string; body: Record<string, unknown> | undefined }[] = [];

function serve() {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: unknown, init?: RequestInit) => {
      const text = String(url);
      const method = init?.method ?? "GET";
      if (text.includes("/api/dashboard/state")) {
        return new Response(JSON.stringify(dashboardStateFixture({ sales })), {
          headers: { "Content-Type": "application/json" },
        });
      }
      const path = text.split("/api/dashboard/crm")[1] || "/";
      calls.push({ method, path, body: init?.body ? JSON.parse(String(init.body)) : undefined });
      const answer = reply[`${method} ${path}`] ?? { status: 200, body: { ok: true } };
      return new Response(JSON.stringify(answer.body), {
        status: answer.status,
        headers: { "Content-Type": "application/json" },
      });
    }),
  );
}

async function flush() {
  for (let i = 0; i < 4; i += 1) {
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
  }
}

async function mount(mode: "connect" | "replace" | "settings" = "connect") {
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  await act(async () =>
    root!.render(
      <MemoryRouter>
        <CrmConnectDialog />
      </MemoryRouter>,
    ),
  );
  await act(async () => {
    await refreshDashboardState();
  });
  await act(async () => openCrmDialog(mode));
  await flush();
}

const dialog = () => document.body.querySelector<HTMLElement>("[role=dialog]");
const button = (label: string) =>
  Array.from(dialog()?.querySelectorAll<HTMLButtonElement>("button") ?? []).find((node) =>
    node.textContent?.includes(label),
  );

async function click(node: HTMLElement | undefined) {
  expect(node).toBeTruthy();
  await act(async () => node!.click());
  await flush();
}

async function type(input: HTMLInputElement, value: string) {
  await act(async () => {
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!;
    setter.call(input, value);
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
}

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(FIXTURE_NOW * 1000);
  calls.length = 0;
  reply = {};
  sales = { status: "not_connected", candidates: [] };
  $dashboardState.set(null);
  $dashboardStatus.set("idle");
  closeCrmDialog();
  serve();
});

afterEach(async () => {
  if (root) await act(async () => root!.unmount());
  root = null;
  container?.remove();
  document.body.innerHTML = "";
  document.body.style.overflow = "";
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe("окно подключения CRM", () => {
  it("первый шаг — выбор CRM и честное «только чтение»", async () => {
    await mount();
    expect(dialog()?.textContent).toContain("Какая у вас CRM?");
    expect(dialog()?.textContent).toContain("Битрикс24");
    expect(dialog()?.textContent).toContain("amoCRM");
    expect(dialog()?.textContent).toContain("Korra будет только читать CRM");
    expect(document.body.style.overflow).toBe("hidden");
  });

  it("Битрикс24: путь в портале, поле вебхука скрыто, «Проверить» ждёт ключ", async () => {
    await mount();
    await click(button("Дальше"));
    expect(dialog()?.textContent).toContain("Приложения");
    expect(dialog()?.textContent).toContain("Входящий вебхук");
    expect(dialog()?.textContent).toContain("нельзя сделать «только для чтения»");
    const field = dialog()!.querySelector<HTMLInputElement>("input")!;
    expect(field.type).toBe("password");
    expect(field.autocomplete).toBe("off");
    expect(button("Проверить")?.disabled).toBe(true);
  });

  it("amoCRM: адрес аккаунта открыт, токен скрыт", async () => {
    await mount();
    await click(dialog()!.querySelector<HTMLElement>('[role=radio]:nth-of-type(2)')!);
    await click(button("Дальше"));
    const inputs = Array.from(dialog()!.querySelectorAll<HTMLInputElement>("input"));
    expect(inputs.map((node) => node.type)).toEqual(["text", "password"]);
    expect(dialog()?.textContent).toContain("Интеграции");
  });

  it("проверка → найденное → сохранение: ключ уходит серверу и не остаётся в окне", async () => {
    reply["POST /check"] = { status: 200, body: { ok: true, found: FOUND } };
    reply["PUT /"] = { status: 200, body: { ok: true, connection: crmConnectionFixture() } };
    await mount();
    await click(button("Дальше"));
    await type(dialog()!.querySelector("input")!, SECRET);
    await click(button("Проверить"));

    expect(calls.find((call) => call.path === "/check")?.body).toEqual({ type: "bitrix24", webhook_url: SECRET });
    expect(dialog()?.textContent).toContain("acme.bitrix24.ru");
    expect(dialog()?.textContent).toContain("Вошли как Анна Петрова · только чтение");
    expect(dialog()?.textContent).toContain("128");
    expect(dialog()?.textContent).toContain("2воронки");
    expect(dialog()?.textContent).not.toContain("SECRETSECRET");

    sales = salesFixture();
    await click(button("Готово — показать продажи"));
    const save = calls.find((call) => call.method === "PUT")!;
    expect(save.body).toEqual({
      type: "bitrix24",
      webhook_url: SECRET,
      settings: { pipeline_id: "0", stuck_days: 7, agents_access: true },
    });
    expect(dialog()).toBeNull();
    expect(document.body.textContent).not.toContain("SECRETSECRET");
    expect($crmDialog.get()).toBeNull();
  });

  it("настройки, выбранные на последнем шаге, уходят вместе с ключом", async () => {
    reply["POST /check"] = { status: 200, body: { ok: true, found: FOUND } };
    reply["PUT /"] = { status: 200, body: { ok: true, connection: crmConnectionFixture() } };
    await mount();
    await click(button("Дальше"));
    await type(dialog()!.querySelector("input")!, SECRET);
    await click(button("Проверить"));
    const selects = dialog()!.querySelectorAll<HTMLSelectElement>("select");
    await act(async () => {
      selects[0].value = "2";
      selects[0].dispatchEvent(new Event("change", { bubbles: true }));
      selects[1].value = "14";
      selects[1].dispatchEvent(new Event("change", { bubbles: true }));
    });
    await click(dialog()!.querySelector<HTMLElement>('[role=switch]')!);
    await click(button("Готово — показать продажи"));
    expect(calls.find((call) => call.method === "PUT")?.body?.settings).toEqual({
      pipeline_id: "2",
      stuck_days: 14,
      agents_access: false,
    });
  });

  it("ошибка проверки названа по-русски, ключ можно поправить без повторного ввода всего", async () => {
    reply["POST /check"] = {
      status: 200,
      body: {
        ok: false,
        error: { code: "bad_key", title: "Битрикс24 не принял ключ", message: "Вебхук удалён или отключён.", retry: false },
      },
    };
    await mount();
    await click(button("Дальше"));
    await type(dialog()!.querySelector("input")!, SECRET);
    await click(button("Проверить"));
    expect(dialog()?.querySelector("[role=alert]")?.textContent).toContain("Битрикс24 не принял ключ");
    expect(dialog()?.querySelector("[role=alert]")?.textContent).toContain("Вебхук удалён или отключён.");
    expect(calls.some((call) => call.method === "PUT")).toBe(false);
    await click(button("Изменить адрес"));
    expect(dialog()!.querySelector<HTMLInputElement>("input")!.value).toBe(SECRET);
  });

  it("недоступная панель не принимается за ошибку ключа", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => Promise.reject(new TypeError("offline"))));
    await mount();
    await click(button("Дальше"));
    await type(dialog()!.querySelector("input")!, SECRET);
    await click(button("Проверить"));
    expect(dialog()?.querySelector("[role=alert]")?.textContent).toContain("Не удалось связаться с панелью");
  });

  it("без прав на задачи предупреждает, что просрочки считаться не будут", async () => {
    reply["POST /check"] = { status: 200, body: { ok: true, found: { ...FOUND, tasks: false } } };
    await mount();
    await click(button("Дальше"));
    await type(dialog()!.querySelector("input")!, SECRET);
    await click(button("Проверить"));
    expect(dialog()?.textContent).toContain("нет прав на задачи");
  });

  it("ключ сохранён, но CRM не ответила: окно говорит об этом и закрывается по «Понятно»", async () => {
    reply["POST /check"] = { status: 200, body: { ok: true, found: FOUND } };
    reply["PUT /"] = {
      status: 200,
      body: {
        ok: true,
        connection: crmConnectionFixture(),
        warning: { code: "network", title: "Ключ сохранён", message: "Данные подтянутся, когда CRM ответит.", retry: true },
      },
    };
    await mount();
    await click(button("Дальше"));
    await type(dialog()!.querySelector("input")!, SECRET);
    await click(button("Проверить"));
    await click(button("Готово — показать продажи"));
    expect(dialog()?.textContent).toContain("Данные подтянутся, когда CRM ответит.");
    await click(button("Понятно"));
    expect(dialog()).toBeNull();
  });

  it("Esc, крестик и фон закрывают окно", async () => {
    await mount();
    await act(async () => {
      document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" }));
    });
    expect(dialog()).toBeNull();
    await act(async () => openCrmDialog("connect"));
    await click(dialog()!.querySelector<HTMLElement>('[aria-label="Закрыть"]')!);
    expect(dialog()).toBeNull();
    await act(async () => openCrmDialog("connect"));
    await click(dialog()!);
    expect(dialog()).toBeNull();
    expect(document.body.style.overflow).toBe("");
  });
});

describe("окно подключения CRM: замена ключа и настройки", () => {
  beforeEach(() => {
    sales = salesFixture();
  });

  it("«Заменить ключ» сразу просит ключ той же CRM, без выбора и «Назад»", async () => {
    await mount("replace");
    expect(dialog()?.textContent).toContain("Битрикс24: адрес вебхука");
    expect(button("Назад")).toBeUndefined();
    expect(dialog()!.querySelector<HTMLInputElement>("input")!.value).toBe("");
  });

  it("настройки открываются сразу на найденном и сохраняют только настройки, без ключа", async () => {
    reply["PATCH /"] = { status: 200, body: crmConnectionFixture() };
    await mount("settings");
    expect(dialog()?.textContent).toContain("Подключение CRM");
    expect(dialog()?.textContent).toContain("acme.bitrix24.ru");
    const days = dialog()!.querySelector<HTMLSelectElement>('select[aria-label="Считать застрявшей"]')!;
    await act(async () => {
      days.value = "14";
      days.dispatchEvent(new Event("change", { bubbles: true }));
    });
    await click(button("Сохранить"));
    expect(calls.filter((call) => call.method === "PATCH")).toEqual([
      { method: "PATCH", path: "/", body: { pipeline_id: "0", stuck_days: 14, agents_access: true } },
    ]);
    expect(calls.some((call) => call.method === "PUT")).toBe(false);
    expect(dialog()).toBeNull();
  });

  it("без подключения замена и настройки превращаются в обычное подключение", async () => {
    sales = { status: "not_connected", candidates: [] };
    await mount("replace");
    expect(dialog()?.textContent).toContain("Какая у вас CRM?");
  });
});

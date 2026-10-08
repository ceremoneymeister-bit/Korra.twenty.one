// @vitest-environment jsdom
import { readFileSync } from "node:fs";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter, useLocation } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  crmConnectionFixture,
  dashboardStateFixture,
  FIXTURE_NOW,
  salesFixture,
} from "@/components/dashboard/dashboard-state.fixture";
import { SALES_WIDGET } from "@/components/dashboard/widgets/SalesWidget";
import type { WidgetSize } from "@/lib/dashboard-layout";
import { $crmDialog, closeCrmDialog, type DashboardSales } from "@/lib/crm";
import { $dashboardState, $dashboardStatus, refreshDashboardState } from "@/lib/dashboard-state";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

// `?raw` у css в vitest пуст, поэтому читаем файл с диска.
const widgetStyles = readFileSync(`${process.cwd()}/src/components/dashboard/dashboard-widgets.css`, "utf8");

let root: Root | null = null;
let container: HTMLDivElement;
let sales: DashboardSales;
let reply: Record<string, { status: number; body: unknown }>;
const calls: { method: string; path: string; body: unknown }[] = [];

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
      if (text.includes("/api/dashboard/crm")) {
        const path = text.split("/api/dashboard/crm")[1] || "/";
        calls.push({ method, path, body: init?.body ? JSON.parse(String(init.body)) : undefined });
        const answer = reply[`${method} ${path}`] ?? { status: 200, body: { ok: true } };
        return new Response(JSON.stringify(answer.body), {
          status: answer.status,
          headers: { "Content-Type": "application/json" },
        });
      }
      throw new Error(`unexpected request ${text}`);
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

function Where() {
  const location = useLocation();
  return <output data-testid="where">{location.pathname + location.search}</output>;
}

async function mount(size: WidgetSize | undefined, part: "Body" | "HeaderNote" = "Body") {
  const Part = (part === "Body" ? SALES_WIDGET.Body : SALES_WIDGET.HeaderNote)!;
  container = document.createElement("div");
  container.className = "korra-dashboard";
  document.body.appendChild(container);
  root = createRoot(container);
  await act(async () =>
    root!.render(
      <MemoryRouter initialEntries={["/dashboard"]}>
        <Part size={size} />
        <Where />
      </MemoryRouter>,
    ),
  );
  await act(async () => {
    await refreshDashboardState();
  });
  await flush();
}

const text = () => container.textContent ?? "";
const byText = (root: ParentNode, label: string, selector = "button, a") =>
  Array.from(root.querySelectorAll<HTMLElement>(selector)).find((node) => node.textContent?.includes(label));

async function click(node: HTMLElement | undefined) {
  expect(node).toBeTruthy();
  await act(async () => node!.click());
  await flush();
}

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(FIXTURE_NOW * 1000);
  calls.length = 0;
  reply = {};
  sales = salesFixture();
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
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe("карточка «Продажи»: размеры", () => {
  it("M: деньги месяца, изменение к прошлому месяцу и три сигнала", async () => {
    await mount("m");
    expect(text()).toContain("Выиграно в сентябре · 14 сделок");
    expect(text()).toContain("2,8");
    expect(text()).toContain("млн");
    expect(text()).toContain("+18 %");
    expect(text()).toContain("к августу на эту дату");
    expect(text()).toContain("заявок сегодня");
    expect(container.querySelector('[aria-label="7 застряли: разобрать с агентом"]')).toBeTruthy();
    expect(text()).toContain("просрочено");
    expect(container.querySelector("[data-testid=sales-river]")).toBeNull();
  });

  it("S: сумма застрявших и доля воронки, без подписи обновления", async () => {
    await mount("s");
    expect(text()).toContain("1,3");
    expect(text()).toContain("стоят без движения дольше 7 дней");
    expect(text()).toContain("7 сделок");
    expect(text()).toContain("21 %");
  });

  it("S без застрявших говорит о выигранном за месяц", async () => {
    sales = salesFixture({ stuck: { count: 0, amount: 0, days: 7, approx: false, top: [] } });
    await mount("s");
    expect(text()).toContain("выиграно в этом месяце");
    expect(text()).toContain("застрявших нет");
  });

  it("L: река по этапам со ссылками на сделки, легенда и кнопка разбора", async () => {
    await mount("l");
    const stages = container.querySelectorAll("[data-stage]");
    expect(Array.from(stages).map((node) => node.getAttribute("data-stage"))).toEqual(["NEW", "TALK", "OFFER"]);
    const dot = container.querySelector<HTMLAnchorElement>(".kdw-sales-dot--stuck");
    expect(dot?.getAttribute("href")).toMatch(/^https:\/\/acme\.bitrix24\.ru\/crm\/deal\/details\//);
    expect(dot?.getAttribute("target")).toBe("_blank");
    expect(dot?.getAttribute("rel")).toBe("noopener noreferrer");
    expect(text()).toContain("стоит 7+ дней");
    expect(text()).toContain("Воронка «Продажи»");
    expect(text()).toContain("больше всего стоит на «Переговоры»");
    expect(text()).toContain("Разобрать 7 застрявших с агентом");
    expect(text()).toContain("Открыть Битрикс24 ↗");
    expect(container.querySelector<HTMLAnchorElement>(".kdw-sales-link")?.getAttribute("href")).toBe(
      "https://acme.bitrix24.ru/crm/deal/kanban/",
    );
  });

  it("L: на этап не больше 36 кружков, остальные считаются плюсом", async () => {
    const many = Array.from({ length: 40 }, (_, index) => ({
      id: String(index),
      title: `Сделка ${index}`,
      amount: 1000,
      days: 1,
      stuck: false,
      late: false,
      new: false,
      url: `https://acme.bitrix24.ru/crm/deal/details/${index}/`,
      manager: "",
      stage: "Новые",
    }));
    const base = salesFixture();
    sales = salesFixture({
      river: { ...base.river, stages: [{ id: "NEW", name: "Новые", count: 40, amount: 40_000, stuck: 0, deals: many }] },
    });
    await mount("l");
    expect(container.querySelectorAll(".kdw-sales-dot")).toHaveLength(36);
    expect(text()).toContain("+4");
  });

  it("amoCRM: подпись источника и ссылка на свой аккаунт", async () => {
    sales = salesFixture({ links: { portal: "https://acme.amocrm.ru/leads/" } }, "amocrm");
    await mount("l");
    expect(text()).toContain("Открыть amoCRM ↗");
    expect(text()).toContain("₽");
  });

  it("знак валюты берётся из данных, а не из зоны портала", async () => {
    sales = salesFixture({ portal: "acme.bitrix24.ru", currency: "KZT" });
    await mount("m");
    expect(text()).toContain("₸");
    expect(text()).not.toContain("₽");
  });

  it("валюта без известного знака показана кодом, а пустая — без знака", async () => {
    sales = salesFixture({ currency: "CHF" });
    await mount("m");
    expect(text()).toContain("CHF");
    await act(async () => root!.unmount());
    container.remove();
    sales = salesFixture({ currency: "" });
    await mount("m");
    expect(text()).not.toContain("₽");
    expect(text()).not.toContain("CHF");
  });

  it("без прав на задачи показывает прочерк вместо нуля просрочек", async () => {
    sales = salesFixture({ overdue: { available: false, tasks: 0, managers: 0, limited: false } });
    await mount("m");
    expect(text()).toContain("—");
    expect(text()).toContain("задачи недоступны");
  });
});

describe("карточка «Продажи»: валюта и полнота (K21-322 F4–F7)", () => {
  const NOTE = "и сделки в других валютах — не в сумме";
  const base = () => salesFixture();

  it.each(["s", "m", "l"] as const)("%s: сделки в других валютах названы и в сумму не входят", async (size) => {
    sales = salesFixture({ other_currencies: ["USD"] });
    await mount(size);
    expect(text()).toContain(NOTE);
  });

  it.each(["s", "m", "l"] as const)("%s: одна валюта — без подписи", async (size) => {
    await mount(size);
    expect(text()).not.toContain(NOTE);
  });

  it("L: сделка в чужой валюте показана в своей валюте", async () => {
    const own = base();
    sales = salesFixture({
      other_currencies: ["USD"],
      river: {
        ...own.river,
        stages: own.river.stages.map((stage, index) =>
          index === 0 ? { ...stage, deals: stage.deals.map((d) => ({ ...d, currency: "USD", amount: 5000 })) } : stage,
        ),
      },
    });
    await mount("l");
    expect(container.querySelector('[data-stage="NEW"] a')?.getAttribute("title")).toContain("$");
  });

  it("M: неполная история выигранных — «не менее» у суммы, без процента", async () => {
    sales = salesFixture({ won: { ...base().won, limited: true, change_pct: null } });
    await mount("m");
    expect(text()).toContain("не менее");
    expect(text()).not.toContain("%");
    expect(text()).not.toContain("в прошлом месяце продаж не было");
  });

  it("S: неполная история выигранных и застрявших — «не менее» и у суммы, и у числа", async () => {
    sales = salesFixture({ stuck: { ...base().stuck, limited: true } });
    await mount("s");
    expect(text()).toContain("не менее 1,3");
    expect(text()).toContain("не менее 7 сделок");
    await act(async () => root!.unmount());
    container.remove();
    sales = salesFixture({ stuck: { count: 0, amount: 0, days: 7, approx: false, top: [] }, won: { ...base().won, limited: true, change_pct: null } });
    await mount("s");
    expect(text()).toContain("не менее 2,8");
  });

  it.each(["m", "l"] as const)("%s: новые заявки, застрявшие и просрочки — нижние границы", async (size) => {
    sales = salesFixture({
      new_leads: { ...base().new_leads, limited: true },
      stuck: { ...base().stuck, limited: true },
      overdue: { ...base().overdue, limited: true },
    });
    await mount(size);
    const signals = container.querySelector(".kdw-sales-signals")!.textContent!;
    expect(signals).toContain("не менее 6");
    expect(signals).toContain("не менее 7");
    expect(signals).toContain("не менее 4");
  });

  it("amoCRM: приближённое число застрявших — «около», а не точное", async () => {
    sales = salesFixture({ stuck: { ...base().stuck, approx: true } });
    await mount("m");
    expect(container.querySelector(".kdw-sales-signals")!.textContent).toContain("около");
  });

  it("L: число открытых сделок — нижняя граница, прочитанных меньше", async () => {
    const own = base();
    sales = salesFixture({ river: { ...own.river, deals_total: 1500, total_exact: false, deals_loaded: 300, truncated: true } });
    await mount("l");
    expect(container.querySelector(".kdw-sales-river-h")!.textContent).toContain("не менее 1500 сделок");
    expect(container.querySelector(".kdw-sales-river-h")!.textContent).toContain("первые 300");
  });

  it("L: точное число при обрезке 301–499 не называется нижней границей", async () => {
    const own = base();
    sales = salesFixture({ river: { ...own.river, deals_total: 420, total_exact: true, deals_loaded: 300, truncated: true } });
    await mount("l");
    const head = container.querySelector(".kdw-sales-river-h")!.textContent!;
    expect(head).toContain("420 сделок");
    expect(head).not.toContain("не менее 420");
    expect(head).toContain("первые 300");
  });

  it("L: дни у приближённых сделок — «не менее», тройка не обещана самой давней", async () => {
    const own = base();
    sales = salesFixture({
      stuck: { ...own.stuck, top_exact: false, top: own.stuck.top.map((d) => ({ ...d, days_min: true })) },
    });
    await mount("l");
    const cards = container.querySelector(".kdw-sales-stuck")!;
    expect(cards.textContent).toContain("не менее");
    expect(cards.textContent).toContain("давно без движения");
    expect(cards.textContent).toContain("есть и более давние");
  });

  it("L: точные дни и точная тройка — без оговорок", async () => {
    await mount("l");
    const cards = container.querySelector(".kdw-sales-stuck")!;
    expect(cards.textContent).not.toContain("не менее");
    expect(cards.textContent).not.toContain("давно без движения");
  });

  it("разбор с агентом: в поручении та же нижняя граница и валюта", async () => {
    sales = salesFixture({ stuck: { ...base().stuck, limited: true }, currency: "KZT" });
    await mount("l");
    await click(byText(container, "с агентом"));
    const draft = new URLSearchParams(container.querySelector("[data-testid=where]")!.textContent!.split("?")[1]).get("draft")!;
    expect(draft).toContain("не менее 7");
    expect(draft).toContain("₸");
  });
});

describe("карточка «Продажи»: плитка L и «Неразобранное» (K21-322 F12)", () => {
  it.each(["m", "l"] as const)("%s, amoCRM: в сигнале заявок — «неразобр.»", async (size) => {
    sales = salesFixture({ new_leads: { today: 14, series: [1, 2, 3, 4, 5, 6, 14], unsorted: 4 } }, "amocrm");
    await mount(size);
    const first = container.querySelector(".kdw-sales-signals .kdw-sales-sig")!.textContent!;
    expect(first).toContain("14");
    expect(first).toContain("4 неразобр.");
  });

  it("Битрикс24 без «Неразобранного» не получает пустой подписи", async () => {
    sales = salesFixture({ new_leads: { today: 6, series: [0, 0, 0, 0, 0, 0, 6], unsorted: null } });
    await mount("l");
    expect(container.querySelector(".kdw-sales-signals")!.textContent).not.toContain("неразобр");
  });

  it("L: три застрявшие сделки видны в штатной плитке ≈500 px, команда прячется только на тесной", () => {
    expect(widgetStyles).toMatch(/\.kdw-sales-extras \{[^}]*display: none/);
    expect(widgetStyles).toMatch(/@container kdw-sales \(min-height: 400px\) \{\s*\.kdw-sales-extras \{\s*display: flex/);
    expect(widgetStyles).toMatch(/\.kdw-sales-team \{[^}]*display: none/);
    expect(widgetStyles).toMatch(/@container kdw-sales \(min-height: 540px\) \{\s*\.kdw-sales-team \{\s*display: flex/);
    expect(widgetStyles).not.toMatch(/@container kdw-sales \(min-height: 540px\) \{\s*\.kdw-sales-extras/);
  });

  it("L: река занимает по содержимому — точки сверху, колонки не растягиваются пустотой", () => {
    expect(widgetStyles).toMatch(/\.kdw-sales-river-wrap \{[^}]*flex: 0 1 auto/);
    expect(widgetStyles).toMatch(/\.kdw-sales-river \{[^}]*flex: 0 1 auto/);
    expect(widgetStyles).toMatch(/\.kdw-sales-dots \{[^}]*align-content: flex-start/);
  });
});

describe("карточка «Продажи»: наличие на доске", () => {
  it("есть только там, где сводка принесла раздел продаж", () => {
    expect(SALES_WIDGET.isAvailable?.(null)).toBe(false);
    expect(SALES_WIDGET.isAvailable?.(dashboardStateFixture())).toBe(false);
    expect(SALES_WIDGET.isAvailable?.(dashboardStateFixture({ sales }))).toBe(true);
    expect(SALES_WIDGET.isAvailable?.(dashboardStateFixture({ sales: { status: "not_connected", candidates: [] } }))).toBe(true);
  });
});

describe("карточка «Продажи»: телефон", () => {
  it("река листается вбок: колонки фиксированной ширины с привязкой прокрутки", () => {
    const block = widgetStyles.match(/@media \(max-width: 680px\) \{[\s\S]*?kdw-sales-river[\s\S]*?\n\}/);
    expect(block?.[0]).toBeTruthy();
    expect(block![0]).toContain("overflow-x: auto");
    expect(block![0]).toContain("scroll-snap-type: x mandatory");
    expect(block![0]).toMatch(/\.kdw-sales-col \{[^}]*flex: 0 0 132px[^}]*scroll-snap-align: start/);
  });

  it("река — один прокручиваемый ряд колонок, доступный с клавиатуры", async () => {
    await mount("l");
    const river = container.querySelector<HTMLElement>("[data-testid=sales-river]")!;
    expect(river.tabIndex).toBe(0);
    expect(river.children).toHaveLength(3);
    expect(widgetStyles).toMatch(/\.kdw-sales-river \{[^}]*overflow-x: auto/);
  });

  it("невысокая плитка M прячет сигналы, а не вылезает за неё", () => {
    expect(widgetStyles).toMatch(/@container kdw-sales \(max-height: 84px\) \{\s*\.kdw-sales-signals \{\s*display: none/);
  });
});

describe("карточка «Продажи»: не подключено", () => {
  beforeEach(() => {
    sales = { status: "not_connected", candidates: [] };
  });

  it("предлагает подключить CRM и открывает окно подключения", async () => {
    await mount("m");
    expect(text()).toContain("Подключите Битрикс24 или amoCRM");
    await click(byText(container, "Подключить CRM"));
    expect($crmDialog.get()).toBe("connect");
  });

  it("S предлагает то же одной кнопкой", async () => {
    await mount("s");
    await click(byText(container, "Подключить CRM"));
    expect($crmDialog.get()).toBe("connect");
  });

  it("ключ агента из .env предлагается к переносу, «Использовать» — один запрос", async () => {
    sales = {
      status: "not_connected",
      candidates: [
        { profile: "analyst", label: "Аналитик", type: "bitrix24", source_label: "Битрикс24", portal: "acme.bitrix24.ru" },
      ],
    };
    reply["POST /adopt"] = { status: 200, body: { ok: true, connection: crmConnectionFixture() } };
    await mount("m");
    expect(text()).toContain("У агента «Аналитик» уже подключён Битрикс24");
    expect(text()).toContain("Сделать подключение общим");
    sales = salesFixture();
    await click(byText(container, "Использовать"));
    expect(calls.filter((call) => call.path === "/adopt")).toEqual([
      { method: "POST", path: "/adopt", body: { profile: "analyst", type: "bitrix24" } },
    ]);
    expect(text()).toContain("Выиграно в сентябре");
  });

  it("сбой переноса показан рядом с предложением, карточка остаётся прежней", async () => {
    sales = {
      status: "not_connected",
      candidates: [
        { profile: "analyst", label: "Аналитик", type: "bitrix24", source_label: "Битрикс24", portal: "acme.bitrix24.ru" },
      ],
    };
    reply["POST /adopt"] = {
      status: 422,
      body: { ok: false, error: { code: "bad_key", title: "Ключ не подошёл", message: "Битрикс24 отклонил ключ.", retry: false } },
    };
    await mount("m");
    await click(byText(container, "Использовать"));
    expect(container.querySelector("[role=alert]")?.textContent).toContain("Битрикс24 отклонил ключ.");
    expect(text()).toContain("Использовать");
  });

  it("«Подключить другой» открывает окно подключения", async () => {
    sales = {
      status: "not_connected",
      candidates: [
        { profile: "analyst", label: "Аналитик", type: "amocrm", source_label: "amoCRM", portal: "acme.amocrm.ru" },
      ],
    };
    await mount("m");
    await click(byText(container, "Подключить другой"));
    expect($crmDialog.get()).toBe("connect");
  });
});

describe("карточка «Продажи»: состояния", () => {
  it("ошибка ключа ведёт к замене ключа, а не к бесконечным повторам", async () => {
    sales = {
      status: "error",
      connection: crmConnectionFixture(),
      error: { code: "bad_key", title: "Битрикс24 не принимает ключ", message: "Вебхук удалён или отключён.", retry: false },
    };
    await mount("m");
    expect(container.querySelector("[role=alert]")?.textContent).toContain("Битрикс24 не принимает ключ");
    await click(byText(container, "Заменить ключ"));
    expect($crmDialog.get()).toBe("replace");
  });

  it("временный сбой предлагает повторить и перечитывает сводку", async () => {
    sales = {
      status: "error",
      connection: crmConnectionFixture(),
      error: { code: "network", title: "Битрикс24 не отвечает", message: "Попробуйте через минуту.", retry: true },
    };
    await mount("m");
    sales = salesFixture();
    await click(byText(container, "Повторить"));
    expect(text()).toContain("Выиграно в сентябре");
  });

  it("первое чтение показывает ожидание", async () => {
    sales = { status: "loading", connection: crmConnectionFixture() };
    await mount("m");
    expect(text()).toContain("Читаем CRM…");
  });

  it("при сбое обновления показывает «данные от …» и последние цифры", async () => {
    sales = salesFixture({
      stale: true,
      as_of: "2026-09-23T08:30:00+00:00",
      error: { code: "network", title: "Не отвечает", message: "…", retry: true },
    });
    await mount("m", "HeaderNote");
    expect(container.querySelector("[data-testid=sales-updated]")?.textContent).toBe("данные от 11:30");
    await act(async () => root!.unmount());
    container.remove();
    await mount("m");
    expect(text()).toContain("2,8");
  });

  it("обычная подпись — «обновлено … назад»; на S её нет, источник сокращён", async () => {
    await mount("m", "HeaderNote");
    expect(text()).toContain("Битрикс24");
    expect(container.querySelector("[data-testid=sales-updated]")?.textContent).toMatch(/^обновлено 3 мин/);
    await act(async () => root!.unmount());
    container.remove();
    await mount("s", "HeaderNote");
    expect(text()).toContain("Б24");
    expect(container.querySelector("[data-testid=sales-updated]")).toBeNull();
  });
});

describe("карточка «Продажи»: разбор с агентом", () => {
  it("кнопка ведёт в чат основного агента с готовым черновиком", async () => {
    await mount("l");
    await click(byText(container, "Разобрать 7 застрявших с агентом"));
    const where = container.querySelector("[data-testid=where]")!.textContent!;
    expect(where.startsWith("/agents?agent=default&draft=")).toBe(true);
    const draft = new URLSearchParams(where.split("?")[1]).get("draft")!;
    expect(draft).toContain("7");
    expect(draft).toContain("Битрикс24");
    expect(draft).toContain("crm_sales");
    expect(draft).not.toMatch(/webhook|token|rest\/\d+/i);
  });

  it("сигнал «застряли» на M делает то же самое", async () => {
    await mount("m");
    await click(container.querySelector<HTMLElement>('[aria-label="7 застряли: разобрать с агентом"]')!);
    expect(container.querySelector("[data-testid=where]")!.textContent).toMatch(/^\/agents\?agent=default&draft=/);
  });

  it("без застрявших кнопки нет", async () => {
    sales = salesFixture({ stuck: { count: 0, amount: 0, days: 7, approx: false, top: [] } });
    await mount("l");
    expect(byText(container, "Разобрать")).toBeUndefined();
    expect(text()).toContain("Застрявших сделок нет");
  });
});

describe("карточка «Продажи»: меню «⋯»", () => {
  const menu = () => document.body.querySelector<HTMLElement>("[role=menu]");

  async function open() {
    await mount("m", "HeaderNote");
    await click(container.querySelector<HTMLElement>('[aria-label="Меню карточки «Продажи»"]')!);
    expect(menu()).toBeTruthy();
  }

  it("пять пунктов, как в макете", async () => {
    await open();
    const items = Array.from(menu()!.querySelectorAll("[role=menuitem]")).map((node) => node.textContent);
    expect(items).toEqual([
      "Воронка и «застряла»Продажи · 7 дн.",
      "Доступ агентамвсе",
      "Заменить ключ",
      "Проверить подключениеработает",
      "Отключить Битрикс24",
    ]);
  });

  it("Esc закрывает меню и возвращает фокус на кнопку", async () => {
    await open();
    await act(async () => {
      document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" }));
    });
    expect(menu()).toBeNull();
  });

  it("«Воронка и «застряла»» и «Заменить ключ» открывают окно в своём режиме", async () => {
    await open();
    await click(byText(menu()!, "Воронка и «застряла»"));
    expect($crmDialog.get()).toBe("settings");
    await click(container.querySelector<HTMLElement>('[aria-label="Меню карточки «Продажи»"]')!);
    await click(byText(menu()!, "Заменить ключ"));
    expect($crmDialog.get()).toBe("replace");
  });

  it("«Доступ агентам» переключается сразу и без окна", async () => {
    reply["PATCH /"] = { status: 200, body: crmConnectionFixture() };
    await open();
    await click(byText(menu()!, "Доступ агентам"));
    expect(calls.filter((call) => call.method === "PATCH")).toEqual([
      { method: "PATCH", path: "/", body: { agents_access: false } },
    ]);
    expect(menu()?.textContent).toContain("Агенты больше не читают CRM.");
  });

  it("«Проверить подключение» перепроверяет сохранённый ключ и пишет итог в меню", async () => {
    reply["POST /check"] = { status: 200, body: { ok: true, found: {}, connection: crmConnectionFixture() } };
    await open();
    await click(byText(menu()!, "Проверить подключение"));
    expect(calls.filter((call) => call.path === "/check")).toEqual([{ method: "POST", path: "/check", body: {} }]);
    expect(menu()?.textContent).toContain("Подключение работает.");
  });

  it("неудачная проверка называет причину по-русски", async () => {
    reply["POST /check"] = {
      status: 200,
      body: { ok: false, error: { code: "bad_key", title: "Ключ не принят", message: "Вебхук удалён.", retry: false } },
    };
    await open();
    await click(byText(menu()!, "Проверить подключение"));
    expect(menu()?.textContent).toContain("Ключ не принят. Вебхук удалён.");
  });

  it("отключение требует подтверждения и ничего не шлёт до него", async () => {
    reply["DELETE /"] = { status: 200, body: { state: "not_connected" } };
    await open();
    await click(byText(menu()!, "Отключить Битрикс24"));
    expect(calls.filter((call) => call.method === "DELETE")).toHaveLength(0);
    const dialog = document.body.querySelector("[role=dialog], [role=alertdialog]") as HTMLElement;
    expect(dialog.textContent).toContain("Отключить Битрикс24?");
    expect(dialog.textContent).toContain("В самой CRM ничего не меняется");
    sales = { status: "not_connected", candidates: [] };
    await click(byText(dialog, "Отключить", "button"));
    expect(calls.filter((call) => call.method === "DELETE")).toHaveLength(1);
    expect(menu()).toBeNull();
  });

  it("«Отмена» оставляет подключение", async () => {
    await open();
    await click(byText(menu()!, "Отключить Битрикс24"));
    const dialog = document.body.querySelector("[role=dialog], [role=alertdialog]") as HTMLElement;
    await click(byText(dialog, "Отмена", "button"));
    expect(calls.filter((call) => call.method === "DELETE")).toHaveLength(0);
  });
});

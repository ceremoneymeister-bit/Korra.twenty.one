// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { dashboardStateFixture, FIXTURE_NOW } from "@/components/dashboard/dashboard-state.fixture";
import type { DashboardWidget } from "@/components/dashboard/widget-types";
import { ATTENTION_WIDGET } from "@/components/dashboard/widgets/AttentionWidget";
import { CODEX_QUOTA_WIDGET } from "@/components/dashboard/widgets/CodexQuotaWidget";
import { METRICS_WIDGET } from "@/components/dashboard/widgets/MetricsWidget";
import { RECENT_RESULTS_WIDGET } from "@/components/dashboard/widgets/RecentResultsWidget";
import { UPCOMING_TASKS_WIDGET } from "@/components/dashboard/widgets/UpcomingTasksWidget";
import { $chatRuns, $chatRunsReachable, $chatRunsUpdatedAt, type ChatRun } from "@/lib/chat-runs";
import type { WidgetSize } from "@/lib/dashboard-layout";
import {
  $dashboardPeriod,
  $dashboardState,
  $dashboardStatus,
  refreshDashboardState,
  type DashboardQuota,
  type DashboardState,
  type QuotaWindow,
} from "@/lib/dashboard-state";
import widgetStyles from "./dashboard-widgets.css?raw";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

let root: Root | null = null;
let container: HTMLDivElement;
let served: DashboardState | Error;
let runs: ChatRun[] | Error;
const requests: string[] = [];
const resetCalls: string[] = [];
let resetReply: { status: number; body: unknown } | Error;

/** Настоящий транспорт `fetchJSON`: сводка и работы приходят ответом сервера. */
function serve() {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: unknown, init?: RequestInit) => {
      const text = String(url);
      requests.push(text);
      if (text.includes("/api/dashboard/codex-limit/reset")) {
        resetCalls.push(init?.method ?? "GET");
        if (resetReply instanceof Error) throw resetReply;
        return new Response(JSON.stringify(resetReply.body), {
          status: resetReply.status,
          headers: { "Content-Type": "application/json" },
        });
      }
      if (text.includes("/api/dashboard/state")) {
        if (served instanceof Error) throw served;
        return new Response(JSON.stringify(served), { headers: { "Content-Type": "application/json" } });
      }
      if (text.includes("/api/chat/runs")) {
        if (runs instanceof Error) throw runs;
        return new Response(JSON.stringify({ runs }), { headers: { "Content-Type": "application/json" } });
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

async function mount(widget: DashboardWidget, size?: WidgetSize, part: "Body" | "HeaderNote" = "Body") {
  const Body = (part === "Body" ? widget.Body : widget.HeaderNote)!;
  container = document.createElement("div");
  container.className = "korra-dashboard";
  document.body.appendChild(container);
  root = createRoot(container);
  await act(async () =>
    root!.render(
      <MemoryRouter initialEntries={["/dashboard"]}>
        <Body size={size} />
      </MemoryRouter>,
    ),
  );
  // Хранилище модульное и между тестами остаётся подключённым: сводку
  // запрашиваем явно, путь при этом настоящий — fetch → разбор → атомы.
  await act(async () => {
    await refreshDashboardState();
  });
  await flush();
}

function text(): string {
  return container.textContent ?? "";
}

function links(): string[] {
  return Array.from(container.querySelectorAll("a")).map((node) => node.getAttribute("href") ?? "");
}

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(FIXTURE_NOW * 1000);
  requests.length = 0;
  resetCalls.length = 0;
  resetReply = { status: 200, body: {} };
  served = dashboardStateFixture();
  runs = [];
  $dashboardState.set(null);
  $dashboardStatus.set("idle");
  $dashboardPeriod.set("week");
  $chatRuns.set([]);
  $chatRunsReachable.set(true);
  $chatRunsUpdatedAt.set(Date.now());
  serve();
});

afterEach(async () => {
  if (root) await act(async () => root!.unmount());
  root = null;
  container?.remove();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe("«Требует внимания» на живой сводке", () => {
  it("первыми идут решения, и каждая строка ведёт в конкретную задачу или чат", async () => {
    // Поток работ отдаёт сервер: разговор, где агент ждёт решения.
    runs = [
      {
        message_id: "d1",
        session_id: "s-9",
        profile: "analyst",
        status: "waiting_decision",
        updated_at: FIXTURE_NOW - 30,
        history_count: 1,
        title: "Отправить письмо клиенту",
        user_message: { role: "user", content: "Ожидает вашего решения" },
      },
    ];
    $chatRuns.set(runs);
    await mount(ATTENTION_WIDGET);

    const rows = Array.from(container.querySelectorAll<HTMLElement>("[data-attention-kind]"));
    expect(rows.map((row) => row.dataset.attentionKind)).toEqual([
      "chat_decision",
      "kanban_question",
      "delivery",
    ]);
    expect(rows[0].textContent).toContain("Аналитик ждёт вашего решения");
    expect(rows[0].textContent).toContain("Отправить письмо клиенту");
    expect(links()).toEqual([
      "/agents?agent=analyst&resume=s-9",
      "/kanban?board=default&task=t1",
      "/agents?agent=default&resume=tg-1",
    ]);
    expect(text()).toContain("Вопрос: Какой бюджет?");
    expect(container.querySelector("[data-attention-calm]")).toBeNull();
  });

  it("говорит «решений не ждут» только когда все источники прочитаны и пусты", async () => {
    served = dashboardStateFixture({
      attention: { status: "ok", items: [], count: 0, errors: [], checked: ["kanban", "cron"] },
    });
    await mount(ATTENTION_WIDGET);
    expect(container.querySelector("[data-attention-calm]")).not.toBeNull();
    expect(text()).toContain("Решений от вас не ждут");
  });

  it("непрочитанный источник называется словами, а не выдаётся за «всё спокойно»", async () => {
    served = dashboardStateFixture({
      attention: {
        status: "ok",
        items: [],
        count: 0,
        errors: [{ source: "kanban", label: "канбан" }],
        checked: ["delivery"],
      },
    });
    $chatRunsReachable.set(false);
    await mount(ATTENTION_WIDGET);

    expect(container.querySelector("[data-attention-calm]")).toBeNull();
    const alert = container.querySelector("[data-attention-unverified]");
    expect(alert?.getAttribute("role")).toBe("alert");
    expect(alert?.textContent).toContain("канбан");
    expect(alert?.textContent).toContain("чаты агентов");
  });

  it("длинный список сворачивается до трёх строк и раскрывается по нажатию", async () => {
    const base = dashboardStateFixture();
    const attention = base.attention as Extract<DashboardState["attention"], { items: unknown }>;
    const many = Array.from({ length: 5 }, (_, index) => ({
      ...attention.items[1],
      id: `delivery:${index}`,
      title: `Ответ ${index} не дошёл`,
    }));
    served = dashboardStateFixture({ attention: { ...attention, items: many, count: 5 } });
    await mount(ATTENTION_WIDGET);

    expect(container.querySelectorAll("[data-attention-kind]")).toHaveLength(3);
    const more = Array.from(container.querySelectorAll("button")).find((node) => node.textContent === "Ещё 2");
    await act(async () => more!.click());
    expect(container.querySelectorAll("[data-attention-kind]")).toHaveLength(5);
  });

  it("«Ещё N» считает и то, что сервер не прислал, и честно говорит о первой странице", async () => {
    const base = dashboardStateFixture();
    const attention = base.attention as Extract<DashboardState["attention"], { items: unknown }>;
    const page = Array.from({ length: 20 }, (_, index) => ({
      ...attention.items[1],
      id: `delivery:${index}`,
      title: `Ответ ${index} не дошёл`,
    }));
    served = dashboardStateFixture({ attention: { ...attention, items: page, count: 45, truncated: true } });
    await mount(ATTENTION_WIDGET);

    expect(container.querySelectorAll("[data-attention-kind]")).toHaveLength(3);
    // 17 полученных строк не видно, ещё 25 сервер не прислал.
    const more = Array.from(container.querySelectorAll("button")).find((node) => node.textContent === "Ещё 42");
    expect(more).toBeTruthy();
    await act(async () => more!.click());
    expect(container.querySelectorAll("[data-attention-kind]")).toHaveLength(20);
    expect(container.querySelector("[data-attention-truncated]")?.textContent).toBe("Показаны первые 20 из 45");
  });

  describe("сбой обновления после удачной сводки", () => {
    async function failNextRefresh() {
      served = new Error("offline");
      await act(async () => {
        await refreshDashboardState();
      });
      await flush();
    }

    it("пустая сводка не остаётся «решений не ждут»: последнее известное с пометкой и повтором", async () => {
      served = dashboardStateFixture({
        attention: { status: "ok", items: [], count: 0, errors: [], checked: ["kanban", "cron"] },
      });
      await mount(ATTENTION_WIDGET);
      expect(container.querySelector("[data-attention-calm]")).not.toBeNull();

      await failNextRefresh();
      expect($dashboardStatus.get()).toBe("error");
      expect(container.querySelector("[data-attention-calm]")).toBeNull();
      expect(text()).not.toContain("Решений от вас не ждут");
      const stale = container.querySelector("[data-attention-stale]");
      expect(stale?.textContent).toContain("При последней проверке в 13:00 решений от вас не ждали");
      const alert = container.querySelector("[data-attention-unverified]");
      expect(alert?.textContent).toContain("Не удалось обновить");
      const retry = Array.from(alert!.querySelectorAll("button")).find((node) =>
        node.textContent?.includes("Повторить"),
      );
      expect(retry).toBeTruthy();

      // Повтор удался — снова честное «спокойно».
      served = dashboardStateFixture({
        attention: { status: "ok", items: [], count: 0, errors: [], checked: ["kanban", "cron"] },
      });
      await act(async () => retry!.click());
      await flush();
      expect(container.querySelector("[data-attention-calm]")).not.toBeNull();
    });

    it("строки остаются, но помечены как последнее известное", async () => {
      await mount(ATTENTION_WIDGET);
      await failNextRefresh();
      expect(container.querySelectorAll("[data-attention-kind]")).toHaveLength(2);
      expect(container.querySelector("[data-attention-stale]")?.textContent).toContain(
        "Показано на момент последней проверки в 13:00",
      );
      expect(container.querySelector("[data-attention-unverified]")?.textContent).toContain("Не удалось обновить");
    });
  });
});

describe("«Мои показатели»", () => {
  it("крупное число, динамика к прошлой неделе и столбики по дням", async () => {
    await mount(METRICS_WIDGET, "m");
    expect(container.querySelector(".kdw-metric-value")?.textContent).toBe("12");
    expect(text()).toContain("диалогов за неделю");
    expect(text()).toContain("+50 %");
    expect(container.querySelectorAll(".kdw-bar")).toHaveLength(7);
    expect(container.querySelector('[role="img"]')?.getAttribute("aria-label")).toContain("1, 2, 0, 1, 3, 2, 3");
  });

  it("период переключается и уходит на сервер", async () => {
    await mount(METRICS_WIDGET, "l");
    const month = Array.from(container.querySelectorAll("button")).find((node) => node.textContent === "Месяц");
    expect(month?.getAttribute("aria-pressed")).toBe("false");
    await act(async () => month!.click());
    await flush();
    expect(requests.some((url) => url.includes("/api/dashboard/state?period=month"))).toBe(true);
    // На L рядом — с чем сравниваем (период, который вернул сервер),
    // сообщения, фоновые запуски и токены.
    expect(text()).toContain("к прошлой неделе");
    expect(text()).toContain("Сообщения");
    expect(text()).toContain("46");
    expect(text()).toContain("32 тыс.");
  });

  it("пустой период — первый шаг, а не ноль", async () => {
    const base = dashboardStateFixture();
    served = dashboardStateFixture({
      metrics: { ...(base.metrics as Extract<DashboardState["metrics"], { totals: unknown }>), status: "empty" },
    });
    await mount(METRICS_WIDGET, "m");
    expect(text()).toContain("Диалогов за неделю пока нет");
    expect(links()).toContain("/agents");
    expect(container.querySelector(".kdw-metric-value")).toBeNull();
  });

  it("компактный размер — число и динамика без графика", async () => {
    await mount(METRICS_WIDGET, "s");
    expect(container.querySelector(".kdw-metric-value")?.textContent).toBe("12");
    expect(text()).toContain("+50 %");
    expect(container.querySelector(".kdw-bar")).toBeNull();
  });
});

describe("«Ближайшие задачи» — лента дня", () => {
  it("показывает, что уже отработало и что будет дальше, в поясе установки", async () => {
    await mount(UPCOMING_TASKS_WIDGET, "m");
    const rows = Array.from(container.querySelectorAll<HTMLElement>(".kdw-event"));
    expect(rows.map((row) => row.querySelector(".kdw-event-time")?.textContent)).toEqual([
      "09:00",
      "13:30",
      "18:00",
    ]);
    expect(rows[0].dataset.eventState).toBe("done");
    expect(rows[1].classList.contains("kdw-event--next")).toBe(true);
    expect(rows[1].textContent).toContain("через 30 мин, ещё 10 сегодня");
  });

  it("компактный размер — ближайший запуск", async () => {
    await mount(UPCOMING_TASKS_WIDGET, "s");
    expect(container.querySelector(".kdw-next-time")?.textContent).toBe("13:30");
    expect(text()).toContain("Проверка почты");
  });

  it("на L видно неделю", async () => {
    await mount(UPCOMING_TASKS_WIDGET, "l");
    expect(container.querySelectorAll(".kdw-week li")).toHaveLength(7);
  });

  it("все расписания на паузе — так и сказано, а не «запусков нет»", async () => {
    const base = dashboardStateFixture();
    served = dashboardStateFixture({
      upcoming: {
        ...(base.upcoming as Extract<DashboardState["upcoming"], { events: unknown }>),
        events: [],
        next_later: null,
        jobs_total: 11,
        jobs_active: 0,
      },
    });
    await mount(UPCOMING_TASKS_WIDGET, "m");
    expect(container.querySelector("[data-upcoming-paused]")?.textContent).toContain("Все расписания на паузе");
    expect(text()).toContain("Задач: 11");
  });

  it("без расписаний — понятный первый шаг", async () => {
    const base = dashboardStateFixture();
    served = dashboardStateFixture({
      upcoming: {
        ...(base.upcoming as Extract<DashboardState["upcoming"], { events: unknown }>),
        status: "empty",
        events: [],
        jobs_total: 0,
        jobs_active: 0,
      },
    });
    await mount(UPCOMING_TASKS_WIDGET, "m");
    expect(text()).toContain("Расписаний пока нет");
    expect(links()).toContain("/cron");
  });
});

describe("«Готовые файлы»", () => {
  it("обложки несут настоящее имя и начало файла и ведут к файлу на его месте", async () => {
    await mount(RECENT_RESULTS_WIDGET, "m");
    const tiles = Array.from(container.querySelectorAll<HTMLElement>(".kdw-artifact"));
    expect(tiles.map((tile) => tile.querySelector(".kdw-artifact-name")?.textContent)).toEqual([
      "Отчёт.md",
      "Прайс.xlsx",
      "План.pptx",
    ]);
    // Заголовок обложки — настоящий заголовок файла.
    expect(tiles[0].textContent).toContain("Итоги недели");
    expect(tiles[0].textContent).toContain("Текст · сегодня в 12:59");
    expect(links()[0]).toBe(
      `/files?${new URLSearchParams({
        path: "/opt/data/workspace/agents/0123456789abcdef/results",
        highlight: "Отчёт.md",
      })}`,
    );
    expect(container.querySelector('[aria-label="Скачать «Отчёт.md»"]')).not.toBeNull();
    expect(text()).toContain("3 новых файла за неделю");
  });

  it("на L главная обложка несёт первые строки файла и автора", async () => {
    await mount(RECENT_RESULTS_WIDGET, "l");
    const main = container.querySelector<HTMLElement>(".kdw-artifact--main")!;
    expect(main.textContent).toContain("Выручка выросла");
    expect(main.textContent).toContain("Новых клиентов: 3");
    expect(main.textContent).toContain("Автор: Аналитик");
  });

  it("компактный размер — одна обложка", async () => {
    await mount(RECENT_RESULTS_WIDGET, "s");
    expect(container.querySelectorAll(".kdw-artifact")).toHaveLength(1);
  });

  it("без файлов — первый шаг с поручением агенту", async () => {
    served = dashboardStateFixture({
      artifacts: { status: "empty", items: [], recent_count: 0, organized: true, root: "/w", truncated: false },
    });
    await mount(RECENT_RESULTS_WIDGET, "m");
    expect(text()).toContain("Готовых файлов пока нет");
    expect(links()).toContain("/agents");
  });

  it("сбой источника — ошибка с повтором", async () => {
    served = dashboardStateFixture({ artifacts: { status: "error" } });
    await mount(RECENT_RESULTS_WIDGET, "m");
    expect(text()).toContain("Не удалось загрузить файлы");
    expect(container.querySelector('[role="alert"]')).not.toBeNull();
  });
});

describe("«Лимит Codex»", () => {
  const WEEK = 10080;
  const RESETS_AT = Date.UTC(2026, 8, 30, 11, 0) / 1000; // ср, 30 сент. 14:00 по Москве

  function win(overrides: Partial<QuotaWindow> = {}): QuotaWindow {
    return {
      key: "primary",
      used_percent: 36,
      remaining_percent: 64,
      window_minutes: WEEK,
      label: "неделя",
      resets_at: RESETS_AT,
      renewed: false,
      forecast: null,
      level: "normal",
      ...overrides,
    };
  }

  function quotaOf(windows: QuotaWindow[], overrides: Partial<DashboardQuota> = {}): DashboardQuota {
    const headline = windows.reduce((a, b) => (b.remaining_percent < a.remaining_percent ? b : a));
    return {
      available: true,
      status: "ok",
      level: headline.level,
      windows,
      used_percent: headline.used_percent,
      window_minutes: headline.window_minutes,
      window_label: headline.label,
      resets_at: headline.resets_at,
      plan_type: "pro",
      captured_at: FIXTURE_NOW - 60,
      stale: false,
      limit_reached: false,
      reset_credits: { available: 2, applicable: 0 },
      can_reset: false,
      forecast: null,
      ...overrides,
    };
  }

  function serveQuota(quota: DashboardQuota) {
    served = dashboardStateFixture({ quota });
  }

  const meters = () => Array.from(container.querySelectorAll<HTMLElement>('[role="meter"]'));
  const dialog = () => document.body.querySelector<HTMLElement>('[role="dialog"]');
  const buttonByText = (scope: ParentNode, label: string) =>
    Array.from(scope.querySelectorAll("button")).find((node) => node.textContent?.trim() === label);

  async function press(node: Element | undefined) {
    expect(node).toBeDefined();
    await act(async () => {
      node!.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
  }

  it("сводка из фикстуры: оставшаяся доля, сброс, запас и тариф", async () => {
    await mount(CODEX_QUOTA_WIDGET, "m");
    expect(text()).toContain("Неделя");
    expect(text()).toContain("64 % осталось");
    expect(text()).toMatch(/Сброс пт, 25 сент\.? в 14:00 · через 2 дн\. 1 ч/);
    expect(text()).toContain("В запасе 2 сброса");
    expect(text()).toContain("Тариф Pro");
    expect(container.querySelector("#codex-quota")).not.toBeNull();
    expect(CODEX_QUOTA_WIDGET.title).toBe("Лимит Codex");
    expect(CODEX_QUOTA_WIDGET.id).toBe("codex-quota");
  });

  it("полный лимит: линия залита целиком, прогноза нет", async () => {
    serveQuota(
      quotaOf([win({ used_percent: 0, remaining_percent: 100 })], { reset_credits: { available: 1, applicable: 0 } }),
    );
    await mount(CODEX_QUOTA_WIDGET, "m");
    const [meter] = meters();
    expect(meters()).toHaveLength(1);
    expect(meter.getAttribute("aria-valuenow")).toBe("100");
    expect(meter.getAttribute("aria-valuetext")).toBe("осталось 100 %");
    expect(meter.querySelector("i")?.style.width).toBe("100%");
    expect(text()).toContain("100 % осталось");
    expect(text()).toMatch(/Сброс ср, 30 сент\.? в 14:00 · через 7 дн\. 1 ч/);
    expect(text()).not.toContain("Тратится");
    expect(text()).not.toContain("Темп спокойный");
    expect(text()).toContain("В запасе 1 сброс");
    expect(text()).not.toContain("1 сбросов");
  });

  it("окно, обновившееся без ответа агента, показано полным лимитом без ожидания", async () => {
    serveQuota(quotaOf([win({ used_percent: 0, remaining_percent: 100, renewed: true })]));
    await mount(CODEX_QUOTA_WIDGET, "m");
    expect(meters()[0].getAttribute("aria-valuenow")).toBe("100");
    expect(text()).not.toContain("Окно квоты обновилось");
    expect(text()).not.toContain("Ждём");
  });

  it("быстрый темп: во сколько раз, когда кончится и насколько раньше сброса", async () => {
    const exhaustsAt = RESETS_AT - 60 * 3600;
    serveQuota(
      quotaOf([
        win({
          level: "warn",
          forecast: { pace: 1.5, exhausts_at: exhaustsAt, exhausts_before_reset: true },
        }),
      ]),
    );
    await mount(CODEX_QUOTA_WIDGET, "m");
    expect(text()).toContain("Тратится в 1,5 раза быстрее ровного темпа.");
    expect(text()).toMatch(/Так лимит кончится в пн, 28 сент\.? около 02:00 — за 2,5 дня до сброса\./);
    expect(container.querySelector("[data-quota-level]")?.getAttribute("data-quota-level")).toBe("warn");
    expect(container.querySelector(".kdw-limit-bar")?.getAttribute("data-level")).toBe("warn");
  });

  it("спокойный темп: одна короткая строка", async () => {
    serveQuota(
      quotaOf([
        win({ forecast: { pace: 0.7, exhausts_at: RESETS_AT + 86_400, exhausts_before_reset: false } }),
      ]),
    );
    await mount(CODEX_QUOTA_WIDGET, "m");
    expect(text()).toContain("Темп спокойный — хватит до сброса.");
    expect(text()).not.toContain("Тратится");
  });

  it("лимит исчерпан: кнопка сброса, подтверждение, один запрос и новое состояние", async () => {
    serveQuota(
      quotaOf(
        [win({ used_percent: 100, remaining_percent: 0, level: "critical" })],
        { limit_reached: true, can_reset: true, reset_credits: { available: 2, applicable: 1 } },
      ),
    );
    const fresh = quotaOf([win({ used_percent: 0, remaining_percent: 100 })], {
      reset_credits: { available: 1, applicable: 0 },
    });
    resetReply = {
      status: 200,
      body: { ok: true, status: "reset", message: "Лимит сброшен — снова полный. В запасе остался 1 сброс.", quota: fresh },
    };
    await mount(CODEX_QUOTA_WIDGET, "m");
    expect(text()).toContain("Лимит исчерпан");
    expect(text()).toMatch(/Агенты не ответят до сброса: ср, 30 сент\.? в 14:00 · через 7 дн\. 1 ч/);
    expect(text()).toContain("0 % осталось");
    expect(meters()[0].getAttribute("aria-valuenow")).toBe("0");
    expect(dialog()).toBeNull();

    await press(buttonByText(container, "Сбросить лимит"));
    expect(dialog()?.textContent).toContain("Сбросить лимит Codex?");
    expect(dialog()?.textContent).toContain(
      "Запасной сброс сразу вернёт полный лимит. В запасе останется 1. Отменить нельзя.",
    );
    expect(resetCalls).toEqual([]);

    await press(buttonByText(dialog()!, "Отмена"));
    expect(dialog()).toBeNull();
    expect(resetCalls).toEqual([]);

    await press(buttonByText(container, "Сбросить лимит"));
    // После сброса сервер уже отдаёт полный лимит — и в ответе, и на следующем опросе.
    served = dashboardStateFixture({ quota: fresh });
    // Двойной щелчок по «Сбросить» не отправляет второй запрос.
    const confirm = buttonByText(dialog()!, "Сбросить")!;
    await act(async () => {
      confirm.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      confirm.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    await flush();
    expect(resetCalls).toEqual(["POST"]);
    expect(dialog()).toBeNull();
    expect(text()).toContain("100 % осталось");
    expect(text()).toContain("Лимит сброшен — снова полный.");
    expect(buttonByText(container, "Сбросить лимит")).toBeUndefined();
    expect(text()).not.toContain("Лимит исчерпан");
  });

  it("отказ сервера: показывает русское сообщение, лимит остаётся исчерпанным", async () => {
    serveQuota(
      quotaOf([win({ used_percent: 100, remaining_percent: 0, level: "critical" })], {
        limit_reached: true,
        can_reset: true,
        reset_credits: { available: 1, applicable: 1 },
      }),
    );
    resetReply = {
      status: 200,
      body: {
        ok: false,
        status: "unavailable",
        message: "Не удалось применить сброс: Codex не ответил или вход устарел. Попробуйте позже.",
        quota: null,
      },
    };
    await mount(CODEX_QUOTA_WIDGET, "m");
    await press(buttonByText(container, "Сбросить лимит"));
    await press(buttonByText(dialog()!, "Сбросить"));
    await flush();
    expect(text()).toContain("Не удалось применить сброс");
    expect(text()).toContain("Лимит исчерпан");
    expect(buttonByText(container, "Сбросить лимит")).toBeDefined();
  });

  it("сбой сети при сбросе не ломает карточку", async () => {
    serveQuota(
      quotaOf([win({ used_percent: 100, remaining_percent: 0, level: "critical" })], {
        limit_reached: true,
        can_reset: true,
        reset_credits: { available: 1, applicable: 1 },
      }),
    );
    resetReply = new Error("offline");
    await mount(CODEX_QUOTA_WIDGET, "m");
    await press(buttonByText(container, "Сбросить лимит"));
    await press(buttonByText(dialog()!, "Сбросить"));
    await flush();
    expect(text()).toContain("Не удалось связаться с панелью");
    expect(buttonByText(container, "Сбросить лимит")).toBeDefined();
  });

  it("кнопки сброса нет, пока сервер её не разрешил", async () => {
    serveQuota(quotaOf([win()], { can_reset: false, reset_credits: { available: 2, applicable: 0 } }));
    await mount(CODEX_QUOTA_WIDGET, "l");
    expect(buttonByText(container, "Сбросить лимит")).toBeUndefined();
    expect(text()).toContain("В запасе 2 сброса");
  });

  it("два окна: две тонкие линии со своими сбросами и без лишнего баланса кредитов", async () => {
    serveQuota(
      quotaOf([
        win({
          key: "primary",
          window_minutes: 300,
          label: "5 ч",
          used_percent: 82,
          remaining_percent: 18,
          level: "warn",
          resets_at: FIXTURE_NOW + 3 * 3600,
        }),
        win({ key: "secondary", used_percent: 29, remaining_percent: 71 }),
      ]),
    );
    await mount(CODEX_QUOTA_WIDGET, "l");
    expect(meters().map((meter) => meter.getAttribute("aria-valuenow"))).toEqual(["18", "71"]);
    expect(container.querySelectorAll(".kdw-limit--thin")).toHaveLength(2);
    expect(text()).toContain("5 часов");
    expect(text()).toContain("Неделя");
    expect(text()).toContain("18 % осталось");
    expect(text()).toContain("71 % осталось");
    expect(text()).toMatch(/Сброс сегодня в 16:00 · через 3 ч/);
    expect(text()).not.toMatch(/кредит|баланс/i);
  });

  it("размер S: проценты, линия, короткий сброс и короткий прогноз", async () => {
    serveQuota(
      quotaOf([
        win({
          level: "warn",
          forecast: { pace: 1.5, exhausts_at: RESETS_AT - 60 * 3600, exhausts_before_reset: true },
        }),
      ]),
    );
    await mount(CODEX_QUOTA_WIDGET, "s");
    expect(text()).toContain("64 % осталось");
    expect(meters()).toHaveLength(1);
    expect(text()).toContain("сброс ср в 14:00");
    expect(text()).toContain("при таком темпе кончится в пн");
    expect(text()).not.toContain("Тариф");
    expect(text()).not.toContain("В запасе");
  });

  it("шапка: свежесть данных, а старые данные заметны и на узком экране", async () => {
    await mount(CODEX_QUOTA_WIDGET, "m", "HeaderNote");
    expect(text()).toBe("обновлено 5 мин назад");
    expect(container.querySelector(".kdw-quota-updated--stale")).toBeNull();
  });

  it("шапка: данные старше 30 минут — «данные от …» цветом предупреждения даже в S", async () => {
    serveQuota(quotaOf([win()], { captured_at: FIXTURE_NOW - 3 * 3600 }));
    await mount(CODEX_QUOTA_WIDGET, "s", "HeaderNote");
    expect(text()).toBe("данные от 10:00");
    expect(container.querySelector(".kdw-quota-updated--stale")).not.toBeNull();
    // Подпись не прячется ни на одном размере полотна, ни в контейнерном запросе.
    const hidden = /display:\s*none/;
    for (const block of widgetStyles.split("}")) {
      if (block.includes("kdw-quota-updated")) expect(block).not.toMatch(hidden);
    }
  });

  it("шапка: на 30-й минуте данные ещё свежие, на 31-й уже нет", async () => {
    serveQuota(quotaOf([win()], { captured_at: FIXTURE_NOW - 29 * 60 }));
    await mount(CODEX_QUOTA_WIDGET, "m", "HeaderNote");
    expect(text()).toBe("обновлено 29 мин назад");
    await act(async () => root!.unmount());
    root = null;
    container.remove();
    serveQuota(quotaOf([win()], { captured_at: FIXTURE_NOW - 31 * 60 }));
    await mount(CODEX_QUOTA_WIDGET, "m", "HeaderNote");
    expect(text()).toMatch(/^данные от /);
  });

  it("до первого ответа честно ждёт, а не показывает ноль", async () => {
    served = dashboardStateFixture({ quota: { available: true, status: "waiting" } });
    await mount(CODEX_QUOTA_WIDGET, "m");
    expect(text()).toContain("Лимит пока не известен");
    expect(text()).not.toContain("0 %");
    expect(meters()).toHaveLength(0);
  });

  it("доступна только там, где подключена подписка", () => {
    expect(CODEX_QUOTA_WIDGET.isAvailable?.(dashboardStateFixture())).toBe(true);
    expect(
      CODEX_QUOTA_WIDGET.isAvailable?.(dashboardStateFixture({ quota: { available: false, status: "absent" } })),
    ).toBe(false);
    expect(CODEX_QUOTA_WIDGET.isAvailable?.(null)).toBe(false);
  });

  it("без подписки не обещает ответа Codex и не показывает процент", async () => {
    served = dashboardStateFixture({ quota: { available: false, status: "absent" } });
    await mount(CODEX_QUOTA_WIDGET, "m");
    expect(text()).toContain("Подписка ChatGPT не подключена");
    expect(text()).not.toContain("Ждём первого ответа");
    expect(text()).not.toContain("%");
  });
});

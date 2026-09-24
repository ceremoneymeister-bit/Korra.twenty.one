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
  type DashboardState,
} from "@/lib/dashboard-state";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

let root: Root | null = null;
let container: HTMLDivElement;
let served: DashboardState | Error;
let runs: ChatRun[] | Error;
const requests: string[] = [];

/** Настоящий транспорт `fetchJSON`: сводка и работы приходят ответом сервера. */
function serve() {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: unknown) => {
      const text = String(url);
      requests.push(text);
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

async function mount(widget: DashboardWidget, size?: WidgetSize) {
  const { Body } = widget;
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

describe("«Артефакты»", () => {
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
    expect(text()).toContain("Не удалось прочитать файлы");
    expect(container.querySelector('[role="alert"]')).not.toBeNull();
  });
});

describe("«Квота Codex»", () => {
  it("процент, окно и время сброса", async () => {
    await mount(CODEX_QUOTA_WIDGET, "m");
    expect(text()).toContain("Израсходовано 62 % · неделя");
    expect(text()).toMatch(/Сброс пт, 25 сент\.? в 14:00/);
    expect(container.querySelector("[data-quota-level]")?.getAttribute("data-quota-level")).toBe("normal");
    expect(container.querySelector(".kdw-quota-warning")).toBeNull();
    expect(container.querySelector("#codex-quota")).not.toBeNull();
  });

  it.each([
    [85, "warn", "status"],
    [97, "critical", "alert"],
  ] as const)("при %i %% предупреждает заранее (%s)", async (used, level, role) => {
    const base = dashboardStateFixture();
    served = dashboardStateFixture({ quota: { ...base.quota, used_percent: used, level } });
    await mount(CODEX_QUOTA_WIDGET, "l");
    expect(container.querySelector(".kdw-quota-warning")?.getAttribute("role")).toBe(role);
  });

  it("до первого ответа честно ждёт, а не показывает ноль", async () => {
    served = dashboardStateFixture({ quota: { available: true, status: "waiting" } });
    await mount(CODEX_QUOTA_WIDGET, "m");
    expect(text()).toContain("Ждём первого ответа Codex");
    expect(text()).not.toContain("0 %");
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

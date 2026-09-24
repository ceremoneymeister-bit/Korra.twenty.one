import { describe, expect, it } from "vitest";

import type { ChatRun } from "@/lib/chat-runs";
import {
  attentionView,
  formatCompact,
  formatDuration,
  formatMoment,
  formatRelative,
  metricDelta,
  nextEvent,
  plural,
  timelineWindow,
  type AttentionItem,
  type DashboardAttention,
  type DashboardEvent,
} from "@/lib/dashboard-state";

const NOW = Date.UTC(2026, 8, 23, 10, 0) / 1000; // 13:00 в Москве
const MSK = "Europe/Moscow";

describe("динамика показателя", () => {
  it("сравнивает с прошлым периодом и не делит на ноль", () => {
    expect(metricDelta(12, 8)).toEqual({ direction: "up", percent: 50 });
    expect(metricDelta(6, 8)).toEqual({ direction: "down", percent: 25 });
    expect(metricDelta(8, 8)).toEqual({ direction: "flat", percent: 0 });
    expect(metricDelta(3, 0)).toEqual({ direction: "new", percent: null });
    expect(metricDelta(0, 0)).toEqual({ direction: "flat", percent: 0 });
  });

  it("крупные числа сокращаются по-русски, мелкие остаются точными", () => {
    expect(formatCompact(950)).toBe("950");
    expect(formatCompact(12_400)).toBe("12,4 тыс.");
    expect(formatCompact(3_200_000)).toBe("3,2 млн");
  });

  it("склоняет слова после числа", () => {
    const forms = ["диалог", "диалога", "диалогов"] as const;
    expect([1, 2, 5, 11, 21, 22, 25].map((n) => plural(n, forms))).toEqual([
      "диалог",
      "диалога",
      "диалогов",
      "диалогов",
      "диалог",
      "диалога",
      "диалогов",
    ]);
  });
});

describe("время", () => {
  it("называет день словами в поясе установки", () => {
    expect(formatMoment(Date.UTC(2026, 8, 23, 15, 0) / 1000, NOW, MSK)).toBe("сегодня в 18:00");
    expect(formatMoment(Date.UTC(2026, 8, 24, 6, 0) / 1000, NOW, MSK)).toBe("завтра в 09:00");
    // 23:30 UTC 23.09 — это уже 24.09 в Москве.
    expect(formatMoment(Date.UTC(2026, 8, 23, 23, 30) / 1000, NOW, MSK)).toBe("завтра в 02:30");
  });

  it("относительное время и длительности", () => {
    expect(formatRelative(NOW + 19 * 60, NOW)).toBe("через 19 мин");
    expect(formatRelative(NOW - 5 * 60, NOW)).toBe("5 мин назад");
    expect(formatRelative(NOW + 3 * 86_400, NOW)).toBe("через 3 дня");
    expect(formatDuration(2 * 86_400 + 4 * 3600)).toBe("2 дн. 4 ч");
    expect(formatDuration(3 * 3600 + 10 * 60)).toBe("3 ч 10 мин");
  });
});

function event(id: string, hour: number, state: DashboardEvent["state"]): DashboardEvent {
  return {
    id,
    job_id: id,
    title: id,
    schedule: null,
    profile: "",
    agent: "Корра",
    at: Date.UTC(2026, 8, 23, hour - 3) / 1000,
    state,
    href: "/cron",
  };
}

describe("окно ленты дня", () => {
  const events = [
    event("early", 7, "done"),
    event("morning", 9, "done"),
    event("noon", 12, "failed"),
    event("afternoon", 15, "planned"),
    event("evening", 18, "planned"),
    event("night", 22, "planned"),
  ];

  it("последнее случившееся и ближайшие запланированные", () => {
    expect(timelineWindow(events, NOW, 3).map((e) => e.id)).toEqual(["noon", "afternoon", "evening"]);
    expect(nextEvent(events, NOW)?.id).toBe("afternoon");
  });

  it("вечером добирает прошедшими, а не оставляет пустые строки", () => {
    const late = Date.UTC(2026, 8, 23, 20, 0) / 1000; // 23:00 в Москве
    const done = events.map((item) => ({ ...item, state: "done" as const }));
    expect(timelineWindow(done, late, 3).map((e) => e.id)).toEqual(["afternoon", "evening", "night"]);
    expect(nextEvent(done, late)).toBeNull();
    expect(timelineWindow(events, NOW, 0)).toEqual([]);
  });
});

function run(over: Partial<ChatRun>): ChatRun {
  return {
    message_id: "m",
    session_id: "s",
    profile: "",
    status: "running",
    updated_at: NOW,
    history_count: 0,
    user_message: { role: "user", content: "" },
    ...over,
  };
}

const ITEM: AttentionItem = {
  id: "cron:1",
  source: "cron",
  kind: "cron_incident",
  severity: "problem",
  title: "Задача «Сводка» не выполнилась",
  detail: "Нет доступа к модели или сервису.",
  agent: "Корра",
  profile: "",
  href: "/cron",
  action: "Открыть задачи",
  at: NOW - 100,
};

const SECTION: DashboardAttention = { status: "ok", items: [ITEM], count: 1, errors: [], checked: ["cron"] };

const chat = (runs: ChatRun[], known = true, reachable: boolean | null = true) => ({
  runs,
  known,
  reachable,
  labelFor: (profile: string) => (profile ? profile.toUpperCase() : "Корра"),
  hrefFor: (profile: string, sessionId: string | null) => `/agents?agent=${profile || "default"}&resume=${sessionId}`,
});

describe("полоса «Требует внимания»", () => {
  it("решение в чате идёт первым, по одной строке на разговор", () => {
    const view = attentionView(SECTION, "ready", chat([
      run({ message_id: "a", session_id: "s1", profile: "sales", status: "waiting_decision" }),
      run({ message_id: "b", session_id: "s1", profile: "sales", status: "waiting_decision" }),
      run({ message_id: "c", session_id: "s2", status: "running" }),
    ]));
    expect(view.rows.map((row) => row.kind)).toEqual(["chat_decision", "cron_incident"]);
    expect(view.rows[0].title).toBe("SALES ждёт вашего решения");
    expect(view.rows[0].href).toBe("/agents?agent=sales&resume=s1");
    expect(view.calm).toBe(false);
  });

  it("«всё спокойно» — только когда все источники ответили", () => {
    const empty = { ...SECTION, items: [], count: 0 };
    expect(attentionView(empty, "ready", chat([])).calm).toBe(true);
    // Сводка ещё идёт — это не «спокойно», но и не сбой.
    const pending = attentionView(null, "pending", chat([]));
    expect(pending.calm).toBe(false);
    expect(pending.unverified).toEqual([]);
    // Поток работ ещё ни разу не ответил и недоступен — чаты не проверены.
    expect(attentionView(empty, "ready", chat([], false, false)).unverified).toEqual(["чаты агентов"]);
  });

  it("сбой источников называется словами", () => {
    const broken = { ...SECTION, items: [], errors: [{ source: "kanban", label: "канбан" }] };
    expect(attentionView(broken, "ready", chat([])).unverified).toEqual(["канбан"]);
    expect(attentionView(null, "error", chat([])).unverified[0]).toContain("канбан");
  });

  it("последнее известное после сбоя обновления — не «всё спокойно»", () => {
    const empty = { ...SECTION, items: [], count: 0 };
    const stale = attentionView(empty, "stale", chat([]));
    expect(stale.calm).toBe(false);
    expect(stale.stale).toBe(true);
    expect(stale.unverified[0]).toContain("канбан");
    // Последняя удачная проверка была пустой и полной — это можно сказать,
    // но только как прошлое.
    expect(stale.lastKnownCalm).toBe(true);
    expect(attentionView({ ...empty, errors: [{ source: "cron", label: "расписание" }] }, "stale", chat([])).lastKnownCalm).toBe(false);
    // Строки последней сводки остаются на месте.
    const rows = attentionView(SECTION, "stale", chat([]));
    expect(rows.rows.map((row) => row.id)).toEqual(["cron:1"]);
    expect(rows.lastKnownCalm).toBe(false);
  });
});

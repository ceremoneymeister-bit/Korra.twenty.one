/**
 * Сводка дашборда для тестов — в той форме, которую отдаёт
 * `GET /api/dashboard/state` (см. `korra_cli/dashboard_state.py`).
 *
 * Время — среда 23.09.2026 13:00 по Москве; часовой пояс установки задан
 * явно, чтобы строки «сегодня в 18:00» не зависели от машины, где идёт тест.
 */

import type { CrmConnection, CrmType, SalesDeal, SalesReady } from "@/lib/crm";
import type { DashboardState } from "@/lib/dashboard-state";

export const FIXTURE_NOW = Date.UTC(2026, 8, 23, 10, 0) / 1000;

const at = (hour: number, minute = 0, day = 23) => Date.UTC(2026, 8, day, hour - 3, minute) / 1000;

export function dashboardStateFixture(overrides: Partial<DashboardState> = {}): DashboardState {
  return {
    version: 1,
    generated_at: FIXTURE_NOW,
    timezone: "Europe/Moscow",
    attention: {
      status: "ok",
      items: [
        {
          id: "kanban:default:t1:question",
          source: "kanban",
          kind: "kanban_question",
          severity: "action",
          title: "Смета для клиента",
          detail: "Вопрос: Какой бюджет?",
          agent: "Аналитик",
          profile: "analyst",
          href: "/kanban?board=default&task=t1",
          action: "Ответить",
          at: FIXTURE_NOW - 600,
        },
        {
          id: "delivery:default:o1",
          source: "delivery",
          kind: "delivery",
          severity: "problem",
          title: "Ответ не дошёл до Telegram",
          detail: "«Готов отчёт за неделю»",
          agent: "Корра",
          profile: "",
          href: "/agents?agent=default&resume=tg-1",
          action: "Открыть чат",
          at: FIXTURE_NOW - 900,
        },
      ],
      count: 2,
      errors: [],
      checked: ["kanban", "delivery", "cron", "provider", "updates"],
    },
    agents: {
      status: "ok",
      agents: [
        { profile: "", label: "Корра", last_active_at: at(12, 20) },
        { profile: "analyst", label: "Аналитик", last_active_at: at(9, 5) },
      ],
      unreadable: [],
    },
    metrics: {
      status: "ok",
      period: "week",
      days: 7,
      start: at(0, 0, 17),
      end: at(0, 0, 24),
      labels: ["2026-09-17", "2026-09-18", "2026-09-19", "2026-09-20", "2026-09-21", "2026-09-22", "2026-09-23"],
      series: {
        dialogs: [1, 2, 0, 1, 3, 2, 3],
        messages: [4, 9, 0, 3, 12, 8, 10],
        background: [2, 2, 2, 2, 2, 2, 1],
        tokens: [1000, 5000, 0, 2000, 9000, 7000, 8000],
      },
      totals: { dialogs: 12, messages: 46, background: 13, tokens: 32_000 },
      previous: { dialogs: 8, messages: 40, background: 14, tokens: 30_000 },
      unreadable: [],
    },
    upcoming: {
      status: "ok",
      now: FIXTURE_NOW,
      day_start: at(0),
      day_end: at(0, 0, 24),
      events: [
        {
          id: "default:morning:done",
          job_id: "morning",
          title: "Утренняя сводка",
          schedule: "0 9 * * *",
          profile: "",
          agent: "Корра",
          at: at(9),
          state: "done",
          href: "/cron",
        },
        {
          id: "analyst:mail:planned",
          job_id: "mail",
          title: "Проверка почты",
          schedule: "каждые 60 мин",
          profile: "analyst",
          agent: "Аналитик",
          at: at(13, 30),
          state: "planned",
          href: "/cron",
          repeats_today: 11,
        },
        {
          id: "default:evening:planned",
          job_id: "evening",
          title: "Итоги дня",
          schedule: "0 18 * * *",
          profile: "",
          agent: "Корра",
          at: at(18),
          state: "planned",
          href: "/cron",
        },
      ],
      next_later: null,
      week: [
        { date: "2026-09-23", planned: 12, done: 1, failed: 0 },
        { date: "2026-09-24", planned: 25, done: 0, failed: 0 },
        { date: "2026-09-25", planned: 25, done: 0, failed: 0 },
        { date: "2026-09-26", planned: 24, done: 0, failed: 0 },
        { date: "2026-09-27", planned: 24, done: 0, failed: 0 },
        { date: "2026-09-28", planned: 25, done: 0, failed: 0 },
        { date: "2026-09-29", planned: 25, done: 0, failed: 0 },
      ],
      jobs_total: 3,
      jobs_active: 3,
      unreadable: [],
    },
    artifacts: {
      status: "ok",
      items: [
        {
          path: "/opt/data/workspace/agents/0123456789abcdef/results/Отчёт.md",
          name: "Отчёт.md",
          folder: "/opt/data/workspace/agents/0123456789abcdef/results",
          ext: "md",
          kind: "text",
          size: 120,
          modified_at: FIXTURE_NOW - 60,
          section: "agent",
          profile: "analyst",
          agent: "Аналитик",
          title: "Итоги недели",
          excerpt: ["Выручка выросла", "Новых клиентов: 3"],
        },
        {
          path: "/opt/data/workspace/shared/Прайс.xlsx",
          name: "Прайс.xlsx",
          folder: "/opt/data/workspace/shared",
          ext: "xlsx",
          kind: "table",
          size: 4096,
          modified_at: FIXTURE_NOW - 7200,
          section: "shared",
          profile: null,
          agent: null,
        },
        {
          path: "/opt/data/workspace/План.pptx",
          name: "План.pptx",
          folder: "/opt/data/workspace",
          ext: "pptx",
          kind: "presentation",
          size: 90_000,
          modified_at: FIXTURE_NOW - 86_400 * 2,
          section: "workspace",
          profile: null,
          agent: null,
        },
      ],
      recent_count: 3,
      organized: true,
      root: "/opt/data/workspace",
      truncated: false,
    },
    quota: {
      available: true,
      status: "ok",
      level: "normal",
      used_percent: 36,
      window_minutes: 10080,
      window_label: "неделя",
      resets_at: at(14, 0, 25),
      windows: [
        {
          key: "primary",
          used_percent: 36,
          remaining_percent: 64,
          window_minutes: 10080,
          label: "неделя",
          resets_at: at(14, 0, 25),
          renewed: false,
          forecast: { pace: 0.51, exhausts_at: at(14, 0, 25) + 86_400 * 4, exhausts_before_reset: false },
          level: "normal",
        },
      ],
      plan_type: "pro",
      captured_at: FIXTURE_NOW - 300,
      stale: false,
      limit_reached: false,
      reset_credits: { available: 2, applicable: 0 },
      can_reset: false,
      forecast: null,
    },
    ...overrides,
  };
}

export function crmConnectionFixture(overrides: Partial<CrmConnection> = {}): CrmConnection {
  return {
    type: "bitrix24",
    source_label: "Битрикс24",
    portal: "acme.bitrix24.ru",
    route: "installation",
    account: {
      user: "Анна Петрова",
      deals: 128,
      deals_capped: false,
      managers: 6,
      tasks: true,
      pipelines: [
        { id: "0", name: "Продажи" },
        { id: "2", name: "Партнёры" },
      ],
    },
    settings: { pipeline_id: "0", stuck_days: 7, agents_access: true },
    last_check: { ok: true, at: "2026-09-23T09:55:00+00:00", code: null },
    saved_at: "2026-09-20T08:00:00+00:00",
    ...overrides,
  };
}

const deal = (id: string, title: string, amount: number, days: number, extra: Partial<SalesDeal> = {}): SalesDeal => ({
  id,
  title,
  amount,
  days,
  stuck: days >= 7,
  late: false,
  new: false,
  url: `https://acme.bitrix24.ru/crm/deal/details/${id}/`,
  manager: "Иван Орлов",
  stage: "Переговоры",
  ...extra,
});

/** Карточка «Продажи» с данными Битрикс24: сентябрь, 7 застрявших сделок. */
export function salesFixture(overrides: Partial<SalesReady> = {}, type: CrmType = "bitrix24"): SalesReady {
  const connection = crmConnectionFixture(
    type === "amocrm" ? { type, source_label: "amoCRM", portal: "acme.amocrm.ru" } : {},
  );
  const stuckTop = [
    deal("812", "Поставка для «Север»", 840_000, 21),
    deal("790", "Ремонт склада", 420_000, 15),
    deal("655", "Договор с «Дельта»", 300_000, 11),
  ];
  return {
    status: "ok",
    connection,
    source: type,
    source_label: connection.source_label,
    portal: connection.portal,
    pipeline: { id: "0", name: "Продажи" },
    stuck_days: 7,
    won: {
      amount: 2_840_000,
      count: 14,
      prev_amount: 2_400_000,
      prev_count: 11,
      change_pct: 18,
      weeks: [
        { start: "2026-09-01", amount: 600_000, count: 3 },
        { start: "2026-09-08", amount: 900_000, count: 4 },
        { start: "2026-09-15", amount: 700_000, count: 3 },
        { start: "2026-09-22", amount: 640_000, count: 4 },
      ],
      limited: false,
    },
    new_leads: { today: 6, series: [2, 4, 3, 5, 1, 0, 6], unsorted: 2 },
    stuck: { count: 7, amount: 1_260_000, days: 7, approx: false, top: stuckTop },
    overdue: { available: true, tasks: 4, managers: 2, limited: false },
    river: {
      deals_total: 31,
      deals_loaded: 31,
      amount_total: 6_100_000,
      truncated: false,
      limit: 300,
      busiest_stage: "Переговоры",
      stages: [
        { id: "NEW", name: "Новые", count: 2, amount: 300_000, stuck: 0, deals: [deal("1", "Заявка с сайта", 100_000, 0, { new: true, stage: "Новые" }), deal("2", "Звонок", 200_000, 1, { stage: "Новые" })] },
        { id: "TALK", name: "Переговоры", count: 3, amount: 1_560_000, stuck: 2, deals: [...stuckTop.slice(0, 2).map((d) => ({ ...d })), deal("3", "Тендер", 300_000, 3)] },
        { id: "OFFER", name: "Предложение", count: 1, amount: 300_000, stuck: 1, deals: [{ ...stuckTop[2], stage: "Предложение", late: true }] },
      ],
    },
    managers: [
      { id: "7", name: "Иван Орлов", initials: "ИО", overdue: 3, stuck: 4, won_amount: 1_400_000, won_count: 6, leader: true },
      { id: "8", name: "Мария Дым", initials: "МД", overdue: 1, stuck: 3, won_amount: 900_000, won_count: 5, leader: false },
    ],
    links: { portal: "https://acme.bitrix24.ru/crm/deal/kanban/" },
    as_of: "2026-09-23T09:57:00+00:00",
    stale: false,
    ...overrides,
  };
}

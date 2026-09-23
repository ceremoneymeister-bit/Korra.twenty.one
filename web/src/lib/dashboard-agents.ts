/**
 * Карточка «Агенты» на дашборде: профили контура плюс их живая работа.
 *
 * Здесь только чистое правило «профили + работы → строки карточки», без
 * React и без сети: состояние агента и адрес перехода проверяются тестом, а
 * виджет остаётся про вёрстку.
 *
 * Два обещания, ради которых этот файл существует отдельно:
 *
 * - **Никаких выдуманных состояний.** Если состав агентов не пришёл — строк
 *   нет. До первого снимка активности состав виден, но занятость остаётся
 *   неизвестной. Только после снимка при обрыве можно показать последнее
 *   известное состояние.
 * - **Переход ведёт туда, где человек продолжит.** Не «на экран агентов», а
 *   к нужному агенту и в тот самый чат, в котором идёт работа.
 */

import { buildAgentTabs, MAIN_AGENT_TAB, type AgentTabConfig } from "@/lib/agent-tabs";
import { isRunBusy, type ChatRun } from "@/lib/chat-runs";

export type AgentActivity = "waiting" | "working" | "ready" | "failed" | "idle" | "unknown";

export interface DashboardAgentRow {
  /** Имя профиля; "" — главный агент панели. */
  profile: string;
  label: string;
  activity: AgentActivity;
  /** Одна строка о состоянии — то, что человек читает на карточке. */
  note: string;
  /** Чат, в котором идёт (или только что закончилась) эта работа. */
  sessionId: string | null;
  /** Что именно попросили — показывается только там, где есть место. */
  step: string;
  /** Ответ готов и ещё не открыт. */
  unread: boolean;
  /** Время последнего события работы, unix-секунды. */
  updatedAt: number | null;
  /** Готовый шаблон, из которого создан агент (`distribution_name`):
   *  по нему выбирается знак аватара. */
  template: string | null;
}

const ACTIVITY_RANK: Record<AgentActivity, number> = {
  waiting: 0,
  working: 1,
  ready: 2,
  failed: 3,
  idle: 4,
  unknown: 5,
};

const ACTIVITY_NOTE: Record<AgentActivity, string> = {
  waiting: "Ждёт вашего решения",
  working: "Работает",
  ready: "Ответ готов",
  failed: "Последняя работа не завершилась",
  idle: "Готов к поручению",
  unknown: "Состояние уточняется",
};

function runActivity(run: ChatRun): AgentActivity {
  if (run.status === "waiting_decision") return "waiting";
  if (isRunBusy(run)) return "working";
  if (run.status === "failed" || run.status === "interrupted") return "failed";
  if (run.status === "completed" && run.unread === true) return "ready";
  return "idle";
}

/** Самая важная работа агента: решение → работа → готовый ответ → сбой. */
function leadingRun(runs: readonly ChatRun[]): ChatRun | null {
  let best: ChatRun | null = null;
  let bestRank = Number.POSITIVE_INFINITY;
  for (const run of runs) {
    const rank = ACTIVITY_RANK[runActivity(run)];
    const newer = best === null || run.updated_at > best.updated_at;
    if (rank < bestRank || (rank === bestRank && newer)) {
      best = run;
      bestRank = rank;
    }
  }
  return best;
}

function cleanStep(run: ChatRun | null): string {
  const text = typeof run?.user_message?.content === "string" ? run.user_message.content : "";
  return text.replace(/\s+/g, " ").trim();
}

export interface AgentRosterInput {
  /** Ответ `/api/profiles`; `null` — ещё не пришёл или не дошёл. */
  profiles: unknown;
  /** Последний известный список работ. */
  runs: readonly ChatRun[];
  /** Хотя бы один ответ о работах уже был получен. */
  activityKnown?: boolean;
}

/**
 * Строки карточки в порядке «кому я нужен прямо сейчас».
 *
 * Свободные агенты не исчезают: человеку важно видеть и того, кто ждёт
 * поручения. Но решения и работа идут первыми — с них начинается день.
 */
export function agentRows({
  profiles,
  runs,
  activityKnown = true,
}: AgentRosterInput): DashboardAgentRow[] {
  // `buildAgentTabs` намеренно всегда отдаёт главную вкладку: полоса агентов
  // не должна быть пустой даже без ответа сервера. Дашборду это не подходит —
  // строка «Корра · готов к поручению» без прочитанного состава была бы
  // выдуманным фактом. Рабочая установка всегда отдаёт хотя бы `default`,
  // поэтому пустой ответ значит «состав прочитать не удалось», а не
  // «агентов нет».
  if (!Array.isArray(profiles) || profiles.length === 0) return [];
  const tabs: AgentTabConfig[] = buildAgentTabs(profiles);
  const templates = new Map<string, string>();
  for (const item of profiles as unknown[]) {
    if (!item || typeof item !== "object") continue;
    const { distribution_name: template, is_default: isDefault, name } = item as {
      distribution_name?: unknown;
      is_default?: unknown;
      name?: unknown;
    };
    if (typeof template !== "string" || !template.trim()) continue;
    const key = isDefault === true || name === "default" ? MAIN_AGENT_TAB.profile : String(name ?? "");
    templates.set(key, template.trim());
  }
  const byProfile = new Map<string, ChatRun[]>();
  for (const run of runs) {
    const list = byProfile.get(run.profile);
    if (list) list.push(run);
    else byProfile.set(run.profile, [run]);
  }
  const rows = tabs.map((tab) => {
    const run = leadingRun(byProfile.get(tab.profile) ?? []);
    // Пустой массив означает «все свободны» только после настоящего ответа.
    // До первого снимка даже оставшиеся в модульном store строки не являются
    // подтверждённой активностью этого показа.
    const activity = activityKnown ? (run ? runActivity(run) : "idle") : "unknown";
    return {
      profile: tab.profile,
      label: tab.label,
      activity,
      note: ACTIVITY_NOTE[activity],
      sessionId: activityKnown ? (run?.session_id ?? null) : null,
      step: activity === "idle" || activity === "unknown" ? "" : cleanStep(run),
      unread: activityKnown && run?.unread === true,
      updatedAt: activityKnown && run ? run.updated_at : null,
      template: templates.get(tab.profile) ?? null,
    } satisfies DashboardAgentRow;
  });
  return rows.sort((a, b) => {
    const byActivity = ACTIVITY_RANK[a.activity] - ACTIVITY_RANK[b.activity];
    if (byActivity !== 0) return byActivity;
    // Главный агент остаётся первым среди равных: он и на полосе вкладок первый.
    if (a.profile === MAIN_AGENT_TAB.profile) return -1;
    if (b.profile === MAIN_AGENT_TAB.profile) return 1;
    return (b.updatedAt ?? 0) - (a.updatedAt ?? 0);
  });
}

/** Сколько агентов сейчас заняты — для компактного размера карточки. */
export function busyAgentCount(rows: readonly DashboardAgentRow[]): number {
  return rows.filter((row) => row.activity === "working" || row.activity === "waiting").length;
}

/**
 * Адрес, по которому человек продолжит работу именно этого агента.
 *
 * Снаружи главный агент известен серверным именем `default` — таким его ждёт
 * экран агентов (`?agent=default` → главная вкладка панели). `resume`
 * открывает нужный чат, а не последний открытый.
 */
export function agentHref(row: Pick<DashboardAgentRow, "profile" | "sessionId">): string {
  const params = new URLSearchParams();
  params.set("agent", row.profile === MAIN_AGENT_TAB.profile ? "default" : row.profile);
  if (row.sessionId) params.set("resume", row.sessionId);
  return `/agents?${params}`;
}

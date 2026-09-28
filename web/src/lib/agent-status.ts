/**
 * Состояние агентов для мобильной шапки: кто работает, кто ждёт человека.
 *
 * Раньше у вкладки была одна зелёная цифра на всё: «работает сейчас»,
 * «новый ответ» и «ждёт вашего решения» выглядели одинаково, и Марина
 * (Бирюкова, 27.09) считала, что «Нюра | Bitrix» всё ещё думает, хотя четыре
 * отправки ждали её ответа. Здесь каждое состояние — отдельное поле, а знак
 * на аватаре выбирает форма (кольцо, пунктир, точка, число, «!»), а не цвет.
 *
 * Источник — тот же глобальный опрос `/api/chat/runs`, что и у полосы
 * вкладок: новых запросов на каждого агента не появляется. Решения приходят
 * строками `waiting_decision`, и каждая несёт `pending_decisions` — сколько
 * решений ждёт в этом разговоре (старый сервер поля не знает: тогда строка
 * считается за одно).
 */

import type { ChatRun } from "@/lib/chat-runs";

export interface AgentStatus {
  /** Ходов, которые идут прямо сейчас. */
  running: number;
  /** Ходов в очереди: все места заняты, начнётся само. */
  queued: number;
  /** Решений, которые ждут ответа человека. Число бывает только у них. */
  decisions: number;
  /** Есть непрочитанный готовый ответ. */
  unread: boolean;
  /** Последняя работа не завершилась, и человек её ещё не открыл. */
  error: boolean;
  /** Статус работы не удалось подтвердить. */
  stale: boolean;
  /** Последнее изменение по этому агенту, секунды эпохи; 0 — неизвестно. */
  lastActivity: number;
}

export const IDLE_STATUS: AgentStatus = Object.freeze({
  running: 0,
  queued: 0,
  decisions: 0,
  unread: false,
  error: false,
  stale: false,
  lastActivity: 0,
});

/** Что требует человека, по убыванию срочности. */
export type AttentionKind = "decision" | "error" | "unread";

function blank(): AgentStatus {
  return { ...IDLE_STATUS };
}

/**
 * Свести ходы, непрочитанные ответы и сбои к состоянию каждого агента.
 *
 * Ключ — профиль агента в том виде, в каком он ходит в запросах: `""` —
 * главный агент. Агенты без единой строки получают пустое состояние.
 */
export function buildAgentStatuses(
  profiles: readonly string[],
  runs: readonly ChatRun[],
  unread: readonly ChatRun[] = [],
  failed: readonly ChatRun[] = [],
): Map<string, AgentStatus> {
  const result = new Map<string, AgentStatus>();
  for (const profile of profiles) result.set(profile, blank());
  const get = (profile: string) => {
    let status = result.get(profile);
    if (!status) {
      status = blank();
      result.set(profile, status);
    }
    return status;
  };
  for (const run of runs) {
    const status = get(run.profile);
    status.lastActivity = Math.max(status.lastActivity, Number(run.updated_at) || 0);
    if (run.status === "running") status.running += 1;
    else if (run.status === "queued") status.queued += 1;
    else if (run.status === "waiting_decision") {
      const count = Number(run.pending_decisions);
      status.decisions += Number.isFinite(count) && count > 0 ? Math.floor(count) : 1;
    } else if (run.status === "stale") status.stale = true;
  }
  for (const run of unread) get(run.profile).unread = true;
  for (const run of failed) get(run.profile).error = true;
  return result;
}

export function attentionOf(status: AgentStatus | undefined): AttentionKind | null {
  if (!status) return null;
  if (status.decisions > 0) return "decision";
  if (status.error) return "error";
  if (status.unread) return "unread";
  return null;
}

const ATTENTION_RANK: Record<AttentionKind, number> = { decision: 1, error: 2, unread: 3 };

/** 1 — решения, 2 — ошибка, 3 — новый ответ, 9 — ничего. */
export function attentionRank(status: AgentStatus | undefined): number {
  const kind = attentionOf(status);
  return kind ? ATTENTION_RANK[kind] : 9;
}

/** Самое срочное из нескольких состояний (для точки на «‹» и «Все агенты»). */
export function strongestAttention(
  statuses: Iterable<AgentStatus | undefined>,
): { kind: AttentionKind | null; count: number } {
  let best = 9;
  let count = 0;
  for (const status of statuses) {
    const rank = attentionRank(status);
    if (rank < 9) {
      count += 1;
      best = Math.min(best, rank);
    }
  }
  const kind = (Object.keys(ATTENTION_RANK) as AttentionKind[]).find(
    (key) => ATTENTION_RANK[key] === best,
  );
  return { kind: kind ?? null, count };
}

/**
 * Сколько «идёт работ» показывает кольцо в полосе.
 *
 * Каждый идущий или ждущий очереди ход — работа; агент, который ждёт
 * решений, — одна работа, сколько бы решений ни ждало (число решений видно
 * на самом агенте). Неподтверждённые статусы тоже считаются: их показывает
 * лист работ с пометкой «требует проверки».
 */
export function activityCount(statuses: Iterable<AgentStatus>): number {
  let total = 0;
  for (const status of statuses) {
    total += status.running + status.queued + (status.decisions > 0 ? 1 : 0) + (status.stale ? 1 : 0);
  }
  return total;
}

/** Русская форма числительного: 1 решение, 2 решения, 5 решений. */
export function pluralRu(n: number, one: string, few: string, many: string): string {
  const m10 = n % 10;
  const m100 = n % 100;
  if (m10 === 1 && m100 !== 11) return one;
  if (m10 >= 2 && m10 <= 4 && (m100 < 12 || m100 > 14)) return few;
  return many;
}

export function decisionsWord(n: number): string {
  return pluralRu(n, "решение", "решения", "решений");
}

/** «4 решения ждут вас» — для полосы решений и строк списка. */
export function decisionsWaitPhrase(n: number): string {
  return `${n} ${pluralRu(n, "решение ждёт", "решения ждут", "решений ждут")} вас`;
}

/** Хвост подписи для экранного диктора: «. ждёт 4 решения, работает». */
export function statusSpeech(status: AgentStatus | undefined): string {
  if (!status) return "";
  const parts: string[] = [];
  if (status.decisions > 0) parts.push(`ждёт ${status.decisions} ${decisionsWord(status.decisions)}`);
  if (status.error) parts.push("ошибка");
  if (status.unread) parts.push("новый ответ");
  if (status.running > 0) parts.push("работает");
  else if (status.queued > 0) parts.push("в очереди");
  if (status.stale) parts.push("статус требует проверки");
  return parts.length ? `. ${parts.join(", ")}` : "";
}

/**
 * Монограмма агента по отличающей части имени.
 *
 * Девять «Нюр» различаются второй частью: «Нюра | Bitrix» → «Bi»,
 * «Нюра | Документы» → «До». Аббревиатура остаётся как есть («РОП»),
 * одиночное имя — одной буквой («Нюра» → «Н»), имя из двух слов —
 * инициалами («Учитель китайского» → «УК»).
 */
export function agentMonogram(label: string): string {
  const name = (label ?? "").trim();
  const piped = name.includes("|");
  const part = (piped ? name.split("|").pop() ?? "" : name).trim() || name;
  if (/^[A-ZА-ЯЁ0-9]{2,3}$/u.test(part)) return part;
  const words = part.split(/[\s_-]+/u).map((word) => word.replace(/[^\p{L}\p{N}]/gu, "")).filter(Boolean);
  if (words.length === 0) return "·";
  if (!piped && words.length >= 2) {
    return (Array.from(words[0])[0] + Array.from(words[1])[0]).toLocaleUpperCase("ru-RU");
  }
  const letters = Array.from(words[0]).slice(0, piped ? 2 : 1);
  return letters[0].toLocaleUpperCase("ru-RU") + letters.slice(1).join("").toLocaleLowerCase("ru-RU");
}

/**
 * Имя «Нюра | Bitrix» в двух частях: общая приставка приглушённо, отличие —
 * крупно. Без «|» приставки нет.
 */
export function splitAgentName(label: string): { prefix: string; main: string } {
  if (!label.includes("|")) return { prefix: "", main: label };
  const index = label.lastIndexOf("|");
  return { prefix: `${label.slice(0, index).trim()} |`, main: label.slice(index + 1).trim() };
}

/** Ширина цели аватара в полосе. */
export const RAIL_AVATAR_PX = 44;

/**
 * Сколько аватаров помещается в полосу без прокрутки.
 *
 * Ширина экрана минус поля 16 + 16, кольцо работ 44 и зазор 6, внутри лотка —
 * поля 4 и кнопка «Все агенты» 50. На 320 px выходит 4, на 375–390 — 5,
 * на 430 — 6.
 */
export function railSlots(width: number): number {
  const tray = width - 32 - 44 - 6;
  const inner = tray - 4 - 50;
  return Math.max(2, Math.floor(inner / RAIL_AVATAR_PX));
}

export interface RailPickOptions {
  /** Открытый агент — всегда в полосе. */
  active: string;
  /** Закреплённые человеком — сразу после открытого. */
  pinned?: readonly string[];
  /** Когда агент был активен последний раз, секунды эпохи. */
  lastActive?: ReadonlyMap<string, number>;
  slots: number;
}

export interface RailPick<T> {
  shown: T[];
  /** Среди не попавших в полосу есть ждущий человека — самое срочное. */
  hiddenAttention: AttentionKind | null;
}

/**
 * Кто стоит в полосе.
 *
 * Порядок отбора: открытый, закреплённые, ждущие вас (решения, ошибка, новый
 * ответ), работающие, в очереди, недавние. Внутри полосы — порядок
 * пользователя (как на десктопе), чтобы соседи не прыгали при каждой смене
 * состояния.
 */
export function pickRail<T extends { profile: string }>(
  tabs: readonly T[],
  statuses: ReadonlyMap<string, AgentStatus>,
  options: RailPickOptions,
): RailPick<T> {
  const pinned = options.pinned ?? [];
  const order = new Map(tabs.map((tab, index) => [tab.profile, index]));
  const priority = (tab: T): number => {
    if (tab.profile === options.active) return 0;
    const pin = pinned.indexOf(tab.profile);
    if (pin >= 0) return 0.1 + pin * 0.01;
    const status = statuses.get(tab.profile);
    const rank = attentionRank(status);
    if (rank < 9) return rank;
    if (status && status.running > 0) return 4;
    if (status && (status.queued > 0 || status.stale)) return 5;
    return 10;
  };
  const recency = (tab: T): number =>
    Math.max(options.lastActive?.get(tab.profile) ?? 0, statuses.get(tab.profile)?.lastActivity ?? 0);
  const ranked = [...tabs].sort((a, b) => {
    const byPriority = priority(a) - priority(b);
    if (byPriority !== 0) return byPriority;
    const byRecency = recency(b) - recency(a);
    if (byRecency !== 0) return byRecency;
    return (order.get(a.profile) ?? 0) - (order.get(b.profile) ?? 0);
  });
  const chosen = new Set(ranked.slice(0, Math.max(1, options.slots)).map((tab) => tab.profile));
  const shown = tabs.filter((tab) => chosen.has(tab.profile));
  const hidden = tabs.filter((tab) => !chosen.has(tab.profile));
  return {
    shown,
    hiddenAttention: strongestAttention(hidden.map((tab) => statuses.get(tab.profile))).kind,
  };
}

/** Агенты для экрана-списка и шторки: сначала ждущие вас, по срочности. */
export function groupByAttention<T extends { profile: string }>(
  tabs: readonly T[],
  statuses: ReadonlyMap<string, AgentStatus>,
): { waiting: T[]; rest: T[] } {
  const waiting = tabs
    .filter((tab) => attentionRank(statuses.get(tab.profile)) < 9)
    .sort((a, b) => attentionRank(statuses.get(a.profile)) - attentionRank(statuses.get(b.profile)));
  const rest = tabs.filter((tab) => attentionRank(statuses.get(tab.profile)) === 9);
  return { waiting, rest };
}

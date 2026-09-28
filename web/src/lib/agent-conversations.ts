import { map } from "nanostores";

/**
 * Что открыто у каждого агента — для мобильной шапки и экрана-списка.
 *
 * Каждый смонтированный чат агента уже читает свой список разговоров (один
 * запрос при открытии экрана); сюда он кладёт короткую сводку. Шапка берёт
 * отсюда название открытого разговора и «Чаты N», список агентов — последний
 * разговор и время. Лишних запросов на агента не появляется.
 */
export interface AgentConversationSummary {
  /** Открытый разговор; null — новый чат. */
  sessionId: string | null;
  /** Название открытого разговора или «Новый чат». */
  title: string;
  /** Сколько разговоров у агента; null — ещё не знаем. */
  chatCount: number | null;
  /** Последний разговор агента — для экрана-списка. */
  latestTitle: string | null;
  /** Когда агент был активен последний раз, секунды эпохи. */
  lastActive: number | null;
}

export const $agentConversations = map<Record<string, AgentConversationSummary>>({});

function same(a: AgentConversationSummary | undefined, b: AgentConversationSummary): boolean {
  return Boolean(a)
    && a!.sessionId === b.sessionId
    && a!.title === b.title
    && a!.chatCount === b.chatCount
    && a!.latestTitle === b.latestTitle
    && a!.lastActive === b.lastActive;
}

export function publishAgentConversation(profile: string, summary: AgentConversationSummary): void {
  if (same($agentConversations.get()[profile], summary)) return;
  $agentConversations.setKey(profile, summary);
}

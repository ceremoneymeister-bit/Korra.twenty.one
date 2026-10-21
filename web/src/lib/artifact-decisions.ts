/**
 * Решения по документам из чата одним сообщением.
 *
 * Карточка под документом не отправляет решение сразу: оно копится в панели «Ваши ответы» над полем ввода,
 * и владелец отправляет всё разом — одно сообщение, один запуск агента. Здесь — формат этого сообщения и
 * разбор истории (старые одиночные «Согласовано: «X»\nФайл: …» тоже читаются).
 */
import { createContext } from "react";

export type DecisionKind = "approve" | "change" | "defer";
export interface DraftDecision { kind: DecisionKind; name: string; path: string; note?: string }

const WORD: Record<DecisionKind, string> = { approve: "Согласовано", change: "Изменить", defer: "Отложено" };
const KIND = Object.fromEntries(Object.entries(WORD).map(([k, w]) => [w, k])) as Record<string, DecisionKind>;

/** Одно сообщение агенту. Путь — у каждой строки: имена документов повторяются («КП.docx» в разных заказах). */
export function formatDecisions(list: DraftDecision[]): string {
  const lines = list.map((d, i) => {
    const note = d.kind === "change" && d.note?.trim() ? ` — ${d.note.trim().replace(/\s*\n\s*/g, " ")}` : "";
    return `${i + 1}. ${WORD[d.kind]}: «${d.name}»${note}\nФайл: ${d.path}`;
  });
  return `Решения по документам:\n${lines.join("\n")}`;
}

const BATCH_HEAD = "Решения по документам:";
const BATCH_LINE = /^(?:\d+\.\s*)?(Согласовано|Изменить|Отложено):\s*«(.+?)»(?:\s*—\s*(.*?))?\s*(?:\nФайл:\s*(\S.*?))?\s*$/gm;
const SINGLE = /^(Согласовано|Отложено):\s*«(.+?)»(?:\s*\nФайл:\s*(\S.*?))?\s*$/;

/** Решения из текста сообщения владельца. Решение — всё сообщение целиком: пакет с заголовком или прежнее
 *  одиночное. Строка, процитированная внутри обычного текста, ничего не решает. */
export function parseDecisions(text: string): DraftDecision[] {
  const body = text.trim();
  if (body.startsWith(BATCH_HEAD)) {
    return [...body.slice(BATCH_HEAD.length).matchAll(BATCH_LINE)].map(m => (
      { kind: KIND[m[1]], name: m[2], path: m[4] ?? m[2], note: m[3] || undefined }
    ));
  }
  const m = SINGLE.exec(body);
  return m ? [{ kind: KIND[m[1]], name: m[2], path: m[3] ?? m[2], note: undefined }] : [];
}

/** Панель черновых ответов. Нет провайдера — карточка ведёт себя по-старому (отправляет сразу). */
export interface DecisionDrafts {
  drafts: Map<string, DraftDecision>;
  choose: (d: DraftDecision) => void;
  remove: (path: string) => void;
}
export const DecisionDraftsContext = createContext<DecisionDrafts | null>(null);

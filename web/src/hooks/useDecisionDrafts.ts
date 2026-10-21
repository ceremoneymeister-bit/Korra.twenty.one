/**
 * Черновые решения по документам («Ваши ответы») для одного открытого чата.
 *
 * `send()` чата завершается только после всего ответа агента, поэтому отправленные строки уходят из панели
 * сразу по нажатию, а не по завершению промиса: иначе правка уже отправленной строки терялась бы, а решение,
 * выбранное во время ответа, стиралось вместе с ними.
 */
import { useCallback, useMemo, useState } from "react";

import { formatDecisions, type DecisionDrafts, type DraftDecision } from "@/lib/artifact-decisions";

const EMPTY = new Map<string, DraftDecision>();

export function useDecisionDrafts({ chatKey, send, isStreaming }: {
  /** Черновики привязаны к чату: при переходе в другой разговор их не видно и они не теряются. */
  chatKey: string;
  send: (text: string) => Promise<boolean>;
  isStreaming: boolean;
}) {
  const [byChat, setByChat] = useState<Record<string, Map<string, DraftDecision>>>({});
  const drafts = byChat[chatKey] ?? EMPTY;

  const update = useCallback((key: string, change: (m: Map<string, DraftDecision>) => void) => {
    setByChat(all => { const m = new Map(all[key] ?? []); change(m); return { ...all, [key]: m }; });
  }, []);

  const context = useMemo<DecisionDrafts>(() => ({
    drafts,
    choose: d => update(chatKey, m => { m.set(d.path, { ...m.get(d.path), ...d }); }),
    remove: path => update(chatKey, m => { m.delete(path); }),
  }), [drafts, chatKey, update]);

  const sendAll = useCallback(async () => {
    const batch = [...drafts.values()];
    if (isStreaming || !batch.length) return;
    update(chatKey, m => { for (const d of batch) m.delete(d.path); });
    let ok = false;
    try {
      ok = await send(formatDecisions(batch));
    } finally {
      // Не ушло — строки возвращаются, кроме тех, по которым владелец успел выбрать заново.
      if (!ok) update(chatKey, m => {
        const now = new Map(m);
        m.clear();
        for (const d of batch) if (!now.has(d.path)) m.set(d.path, d);
        for (const [path, d] of now) m.set(path, d);
      });
    }
  }, [chatKey, drafts, isStreaming, send, update]);

  return { drafts, context, sendAll };
}

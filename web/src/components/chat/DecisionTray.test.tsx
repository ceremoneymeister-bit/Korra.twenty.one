// @vitest-environment jsdom
import { act, useState } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import { ChatArtifactList } from "@/components/ChatArtifact";
import { DecisionTray } from "@/components/chat/DecisionTray";
import { useDecisionDrafts } from "@/hooks/useDecisionDrafts";
import { DecisionDraftsContext, parseDecisions } from "@/lib/artifact-decisions";
import { splitArtifacts } from "@/lib/chat-artifacts";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const host = document.createElement("div");
document.body.append(host);
const root = createRoot(host);

const { artifacts } = splitArtifacts("[артефакт] /w/1024/КП 1024.docx\n[артефакт] /w/1024/Калькуляция.xlsx\n[артефакт] /w/1024/Отчёт.html");

/** Как BubbleChatPage: send() занимает чат до конца ответа агента; решённое читается из отправленных сообщений. */
function Harness({ send }: { send: (text: string) => Promise<boolean> }) {
  const [sent, setSent] = useState<string[]>([]);
  const [streaming, setStreaming] = useState(false);
  const chatSend = async (text: string) => {
    setSent(s => [...s, text]);
    setStreaming(true);
    try { return await send(text); } finally { setStreaming(false); }
  };
  const { drafts, context, sendAll } = useDecisionDrafts({ chatKey: "chat", send: chatSend, isStreaming: streaming });
  const decided = new Map<string, "approve" | "defer">();
  for (const text of sent) for (const d of parseDecisions(text)) if (d.kind !== "change") decided.set(d.path, d.kind);
  return (
    <>
      <DecisionDraftsContext.Provider value={context}>
        <ChatArtifactList items={artifacts} decided={decided} onDecision={() => true} busy={streaming} />
      </DecisionDraftsContext.Provider>
      <DecisionTray drafts={[...drafts.values()]} onChange={context.choose} onRemove={context.remove}
        onSend={() => void sendAll()} busy={streaming} />
    </>
  );
}

beforeEach(() => { vi.stubGlobal("fetch", vi.fn(async () => new Response("{}"))); });
afterEach(async () => { await act(async () => root.render(null)); vi.unstubAllGlobals(); });

const tray = () => host.querySelector('section[aria-label="Ваши ответы по документам"]');
const rows = () => [...host.querySelectorAll('section[aria-label="Ваши ответы по документам"] li')];
const sendButton = () => [...host.querySelectorAll("button")].find(b => b.textContent?.startsWith("Отправить ответы"))!;
async function pick(label: string) {
  const card = [...host.querySelectorAll("button")].filter(b => b.textContent?.startsWith(label) && !b.closest("section"))[0];
  expect(card, `кнопка «${label}» под документом`).toBeTruthy();
  await act(async () => { card.click(); });
}
function deferred() {
  let resolve!: (ok: boolean) => void;
  const promise = new Promise<boolean>(r => { resolve = r; });
  return { promise, resolve };
}

it("две карточки → две строки → одно сообщение, панель сразу пустеет, пока агент отвечает", async () => {
  const reply = deferred();
  const send = vi.fn<(text: string) => Promise<boolean>>(() => reply.promise);
  await act(async () => root.render(<Harness send={send} />));
  await pick("Согласовать");
  await pick("Отложить");
  expect(rows()).toHaveLength(2);
  expect(sendButton().textContent).toBe("Отправить ответы (2)");

  await act(async () => { sendButton().click(); });
  expect(send).toHaveBeenCalledTimes(1);
  const text = send.mock.calls[0][0];
  expect(text.split("\n")[0]).toBe("Решения по документам:");
  expect(parseDecisions(text).map(d => [d.kind, d.name])).toEqual([["approve", "КП 1024.docx"], ["defer", "Калькуляция.xlsx"]]);
  // Агент ещё отвечает, а панели уже нет, и карточки не обещают «отправится вместе с остальными».
  expect(tray()).toBeNull();
  expect(host.textContent).not.toContain("отправится вместе с остальными");
  expect(host.textContent).toContain("Согласовано");

  await act(async () => { reply.resolve(true); await reply.promise; });
  expect(tray()).toBeNull();
});

it("решение, выбранное во время ответа агента, переживает конец ответа и уходит следующим пакетом", async () => {
  const reply = deferred();
  const send = vi.fn<(text: string) => Promise<boolean>>(() => reply.promise);
  await act(async () => root.render(<Harness send={send} />));
  await pick("Согласовать");
  await act(async () => { sendButton().click(); });

  await pick("Отложить");  // агент отвечает, а владелец решает по следующему документу
  expect(rows()).toHaveLength(1);
  expect(sendButton().disabled).toBe(true);

  await act(async () => { reply.resolve(true); await reply.promise; });
  expect(rows()).toHaveLength(1);
  expect(sendButton().disabled).toBe(false);
  send.mockResolvedValueOnce(true);
  await act(async () => { sendButton().click(); });
  expect(send).toHaveBeenCalledTimes(2);
  expect(parseDecisions(send.mock.calls[1][0]).map(d => d.name)).toEqual(["Калькуляция.xlsx"]);
});


it("send вернул false — отправленные строки возвращаются в панель", async () => {
  const reply = deferred();
  const send = vi.fn<(text: string) => Promise<boolean>>(() => reply.promise);
  await act(async () => root.render(<Harness send={send} />));
  await pick("Согласовать");
  await pick("Отложить");
  await act(async () => { sendButton().click(); });
  expect(tray()).toBeNull();
  await act(async () => { reply.resolve(false); await reply.promise; });
  expect(rows().map(r => r.querySelector('[aria-checked="true"]')!.textContent)).toEqual(["Согласовать", "Отложить"]);
  expect(sendButton().disabled).toBe(false);
});

it("send вернул false: новый выбор по тому же документу не затирается возвращённой строкой", async () => {
  const reply = deferred();
  const send = vi.fn<(text: string) => Promise<boolean>>(() => reply.promise);
  await act(async () => root.render(<Harness send={send} />));
  await pick("Изменить");  // «Изменить» не закрывает карточку: после отправки по документу можно выбрать снова
  await act(async () => { sendButton().click(); });
  expect(tray()).toBeNull();
  await pick("Согласовать");
  await act(async () => { reply.resolve(false); await reply.promise; });
  expect(rows()).toHaveLength(1);
  expect(rows()[0].querySelector('[aria-checked="true"]')!.textContent).toBe("Согласовать");
});

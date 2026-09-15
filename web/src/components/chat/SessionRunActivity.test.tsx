// @vitest-environment jsdom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { MemoryRouter } from "react-router";
import { afterEach, expect, it, vi } from "vitest";
import { $chatRuns, $dismissedRunToasts, $unreadChatRuns, $chatRunsReachable, $viewedChat, markChatViewed, refreshChatRuns, type ChatRun } from "@/lib/chat-runs";
import { saveChatOutbox } from "@/lib/chat-outbox";
import { AgentRunBadge, ChatUnreadMark, SessionRunActivity } from "./SessionRunActivity";

const run: ChatRun = { message_id: "message-lawyer", session_id: "session-lawyer", profile: "lawyer", status: "running", updated_at: 1, history_count: 0, user_message: { role: "user", content: "Договор" } };
afterEach(() => { localStorage.clear(); $viewedChat.set(null); $chatRuns.set([]); $unreadChatRuns.set([]); $dismissedRunToasts.set([]); $chatRunsReachable.set(null); vi.unstubAllGlobals(); });
it("вкладка и список отражают серверную очередь, готовый ответ открывает свой профиль и чат", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ runs: [run] }))));
  const container = document.createElement("div"); document.body.append(container);
  const root = createRoot(container);
  await act(async () => root.render(<MemoryRouter><AgentRunBadge profile="lawyer" /><SessionRunActivity tabs={[{ profile: "lawyer", label: "Юрист" }]} /></MemoryRouter>));
  expect(container.textContent).toContain("В работе");
  await act(async () => { $chatRuns.set([{ ...run, status: "queued" }]); });
  expect(container.textContent).toContain("В очереди");
  expect(container.textContent).toContain("начнёт автоматически");
  await act(async () => { $chatRuns.set([{ ...run, status: "completed" }]); $unreadChatRuns.set([{ ...run, status: "completed" }]); });
  expect(container.textContent).toContain("Ответ готов");
  const notification = [...document.querySelectorAll("a")].find(link => link.textContent?.includes("Юрист ответил"));
  expect(notification?.getAttribute("href")).toBe("/agents?agent=lawyer&resume=session-lawyer");
  await act(async () => root.unmount()); container.remove();
});


it("после F5 готовый ответ из сохранённого исходящего сообщения остаётся непрочитанным", async () => {
  const completed = { ...run, message_id: "message-after-reload-123456", status: "completed" } as ChatRun;
  saveChatOutbox({ messageId: completed.message_id, sessionId: completed.session_id,
    profile: "lawyer", text: "Договор", attachments: [], status: "sending", createdAt: 1 });
  $viewedChat.set({ profile: "accountant", sessionId: "another-session" });
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ runs: [completed] }))));
  await refreshChatRuns();
  expect($unreadChatRuns.get().map(item => item.message_id)).toContain(completed.message_id);
});


it("отметка стоит у конкретного чата; «Скрыть уведомление» не читает ответ, а открытие чата снимает только его отметку", async () => {
  const a = { ...run, message_id: "m-a", session_id: "session-a", status: "completed" } as ChatRun;
  const c = { ...run, message_id: "m-c", session_id: "session-c", status: "completed" } as ChatRun;
  const busyB = { ...run, message_id: "m-b", session_id: "session-b", status: "running" } as ChatRun;
  $chatRuns.set([a, c, busyB]); $unreadChatRuns.set([a, c]);
  const container = document.createElement("div"); document.body.append(container);
  const root = createRoot(container);
  await act(async () => root.render(<MemoryRouter>
    <AgentRunBadge profile="lawyer" />
    <ul><li><ChatUnreadMark profile="lawyer" sessionId="session-a" /></li><li><ChatUnreadMark profile="lawyer" sessionId="session-b" /></li><li><ChatUnreadMark profile="lawyer" sessionId="session-c" /></li></ul>
    <SessionRunActivity tabs={[{ profile: "lawyer", label: "Юрист" }]} />
  </MemoryRouter>));
  // Готовый ответ не прячется за работой другой сессии того же агента.
  expect(container.textContent).toContain("В работе");
  expect(container.textContent).toContain("Ответ готов");
  expect(container.querySelectorAll("[data-unread-response]")).toHaveLength(2);
  const toast = document.querySelector("[data-run-toast]")!;
  expect(toast.className).toMatch(/top-16/);
  expect(toast.className).not.toMatch(/bottom-/);
  expect(toast.querySelectorAll("a")).toHaveLength(2);
  await act(async () => (toast.querySelector("button") as HTMLButtonElement).click());
  expect(document.querySelector("[data-run-toast]")).toBeNull();
  expect($unreadChatRuns.get()).toHaveLength(2);
  expect(container.querySelectorAll("[data-unread-response]")).toHaveLength(2);
  await act(async () => { markChatViewed("lawyer", "session-a"); });
  expect(container.querySelectorAll("[data-unread-response]")).toHaveLength(1);
  expect($unreadChatRuns.get().map(item => item.session_id)).toEqual(["session-c"]);
  // Новый ответ после скрытия снова показывается в уведомлении.
  const d = { ...run, message_id: "m-d", session_id: "session-d", status: "completed" } as ChatRun;
  await act(async () => { $unreadChatRuns.set([...$unreadChatRuns.get(), d]); });
  expect(document.querySelector("[data-run-toast]")?.querySelectorAll("a")).toHaveLength(1);
  await act(async () => root.unmount()); container.remove();
});

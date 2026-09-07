// @vitest-environment jsdom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { MemoryRouter } from "react-router";
import { afterEach, expect, it, vi } from "vitest";
import { $chatRuns, $unreadChatRuns, $chatRunsReachable, $viewedChat, refreshChatRuns, type ChatRun } from "@/lib/chat-runs";
import { saveChatOutbox } from "@/lib/chat-outbox";
import { AgentRunBadge, SessionRunActivity } from "./SessionRunActivity";

const run: ChatRun = { message_id: "message-lawyer", session_id: "session-lawyer", profile: "lawyer", status: "running", updated_at: 1, history_count: 0, user_message: { role: "user", content: "Договор" } };
afterEach(() => { localStorage.clear(); $viewedChat.set(null); $chatRuns.set([]); $unreadChatRuns.set([]); $chatRunsReachable.set(null); vi.unstubAllGlobals(); });
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

// @vitest-environment jsdom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { MemoryRouter } from "react-router";
import { afterEach, expect, it, vi } from "vitest";
import { $chatRuns, $unreadChatRuns, $chatRunsReachable, type ChatRun } from "@/lib/chat-runs";
import { AgentRunBadge, SessionRunActivity } from "./SessionRunActivity";

const run: ChatRun = { message_id: "message-lawyer", session_id: "session-lawyer", profile: "lawyer", status: "running", updated_at: 1, history_count: 0, user_message: { role: "user", content: "Договор" } };
afterEach(() => { $chatRuns.set([]); $unreadChatRuns.set([]); $chatRunsReachable.set(null); vi.unstubAllGlobals(); });
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

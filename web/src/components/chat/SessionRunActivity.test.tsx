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


it("серверный unread не зависит от часов браузера и чтение ставит watermark", async () => {
  const completed = {
    ...run,
    message_id: "message-server-revision",
    status: "completed",
    updated_at: 1, // намеренно намного старше часов браузера
    unread: true,
    event_revision: "1.000000",
  } as ChatRun;
  const fetcher = vi.fn<typeof fetch>(async (input) => {
    if (String(input).includes("/api/chat/runs")) {
      return Response.json({ runs: [completed] });
    }
    return Response.json({ ok: true });
  });
  vi.stubGlobal("fetch", fetcher);

  await refreshChatRuns();
  expect($unreadChatRuns.get().map(item => item.message_id)).toContain(completed.message_id);
  markChatViewed("lawyer", "session-lawyer");
  await Promise.resolve();
  const patch = fetcher.mock.calls.find(([url, init]) =>
    String(url).includes("/api/sessions/session-lawyer") && init?.method === "PATCH");
  expect(JSON.parse(String(patch?.[1]?.body))).toMatchObject({
    unread: false,
    profile: "lawyer",
  });
});


it("показывает ожидание решения и подтверждённые metadata задачи", async () => {
  const waiting = {
    ...run,
    status: "waiting_decision",
    title: "Договор клиента",
    channel: "telegram",
    started_at: 1,
  } as ChatRun;
  $chatRuns.set([waiting]);
  $chatRunsReachable.set(true);
  const container = document.createElement("div"); document.body.append(container);
  const root = createRoot(container);
  await act(async () => root.render(<MemoryRouter>
    <SessionRunActivity tabs={[{ profile: "lawyer", label: "Юрист" }]} />
  </MemoryRouter>));
  expect(container.textContent).toContain("Ожидает вашего решения");
  expect(container.textContent).toContain("Договор клиента");
  expect(container.textContent).toContain("telegram");
  await act(async () => root.unmount()); container.remove();
});


it("называет счётчик текущей работой, а не количеством пользовательских задач", async () => {
  $chatRuns.set([run]);
  const container = document.createElement("div"); document.body.append(container);
  const root = createRoot(container);
  await act(async () => root.render(<MemoryRouter>
    <SessionRunActivity tabs={[{ profile: "lawyer", label: "Юрист" }]} />
  </MemoryRouter>));
  expect(container.querySelector("summary")?.textContent).toContain("В работе: 1");
  expect(container.querySelector("summary")?.getAttribute("aria-label")).toBe("Работа агентов: 1");
  expect(container.textContent).not.toContain("Задачи: 1");
  await act(async () => root.unmount()); container.remove();
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
  // Уведомление живёт в верхней безопасной области у правого края: оно не
  // висит над composer/Send/Stop снизу и не закрывает полосу вкладок,
  // которая начинается сразу под шапкой (прежнее `top-16`).
  expect(toast.className).toMatch(/top-\[max\(0\.5rem,env\(safe-area-inset-top/);
  expect(toast.className).toMatch(/right-\[max\(0\.75rem,env\(safe-area-inset-right/);
  expect(toast.className).not.toMatch(/bottom-/);
  expect(toast.className).not.toMatch(/(?:^|\s)(?:left-\d|top-16)/);
  expect(toast.querySelectorAll("a")).toHaveLength(2);
  // Телефон показывает одну строку и счётчик остальных, широкий экран — все.
  const [first, second] = [...toast.querySelectorAll("li")];
  expect(first.className).not.toMatch(/hidden/);
  expect(second.className).toMatch(/hidden lg:block/);
  expect(toast.querySelector("[data-run-toast-rest='compact']")?.textContent).toContain("+1");
  expect(toast.querySelector("[data-run-toast-rest='wide']")).toBeNull();
  // Скрыть — цель пальца 44 px, подпись доступна скринридеру.
  const dismiss = toast.querySelector("button") as HTMLButtonElement;
  expect(dismiss.getAttribute("aria-label")).toBe("Скрыть уведомление");
  // Шкала Tailwind здесь умножена на плотность темы, поэтому цель
  // пальца задаётся в пикселях, а не в единицах шкалы.
  expect(dismiss.className).toMatch(/size-\[44px\]/);
  await act(async () => dismiss.click());
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

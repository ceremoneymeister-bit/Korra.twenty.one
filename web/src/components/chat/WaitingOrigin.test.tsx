// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter, useLocation } from "react-router";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { $chatRuns, $chatRunsReachable, $unreadChatRuns, type ChatRun } from "@/lib/chat-runs";
import { attentionView } from "@/lib/dashboard-state";
import { agentChatHref } from "@/lib/agent-tabs";
import { normalizeApprovalRequest } from "@/lib/chat-approvals";
import { AgentRunBadge, SessionRunActivity } from "./SessionRunActivity";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
const waiting: ChatRun = {
  message_id: "effect-origin", session_id: "existing-origin", profile: "owner",
  title: "ТЗ сотруднице", status: "waiting_decision", updated_at: 1,
  history_count: 1, pending_decisions: 1,
  user_message: { role: "user", content: "Ожидает вашего решения" },
};
const fallback = {
  ...waiting, message_id: "effect-missing", profile: "sender", session_id: "deleted-origin",
  title: null, decision_fallback: true,
  decision_reason: "Исходный чат удалён. Ответьте в центре решений.",
};
let container: HTMLDivElement;
let root: Root;
let runs: ChatRun[];
beforeEach(() => {
  runs = [];
  vi.stubGlobal("fetch", vi.fn(async () => Response.json({ runs })));
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
});
afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
  $chatRuns.set([]); $chatRunsReachable.set(null); $unreadChatRuns.set([]);
  vi.unstubAllGlobals();
});
async function render(next: ChatRun[]) {
  runs = next;
  $chatRuns.set(next); $chatRunsReachable.set(true);
  await act(async () => root.render(<MemoryRouter><SessionRunActivity tabs={[
    { profile: "owner", label: "Помощник" }, { profile: "sender", label: "Ирина" },
  ]} /></MemoryRouter>));
}
function attention(next: ChatRun[]) {
  return attentionView(null, "ready", {
    runs: next, known: true, reachable: true,
    labelFor: profile => profile === "owner" ? "Помощник" : "Ирина",
    hrefFor: (profile, session) => agentChatHref(profile, session ?? undefined),
  }).rows;
}

it("ожидание называет агента и исходный чат и ведёт в тот же существующий разговор", async () => {
  await render([waiting]);
  const link = container.querySelector("a");
  expect(link?.textContent).toContain("Помощник · ТЗ сотруднице");
  expect(link?.getAttribute("href")).toBe("/agents?agent=owner&resume=existing-origin");
  expect(attention([waiting])[0]).toMatchObject({
    title: "Помощник ждёт вашего решения · ТЗ сотруднице",
    href: "/agents?agent=owner&resume=existing-origin",
  });
});

it("удалённый чат объяснён, а переход раскрывает центр решений вместо несуществующего разговора", async () => {
  await render([fallback]);
  const link = container.querySelector("a");
  expect(link?.textContent).toContain(fallback.decision_reason);
  expect(link?.textContent).toContain("Открыть решения");
  expect(link?.getAttribute("href")).toBe("/agents?agent=sender&decisions=1");
  expect(attention([fallback])[0]).toMatchObject({
    detail: fallback.decision_reason, action: "Открыть решения",
    href: "/agents?agent=sender&decisions=1",
  });
});

it("один pending не повторяется, но разные недоступные исходники не теряются", async () => {
  const second = { ...fallback, message_id: "effect-other" };
  await render([waiting, { ...waiting, message_id: "same-chat" }, fallback, fallback, second]);
  expect(container.querySelectorAll("a")).toHaveLength(3);
  expect(attention([waiting, waiting, fallback, fallback, second])).toHaveLength(3);
});

function LocationProbe() { return <output>{useLocation().search}</output>; }
it("сама метка «Ждёт вас» называет агента и чат, а нажатие открывает исходник", async () => {
  runs = [waiting]; $chatRuns.set(runs);
  await act(async () => root.render(<MemoryRouter><AgentRunBadge profile="owner" label="Помощник" /><LocationProbe /></MemoryRouter>));
  const link = container.querySelector<HTMLAnchorElement>("a")!;
  expect(link.title).toContain("Помощник ждёт вас · ТЗ сотруднице");
  expect(link.getAttribute("aria-label")).toContain("ТЗ сотруднице");
  await act(async () => link.click());
  expect(container.querySelector("output")?.textContent).toBe("?agent=owner&resume=existing-origin");
});

it("несколько чатов у метки явно открывают список решений, а не случайный чат", async () => {
  runs = [waiting, { ...waiting, message_id: "other", session_id: "other-chat", title: "Второй проект" }];
  $chatRuns.set(runs);
  const open = vi.fn();
  await act(async () => root.render(<MemoryRouter><AgentRunBadge profile="owner" label="Помощник" onShowWaiting={open} /></MemoryRouter>));
  const button = container.querySelector<HTMLButtonElement>("button")!;
  expect(button.title).toContain("ТЗ сотруднице; Второй проект");
  expect(button.getAttribute("aria-label")).toContain("Открыть список ожидающих чатов");
  await act(async () => button.click());
  expect(open).toHaveBeenCalledOnce();
});

it("запрет ответа при неоднозначном ledger не превращается в кнопки разрешения", () => {
  const request = normalizeApprovalRequest({ request_id: "effect_ambiguous", decision_kind: "outbound_message", effect_status: "pending", decision_fallback: true, decision_reason: "Найдены разные записи", choices: [] });
  expect(request?.choices).toEqual([]);
});

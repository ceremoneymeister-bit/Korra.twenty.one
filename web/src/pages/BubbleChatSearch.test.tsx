// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { api, type SessionInfo, type SessionSearchResult } from "@/lib/api";
import { BubbleChatSidebar } from "./BubbleChatPage";

const row = (id: string, title: string): SessionInfo => ({
  id, title, source: "dashboard", model: null, started_at: 1, ended_at: null,
  last_active: 1, is_active: false, message_count: 2, tool_call_count: 0,
  input_tokens: 0, output_tokens: 0, preview: null,
});
const hit = (id: string, title: string, snippet = ""): SessionSearchResult => ({
  ...row(id, title), session_id: id, snippet, role: snippet ? "user" : null, session_started: 1,
});
let root: Root, container: HTMLDivElement;
const onSelect = vi.fn();
beforeEach(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  vi.useFakeTimers(); onSelect.mockReset();
  container = document.createElement("div"); document.body.append(container); root = createRoot(container);
});
afterEach(async () => {
  await act(async () => root.unmount()); container.remove(); vi.restoreAllMocks(); vi.useRealTimers();
});
async function render(revision = 0) {
  await act(async () => root.render(<MemoryRouter><BubbleChatSidebar
    sessions={[row("recent", "Недавний чат")]} profile="designer" activeId="recent"
    loading={false} error={null} onSelect={onSelect} onNewChat={vi.fn()} onRequestDelete={vi.fn()}
    historyRevision={revision}
  /></MemoryRouter>));
}
function input() { return container.querySelector<HTMLInputElement>('input[type="search"]')!; }
async function type(value: string) {
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(input(), value);
    input().dispatchEvent(new Event("input", { bubbles: true }));
  });
}
async function key(element: Element, value: string) {
  await act(async () => { element.dispatchEvent(new KeyboardEvent("keydown", { key: value, bubbles: true })); });
}
async function tick() { await act(async () => { await vi.advanceTimersByTimeAsync(250); }); }

it("keeps rows during loading, gives context and opens the current result with Enter", async () => {
  const search = vi.spyOn(api, "searchSessions").mockResolvedValue({ results:[hit("older", "Встреча", "Обсудим >>>запуск<<< сайта")] });
  await render(); await type("запуск");
  expect(container.textContent).toContain("Недавний чат");
  await key(input(), "Enter"); expect(onSelect).not.toHaveBeenCalled();
  await tick();
  expect(container.querySelector("mark")?.textContent).toBe("запуск");
  expect(container.textContent).toContain("Обсудим запуск сайта");
  await key(input(), "Enter"); expect(onSelect).toHaveBeenLastCalledWith("older");
  search.mockImplementationOnce(() => new Promise(() => {}));
  await type("запуск осень"); await tick();
  expect(container.textContent).toContain("Встреча");
  expect(container.textContent).toContain("Обновляем результаты");
  onSelect.mockClear(); await key(input(), "Enter"); expect(onSelect).not.toHaveBeenCalled();
});

it("moves through results with arrows and returns to the full list with Escape", async () => {
  vi.spyOn(api, "searchSessions").mockResolvedValue({ results:[hit("one", "План сайта"), hit("two", "План встречи")] });
  await render(); await type("план"); await tick();
  const rows = container.querySelectorAll<HTMLButtonElement>(".korra-chat-history__item");
  await key(input(), "ArrowDown"); expect(document.activeElement).toBe(rows[0]);
  await key(rows[0], "ArrowDown"); expect(document.activeElement).toBe(rows[1]);
  await act(async () => rows[1].click()); expect(onSelect).toHaveBeenLastCalledWith("two");
  await key(rows[1], "Escape"); expect(document.activeElement).toBe(input());
  expect(input().value).toBe(""); expect(container.textContent).toContain("Недавний чат");
});

it("refreshes a deleted search hit even when the first page has not changed", async () => {
  const search = vi.spyOn(api, "searchSessions").mockResolvedValueOnce({ results:[hit("older", "Архив")] }).mockResolvedValue({ results:[] });
  await render(); await type("архив"); await tick();
  expect(container.textContent).toContain("Архив");
  await render(1); await tick();
  expect(search).toHaveBeenCalledTimes(2);
  expect(container.querySelectorAll(".korra-chat-history__item")).toHaveLength(0);
  expect(container.textContent).toContain("Ничего не найдено");
});

it("renders untrusted snippets as text and highlights Cyrillic titles", async () => {
  vi.spyOn(api, "searchSessions").mockResolvedValue({ results:[hit("older", "ЗАПУСК САЙТА", '<img src=x onerror="evil()"> >>>запуск<<<')] });
  await render(); await type("запуск"); await tick();
  expect(container.querySelector("img")).toBeNull();
  expect(container.querySelector(".korra-chat-history__title mark")?.textContent).toBe("ЗАПУСК");
  expect(container.textContent).toContain('<img src=x onerror="evil()"> запуск');
});

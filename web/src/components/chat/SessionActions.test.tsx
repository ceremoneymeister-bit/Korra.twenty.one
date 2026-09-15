// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { SessionActions } from "./SessionActions";
import { api } from "@/lib/api";
vi.mock("@/lib/api", () => ({ api: { renameSession: vi.fn() } }));
let host: HTMLDivElement;
let root: Root;
const renamed = vi.fn();
const deleted = vi.fn();
beforeEach(() => {
  vi.resetAllMocks();
  vi.mocked(api.renameSession).mockResolvedValue({ ok: true, title: "Новое название" });
  host = document.createElement("div"); document.body.append(host); root = createRoot(host);
});
afterEach(async () => { await act(async () => root.unmount()); host.remove(); });
async function render(profile = "designer", id = "chat-1") {
  await act(async () => root.render(<SessionActions key={`${profile}:${id}`} profile={profile} sessionId={id}
    title="Первое название" onRenamed={renamed} onDelete={deleted} />));
}
async function open() {
  const trigger = host.querySelector('button')!;
  await act(async () => { trigger.focus(); trigger.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true })); });
  const rename = [...document.querySelectorAll('[role="menuitem"]')].find(e => e.textContent?.includes("Переименовать"))!;
  await act(async () => { rename.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true })); });
}
async function type(value: string) {
  const input = document.querySelector('input')!;
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(input, value);
    input.dispatchEvent(new Event('input', { bubbles: true }));
  });
}
async function submit() {
  await act(async () => { document.querySelector('form')!.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })); });
}
it("renames exactly the row's session and profile, then refreshes only the list", async () => {
  await render(); await open(); await type("Договор — согласовать с юристом"); await submit();
  expect(api.renameSession).toHaveBeenCalledWith("chat-1", "Договор — согласовать с юристом", "designer");
  expect(renamed).toHaveBeenCalledOnce(); expect(deleted).not.toHaveBeenCalled();
  expect(document.querySelector('[role="dialog"]')).toBeNull();
  expect(document.activeElement).toBe(host.querySelector('button'));
});
it("cancels with Escape without touching the server", async () => {
  await render(); await open(); await type("Отмена");
  await act(async () => { document.activeElement!.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true })); });
  expect(document.querySelector('[role="dialog"]')).toBeNull();
  expect(api.renameSession).not.toHaveBeenCalled();
});
it.each([
  ['Failed to fetch', 'Проверьте интернет'],
  ['400: {"detail":"Title too long (101 chars, max 100)"}', 'не больше 100'],
  ['400: {"detail":"Title already in use by session other"}', 'уже есть'],
])("keeps input after %s so the owner can correct or retry", async (message, hint) => {
  vi.mocked(api.renameSession).mockRejectedValueOnce(new Error(message));
  await render(); await open(); await type("Не терять этот ввод"); await submit();
  expect(document.querySelector('input')?.value).toBe("Не терять этот ввод");
  expect(document.querySelector('[role="alert"]')?.textContent).toContain(hint);
  expect(renamed).not.toHaveBeenCalled();
  await submit(); expect(renamed).toHaveBeenCalledOnce();
});
it("keeps the existing empty-title contract", async () => {
  await render(); await open(); await type(""); await submit();
  expect(api.renameSession).toHaveBeenCalledWith("chat-1", "", "designer");
});
it("a late rename cannot refresh or close the new profile's editor", async () => {
  let finish!: (value: { ok: boolean; title: string }) => void;
  vi.mocked(api.renameSession).mockReturnValueOnce(new Promise(resolve => { finish = resolve; }));
  await render(); await open(); await type("Первый профиль"); await submit();
  await render("lawyer", "chat-1"); await open(); await type("Второй профиль");
  await act(async () => finish({ ok: true, title: "Первый профиль" }));
  expect(document.querySelector('input')?.value).toBe("Второй профиль");
  expect(renamed).not.toHaveBeenCalled();
});

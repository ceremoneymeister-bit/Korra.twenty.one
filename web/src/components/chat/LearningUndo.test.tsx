// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import { api } from "@/lib/api";
import { LearningUndo } from "./LearningUndo";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
let host: HTMLDivElement;
let root: Root;
beforeEach(() => {
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});
afterEach(async () => {
  await act(async () => root.unmount());
  host.remove();
  vi.restoreAllMocks();
});

const button = () => host.querySelector<HTMLButtonElement>("button");

it("отменяет изменение именно этого сообщения и показывает «Отменено»", async () => {
  const undo = vi.spyOn(api, "undoLearningNotice").mockResolvedValue({
    ok: true, status: "undone", message: "Отменено: заметка удалена",
  });
  await act(async () => root.render(<LearningUndo sessionId="s1" messageId={7} profile="work" undone={false} />));
  expect(button()?.textContent).toBe("Отменить");
  await act(async () => button()?.click());
  expect(undo).toHaveBeenCalledWith("s1", 7, "work");
  expect(host.textContent).toBe("Отменено: заметка удалена");
  expect(button()).toBeNull();
});

it("при конфликте объясняет причину и оставляет кнопку", async () => {
  vi.spyOn(api, "undoLearningNotice").mockResolvedValue({
    ok: false, status: "conflict", message: "Навык уже менялся. Ничего не изменено.",
  });
  await act(async () => root.render(<LearningUndo sessionId="s1" messageId={7} profile="" undone={false} />));
  await act(async () => button()?.click());
  expect(host.textContent).toContain("Ничего не изменено");
  expect(button()).not.toBeNull();
});

it("уже отменённое показывается без кнопки", async () => {
  await act(async () => root.render(<LearningUndo sessionId="s1" messageId={7} profile="" undone />));
  expect(host.textContent).toContain("Отменено");
  expect(button()).toBeNull();
});

// @vitest-environment jsdom

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AgentSheet } from "./AgentSheet";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
});

function sheet(onClose: () => void, rows: number) {
  return (
    <AgentSheet titleId="chats-title" title="Чаты" onClose={onClose}>
      <input aria-label="Поиск в чатах" />
      {Array.from({ length: rows }, (_, index) => <p key={index}>Разговор {index}</p>)}
    </AgentSheet>
  );
}

describe("шторка агента", () => {
  it("обновление списка не выбивает фокус из поиска (0.21.15 Astra review)", async () => {
    await act(async () => root.render(sheet(() => {}, 1)));
    const search = container.querySelector<HTMLInputElement>("input[aria-label='Поиск в чатах']");
    search?.focus();
    expect(document.activeElement).toBe(search);

    // Родитель перерисовывается с новым onClose — как при опросе истории.
    await act(async () => root.render(sheet(() => {}, 2)));
    expect(document.activeElement).toBe(search);
  });

  it("Escape закрывает последним переданным обработчиком", async () => {
    const first = vi.fn();
    const latest = vi.fn();
    await act(async () => root.render(sheet(first, 1)));
    await act(async () => root.render(sheet(latest, 1)));
    await act(async () => {
      document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
    });
    expect(first).not.toHaveBeenCalled();
    expect(latest).toHaveBeenCalledTimes(1);
  });
});

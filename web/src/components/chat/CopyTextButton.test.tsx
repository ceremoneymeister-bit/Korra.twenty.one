// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { Markdown } from "../Markdown";
import { copyTextToClipboard } from "@/lib/clipboard";

vi.mock("@/lib/clipboard", () => ({ copyTextToClipboard: vi.fn() }));

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
  vi.resetAllMocks();
});

it("копирует только выбранный код, сохраняя пробелы, без разметки и курсора потока", async () => {
  const code = "if ready:\n    print('Готово')";
  vi.mocked(copyTextToClipboard).mockResolvedValue(true);
  await act(async () => root.render(<Markdown content={"```text\nПервый\n```\n\n```python\n" + code} streaming />));
  const buttons = host.querySelectorAll<HTMLButtonElement>('button[aria-label="Скопировать код"]');
  await act(async () => buttons[1].click());
  expect(copyTextToClipboard).toHaveBeenCalledWith(code);
  expect(buttons[1].textContent).toContain("Скопировано");
  expect(buttons[0].textContent).toBe("Скопировать код");
});

it("не сообщает об успехе при отказе буфера и даёт повторить копирование", async () => {
  vi.mocked(copyTextToClipboard).mockResolvedValueOnce(false).mockResolvedValueOnce(true);
  await act(async () => root.render(<Markdown content={'```\nСодержимое\n```'} />));
  const button = host.querySelector<HTMLButtonElement>("button")!;
  await act(async () => button.click());
  expect(button.textContent).toContain("Не скопировалось");
  await act(async () => button.click());
  expect(button.textContent).toContain("Скопировано");
});

// @vitest-environment jsdom

import { act, type ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const attachmentMocks = vi.hoisted(() => ({
  uploadAttachment: vi.fn(),
}));

vi.mock("@/lib/chat-attachments", async () => {
  const actual = await vi.importActual<typeof import("@/lib/chat-attachments")>(
    "@/lib/chat-attachments",
  );
  return {
    ...actual,
    uploadAttachment: attachmentMocks.uploadAttachment,
  };
});

import { BubbleChatComposer } from "./BubbleChatPage";

let container: HTMLDivElement;
let root: Root;

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT =
  true;

async function render(ui: ReactNode) {
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  await act(async () => root.render(ui));
}

async function enterText(textarea: HTMLTextAreaElement, value: string) {
  const setter = Object.getOwnPropertyDescriptor(
    HTMLTextAreaElement.prototype,
    "value",
  )?.set;
  await act(async () => {
    setter?.call(textarea, value);
    textarea.dispatchEvent(new Event("input", { bubbles: true }));
  });
}

beforeEach(() => {
  let id = 0;
  vi.stubGlobal("crypto", {
    randomUUID: () => `attachment-${++id}`,
  });
  vi.stubGlobal("requestAnimationFrame", (callback: FrameRequestCallback) => {
    queueMicrotask(() => callback(0));
    return 1;
  });
  vi.stubGlobal("cancelAnimationFrame", () => {});
  attachmentMocks.uploadAttachment.mockReset();
  attachmentMocks.uploadAttachment.mockImplementation(
    (file: File, onProgress: (percent: number) => void) => {
      onProgress(100);
      return {
        abort: vi.fn(),
        promise: Promise.resolve({
          path: `/tmp/${file.name}`,
          name: file.name,
          kind: file.name.split(".").pop() ?? "bin",
          size: file.size,
          reader: "read_file",
        }),
      };
    },
  );
});

afterEach(async () => {
  await act(async () => root?.unmount());
  container?.remove();
  vi.unstubAllGlobals();
});

describe("BubbleChatComposer", () => {
  it("keeps send inactive for an empty draft and activates it for text", async () => {
    const onSend = vi.fn();
    await render(<BubbleChatComposer onSend={onSend} />);

    const textarea = container.querySelector("textarea")!;
    const send = container.querySelector<HTMLButtonElement>(
      'button[aria-label="Отправить"]',
    )!;

    expect(textarea.placeholder).toBe("Напишите Корре…");
    expect(textarea.labels?.[0]?.textContent).toBe("Сообщение Корре");
    expect(send.disabled).toBe(true);

    await enterText(textarea, "Привет, Корра");
    expect(send.disabled).toBe(false);
  });

  it("sends with Enter and leaves Shift+Enter for a new line", async () => {
    const onSend = vi.fn();
    await render(<BubbleChatComposer onSend={onSend} />);
    const textarea = container.querySelector("textarea")!;
    await enterText(textarea, "Проверь расчёт");

    const newline = new KeyboardEvent("keydown", {
      bubbles: true,
      cancelable: true,
      key: "Enter",
      shiftKey: true,
    });
    await act(async () => textarea.dispatchEvent(newline));
    expect(newline.defaultPrevented).toBe(false);
    expect(onSend).not.toHaveBeenCalled();

    const submit = new KeyboardEvent("keydown", {
      bubbles: true,
      cancelable: true,
      key: "Enter",
    });
    await act(async () => textarea.dispatchEvent(submit));
    expect(submit.defaultPrevented).toBe(true);
    expect(onSend).toHaveBeenCalledWith("Проверь расчёт", []);
  });

  it("grows smoothly to 160px, then scrolls, and can shrink again", async () => {
    await render(<BubbleChatComposer onSend={vi.fn()} />);
    const textarea = container.querySelector("textarea")!;
    let scrollHeight = 88;
    Object.defineProperty(textarea, "scrollHeight", {
      configurable: true,
      get: () => scrollHeight,
    });

    await enterText(textarea, "Несколько\nстрок\nтекста");
    expect(textarea.style.height).toBe("88px");
    expect(textarea.style.overflowY).toBe("hidden");

    scrollHeight = 240;
    await enterText(textarea, "Очень\nдлинный\nтекст\nна\nмного\nстрок\nниже");
    expect(textarea.style.height).toBe("160px");
    expect(textarea.style.overflowY).toBe("auto");

    scrollHeight = 40;
    await enterText(textarea, "Коротко");
    expect(textarea.style.height).toBe("40px");
    expect(textarea.style.overflowY).toBe("hidden");
  });

  it("keeps paste upload behavior and renders a removable attachment chip", async () => {
    await render(<BubbleChatComposer onSend={vi.fn()} />);
    const textarea = container.querySelector("textarea")!;
    const file = new File([new Uint8Array(2048)], "смета.txt", {
      type: "text/plain",
    });
    const paste = new Event("paste", { bubbles: true, cancelable: true });
    Object.defineProperty(paste, "clipboardData", {
      value: { files: [file] },
    });

    await act(async () => {
      textarea.dispatchEvent(paste);
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(paste.defaultPrevented).toBe(true);
    const list = container.querySelector('[role="list"]');
    expect(list?.getAttribute("aria-label")).toBe("Прикреплённые файлы");
    expect(list?.textContent).toContain("смета.txt");
    expect(list?.textContent).toContain("TXT · 2 КБ");

    const remove = container.querySelector<HTMLButtonElement>(
      'button[aria-label="Убрать смета.txt"]',
    )!;
    expect(remove.className).toContain("size-10");
    await act(async () => remove.click());
    expect(container.querySelector('[role="listitem"]')).toBeNull();
  });

  it("highlights the whole dropzone while files are dragged over it", async () => {
    await render(<BubbleChatComposer onSend={vi.fn()} />);
    const composer = container.querySelector(".korra-chat-composer")!;
    const dropzone = container.querySelector<HTMLElement>(
      ".korra-chat-composer__dropzone",
    )!;
    const dragOver = new Event("dragover", { bubbles: true, cancelable: true });
    Object.defineProperty(dragOver, "dataTransfer", {
      value: { files: [], types: ["Files"] },
    });

    await act(async () => composer.dispatchEvent(dragOver));
    expect(dragOver.defaultPrevented).toBe(true);
    expect(dropzone.dataset.dragging).toBe("true");
    expect(dropzone.textContent).toContain("Отпустите файлы, чтобы прикрепить");
  });

  it("shows sending and streaming states, then exposes abort", async () => {
    let finishSend: ((delivered: boolean) => void) | undefined;
    const onSend = vi.fn(
      () =>
        new Promise<boolean>((resolve) => {
          finishSend = resolve;
        }),
    );
    await render(<BubbleChatComposer onSend={onSend} />);
    const textarea = container.querySelector("textarea")!;
    await enterText(textarea, "Запусти");
    await act(async () =>
      container
        .querySelector<HTMLButtonElement>('button[aria-label="Отправить"]')
        ?.click(),
    );

    const sending = container.querySelector<HTMLButtonElement>(
      'button[aria-label="Отправляется"]',
    );
    expect(sending?.disabled).toBe(true);
    expect(container.querySelector('[role="status"]')?.textContent).toContain(
      "Отправляется",
    );

    await act(async () => finishSend?.(true));
    await act(async () =>
      root.render(
        <BubbleChatComposer
          onSend={onSend}
          streaming
          disabled
          onAbort={vi.fn()}
        />,
      ),
    );
    expect(container.querySelector('[role="status"]')?.textContent).toContain(
      "Отправляется",
    );
    expect(
      container.querySelector('button[aria-label="Остановить генерацию"]'),
    ).not.toBeNull();

    await act(async () =>
      root.render(
        <BubbleChatComposer
          onSend={onSend}
          streaming
          disabled
          responding
          onAbort={vi.fn()}
        />,
      ),
    );

    const stop = container.querySelector<HTMLButtonElement>(
      'button[aria-label="Остановить генерацию"]',
    )!;
    const surface = container.querySelector<HTMLElement>(
      '[role="group"][aria-label="Сообщение и вложения"]',
    )!;
    expect(stop.disabled).toBe(false);
    expect(stop.className).toContain("min-h-11");
    expect(surface.dataset.state).toBe("streaming");
    expect(surface.getAttribute("aria-busy")).toBe("true");
    expect(container.querySelector("textarea")?.disabled).toBe(true);
    expect(container.querySelector('[role="status"]')?.textContent).toContain(
      "Корра отвечает",
    );

    const onAbort = vi.fn();
    await act(async () =>
      root.render(
        <BubbleChatComposer
          onSend={onSend}
          streaming
          disabled
          responding
          onAbort={onAbort}
        />,
      ),
    );
    await act(async () =>
      container
        .querySelector<HTMLButtonElement>(
          'button[aria-label="Остановить генерацию"]',
        )
        ?.click(),
    );
    expect(onAbort).toHaveBeenCalledOnce();
  });
});

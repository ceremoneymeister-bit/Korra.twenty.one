// @vitest-environment jsdom

import { act, StrictMode, type ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const attachmentMocks = vi.hoisted(() => ({
  createUpload: vi.fn(),
  completeUpload: vi.fn(),
  runUploadQueue: vi.fn(),
}));

const apiMocks = vi.hoisted(() => ({
  transcribeAudio: vi.fn(),
}));

vi.mock("@/lib/upload-session", async () => ({
  ...await vi.importActual<typeof import("@/lib/upload-session")>("@/lib/upload-session"),
  createUpload: attachmentMocks.createUpload, completeUpload: attachmentMocks.completeUpload,
}));
vi.mock("@/lib/upload-queue", async () => ({
  ...await vi.importActual<typeof import("@/lib/upload-queue")>("@/lib/upload-queue"),
  runUploadQueue: attachmentMocks.runUploadQueue,
}));

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return { ...actual, transcribeAudio: apiMocks.transcribeAudio };
});

// Орбита рисует себя через requestAnimationFrame и canvas — в jsdom это шум,
// проверяем только то, что она встаёт на место иконки.
vi.mock("thinking-orbs", () => ({
  ThinkingOrb: ({ state }: { state: string }) => (
    <span data-orb={state} data-testid="orb" />
  ),
}));

import { BubbleChatComposer, BubbleChatSidebar, BubbleChatTranscript } from "./BubbleChatPage";
import { $uploadJobs, dismissUploadJob } from "@/store/upload-jobs";
import type { SessionInfo } from "@/lib/api";
import type { UploadManifest } from "@/lib/upload-session";
import type { ChatMessage } from "@/lib/chat-types";

/** `MediaRecorder` из jsdom не существует — подменяем предсказуемым. */
class FakeMediaRecorder {
  static isTypeSupported(type: string): boolean {
    return type === "audio/webm;codecs=opus";
  }

  state: "inactive" | "recording" = "inactive";
  stream: MediaStream;
  mimeType: string;
  ondataavailable: ((event: { data: Blob }) => void) | null = null;
  onstop: (() => void) | null = null;
  onerror: (() => void) | null = null;

  constructor(stream: MediaStream, options?: { mimeType?: string }) {
    this.stream = stream;
    this.mimeType = options?.mimeType ?? "audio/webm";
  }

  start(): void {
    this.state = "recording";
  }

  stop(): void {
    this.state = "inactive";
    this.ondataavailable?.({
      data: new Blob(["звук"], { type: this.mimeType }),
    });
    this.onstop?.();
  }
}

/** Дать окружению рабочий микрофон; возвращает дорожку, которую композер
 *  обязан отпустить после записи. */
function enableMicrophone() {
  const track = { stop: vi.fn() };
  Object.defineProperty(window, "isSecureContext", {
    configurable: true,
    value: true,
    writable: true,
  });
  Object.defineProperty(navigator, "mediaDevices", {
    configurable: true,
    value: {
      getUserMedia: vi
        .fn()
        .mockResolvedValue({ getTracks: () => [track] } as unknown as MediaStream),
    },
    writable: true,
  });
  vi.stubGlobal("MediaRecorder", FakeMediaRecorder);
  return track;
}

let container: HTMLDivElement;
let root: Root;

function microphoneButton(): HTMLButtonElement {
  return container.querySelector<HTMLButtonElement>(
    ".korra-chat-composer__microphone",
  )!;
}

/** Нажатие микрофона: включение и остановка одинаково асинхронны. */
async function pressMicrophone() {
  await act(async () => {
    microphoneButton().click();
  });
  await act(async () => {});
}

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT =
  true;

async function render(ui: ReactNode) {
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  await act(async () => root.render(<MemoryRouter>{ui}</MemoryRouter>));
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
  apiMocks.transcribeAudio.mockReset();
  apiMocks.transcribeAudio.mockResolvedValue("");
  attachmentMocks.createUpload.mockReset();
  attachmentMocks.completeUpload.mockReset();
  attachmentMocks.runUploadQueue.mockReset();
  const manifests = new Map<string, UploadManifest>();
  attachmentMocks.createUpload.mockImplementation(async (manifest: UploadManifest) => {
    manifests.set(manifest.upload_id, manifest);
    return { upload_id: manifest.upload_id, published: false, received: [], already_present: [] };
  });
  attachmentMocks.runUploadQueue.mockResolvedValue({ completed: [], failed: [] });
  attachmentMocks.completeUpload.mockImplementation(async (id: string) => {
    const manifest = manifests.get(id)!;
    return { published: true, files: manifest.files.map((file, index) => ({
      index, path: `/tmp/${file.path}`, name: file.path, kind: file.path.split(".").pop(), size: file.size, reader: "read_file",
    })), folder: manifest.name ? { path: "/tmp/batch", name: manifest.name, file_count: manifest.files.length,
      total_bytes: manifest.files.reduce((sum, file) => sum + file.size, 0) } : null, skipped: [], excluded: [] };
  });
});

afterEach(async () => {
  await act(async () => root?.unmount());
  container?.remove();
  Object.keys($uploadJobs.get()).forEach(dismissUploadJob);
  vi.unstubAllGlobals();
});

describe("BubbleChatComposer", () => {
  it("не дублирует загрузку одного файла при повторном выборе и StrictMode", async () => {
    await render(<StrictMode><BubbleChatComposer onSend={vi.fn()} /></StrictMode>);
    const input = container.querySelector<HTMLInputElement>('input[type="file"]')!;
    const file = new File(["данные"], "данные.txt", { lastModified: 1 });
    Object.defineProperty(input, "files", { configurable: true, value: [file, file] });
    await act(async () => { input.dispatchEvent(new Event("change", { bubbles: true })); });
    await act(async () => { input.dispatchEvent(new Event("change", { bubbles: true })); });
    expect(attachmentMocks.createUpload).toHaveBeenCalledTimes(1);
    expect(container.querySelectorAll('[role="listitem"]')).toHaveLength(1);
  });

  it("объясняет пустой и слишком большой файл до загрузки", async () => {
    await render(<BubbleChatComposer onSend={vi.fn()} />);
    const input = container.querySelector<HTMLInputElement>('input[type="file"]')!;
    const empty = new File([], "пустой.txt");
    const big = new File(["данные"], "большой.xlsx");
    Object.defineProperty(big, "size", { value: 2 * 1024 ** 3 + 1 });
    for (const file of [empty, big]) {
      Object.defineProperty(input, "files", { configurable: true, value: [file] });
      await act(async () => { input.dispatchEvent(new Event("change", { bubbles: true })); });
      expect(container.querySelector('[role="alert"]')?.textContent).toContain(file.name);
    }
    expect(attachmentMocks.createUpload).not.toHaveBeenCalled();
  });
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
    expect(send.className).toContain("korra-chat-composer__submit");

    const surface = container.querySelector<HTMLElement>(
      ".korra-chat-composer__surface",
    )!;
    const controls = container.querySelector<HTMLElement>(
      ".korra-chat-composer__controls",
    )!;
    expect(textarea.rows).toBe(1);
    expect(surface.contains(textarea)).toBe(true);
    expect(surface.contains(controls)).toBe(true);
    // Без микрофона в окружении диктовка честно выключена и говорит почему.
    expect(microphoneButton().disabled).toBe(true);
    expect(microphoneButton().getAttribute("aria-label")).toBe(
      "Диктовка недоступна",
    );
    expect(microphoneButton().title).toBe("Браузер не умеет записывать звук");
    expect(send.querySelector(".lucide-arrow-up")).not.toBeNull();
    expect(container.textContent).not.toContain(
      "Добавьте файлы скрепкой или перетащите сюда",
    );

    const shortcut = document.getElementById(
      textarea.getAttribute("aria-describedby")!,
    )!;
    expect(shortcut.className).toBe("sr-only");
    expect(shortcut.textContent).toContain("Shift+Enter — новая строка");

    await enterText(textarea, "Привет, Корра");
    expect(send.disabled).toBe(false);
  });

  it("keeps the two-row composer disabled without an active profile", async () => {
    await render(<BubbleChatComposer onSend={vi.fn()} disabled />);

    const surface = container.querySelector<HTMLElement>(
      ".korra-chat-composer__surface",
    )!;
    expect(surface.dataset.state).toBe("disabled");
    expect(container.querySelector<HTMLTextAreaElement>("textarea")?.disabled).toBe(
      true,
    );
    expect(
      container.querySelector<HTMLButtonElement>(
        'button[aria-label="Прикрепить файл"]',
      )?.disabled,
    ).toBe(true);
    expect(
      container.querySelector<HTMLButtonElement>(
        'button[aria-label="Отправить"]',
      )?.disabled,
    ).toBe(true);
  });

  it("always keeps the field above the controls", async () => {
    await render(<BubbleChatComposer onSend={vi.fn()} />);
    const textarea = container.querySelector("textarea")!;
    const controls = container.querySelector<HTMLElement>(
      ".korra-chat-composer__controls",
    )!;
    const surface = container.querySelector<HTMLElement>(
      ".korra-chat-composer__surface",
    )!;

    expect(textarea.parentElement).toBe(surface);
    expect(controls.parentElement).toBe(surface);
    expect(
      textarea.compareDocumentPosition(controls) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();

    await enterText(textarea, "Первая\nвторая");
    expect(textarea.rows).toBe(1);
    expect(controls.dataset.attachments).toBe("true");
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

  it("keeps a long draft compact and scrollable", async () => {
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
    expect(textarea.style.height).toBe("120px");
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
    const list = container.querySelector('[role="list"]')!;
    expect(list.getAttribute("aria-label")).toBe("Прикреплённые файлы");
    expect(list.textContent).toContain("смета.txt");
    expect(list.textContent).toContain("TXT · 2 КБ");

    const remove = container.querySelector<HTMLButtonElement>(
      'button[aria-label="Убрать смета.txt"]',
    )!;
    expect(remove.className).toBe("korra-chat-attachment-chip__action");
    const controls = container.querySelector(".korra-chat-composer__controls")!;
    expect(
      list.compareDocumentPosition(controls) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
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
        <MemoryRouter><BubbleChatComposer
          onSend={onSend}
          streaming
          disabled
          onAbort={vi.fn()}
        /></MemoryRouter>,
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
        <MemoryRouter><BubbleChatComposer
          onSend={onSend}
          streaming
          disabled
          responding
          onAbort={vi.fn()}
        /></MemoryRouter>,
      ),
    );

    const stop = container.querySelector<HTMLButtonElement>(
      'button[aria-label="Остановить генерацию"]',
    )!;
    const surface = container.querySelector<HTMLElement>(
      '[role="group"][aria-label="Сообщение и вложения"]',
    )!;
    expect(stop.disabled).toBe(false);
    expect(stop.className).toContain("korra-chat-composer__submit");
    expect(stop.textContent).toBe("");
    expect(surface.dataset.state).toBe("streaming");
    expect(surface.getAttribute("aria-busy")).toBe("true");
    expect(container.querySelector("textarea")?.disabled).toBe(true);
    expect(container.querySelector('[role="status"]')?.textContent).toContain(
      "Корра отвечает",
    );

    const onAbort = vi.fn();
    await act(async () =>
      root.render(
        <MemoryRouter><BubbleChatComposer
          onSend={onSend}
          streaming
          disabled
          responding
          onAbort={onAbort}
        /></MemoryRouter>,
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

describe("BubbleChatComposer · диктовка", () => {
  it("дописывает распознанное к набранному и не отправляет за владельца", async () => {
    const onSend = vi.fn();
    const track = enableMicrophone();
    apiMocks.transcribeAudio.mockResolvedValue("готова?");
    await render(<BubbleChatComposer onSend={onSend} profile="raschet" />);

    const textarea = container.querySelector<HTMLTextAreaElement>("textarea")!;
    await enterText(textarea, "Смета");
    expect(microphoneButton().disabled).toBe(false);
    expect(microphoneButton().title).toBe("Надиктовать сообщение");

    await pressMicrophone();
    expect(microphoneButton().dataset.state).toBe("recording");
    expect(microphoneButton().getAttribute("aria-pressed")).toBe("true");
    expect(microphoneButton().getAttribute("aria-label")).toBe(
      "Остановить запись (до 10 минут)",
    );
    expect(container.textContent).toContain("Идёт запись · максимум 10 минут");
    expect(microphoneButton().querySelector(".lucide-square")).not.toBeNull();
    expect(textarea.placeholder).toBe("Слушаю…");

    await pressMicrophone();
    expect(apiMocks.transcribeAudio).toHaveBeenCalledTimes(1);
    expect(apiMocks.transcribeAudio.mock.calls[0][2]).toBe("raschet");
    // Пробел между набранным и надиктованным ставит композер.
    expect(textarea.value).toBe("Смета готова?");
    expect(onSend).not.toHaveBeenCalled();
    expect(track.stop).toHaveBeenCalled();
    expect(microphoneButton().dataset.state).toBe("idle");
    expect(textarea.placeholder).toBe("Напишите Корре…");
  });

  it("на время распознавания показывает орбиту вместо иконки", async () => {
    enableMicrophone();
    let finish: (text: string) => void = () => {};
    apiMocks.transcribeAudio.mockReturnValue(
      new Promise<string>((resolve) => {
        finish = resolve;
      }),
    );
    await render(<BubbleChatComposer onSend={vi.fn()} />);

    await pressMicrophone();
    await pressMicrophone();
    expect(microphoneButton().dataset.state).toBe("transcribing");
    expect(microphoneButton().disabled).toBe(true);
    expect(microphoneButton().querySelector("[data-orb]")).not.toBeNull();
    expect(microphoneButton().querySelector(".lucide-mic")).toBeNull();

    await act(async () => finish("сделай смету"));
    await act(async () => {});
    expect(microphoneButton().dataset.state).toBe("idle");
    expect(container.querySelector<HTMLTextAreaElement>("textarea")!.value).toBe(
      "сделай смету",
    );
  });

  it("ненастроенное распознавание объясняет владельцу, чего не хватает", async () => {
    enableMicrophone();
    apiMocks.transcribeAudio.mockRejectedValue(
      new Error(
        "Не получилось распознать речь. Повторите или напишите текстом; если распознавание не настроено, добавьте ключ Deepgram в разделе «Ключи».",
      ),
    );
    await render(<BubbleChatComposer onSend={vi.fn()} />);

    await pressMicrophone();
    await pressMicrophone();

    // Тем же способом, что и отказы вложений — одной строкой в композере.
    expect(
      container.querySelector(".korra-chat-composer__error")?.textContent,
    ).toBe(
      "Не получилось распознать речь. Повторите или напишите текстом; если распознавание не настроено, добавьте ключ Deepgram в разделе «Ключи».",
    );
    expect(microphoneButton().dataset.state).toBe("idle");
  });

  it("на тишину не молчит, а просит повторить", async () => {
    enableMicrophone();
    apiMocks.transcribeAudio.mockResolvedValue("");
    await render(<BubbleChatComposer onSend={vi.fn()} />);

    await pressMicrophone();
    await pressMicrophone();

    expect(
      container.querySelector(".korra-chat-composer__error")?.textContent,
    ).toBe("Ничего не расслышала. Попробуйте ещё раз.");
    expect(container.querySelector<HTMLTextAreaElement>("textarea")!.value).toBe(
      "",
    );
  });

  it("во время ответа агента диктовка недоступна", async () => {
    enableMicrophone();
    await render(<BubbleChatComposer onSend={vi.fn()} streaming disabled />);
    expect(microphoneButton().disabled).toBe(true);
  });
});

/** Стороны, которым padding-утилита Tailwind даёт отступ. */
function paddedSides(element: Element): string[] {
  const BY_AXIS: Record<string, string[]> = {
    "": ["top", "right", "bottom", "left"],
    t: ["top"],
    r: ["right"],
    b: ["bottom"],
    l: ["left"],
    x: ["left", "right"],
    y: ["top", "bottom"],
  };
  const sides = new Set<string>();
  for (const name of element.className.split(/\s+/)) {
    const match = /^p([trblxy]?)-(\d+(?:\.\d+)?)$/.exec(name);
    if (!match || Number(match[2]) === 0) continue;
    for (const side of BY_AXIS[match[1]]) sides.add(side);
  }
  return [...sides].sort();
}

function session(id: string, title: string): SessionInfo {
  return {
    id, source: null, model: null, title, started_at: 0, ended_at: null,
    last_active: Date.now() / 1000 - 120, is_active: false, message_count: 2,
    tool_call_count: 0, input_tokens: 0, output_tokens: 0, preview: null,
  };
}

describe("failed message recovery", () => {
  it("shows the persisted reason and addresses both actions to the selected message at 390px", async () => {
    const retry = vi.fn();
    const discard = vi.fn();
    const message: ChatMessage = {
      id: "user-failed-message-123456",
      clientMessageId: "failed-message-123456",
      role: "user",
      content: "Продолжить после обновления лимита",
      timestamp: 1,
      delivery: "failed",
      failureConfirmed: true,
      failureReason: "Лимит модели обновится 19 сент. 2026 г. в 10:30.",
    };

    await render(
      <BubbleChatTranscript
        messages={[message]}
        sessionId="chat-at-390px"
        onRetry={retry}
        onDiscard={discard}
      />,
    );
    container.style.width = "390px";

    const reason = container.querySelector('[role="status"]');
    expect(reason?.textContent).toContain("19 сент. 2026");
    const actions = container.querySelector('[aria-label="Действия с сообщением, оставшимся без ответа"]')!;
    expect(actions.className).toContain("flex-wrap");
    const buttons = actions.querySelectorAll("button");
    expect(buttons[0].textContent).toContain("Вернуть в поле");
    expect(buttons[1].textContent).toContain("Убрать сохранённую копию");

    await act(async () => {
      buttons[0].click();
      buttons[1].click();
    });
    const target = { sessionId: "chat-at-390px", messageId: "failed-message-123456" };
    expect(retry).toHaveBeenCalledWith(target);
    expect(discard).toHaveBeenCalledWith(target);
  });
});

describe("BubbleChatSidebar", () => {
  const sessions = [
    session("a", "Первый чат"),
    session("b", "Второй чат"),
    session("c", "Третий чат"),
  ];

  async function renderSidebar(activeId: string, layout: "desktop" | "mobile" = "desktop") {
    await render(
      <BubbleChatSidebar
        sessions={sessions}
        activeId={activeId}
        loading={false}
        error={null}
        onSelect={vi.fn()}
        onNewChat={vi.fn()}
        onRequestDelete={vi.fn()}
        layout={layout}
      />,
    );
    return container.querySelector<HTMLElement>('nav[aria-label="Список чатов"]')!;
  }

  it("держит место под внешнюю тень внутри прокручиваемого списка", async () => {
    const nav = await renderSidebar("a");
    // Тень активной карточки уходит и вверх, и вниз, и в стороны, а обрезает
    // её сам scrollport: место обязано быть внутренним отступом, а не внешним.
    expect(nav.className).toContain("overflow-y-auto");
    expect(paddedSides(nav)).toEqual(["bottom", "left", "right", "top"]);
    expect(nav.className).not.toContain("overflow-y-visible");
    // Скриншот-проверка целостности тени — в браузере: DOM её не рисует.
    const active = container.querySelector('[aria-current="page"]')!;
    expect(active.className).toContain("shadow-[var(--neo-depth-1)]");
  });

  it("сохраняет выбор, создание и удаление чата", async () => {
    const nav = await renderSidebar("c");
    expect(container.querySelectorAll('[aria-current="page"]')).toHaveLength(1);
    expect(container.querySelector('[aria-current="page"]')?.textContent).toContain(
      "Третий чат",
    );
    expect(nav.querySelectorAll('button[aria-label^="Действия с чатом"]')).toHaveLength(
      sessions.length,
    );
    expect(
      [...container.querySelectorAll("button")].some((button) =>
        button.textContent?.includes("Новый чат"),
      ),
    ).toBe(true);
  });

  it("показывает тот же серверный список в мобильной панели", async () => {
    await renderSidebar("b", "mobile");
    const aside = container.querySelector<HTMLElement>("aside")!;
    expect(aside.className).toContain("flex");
    expect(aside.className).not.toContain("hidden");
    expect(aside.style.width).toBe("100%");
    expect(aside.querySelectorAll(".korra-chat-history__item")).toHaveLength(3);
    expect(aside.querySelector('[aria-current="page"]')?.textContent).toContain("Второй чат");
  });
});


it("lets the next draft be edited during a reply without sending or clearing it at completion", async () => {
  let finish: (value: boolean) => void = () => {};
  const onSend = vi.fn(() => new Promise<boolean>(resolve => { finish = resolve; }));
  const onAbort = vi.fn();
  await render(<BubbleChatComposer onSend={onSend} onAbort={onAbort} />);
  const textarea = container.querySelector("textarea")!;
  await enterText(textarea, "Первое сообщение");
  await act(async () => container.querySelector<HTMLButtonElement>('button[aria-label="Отправить"]')!.click());
  await act(async () => root.render(<MemoryRouter><BubbleChatComposer onSend={onSend} onAbort={onAbort} streaming responding /></MemoryRouter>));
  expect(textarea.value).toBe("");
  expect(textarea.disabled).toBe(false);
  await enterText(textarea, "Следующий вопрос");
  await act(async () => textarea.dispatchEvent(new KeyboardEvent("keydown", {key:"Enter", bubbles:true})));
  expect(onSend).toHaveBeenCalledTimes(1);
  expect(container.querySelector<HTMLButtonElement>('button[aria-label="Остановить генерацию"]')?.disabled).toBe(false);
  await act(async () => finish(true));
  await act(async () => root.render(<MemoryRouter><BubbleChatComposer onSend={onSend} onAbort={onAbort} /></MemoryRouter>));
  expect(textarea.value).toBe("Следующий вопрос");
  expect(container.querySelector<HTMLButtonElement>('button[aria-label="Отправить"]')?.disabled).toBe(false);
});

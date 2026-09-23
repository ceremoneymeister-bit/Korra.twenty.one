// @vitest-environment jsdom

import { act, useEffect } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const apiMocks = vi.hoisted(() => ({ transcribeRecording: vi.fn() }));

vi.mock("@/lib/api", () => ({
  transcribeRecording: apiMocks.transcribeRecording,
}));

/** Ответ распознавания: текст и сколько звука сервер нашёл в файле. */
function heard(text: string, audioSeconds: number | null = null) {
  return { text, audioSeconds };
}

import {
  useDictation,
  type UseDictationOptions,
  type UseDictationReturn,
} from "./useDictation";

/** Микрофон браузера: одна дорожка, которую хук обязан отпустить. */
function fakeStream() {
  const track = { stop: vi.fn() };
  return {
    stream: { getTracks: () => [track] } as unknown as MediaStream,
    track,
  };
}

/** `MediaRecorder` из jsdom не существует — подменяем предсказуемым. */
class FakeMediaRecorder {
  static supportedTypes: string[] = ["audio/webm;codecs=opus"];
  static instances: FakeMediaRecorder[] = [];

  static isTypeSupported(type: string): boolean {
    return FakeMediaRecorder.supportedTypes.includes(type);
  }

  state: "inactive" | "recording" = "inactive";
  stream: MediaStream;
  mimeType: string;
  startArgs: unknown[] = [];
  ondataavailable: ((event: { data: Blob }) => void) | null = null;
  onstop: (() => void) | null = null;
  onerror: (() => void) | null = null;

  constructor(stream: MediaStream, options?: { mimeType?: string }) {
    this.stream = stream;
    this.mimeType = options?.mimeType ?? "audio/webm";
    FakeMediaRecorder.instances.push(this);
  }

  start(...args: unknown[]): void {
    this.startArgs = args;
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

let container: HTMLDivElement;
let root: Root;
let current: UseDictationReturn;
let getUserMedia: ReturnType<typeof vi.fn>;
let track: { stop: ReturnType<typeof vi.fn> };

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT =
  true;

function Probe(props: UseDictationOptions) {
  const value = useDictation(props);
  useEffect(() => {
    current = value;
  });
  return null;
}

async function mount(options: Partial<UseDictationOptions> = {}) {
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  await act(async () => {
    root.render(
      <Probe onText={vi.fn()} onError={vi.fn()} {...options} />,
    );
  });
}

/** Нажатие кнопки: обе стороны — включение и остановка — асинхронные. */
async function press() {
  await act(async () => {
    current.toggle();
  });
  await act(async () => {});
}

beforeEach(() => {
  apiMocks.transcribeRecording.mockReset();
  apiMocks.transcribeRecording.mockResolvedValue(heard(""));
  FakeMediaRecorder.instances = [];
  FakeMediaRecorder.supportedTypes = ["audio/webm;codecs=opus"];

  const media = fakeStream();
  track = media.track;
  getUserMedia = vi.fn().mockResolvedValue(media.stream);

  Object.defineProperty(window, "isSecureContext", {
    configurable: true,
    value: true,
    writable: true,
  });
  Object.defineProperty(navigator, "mediaDevices", {
    configurable: true,
    value: { getUserMedia },
    writable: true,
  });
  vi.stubGlobal("MediaRecorder", FakeMediaRecorder);
});

afterEach(async () => {
  await act(async () => root?.unmount());
  container?.remove();
  vi.unstubAllGlobals();
});

describe("useDictation", () => {
  it("в покое диктовка доступна и микрофон не тронут", async () => {
    await mount();
    expect(current.supported).toBe(true);
    expect(current.unavailableReason).toBeNull();
    expect(current.state).toBe("idle");
    expect(getUserMedia).not.toHaveBeenCalled();
  });

  it("первое нажатие пишет, второе распознаёт и отдаёт текст", async () => {
    const onText = vi.fn();
    apiMocks.transcribeRecording.mockResolvedValue(heard("  проверь смету  "));
    await mount({ onText, profile: "raschet" });

    await press();
    expect(current.state).toBe("recording");
    expect(getUserMedia).toHaveBeenCalledWith({ audio: true });
    // Запись одним куском: с timeslice Safari раз в секунду сбрасывает кодер.
    expect(FakeMediaRecorder.instances[0].startArgs).toEqual([]);

    await press();
    expect(apiMocks.transcribeRecording).toHaveBeenCalledTimes(1);
    const [blob, options] = apiMocks.transcribeRecording.mock.calls[0];
    expect((blob as Blob).size).toBeGreaterThan(0);
    expect(options).toMatchObject({
      mimeType: "audio/webm;codecs=opus",
      profile: "raschet",
    });
    expect(typeof options.recordedMs).toBe("number");
    // Текст приходит без крайних пробелов — дописывать его будет поле ввода.
    expect(onText).toHaveBeenCalledWith("проверь смету");
    expect(current.state).toBe("idle");
    expect(current.kept).toBeNull();
    // Микрофон отпущен: индикатор записи в браузере гаснуть обязан.
    expect(track.stop).toHaveBeenCalled();
  });

  it("не обрывает длинную мысль через две минуты и распознаёт на пределе", async () => {
    vi.useFakeTimers();
    const onText = vi.fn();
    apiMocks.transcribeRecording.mockResolvedValue(heard("длинная мысль закончена", 600));

    try {
      await mount({ onText });
      await press();

      await act(async () => {
        vi.advanceTimersByTime(120_000);
      });
      expect(current.state).toBe("recording");
      // Таймер записи виден владельцу.
      expect(current.elapsedSeconds).toBe(120);
      expect(apiMocks.transcribeRecording).not.toHaveBeenCalled();

      await act(async () => {
        vi.advanceTimersByTime(8 * 60_000);
      });
      await act(async () => {});

      expect(apiMocks.transcribeRecording).toHaveBeenCalledTimes(1);
      // Длина по часам браузера уходит на сервер вместе с записью.
      expect(apiMocks.transcribeRecording.mock.calls[0][1].recordedMs).toBe(600_000);
      expect(onText).toHaveBeenCalledWith("длинная мысль закончена");
      expect(current.state).toBe("idle");
      expect(track.stop).toHaveBeenCalled();
    } finally {
      vi.useRealTimers();
    }
  });

  it("где браузер пишет только mp4 (Safari до 18.4), пишет в mp4", async () => {
    FakeMediaRecorder.supportedTypes = ["audio/mp4"];
    await mount();

    await press();
    await press();

    expect(apiMocks.transcribeRecording.mock.calls[0][1].mimeType).toBe("audio/mp4");
  });

  it("отказ в доступе к микрофону объясняется по-русски", async () => {
    const onError = vi.fn();
    getUserMedia.mockRejectedValue(
      Object.assign(new Error("denied"), { name: "NotAllowedError" }),
    );
    await mount({ onError });

    await press();

    expect(onError).toHaveBeenCalledWith(
      "Доступ к микрофону запрещён. Разрешите его в настройках браузера и повторите.",
    );
    expect(current.state).toBe("idle");
    expect(apiMocks.transcribeRecording).not.toHaveBeenCalled();
  });

  it("тишину не выдаёт за текст", async () => {
    const onText = vi.fn();
    const onEmpty = vi.fn();
    apiMocks.transcribeRecording.mockResolvedValue(heard("   "));
    await mount({ onText, onEmpty });

    await press();
    await press();

    expect(onEmpty).toHaveBeenCalledTimes(1);
    expect(onText).not.toHaveBeenCalled();
    expect(current.state).toBe("idle");
    expect(current.kept).toBeNull();
  });

  it("пустой ответ на длинную запись не выдаёт за тишину и запись хранит", async () => {
    vi.useFakeTimers();
    const onEmpty = vi.fn();
    try {
      await mount({ onEmpty });
      await press();
      await act(async () => {
        vi.advanceTimersByTime(90_000);
      });
      await press();

      expect(onEmpty).not.toHaveBeenCalled();
      expect(current.kept).toMatchObject({ kind: "failed", recordedSeconds: 90 });
    } finally {
      vi.useRealTimers();
    }
  });

  it("отказ распознавания хранит запись, а повтор отдаёт текст", async () => {
    const onError = vi.fn();
    const onText = vi.fn();
    apiMocks.transcribeRecording.mockRejectedValueOnce(
      new Error(
        "Не получилось распознать речь. Повторите или напишите текстом; если распознавание не настроено, добавьте ключ Deepgram в разделе «Ключи».",
      ),
    );
    await mount({ onError, onText });

    await press();
    await press();

    // Причина дословно — в карточке сохранённой записи, а не в гаснущей строке.
    expect(current.kept).toMatchObject({
      kind: "failed",
      message:
        "Не получилось распознать речь. Повторите или напишите текстом; если распознавание не настроено, добавьте ключ Deepgram в разделе «Ключи».",
    });
    expect(onError).not.toHaveBeenCalled();
    expect(current.state).toBe("idle");
    expect(track.stop).toHaveBeenCalled();

    apiMocks.transcribeRecording.mockResolvedValueOnce(heard("вторая попытка"));
    await act(async () => {
      current.retry();
    });
    await act(async () => {});

    expect(apiMocks.transcribeRecording).toHaveBeenCalledTimes(2);
    // Повтор несёт ту же запись, а не новую.
    expect(apiMocks.transcribeRecording.mock.calls[1][0]).toBe(
      apiMocks.transcribeRecording.mock.calls[0][0],
    );
    expect(onText).toHaveBeenCalledWith("вторая попытка");
    expect(current.kept).toBeNull();
    expect(current.state).toBe("idle");
  });

  it("обрыв связи на загрузке не теряет запись и объясняет по-русски", async () => {
    apiMocks.transcribeRecording.mockRejectedValue(new TypeError("Load failed"));
    await mount();

    await press();
    await press();

    expect(current.kept?.kind).toBe("failed");
    expect(current.kept?.message).toMatch(/^Не удалось связаться с сервером/);
  });

  it("пока нераспознанная запись не разобрана, новая не начинается", async () => {
    apiMocks.transcribeRecording.mockRejectedValue(new TypeError("Failed to fetch"));
    await mount();
    await press();
    await press();
    expect(current.kept?.kind).toBe("failed");

    await press();
    expect(current.state).toBe("idle");
    expect(FakeMediaRecorder.instances).toHaveLength(1);

    await act(async () => {
      current.discard();
    });
    expect(current.kept).toBeNull();
    await press();
    expect(current.state).toBe("recording");
  });

  it("сервер нашёл заметно меньше звука, чем записано, — текст отдаёт и предупреждает", async () => {
    vi.useFakeTimers();
    const onText = vi.fn();
    const createObjectURL = vi.fn(() => "blob:dictation");
    const revokeObjectURL = vi.fn();
    const original = {
      create: URL.createObjectURL,
      revoke: URL.revokeObjectURL,
    };
    URL.createObjectURL = createObjectURL;
    URL.revokeObjectURL = revokeObjectURL;
    apiMocks.transcribeRecording.mockResolvedValue(heard("только начало", 31));
    try {
      await mount({ onText });
      await press();
      await act(async () => {
        vi.advanceTimersByTime(312_000);
      });
      await press();

      expect(onText).toHaveBeenCalledWith("только начало");
      expect(current.kept).toMatchObject({
        kind: "partial",
        recordedSeconds: 312,
        audioSeconds: 31,
        url: "blob:dictation",
      });
      expect(current.kept?.message).toContain("0:31 из 5:12");
      expect(current.kept?.fileName).toMatch(/^диктовка-\d{4}-\d{2}-\d{2}-\d{4}\.webm$/);

      // Неполная запись новую не держит: её текст уже в поле.
      await press();
      expect(current.state).toBe("recording");
      expect(current.kept).toBeNull();
      expect(revokeObjectURL).toHaveBeenCalledWith("blob:dictation");
    } finally {
      URL.createObjectURL = original.create;
      URL.revokeObjectURL = original.revoke;
      vi.useRealTimers();
    }
  });

  it("запись без потерь не оставляет предупреждений", async () => {
    vi.useFakeTimers();
    apiMocks.transcribeRecording.mockResolvedValue(heard("вся мысль", 299.4));
    try {
      await mount();
      await press();
      await act(async () => {
        vi.advanceTimersByTime(300_000);
      });
      await press();
      expect(current.kept).toBeNull();
    } finally {
      vi.useRealTimers();
    }
  });

  it("отмена выбрасывает запись, не распознавая её", async () => {
    await mount();
    await press();
    expect(current.state).toBe("recording");

    await act(async () => {
      current.cancel();
    });
    await act(async () => {});

    expect(apiMocks.transcribeRecording).not.toHaveBeenCalled();
    expect(track.stop).toHaveBeenCalled();
    expect(current.state).toBe("idle");
  });

  async function unmount() {
    await act(async () => root.unmount());
    container.remove();
  }

  it("нераспознанная запись переживает пересоздание поля ввода того же чата", async () => {
    const onText = vi.fn();
    apiMocks.transcribeRecording.mockRejectedValueOnce(new TypeError("Load failed"));
    await mount({ keepKey: "chat-a", onText });
    await press();
    await press();
    expect(current.kept?.kind).toBe("failed");

    // Смена чата пересоздаёт поле; вернувшись, владелец находит запись.
    await unmount();
    await mount({ keepKey: "chat-a", onText });
    expect(current.kept?.kind).toBe("failed");

    apiMocks.transcribeRecording.mockResolvedValueOnce(heard("нашлась"));
    await act(async () => {
      current.retry();
    });
    await act(async () => {});
    expect(onText).toHaveBeenCalledWith("нашлась");
    expect(current.kept).toBeNull();

    await unmount();
    await mount({ keepKey: "chat-a" });
    expect(current.kept).toBeNull();
  });

  it("поле закрыли посреди записи — надиктованное уходит в сохранённые", async () => {
    await mount({ keepKey: "chat-b" });
    await press();
    expect(current.state).toBe("recording");

    await unmount();
    expect(apiMocks.transcribeRecording).not.toHaveBeenCalled();
    expect(track.stop).toHaveBeenCalled();

    await mount({ keepKey: "chat-b" });
    expect(current.kept).toMatchObject({
      kind: "failed",
      message: "Запись остановилась, когда закрылось поле ввода. Распознайте её здесь.",
    });
    await act(async () => {
      current.discard();
    });
  });

  it("поле закрыли во время распознавания — запись уходит в сохранённые", async () => {
    apiMocks.transcribeRecording.mockReturnValue(new Promise(() => {}));
    await mount({ keepKey: "chat-c" });
    await press();
    await press();
    expect(current.state).toBe("transcribing");

    await unmount();
    await mount({ keepKey: "chat-c" });
    expect(current.kept).toMatchObject({
      kind: "failed",
      message: "Распознавание прервалось: поле ввода закрылось раньше, чем пришёл текст.",
    });
    await act(async () => {
      current.discard();
    });
  });

  it("без ключа черновика закрытое поле ничего за собой не оставляет", async () => {
    await mount();
    await press();
    await unmount();
    await mount();
    expect(current.kept).toBeNull();
  });

  it("без записи звука в браузере кнопка остаётся выключенной", async () => {
    vi.stubGlobal("MediaRecorder", undefined);
    await mount();

    expect(current.supported).toBe(false);
    expect(current.unavailableReason).toBe("Браузер не умеет записывать звук");

    await press();
    expect(getUserMedia).not.toHaveBeenCalled();
  });

  it("по http диктовка недоступна и говорит почему", async () => {
    Object.defineProperty(window, "isSecureContext", {
      configurable: true,
      value: false,
      writable: true,
    });
    await mount();

    expect(current.supported).toBe(false);
    expect(current.unavailableReason).toBe("Диктовка работает только по HTTPS");
  });
});

// @vitest-environment jsdom

import { act, useEffect } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const apiMocks = vi.hoisted(() => ({ transcribeAudio: vi.fn() }));

vi.mock("@/lib/api", () => ({
  transcribeAudio: apiMocks.transcribeAudio,
}));

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
  ondataavailable: ((event: { data: Blob }) => void) | null = null;
  onstop: (() => void) | null = null;
  onerror: (() => void) | null = null;

  constructor(stream: MediaStream, options?: { mimeType?: string }) {
    this.stream = stream;
    this.mimeType = options?.mimeType ?? "audio/webm";
    FakeMediaRecorder.instances.push(this);
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
  apiMocks.transcribeAudio.mockReset();
  apiMocks.transcribeAudio.mockResolvedValue("");
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
    apiMocks.transcribeAudio.mockResolvedValue("  проверь смету  ");
    await mount({ onText, profile: "raschet" });

    await press();
    expect(current.state).toBe("recording");
    expect(getUserMedia).toHaveBeenCalledWith({ audio: true });

    await press();
    expect(apiMocks.transcribeAudio).toHaveBeenCalledTimes(1);
    const [blob, mimeType, profile] = apiMocks.transcribeAudio.mock.calls[0];
    expect((blob as Blob).size).toBeGreaterThan(0);
    expect(mimeType).toBe("audio/webm;codecs=opus");
    expect(profile).toBe("raschet");
    // Текст приходит без крайних пробелов — дописывать его будет поле ввода.
    expect(onText).toHaveBeenCalledWith("проверь смету");
    expect(current.state).toBe("idle");
    // Микрофон отпущен: индикатор записи в браузере гаснуть обязан.
    expect(track.stop).toHaveBeenCalled();
  });

  it("в Safari пишет в mp4 — единственный формат, который там есть", async () => {
    FakeMediaRecorder.supportedTypes = ["audio/mp4"];
    await mount();

    await press();
    await press();

    expect(apiMocks.transcribeAudio.mock.calls[0][1]).toBe("audio/mp4");
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
    expect(apiMocks.transcribeAudio).not.toHaveBeenCalled();
  });

  it("тишину не выдаёт за текст", async () => {
    const onText = vi.fn();
    const onEmpty = vi.fn();
    apiMocks.transcribeAudio.mockResolvedValue("   ");
    await mount({ onText, onEmpty });

    await press();
    await press();

    expect(onEmpty).toHaveBeenCalledTimes(1);
    expect(onText).not.toHaveBeenCalled();
    expect(current.state).toBe("idle");
  });

  it("ненастроенное распознавание доходит до владельца дословно", async () => {
    const onError = vi.fn();
    apiMocks.transcribeAudio.mockRejectedValue(
      new Error(
        "Не получилось распознать речь. Повторите или напишите текстом; если распознавание не настроено, добавьте ключ Deepgram в разделе «Ключи».",
      ),
    );
    await mount({ onError });

    await press();
    await press();

    expect(onError).toHaveBeenCalledWith(
      "Не получилось распознать речь. Повторите или напишите текстом; если распознавание не настроено, добавьте ключ Deepgram в разделе «Ключи».",
    );
    expect(current.state).toBe("idle");
  });

  it("отмена выбрасывает запись, не распознавая её", async () => {
    await mount();
    await press();
    expect(current.state).toBe("recording");

    await act(async () => {
      current.cancel();
    });
    await act(async () => {});

    expect(apiMocks.transcribeAudio).not.toHaveBeenCalled();
    expect(track.stop).toHaveBeenCalled();
    expect(current.state).toBe("idle");
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

// @vitest-environment jsdom
import { describe, expect, it, vi } from "vitest";
import { UploadError, type PartReceipt } from "@/lib/upload-session";
import {
  MAX_PART_ATTEMPTS,
  backoffDelay,
  deviceConcurrency,
  runUploadQueue,
  type UploadGate,
  type UploadQueueFile,
} from "./upload-queue";

const PART = 4;

function files(sizes: number[]): UploadQueueFile[] {
  return sizes.map((size, index) => ({
    index,
    blob: new Blob(["x".repeat(size)]),
    offset: 0,
    complete: false,
  }));
}

/** Шлюз без таймеров: тест сам решает, когда вкладка видима и когда сеть вернулась. */
function testGate(overrides: Partial<UploadGate> = {}): UploadGate & { slept: number[] } {
  const slept: number[] = [];
  return {
    slept,
    canStart: () => true,
    waitUntilStartable: async () => {},
    sleep: async (ms) => {
      slept.push(ms);
      return false;
    },
    ...overrides,
  } as UploadGate & { slept: number[] };
}

/** Приёмщик частей: собирает файл по смещениям, как это делает сервер. */
function server(behaviour?: (call: { index: number; offset: number }) => UploadError | null) {
  const received = new Map<number, number>();
  const calls: { index: number; offset: number }[] = [];
  let flying = 0;
  let peak = 0;
  const uploadPart = async (
    _id: string,
    index: number,
    offset: number,
    chunk: Blob,
  ): Promise<PartReceipt> => {
    calls.push({ index, offset });
    flying += 1;
    peak = Math.max(peak, flying);
    try {
      await Promise.resolve();
      const failure = behaviour?.({ index, offset });
      if (failure) throw failure;
      const bytes = offset + chunk.size;
      received.set(index, bytes);
      return { index, bytes, complete: bytes >= sizeOf(index) };
    } finally {
      flying -= 1;
    }
  };
  let sizes: number[] = [];
  const sizeOf = (index: number) => sizes[index];
  return {
    uploadPart,
    calls,
    received,
    get peak() {
      return peak;
    },
    withSizes(values: number[]) {
      sizes = values;
      return this;
    },
  };
}

describe("планировщик частей", () => {
  it.each([
    { index: 0, bytes: 0, complete: false },
    { index: 0, bytes: 99, complete: false },
    { index: 0, bytes: 2.5, complete: false },
    { index: 0, bytes: 2, complete: true },
  ])("останавливает файл на невозможной квитанции %j", async receipt => {
    const uploadPart = vi.fn().mockResolvedValue(receipt);
    const outcome = await runUploadQueue({ uploadId: "u1", files: files([4]),
      signal: new AbortController().signal, partBytes: PART, gate: testGate(), uploadPart });
    expect(outcome.completed).toEqual([]);
    expect(outcome.failed).toHaveLength(1);
    expect(uploadPart).toHaveBeenCalledOnce();
  });

  it("не зацикливается на 409 без нового смещения", async () => {
    const uploadPart = vi.fn().mockRejectedValue(new UploadError(409, "", { bytes: 0 }));
    const outcome = await runUploadQueue({ uploadId: "u1", files: files([4]),
      signal: new AbortController().signal, partBytes: PART, gate: testGate(), uploadPart });
    expect(outcome.failed).toHaveLength(1);
    expect(uploadPart).toHaveBeenCalledOnce();
  });

  it("режет файл на части и не держит в полёте больше заданного числа", async () => {
    const sizes = [10, 10, 10, 10];
    const transport = server().withSizes(sizes);
    const queue = files(sizes);
    const outcome = await runUploadQueue({
      uploadId: "u1",
      files: queue,
      signal: new AbortController().signal,
      partBytes: PART,
      concurrency: 2,
      gate: testGate(),
      uploadPart: transport.uploadPart,
    });
    expect(outcome.completed).toEqual([0, 1, 2, 3]);
    expect(outcome.failed).toEqual([]);
    expect(transport.peak).toBeLessThanOrEqual(2);
    // По три части на файл: 4 + 4 + 2.
    expect(transport.calls.filter((call) => call.index === 0).map((call) => call.offset))
      .toEqual([0, 4, 8]);
    // Порядок выдачи файлов — по возрастанию номера.
    expect(transport.calls[0].index).toBe(0);
    expect(transport.calls[1].index).toBe(1);
  });

  it("не перекачивает то, что сервер уже принял", async () => {
    const sizes = [10, 10];
    const transport = server().withSizes(sizes);
    const queue = files(sizes);
    queue[0].complete = true;
    queue[1].offset = 8;
    await runUploadQueue({
      uploadId: "u1",
      files: queue,
      signal: new AbortController().signal,
      partBytes: PART,
      concurrency: 3,
      gate: testGate(),
      uploadPart: transport.uploadPart,
    });
    expect(transport.calls).toEqual([{ index: 1, offset: 8 }]);
  });

  it("409 со смещением — не ошибка, а пересинхронизация без задержки", async () => {
    const sizes = [10];
    let sent = false;
    const transport = server(({ offset }) => {
      if (offset === 0 && !sent) {
        sent = true;
        return new UploadError(409, "Часть пришла не по порядку", { bytes: 6 });
      }
      return null;
    }).withSizes(sizes);
    const gate = testGate();
    const outcome = await runUploadQueue({
      uploadId: "u1",
      files: files(sizes),
      signal: new AbortController().signal,
      partBytes: PART,
      concurrency: 1,
      gate,
      uploadPart: transport.uploadPart,
    });
    expect(transport.calls.map((call) => call.offset)).toEqual([0, 6]);
    expect(gate.slept).toEqual([]);
    expect(outcome.completed).toEqual([0]);
  });

  it("повторяет обрыв с растущей задержкой и сдаётся после шести попыток", async () => {
    const sizes = [4];
    const transport = server(() => new UploadError(0, "")).withSizes(sizes);
    const gate = testGate();
    const outcome = await runUploadQueue({
      uploadId: "u1",
      files: files(sizes),
      signal: new AbortController().signal,
      partBytes: PART,
      concurrency: 1,
      gate,
      uploadPart: transport.uploadPart,
    });
    expect(transport.calls).toHaveLength(MAX_PART_ATTEMPTS);
    expect(gate.slept).toHaveLength(MAX_PART_ATTEMPTS - 1);
    expect(gate.slept[0]).toBeLessThan(gate.slept[1]);
    expect(outcome.failed).toEqual([{ index: 0, error: expect.objectContaining({ status: 0 }) }]);
  });

  it("возврат сети сбрасывает счётчик попыток, поэтому загрузка не сдаётся", async () => {
    const sizes = [4];
    let failures = 0;
    const transport = server(() => (failures++ < 8 ? new UploadError(503, "") : null))
      .withSizes(sizes);
    const gate = testGate({ sleep: async () => true });
    const outcome = await runUploadQueue({
      uploadId: "u1",
      files: files(sizes),
      signal: new AbortController().signal,
      partBytes: PART,
      concurrency: 1,
      gate,
      uploadPart: transport.uploadPart,
    });
    expect(transport.calls).toHaveLength(9);
    expect(outcome.completed).toEqual([0]);
  });

  it("отказ без повтора останавливает только свой файл", async () => {
    const sizes = [4, 4];
    const transport = server(({ index }) =>
      index === 0 ? new UploadError(413, "Файл больше 2 ГБ") : null).withSizes(sizes);
    const outcome = await runUploadQueue({
      uploadId: "u1",
      files: files(sizes),
      signal: new AbortController().signal,
      partBytes: PART,
      concurrency: 1,
      gate: testGate(),
      uploadPart: transport.uploadPart,
    });
    expect(outcome.completed).toEqual([1]);
    expect(outcome.failed[0].error.detail).toBe("Файл больше 2 ГБ");
  });

  it("в скрытой вкладке новые части не стартуют", async () => {
    const sizes = [4];
    const transport = server().withSizes(sizes);
    let visible = false;
    let release!: () => void;
    const waiting = new Promise<void>((resolve) => {
      release = resolve;
    });
    const gate = testGate({
      canStart: () => visible,
      waitUntilStartable: async () => {
        await waiting;
      },
    });
    const queue = runUploadQueue({
      uploadId: "u1",
      files: files(sizes),
      signal: new AbortController().signal,
      partBytes: PART,
      concurrency: 1,
      gate,
      uploadPart: transport.uploadPart,
    });
    await Promise.resolve();
    expect(transport.calls).toEqual([]);
    visible = true;
    release();
    await queue;
    expect(transport.calls).toEqual([{ index: 0, offset: 0 }]);
  });

  it("пауза прекращает передачу, дошедшее сохраняется", async () => {
    const sizes = [12];
    const controller = new AbortController();
    const transport = server(({ offset }) => {
      if (offset === 4) controller.abort();
      return null;
    }).withSizes(sizes);
    const queue = files(sizes);
    const outcome = await runUploadQueue({
      uploadId: "u1",
      files: queue,
      signal: controller.signal,
      partBytes: PART,
      concurrency: 1,
      gate: testGate(),
      uploadPart: transport.uploadPart,
    });
    expect(queue[0].offset).toBe(8);
    expect(queue[0].complete).toBe(false);
    expect(outcome.failed).toEqual([]);
  });

  it("сообщает прогресс с учётом байтов в полёте", async () => {
    const sizes = [8];
    const seen: number[] = [];
    const uploadPart = async (
      _id: string,
      index: number,
      offset: number,
      chunk: Blob,
      onProgress?: (loaded: number) => void,
    ): Promise<PartReceipt> => {
      onProgress?.(2);
      return { index, bytes: offset + chunk.size, complete: offset + chunk.size >= 8 };
    };
    await runUploadQueue({
      uploadId: "u1",
      files: files(sizes),
      signal: new AbortController().signal,
      partBytes: PART,
      concurrency: 1,
      gate: testGate(),
      uploadPart,
      onProgress: (progress) => seen.push(progress.bytes),
    });
    expect(seen[0]).toBe(0);
    expect(seen).toContain(2);
    expect(seen.at(-1)).toBe(8);
  });
});

describe("настройки канала", () => {
  it("растит задержку и добавляет разброс", () => {
    expect(backoffDelay(0, () => 0)).toBe(1000);
    expect(backoffDelay(1, () => 0)).toBe(2000);
    expect(backoffDelay(99, () => 0)).toBe(30_000);
    expect(backoffDelay(0, () => 1)).toBe(1500);
  });

  it("на тач-устройстве и сотовой сети держит два потока вместо трёх", () => {
    vi.stubGlobal("matchMedia", () => ({ matches: false }));
    expect(deviceConcurrency()).toBe(3);
    vi.stubGlobal("matchMedia", () => ({ matches: true }));
    expect(deviceConcurrency()).toBe(2);
    vi.stubGlobal("matchMedia", () => ({ matches: false }));
    Object.defineProperty(navigator, "connection", {
      value: { type: "cellular" },
      configurable: true,
    });
    expect(deviceConcurrency()).toBe(2);
    vi.unstubAllGlobals();
  });
});

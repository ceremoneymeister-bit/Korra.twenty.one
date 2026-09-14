// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { cancelUpload, uploadStatus, UploadError, completeUpload, createUpload, type UploadStatus } from "@/lib/upload-session";
import { runUploadQueue } from "@/lib/upload-queue";
import {
  $uploadJobs,
  cancelUploadJob,
  activeUploadJobs,
  dismissUploadJob,
  excludeUploadFile,
  pauseUploadJob,
  resumeUploadJob,
  startUploadJob,
  type StartUploadInput,
} from "./upload-jobs";

vi.mock("@/lib/upload-session", async () => {
  const actual = await vi.importActual<typeof import("@/lib/upload-session")>(
    "@/lib/upload-session",
  );
  return {
    ...actual,
    createUpload: vi.fn(),
    completeUpload: vi.fn(),
    cancelUpload: vi.fn(),
    uploadStatus: vi.fn(),
  };
});
vi.mock("@/lib/upload-queue", async () => {
  const actual = await vi.importActual<typeof import("@/lib/upload-queue")>("@/lib/upload-queue");
  return { ...actual, runUploadQueue: vi.fn() };
});

const create = vi.mocked(createUpload);
const complete = vi.mocked(completeUpload);
const queue = vi.mocked(runUploadQueue);

const UPLOAD_ID = "0f1e2d3c-4b5a-4968-8778-695a4b3c2d1e";

function input(overrides: Partial<StartUploadInput> = {}): StartUploadInput {
  return {
    title: "Референсы",
    blobs: [new Blob(["a"]), new Blob(["bb"])],
    manifest: {
      upload_id: UPLOAD_ID,
      origin: "chat",
      files: [{ path: "a.jpg", size: 1 }, { path: "b.jpg", size: 2 }],
    },
    ...overrides,
  };
}

function status(overrides: Partial<UploadStatus> = {}): UploadStatus {
  return {
    upload_id: UPLOAD_ID,
    origin: "chat",
    target: "/opt/data/workspace/client/inbox/2026-09-12/a1b2c3-1405",
    published: false,
    received: [
      { index: 0, bytes: 0, complete: false },
      { index: 1, bytes: 0, complete: false },
    ],
    already_present: [],
    limits: {
      part_bytes: 4, max_files: 10_000, max_directories: 10_000,
      max_file_bytes: 2 * 1024 ** 3, max_total_bytes: 20 * 1024 ** 3,
      chat_max_files: 500, chat_folder_threshold: 30,
    },
    ...overrides,
  };
}

const published = {
  published: true as const,
  files: [{
    index: 0, path: "/w/a.jpg", name: "a.jpg", kind: "jpg", size: 1,
    reader: "image", deduplicated: false, skipped: false,
  }],
  folder: null,
  skipped: [],
  excluded: [],
};

afterEach(async () => {
  for (const job of Object.values($uploadJobs.get())) {
    pauseUploadJob(job.uploadId);
  }
  await vi.waitFor(() => expect(activeUploadJobs()).toEqual([]));
  for (const job of Object.values($uploadJobs.get())) dismissUploadJob(job.uploadId);
  create.mockReset();
  complete.mockReset();
  queue.mockReset();
  vi.mocked(cancelUpload).mockReset();
  vi.mocked(uploadStatus).mockReset();
});

describe("задача загрузки живёт во вкладке", () => {
  it("при потерянном ответе отмены сверяет публикацию и сохраняет результат", async () => {
    create.mockResolvedValue(status());
    queue.mockResolvedValue({ completed: [0, 1], failed: [] });
    complete.mockRejectedValue(new UploadError(0, "network"));
    startUploadJob(input());
    await vi.waitFor(() => expect($uploadJobs.get()[UPLOAD_ID].status).toBe("failed"));
    vi.mocked(cancelUpload).mockRejectedValue(new UploadError(409, "already published"));
    vi.mocked(uploadStatus).mockResolvedValue(status({ published: true, result: published }));
    await cancelUploadJob(UPLOAD_ID);
    expect($uploadJobs.get()[UPLOAD_ID].result).toBe(published);
    expect($uploadJobs.get()[UPLOAD_ID].status).toBe("complete");
  });

  it("не скрывает задачу, если исход отмены остался неизвестным", async () => {
    create.mockRejectedValue(new UploadError(0, "network"));
    startUploadJob(input());
    await vi.waitFor(() => expect($uploadJobs.get()[UPLOAD_ID].status).toBe("failed"));
    vi.mocked(cancelUpload).mockRejectedValue(new UploadError(0, "network"));
    vi.mocked(uploadStatus).mockRejectedValue(new UploadError(0, "network"));
    await expect(cancelUploadJob(UPLOAD_ID)).rejects.toBeInstanceOf(UploadError);
    expect($uploadJobs.get()[UPLOAD_ID]).toBeDefined();
  });

  it("объявляет список, передаёт части и публикует пакет", async () => {
    create.mockResolvedValue(status());
    queue.mockResolvedValue({ completed: [0, 1], failed: [] });
    complete.mockResolvedValue(published);

    startUploadJob(input());
    await vi.waitFor(() => expect($uploadJobs.get()[UPLOAD_ID].status).toBe("complete"));
    expect($uploadJobs.get()[UPLOAD_ID].result).toBe(published);
    // Размер части берётся из ответа сервера, а не из константы клиента.
    expect(queue.mock.calls[0][0].partBytes).toBe(4);
    expect(queue.mock.calls[0][0].files.map((file) => file.index)).toEqual([0, 1]);
  });

  it("не перекачивает то, что сервер уже принял или знает по sha256", async () => {
    create.mockResolvedValue(status({
      already_present: [1],
      received: [{ index: 0, bytes: 1024, complete: false }, { index: 1, bytes: 2, complete: true }],
    }));
    queue.mockResolvedValue({ completed: [0], failed: [] });
    complete.mockResolvedValue(published);

    startUploadJob(input());
    await vi.waitFor(() => expect(queue).toHaveBeenCalled());
    expect(queue.mock.calls[0][0].files).toEqual([
      { index: 0, blob: expect.anything(), offset: 1024, complete: false },
      { index: 1, blob: expect.anything(), offset: 2, complete: true },
    ]);
  });

  it("повторный старт уже опубликованной сессии сразу отдаёт её результат", async () => {
    create.mockResolvedValue(status({ published: true, result: published }));
    startUploadJob(input());
    await vi.waitFor(() => expect($uploadJobs.get()[UPLOAD_ID].status).toBe("complete"));
    expect(queue).not.toHaveBeenCalled();
    expect(complete).not.toHaveBeenCalled();
  });

  it("пауза оставляет задачу возобновляемой, а не проваленной", async () => {
    create.mockResolvedValue(status());
    let release!: () => void;
    queue.mockImplementation(async (options) => {
      await new Promise<void>((resolve) => {
        release = resolve;
        options.signal.addEventListener("abort", () => resolve(), { once: true });
      });
      return { completed: [], failed: [] };
    });

    startUploadJob(input());
    await vi.waitFor(() => expect($uploadJobs.get()[UPLOAD_ID].status).toBe("uploading"));
    pauseUploadJob(UPLOAD_ID);
    await vi.waitFor(() => expect($uploadJobs.get()[UPLOAD_ID].status).toBe("paused"));
    expect($uploadJobs.get()[UPLOAD_ID].error).toBeUndefined();

    queue.mockResolvedValue({ completed: [0, 1], failed: [] });
    complete.mockResolvedValue(published);
    resumeUploadJob(UPLOAD_ID);
    await vi.waitFor(() => expect($uploadJobs.get()[UPLOAD_ID].status).toBe("complete"));
    release?.();
  });

  it("исчерпанные повторы показываются текстом сервера и не публикуют пакет", async () => {
    create.mockResolvedValue(status());
    queue.mockResolvedValue({
      completed: [0],
      failed: [{
        index: 1,
        error: Object.assign(new Error("На диске контура не хватает места"), {
          detail: "На диске контура не хватает места",
          status: 507,
          retryable: false,
        }),
      }],
    } as never);

    startUploadJob(input());
    await vi.waitFor(() => expect($uploadJobs.get()[UPLOAD_ID].status).toBe("failed"));
    expect($uploadJobs.get()[UPLOAD_ID].error).toBe("На диске контура не хватает места");
    expect(complete).not.toHaveBeenCalled();
  });

  it("отменённый файл исключается из публикации, остальные доходят", async () => {
    create.mockResolvedValue(status());
    queue.mockResolvedValue({ completed: [0], failed: [] });
    complete.mockResolvedValue(published);

    const id = startUploadJob(input());
    excludeUploadFile(id, 1);
    await vi.waitFor(() => expect(complete).toHaveBeenCalled());
    expect(complete.mock.calls[0][1]).toEqual([1]);
  });

  it("держит предупреждение об уходе со страницы, пока передача идёт", async () => {
    const add = vi.spyOn(window, "addEventListener");
    const remove = vi.spyOn(window, "removeEventListener");
    create.mockResolvedValue(status());
    queue.mockResolvedValue({ completed: [0, 1], failed: [] });
    complete.mockResolvedValue(published);

    startUploadJob(input());
    expect(add.mock.calls.some(([event]) => event === "beforeunload")).toBe(true);
    await vi.waitFor(() => expect($uploadJobs.get()[UPLOAD_ID].status).toBe("complete"));
    expect(remove.mock.calls.some(([event]) => event === "beforeunload")).toBe(true);
    add.mockRestore();
    remove.mockRestore();
  });
});

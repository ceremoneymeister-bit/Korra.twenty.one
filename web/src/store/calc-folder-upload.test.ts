// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { uploadFolder, type FolderSelection, type UploadProgress } from "@/lib/calc-folder-upload";
import { $folderUpload, dismissFolderUpload, pauseFolderUpload, resumeFolderUpload, startFolderUpload } from "./calc-folder-upload";

vi.mock("@/lib/calc-folder-upload", () => ({ uploadFolder: vi.fn(), folderUploadKey: () => "stable-upload" }));
const upload = vi.mocked(uploadFolder);
const selection: FolderSelection = { name: "Заказ 321", files: [{ path: "Чертежи/деталь.pdf", file: new File(["pdf"], "деталь.pdf") }] };
function pendingTransfer() {
  let complete!: (order: string) => void;
  let reject!: (error: Error) => void;
  let progress!: (progress: UploadProgress) => void;
  let signal!: AbortSignal;
  upload.mockImplementation((_selection, _id, onProgress, abortSignal) => {
    progress = onProgress; signal = abortSignal;
    return new Promise<string>((resolve, fail) => { complete = resolve; reject = fail; });
  });
  return { complete: (order: string) => complete(order), reject: (error: Error) => reject(error),
    progress: (value: UploadProgress) => progress(value), get signal() { return signal; } };
}
afterEach(async () => {
  pauseFolderUpload();
  await Promise.resolve();
  dismissFolderUpload();
  upload.mockReset();
});

describe("tab-owned folder upload", () => {
  it("keeps uploading without page subscribers and releases files after completion", async () => {
    const pending = pendingTransfer();
    const unmountPage = $folderUpload.subscribe(vi.fn());
    expect(startFolderUpload(selection)).toBe(true);
    unmountPage();
    expect(pending.signal.aborted).toBe(false);
    pending.progress({ completed: 1, total: 1, bytes: 3, totalBytes: 3 });
    expect($folderUpload.get()?.progress.completed).toBe(1);
    const mountOtherPage = $folderUpload.subscribe(vi.fn());
    pending.complete("order-321");
    await vi.waitFor(() => expect($folderUpload.get()?.status).toBe("complete"));
    expect($folderUpload.get()?.orderId).toBe("order-321");
    expect($folderUpload.get()?.selection).toBeNull();
    mountOtherPage();
  });

  it("warns before closing the browser page only while bytes are being transferred", async () => {
    const pending = pendingTransfer(); startFolderUpload(selection);
    const active = new Event("beforeunload", { cancelable: true }); window.dispatchEvent(active);
    expect(active.defaultPrevented).toBe(true);
    pending.complete("order-321");
    await vi.waitFor(() => expect($folderUpload.get()?.status).toBe("complete"));
    const done = new Event("beforeunload", { cancelable: true }); window.dispatchEvent(done);
    expect(done.defaultPrevented).toBe(false);
  });

  it("waits for pause cleanup and resumes the same job without reselecting files", async () => {
    const pending = pendingTransfer(); startFolderUpload(selection);
    expect(startFolderUpload({ ...selection, name: "Другой заказ" })).toBe(false);
    pauseFolderUpload();
    expect(pending.signal.aborted).toBe(true);
    expect($folderUpload.get()?.status).toBe("pausing");
    resumeFolderUpload(); expect(upload).toHaveBeenCalledTimes(1);
    pending.reject(new Error("aborted"));
    await vi.waitFor(() => expect($folderUpload.get()?.status).toBe("paused"));
    upload.mockResolvedValue("same-order"); resumeFolderUpload();
    await vi.waitFor(() => expect($folderUpload.get()?.status).toBe("complete"));
    expect(upload.mock.lastCall?.[0]).toBe(selection);
    expect(upload.mock.lastCall?.[1]).toBe("stable-upload");
  });

  it("retains the selected folder after a network failure for retry from another section", async () => {
    upload.mockRejectedValue(new Error("Failed to fetch")); startFolderUpload(selection);
    await vi.waitFor(() => expect($folderUpload.get()?.status).toBe("failed"));
    expect($folderUpload.get()?.selection).toBe(selection);
    upload.mockResolvedValue("same-order"); resumeFolderUpload();
    await vi.waitFor(() => expect($folderUpload.get()?.status).toBe("complete"));
  });
});

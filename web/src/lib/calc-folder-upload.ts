import { fetchJSON } from "@/lib/api";

export const FOLDER_LIMITS = { files: 10_000, fileBytes: 100 * 1024 ** 2, bytes: 20 * 1024 ** 3 };

export interface FolderFile { path: string; file: File }
export interface FolderSelection { name: string; files: FolderFile[] }
export interface UploadReceipt { upload_id: string; received: number[]; order_id?: string }
export interface UploadProgress { completed: number; total: number; bytes: number; totalBytes: number }

/** A directory reader returns batches (often 100 entries), not the whole folder. */
export async function readDroppedFolder(items: DataTransferItemList): Promise<FolderSelection> {
  // Capture entries before the drop event's protected data store closes.
  const entries = Array.from(items).filter((item) => item.kind === "file")
    .map((item) => item.webkitGetAsEntry?.());
  if (entries.length !== 1 || !entries[0]?.isDirectory) {
    throw new Error("Перетащите одну папку заказа. Или выберите её кнопкой загрузки.");
  }
  const root = entries[0];
  const files: FolderFile[] = [];
  async function visit(entry: FileSystemEntry, parent: string): Promise<void> {
    const path = parent ? `${parent}/${entry.name}` : entry.name;
    if (entry.isFile) {
      const file = await new Promise<File>((resolve, reject) =>
        (entry as FileSystemFileEntry).file(resolve, reject));
      files.push({ path, file });
      if (files.length > FOLDER_LIMITS.files) throw new Error("В папке может быть до 10 000 файлов.");
    } else if (entry.isDirectory) {
      const reader = (entry as FileSystemDirectoryEntry).createReader();
      for (;;) {
        const batch = await new Promise<FileSystemEntry[]>((resolve, reject) => reader.readEntries(resolve, reject));
        if (!batch.length) break;
        for (const child of batch) await visit(child, path);
      }
    }
  }
  const reader = (root as FileSystemDirectoryEntry).createReader();
  for (;;) {
    const batch = await new Promise<FileSystemEntry[]>((resolve, reject) => reader.readEntries(resolve, reject));
    if (!batch.length) break;
    for (const entry of batch) await visit(entry, "");
  }
  return validateSelection({ name: root.name, files });
}

export function selectedFolder(files: File[]): FolderSelection {
  const names = new Set(files.map((file) => (file.webkitRelativePath ?? "").split("/")[0]));
  if (names.size !== 1 || files.some((file) => !file.webkitRelativePath?.includes("/"))) {
    throw new Error("Выберите одну папку заказа целиком.");
  }
  return validateSelection({ name: [...names][0], files: files.map((file) => ({
    file, path: file.webkitRelativePath.split("/").slice(1).join("/"),
  })) });
}

export function validateSelection(selection: FolderSelection): FolderSelection {
  const { files } = selection;
  if (!files.length) throw new Error("В папке нет файлов. Выберите папку с документами заказа.");
  if (files.length > FOLDER_LIMITS.files) throw new Error("В папке может быть до 10 000 файлов.");
  if (files.some(({ file }) => file.size > FOLDER_LIMITS.fileBytes)) {
    throw new Error("Один файл может занимать до 100 МБ.");
  }
  if (files.reduce((sum, { file }) => sum + file.size, 0) > FOLDER_LIMITS.bytes) {
    throw new Error("Общий размер папки может быть до 20 ГБ.");
  }
  return { ...selection, files: [...files].sort((a, b) => a.path.localeCompare(b.path, "ru", { numeric: true })) };
}

export function folderUploadKey(selection: FolderSelection): string {
  const signature = JSON.stringify([selection.name, selection.files.map(({ path, file }) => [path, file.size, file.lastModified])]);
  try {
    const saved = JSON.parse(sessionStorage.getItem("calc.folder-upload") ?? "null");
    if (saved?.signature === signature && typeof saved.id === "string") return saved.id;
  } catch { /* Private browsing may disable storage. The current selection still retries. */ }
  const id = crypto.randomUUID();
  try { sessionStorage.setItem("calc.folder-upload", JSON.stringify({ id, signature })); } catch { /* optional persistence */ }
  return id;
}

export async function uploadFolder(
  selection: FolderSelection,
  uploadId: string,
  onProgress: (progress: UploadProgress) => void,
  signal: AbortSignal,
): Promise<string> {
  const request = <T>(url: string, init?: RequestInit) => fetchJSON<T>(url, { ...init, signal });
  const receipt = await request<UploadReceipt>("/api/calc/folder-uploads", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ upload_id: uploadId, folder_name: selection.name,
      files: selection.files.map(({ path, file }) => ({ path, size: file.size })) }),
  });
  if (receipt.order_id) return receipt.order_id;
  const received = new Set(receipt.received);
  const totalBytes = selection.files.reduce((sum, { file }) => sum + file.size, 0);
  let bytes = selection.files.reduce((sum, { file }, index) => sum + (received.has(index) ? file.size : 0), 0);
  const update = () => onProgress({ completed: received.size, total: selection.files.length, bytes, totalBytes });
  update();
  let cursor = 0;
  let failure: unknown;
  async function worker() {
    while (cursor < selection.files.length && !failure && !signal.aborted) {
      const index = cursor++;
      if (received.has(index)) continue;
      try {
        await request(`/api/calc/folder-uploads/${uploadId}/files/${index}`, {
          method: "PUT", body: selection.files[index].file,
          headers: { "Content-Type": "application/octet-stream" },
        });
        received.add(index);
        bytes += selection.files[index].file.size;
        update();
      } catch (error) { failure = error; }
    }
  }
  await Promise.all([worker(), worker(), worker()]);
  if (failure) throw failure;
  signal.throwIfAborted();
  const result = await request<{ order_id: string }>(`/api/calc/folder-uploads/${uploadId}/complete`, { method: "POST" });
  return result.order_id;
}

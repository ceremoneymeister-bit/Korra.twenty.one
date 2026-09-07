import { fetchJSON } from "@/lib/api";

export const FOLDER_LIMITS = { files: 10_000, directories: 10_000, fileBytes: 100 * 1024 ** 2, bytes: 20 * 1024 ** 3 };

export interface FolderFile { path: string; file: File }
export interface FolderSelection {
  name: string;
  files: FolderFile[];
  directories?: string[];
  directoryCapture?: "complete" | "files-only";
}
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
  const directories: string[] = [];
  async function visit(entry: FileSystemEntry, parent: string): Promise<void> {
    const path = parent ? `${parent}/${entry.name}` : entry.name;
    if (entry.isFile) {
      const file = await new Promise<File>((resolve, reject) =>
        (entry as FileSystemFileEntry).file(resolve, reject));
      files.push({ path, file });
      if (files.length > FOLDER_LIMITS.files) throw new Error("В папке может быть до 10 000 файлов.");
    } else if (entry.isDirectory) {
      directories.push(path);
      if (directories.length > FOLDER_LIMITS.directories) throw new Error("В заказе может быть до 10 000 вложенных папок.");
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
  return validateSelection({ name: root.name, files, directories, directoryCapture: "complete" });
}

/** Read-only traversal of the handle returned by showDirectoryPicker(). */
export async function readDirectoryHandle(root: FileSystemDirectoryHandle): Promise<FolderSelection> {
  const files: FolderFile[] = [];
  const directories: string[] = [];
  async function visit(directory: FileSystemDirectoryHandle, parent: string): Promise<void> {
    for await (const entry of directory.values()) {
      const path = parent ? `${parent}/${entry.name}` : entry.name;
      if (entry.kind === "directory") {
        directories.push(path);
        if (directories.length > FOLDER_LIMITS.directories) throw new Error("В заказе может быть до 10 000 вложенных папок.");
        await visit(entry, path);
      } else {
        files.push({ path, file: await entry.getFile() });
        if (files.length > FOLDER_LIMITS.files) throw new Error("В папке может быть до 10 000 файлов.");
      }
    }
  }
  await visit(root, "");
  return validateSelection({ name: root.name, files, directories, directoryCapture: "complete" });
}

export function selectedFolder(files: File[]): FolderSelection {
  if (!files.length) throw new Error("В выбранной папке нет файлов. Перетащите папку, чтобы сохранить её вложенные папки.");
  const names = new Set(files.map((file) => (file.webkitRelativePath ?? "").split("/")[0]));
  if (names.size !== 1 || files.some((file) => !file.webkitRelativePath?.includes("/"))) {
    throw new Error("Выберите одну папку заказа целиком.");
  }
  return validateSelection({ name: [...names][0], directoryCapture: "files-only", files: files.map((file) => ({
    file, path: file.webkitRelativePath.split("/").slice(1).join("/"),
  })) });
}

function collectDirectories(paths: string[], explicit: string[] = []): string[] {
  if (explicit.length > FOLDER_LIMITS.directories) throw new Error("В заказе может быть до 10 000 вложенных папок.");
  const directories = new Set(explicit);
  for (const path of [...paths, ...explicit]) {
    const parts = path.split("/");
    for (let i = 1; i < parts.length; i++) directories.add(parts.slice(0, i).join("/"));
  }
  if (directories.size > FOLDER_LIMITS.directories) throw new Error("В заказе может быть до 10 000 вложенных папок.");
  return [...directories].sort();
}

export function validateSelection(selection: FolderSelection): FolderSelection {
  const { files } = selection;
  const directories = collectDirectories(files.map(({ path }) => path), selection.directories);
  if (!files.length && !directories.length) throw new Error("Папка полностью пуста. Добавьте документы или вложенные папки заказа.");
  if (files.length > FOLDER_LIMITS.files) throw new Error("В папке может быть до 10 000 файлов.");
  if (files.some(({ file }) => file.size > FOLDER_LIMITS.fileBytes)) {
    throw new Error("Один файл может занимать до 100 МБ.");
  }
  if (files.reduce((sum, { file }) => sum + file.size, 0) > FOLDER_LIMITS.bytes) {
    throw new Error("Общий размер папки может быть до 20 ГБ.");
  }
  return { ...selection, directories, files: [...files].sort((a, b) => a.path.localeCompare(b.path, "ru", { numeric: true })) };
}

export function folderUploadKey(selection: FolderSelection): string {
  const paths = selection.files.map(({ path }) => path);
  const inferred = collectDirectories(paths);
  const directories = collectDirectories(paths, selection.directories);
  const parts: unknown[] = [selection.name, selection.files.map(({ path, file }) => [path, file.size, file.lastModified])];
  // Preserve keys issued before directory support for the same file tree.
  // Explicit empty directories change the identity and require a fresh key.
  if (JSON.stringify(directories) !== JSON.stringify(inferred)) parts.push(directories);
  const signature = JSON.stringify(parts);
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
  const request = <T>(url: string, init?: RequestInit): Promise<T> => {
    signal.throwIfAborted();
    // Auth recovery can wait indefinitely after requesting a page reload. If
    // the operator stays on the page, Pause must still settle this request.
    return new Promise<T>((resolve, reject) => {
      const abort = () => { signal.removeEventListener("abort", abort); reject(signal.reason); };
      signal.addEventListener("abort", abort, { once: true });
      fetchJSON<T>(url, { ...init, signal }).then(
        (value) => { signal.removeEventListener("abort", abort); resolve(value); },
        (cause) => { signal.removeEventListener("abort", abort); reject(cause); },
      );
    });
  };
  const receipt = await request<UploadReceipt>("/api/calc/folder-uploads", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ upload_id: uploadId, folder_name: selection.name,
      directories: collectDirectories(selection.files.map(({ path }) => path), selection.directories),
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

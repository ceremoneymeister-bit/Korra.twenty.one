/**
 * Выбор папки целиком: drop, picker каталога и `<input webkitdirectory>`.
 *
 * Порт принятой механики расчётчика (`calc-folder-upload.ts`): читатель
 * каталога отдаёт записи пачками, а не всю папку разом, поэтому обход
 * приходится крутить до пустой пачки. Пустые вложенные папки сохраняем явно —
 * из одних только путей файлов их не восстановить.
 *
 * Ключ возобновления живёт в `sessionStorage`: после перезагрузки вкладки
 * владелец выбирает ту же папку и попадает в ту же сессию, дозагружая только
 * недостающее.
 */

export const FOLDER_LIMITS = {
  files: 10_000,
  directories: 10_000,
  fileBytes: 2 * 1024 ** 3,
  bytes: 20 * 1024 ** 3,
};

const RESUME_STORAGE_KEY = "korra.folder-upload";

export interface FolderFile {
  path: string;
  file: File;
}

export interface FolderSelection {
  name: string;
  files: FolderFile[];
  directories?: string[];
  /** `files-only` — picker без drop: пустые папки в нём не видны. */
  directoryCapture?: "complete" | "files-only";
}

/** Читатель каталога отдаёт пачки (обычно по 100 записей), а не всю папку. */
export async function readDroppedFolder(items: DataTransferItemList): Promise<FolderSelection> {
  // Забираем записи до того, как защищённое хранилище события drop закроется.
  const entries = Array.from(items)
    .filter((item) => item.kind === "file")
    .map((item) => item.webkitGetAsEntry?.());
  if (entries.length !== 1 || !entries[0]?.isDirectory) {
    throw new Error("Перетащите одну папку. Или выберите её кнопкой загрузки.");
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
      if (directories.length > FOLDER_LIMITS.directories) {
        throw new Error("В папке может быть до 10 000 вложенных папок.");
      }
      const reader = (entry as FileSystemDirectoryEntry).createReader();
      for (;;) {
        const batch = await new Promise<FileSystemEntry[]>((resolve, reject) =>
          reader.readEntries(resolve, reject));
        if (!batch.length) break;
        for (const child of batch) await visit(child, path);
      }
    }
  }
  const reader = (root as FileSystemDirectoryEntry).createReader();
  for (;;) {
    const batch = await new Promise<FileSystemEntry[]>((resolve, reject) =>
      reader.readEntries(resolve, reject));
    if (!batch.length) break;
    for (const entry of batch) await visit(entry, "");
  }
  return validateSelection({ name: root.name, files, directories, directoryCapture: "complete" });
}

/** Обход только на чтение того, что вернул showDirectoryPicker(). */
export async function readDirectoryHandle(root: FileSystemDirectoryHandle): Promise<FolderSelection> {
  const files: FolderFile[] = [];
  const directories: string[] = [];
  async function visit(directory: FileSystemDirectoryHandle, parent: string): Promise<void> {
    for await (const entry of directory.values()) {
      const path = parent ? `${parent}/${entry.name}` : entry.name;
      if (entry.kind === "directory") {
        directories.push(path);
        if (directories.length > FOLDER_LIMITS.directories) {
          throw new Error("В папке может быть до 10 000 вложенных папок.");
        }
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
  if (!files.length) {
    throw new Error("В выбранной папке нет файлов. Перетащите папку, чтобы сохранить её вложенные папки.");
  }
  const names = new Set(files.map((file) => (file.webkitRelativePath ?? "").split("/")[0]));
  if (names.size !== 1 || files.some((file) => !file.webkitRelativePath?.includes("/"))) {
    throw new Error("Выберите одну папку целиком.");
  }
  return validateSelection({
    name: [...names][0],
    directoryCapture: "files-only",
    files: files.map((file) => ({
      file,
      path: file.webkitRelativePath.split("/").slice(1).join("/"),
    })),
  });
}

export function collectDirectories(paths: string[], explicit: string[] = []): string[] {
  if (explicit.length > FOLDER_LIMITS.directories) {
    throw new Error("В папке может быть до 10 000 вложенных папок.");
  }
  const directories = new Set(explicit);
  for (const path of [...paths, ...explicit]) {
    const parts = path.split("/");
    for (let i = 1; i < parts.length; i++) directories.add(parts.slice(0, i).join("/"));
  }
  if (directories.size > FOLDER_LIMITS.directories) {
    throw new Error("В папке может быть до 10 000 вложенных папок.");
  }
  return [...directories].sort();
}

export function validateSelection(selection: FolderSelection): FolderSelection {
  const { files } = selection;
  const directories = collectDirectories(files.map(({ path }) => path), selection.directories);
  // A directory handle/drop gives us the root itself even without children.
  // An empty files-only selection carries no such directory identity.
  if (!files.length && !directories.length && selection.directoryCapture !== "complete") {
    throw new Error("Папка полностью пуста. Добавьте в неё файлы или вложенные папки.");
  }
  if (files.length > FOLDER_LIMITS.files) throw new Error("В папке может быть до 10 000 файлов.");
  if (files.some(({ file }) => file.size > FOLDER_LIMITS.fileBytes)) {
    throw new Error("Один файл может занимать до 2 ГБ.");
  }
  if (files.reduce((sum, { file }) => sum + file.size, 0) > FOLDER_LIMITS.bytes) {
    throw new Error("Общий размер папки может быть до 20 ГБ.");
  }
  return {
    ...selection,
    directories,
    files: [...files].sort((a, b) => a.path.localeCompare(b.path, "ru", { numeric: true })),
  };
}

/** Один и тот же выбор после перезагрузки вкладки продолжает ту же сессию. */
export function folderUploadKey(selection: FolderSelection, scope = "", fingerprint = ""): string {
  const paths = selection.files.map(({ path }) => path);
  const directories = collectDirectories(paths, selection.directories);
  const signature = fingerprint ? JSON.stringify([scope, fingerprint]) : JSON.stringify([
    selection.name,
    selection.files.map(({ path, file }) => [path, file.size, file.lastModified]),
    directories,
    scope,
    fingerprint,
  ]);
  let records: { signature: string; id: string }[] = [];
  try {
    const saved = JSON.parse(sessionStorage.getItem(RESUME_STORAGE_KEY) ?? "null");
    records = (Array.isArray(saved) ? saved : saved ? [saved] : [])
      .filter(item => typeof item?.signature === "string" && typeof item?.id === "string");
    const found = records.find(item => item.signature === signature);
    if (found) return found.id;
  } catch {
    /* Приватный режим может отключить хранилище. Повтор выбора всё равно работает. */
  }
  const id = crypto.randomUUID();
  try {
    sessionStorage.setItem(RESUME_STORAGE_KEY, JSON.stringify([...records.slice(-19), { id, signature }]));
  } catch {
    /* Ключ возобновления необязателен. */
  }
  return id;
}

import type { ManagedFileEntry } from "@/lib/api";

export type FileSortMode = "name" | "modified" | "size";

export function filterAndSortFileEntries(
  entries: ManagedFileEntry[],
  query: string,
  sort: FileSortMode,
): ManagedFileEntry[] {
  const needle = query.trim().toLocaleLowerCase("ru-RU");
  const filtered = needle
    ? entries.filter((entry) => entry.name.toLocaleLowerCase("ru-RU").includes(needle))
    : entries;
  return [...filtered].sort((left, right) => {
    if (left.is_directory !== right.is_directory) return left.is_directory ? -1 : 1;
    if (sort === "modified" && left.mtime !== right.mtime) return right.mtime - left.mtime;
    if (sort === "size") {
      const sizeDifference = (right.size ?? -1) - (left.size ?? -1);
      if (sizeDifference !== 0) return sizeDifference;
    }
    return left.name.localeCompare(right.name, "ru-RU", { numeric: true, sensitivity: "base" });
  });
}

export function availableCopyName(name: string, existingNames: Iterable<string>): string {
  const existing = new Set(Array.from(existingNames, (value) => value.toLocaleLowerCase("ru-RU")));
  const lastDot = name.lastIndexOf(".");
  const hasExtension = lastDot > 0;
  const stem = hasExtension ? name.slice(0, lastDot) : name;
  const extension = hasExtension ? name.slice(lastDot) : "";
  for (let index = 1; index < 10_000; index += 1) {
    const suffix = index === 1 ? " (копия)" : ` (копия ${index})`;
    const candidate = `${stem}${suffix}${extension}`;
    if (!existing.has(candidate.toLocaleLowerCase("ru-RU"))) return candidate;
  }
  return `${stem} (копия ${Date.now()})${extension}`;
}

export interface FileBreadcrumb {
  label: string;
  path: string;
}

export function buildFileBreadcrumbs(
  root: string | null | undefined,
  path: string | null | undefined,
  rootLabel = "Файлы",
): FileBreadcrumb[] {
  const normalizedRoot = (root ?? "").replaceAll("\\", "/").replace(/\/$/, "");
  const normalizedPath = (path ?? normalizedRoot).replaceAll("\\", "/").replace(/\/$/, "");
  if (!normalizedRoot || !normalizedPath.startsWith(`${normalizedRoot}/`)) {
    return [{ label: rootLabel, path: normalizedPath || normalizedRoot }];
  }
  const parts = normalizedPath.slice(normalizedRoot.length + 1).split("/").filter(Boolean);
  const breadcrumbs: FileBreadcrumb[] = [{ label: rootLabel, path: normalizedRoot }];
  let cursor = normalizedRoot;
  for (const part of parts) {
    cursor = `${cursor}/${part}`;
    breadcrumbs.push({ label: part, path: cursor });
  }
  return breadcrumbs;
}

/* ------------------------------------------------------------------------- */
/* Понятные имена поверх физической структуры общего workspace (fleet).        */
/* Пути не меняются: это только то, что человек видит в крошках и списке.     */
/* ------------------------------------------------------------------------- */

export const WORKSPACE_ROOT_LABEL = "Мои файлы";
export const CHAT_UPLOADS_LABEL = "Загрузки из чатов";

/** Физическая папка чат-загрузок: `workspace/client/inbox/ГГГГ-ММ-ДД/<пакет>`. */
const CHAT_UPLOADS_SEGMENTS = ["client", "inbox"] as const;
const UPLOAD_DATE = /^(\d{4})-(\d{2})-(\d{2})$/;
const UPLOAD_PACKAGE = /^[0-9a-f]{6}-(\d{2})(\d{2})$/;

function normalizeSlashes(value: string | null | undefined): string {
  return (value ?? "").replaceAll("\\", "/").replace(/\/$/, "");
}

/** Сегменты пути относительно корня; `null`, если путь вне корня. */
export function workspaceRelativeParts(root: string | null | undefined, path: string | null | undefined): string[] | null {
  const normalizedRoot = normalizeSlashes(root);
  const normalizedPath = normalizeSlashes(path);
  if (!normalizedRoot) return null;
  if (normalizedPath === normalizedRoot) return [];
  if (!normalizedPath.startsWith(`${normalizedRoot}/`)) return null;
  return normalizedPath.slice(normalizedRoot.length + 1).split("/").filter(Boolean);
}

export function chatUploadsPath(root: string): string {
  return `${normalizeSlashes(root)}/${CHAT_UPLOADS_SEGMENTS.join("/")}`;
}

function isChatUploadsPrefix(parts: string[]): boolean {
  return parts[0] === CHAT_UPLOADS_SEGMENTS[0] && (parts.length === 1 || parts[1] === CHAT_UPLOADS_SEGMENTS[1]);
}

const uploadDateFormat = new Intl.DateTimeFormat("ru-RU", { day: "numeric", month: "long", year: "numeric" });

/**
 * Имя элемента для показа: `client` и `client/inbox` в корне — «Загрузки из
 * чатов», папка даты — «15 сентября 2026», пакет загрузки — «Загрузка 12:30».
 * Остальное показывается как есть.
 */
export function workspaceEntryLabel(root: string | null | undefined, entryPath: string, name: string): string {
  const parts = workspaceRelativeParts(root, entryPath);
  if (!parts || parts.length === 0) return name;
  if (isChatUploadsPrefix(parts) && parts.length <= 2) return CHAT_UPLOADS_LABEL;
  if (parts.length >= 3 && parts[0] === "client" && parts[1] === "inbox") {
    if (parts.length === 3) {
      const match = UPLOAD_DATE.exec(name);
      if (match) {
        const date = new Date(Number(match[1]), Number(match[2]) - 1, Number(match[3]));
        if (!Number.isNaN(date.getTime())) return uploadDateFormat.format(date).replace(/\s*г\.$/, "");
      }
    }
    if (parts.length === 4) {
      const match = UPLOAD_PACKAGE.exec(name);
      if (match) return `Загрузка ${match[1]}:${match[2]}`;
    }
  }
  return name;
}

/**
 * Куда вести клик по элементу: «Загрузки из чатов» в корне сразу открывает
 * `client/inbox`, минуя служебный уровень `client`.
 */
export function workspaceEntryTarget(root: string | null | undefined, entryPath: string): string {
  const parts = workspaceRelativeParts(root, entryPath);
  if (parts && parts.length === 1 && parts[0] === CHAT_UPLOADS_SEGMENTS[0]) return chatUploadsPath(root!);
  return entryPath;
}

/** Родитель для «..»: из «Загрузок из чатов» — сразу в корень. */
export function workspaceParentPath(root: string | null | undefined, path: string, fallback: string | null): string | null {
  const parts = workspaceRelativeParts(root, path);
  if (parts && isChatUploadsPrefix(parts) && parts.length <= 2) return normalizeSlashes(root);
  return fallback;
}

/** Крошки с человеческими именами; `client/inbox` схлопывается в одну. */
export function buildWorkspaceBreadcrumbs(root: string | null | undefined, path: string | null | undefined): FileBreadcrumb[] {
  const crumbs = buildFileBreadcrumbs(root, path, WORKSPACE_ROOT_LABEL);
  const parts = workspaceRelativeParts(root, path ?? root);
  if (!parts || parts.length === 0) return crumbs;
  const result: FileBreadcrumb[] = [crumbs[0]];
  for (let index = 1; index < crumbs.length; index += 1) {
    const relative = parts.slice(0, index);
    if (relative.length === 2 && isChatUploadsPrefix(relative)) {
      result[result.length - 1] = { label: CHAT_UPLOADS_LABEL, path: crumbs[index].path };
      continue;
    }
    result.push({ label: workspaceEntryLabel(root, crumbs[index].path, crumbs[index].label), path: crumbs[index].path });
  }
  return result;
}

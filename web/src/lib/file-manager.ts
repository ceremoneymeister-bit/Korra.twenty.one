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

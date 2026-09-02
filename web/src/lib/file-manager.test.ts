import { describe, expect, it } from "vitest";

import type { ManagedFileEntry } from "@/lib/api";
import {
  availableCopyName,
  buildFileBreadcrumbs,
  filterAndSortFileEntries,
} from "@/lib/file-manager";

const entries: ManagedFileEntry[] = [
  { name: "Фото 10.png", path: "/data/Фото 10.png", is_directory: false, size: 10, mtime: 30, mime_type: "image/png" },
  { name: "Проекты", path: "/data/Проекты", is_directory: true, size: null, mtime: 10, mime_type: null },
  { name: "фото 2.png", path: "/data/фото 2.png", is_directory: false, size: 20, mtime: 20, mime_type: "image/png" },
];

describe("file manager helpers", () => {
  it("filters without case sensitivity and always keeps folders first", () => {
    expect(filterAndSortFileEntries(entries, "ФОТО", "name").map((entry) => entry.name))
      .toEqual(["фото 2.png", "Фото 10.png"]);
    expect(filterAndSortFileEntries(entries, "", "modified")[0].name).toBe("Проекты");
  });

  it("chooses a non-conflicting Russian copy name", () => {
    expect(availableCopyName("отчёт.pdf", ["отчёт.pdf"])).toBe("отчёт (копия).pdf");
    expect(availableCopyName("отчёт.pdf", ["отчёт.pdf", "Отчёт (КОПИЯ).pdf"]))
      .toBe("отчёт (копия 2).pdf");
  });

  it("builds breadcrumbs without exposing segments above the managed root", () => {
    expect(buildFileBreadcrumbs("/opt/data/workspace", "/opt/data/workspace/Клиенты/Иван"))
      .toEqual([
        { label: "Файлы", path: "/opt/data/workspace" },
        { label: "Клиенты", path: "/opt/data/workspace/Клиенты" },
        { label: "Иван", path: "/opt/data/workspace/Клиенты/Иван" },
      ]);
  });
});

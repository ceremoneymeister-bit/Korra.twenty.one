import { describe, expect, it } from "vitest";

import type { ManagedFileEntry } from "@/lib/api";
import {
  availableCopyName,
  buildFileBreadcrumbs,
  buildWorkspaceBreadcrumbs,
  filterAndSortFileEntries,
  workspaceEntryLabel,
  workspaceEntryTarget,
  workspaceParentPath,
} from "@/lib/file-manager";

const entries: ManagedFileEntry[] = [
  { name: "Фото 10.png", path: "/data/Фото 10.png", is_directory: false, size: 10, created_at: 20, mtime: 30, mime_type: "image/png" },
  { name: "Проекты", path: "/data/Проекты", is_directory: true, size: null, created_at: 10, mtime: 40, mime_type: null },
  { name: "фото 2.png", path: "/data/фото 2.png", is_directory: false, size: 20, created_at: 30, mtime: 20, mime_type: "image/png" },
  { name: "Архив", path: "/data/Архив", is_directory: true, size: null, created_at: null, mtime: 10, mime_type: null },
];

describe("file manager helpers", () => {
  it("filters without case sensitivity and keeps folders first for name", () => {
    expect(filterAndSortFileEntries(entries, "ФОТО", "name").map((entry) => entry.name))
      .toEqual(["фото 2.png", "Фото 10.png"]);
    expect(filterAndSortFileEntries(entries, "", "name").slice(0, 2).map((entry) => entry.name))
      .toEqual(["Архив", "Проекты"]);
  });

  it("mixes files and folders for honest date order and leaves unknown creation last", () => {
    expect(filterAndSortFileEntries(entries, "", "modified").map((entry) => entry.name))
      .toEqual(["Проекты", "Фото 10.png", "фото 2.png", "Архив"]);
    expect(filterAndSortFileEntries(entries, "", "created").map((entry) => entry.name))
      .toEqual(["фото 2.png", "Фото 10.png", "Проекты", "Архив"]);
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

describe("workspace labels over the physical fleet layout", () => {
  const root = "/opt/data/workspace";
  it("names the chat inbox and its date/package folders without touching paths", () => {
    expect(workspaceEntryLabel(root, `${root}/client`, "client")).toBe("Загрузки из чатов");
    expect(workspaceEntryLabel(root, `${root}/client/inbox`, "inbox")).toBe("Загрузки из чатов");
    expect(workspaceEntryLabel(root, `${root}/client/inbox/2026-09-15`, "2026-09-15")).toBe("15 сентября 2026");
    expect(workspaceEntryLabel(root, `${root}/client/inbox/2026-09-15/a1b2c3-1230`, "a1b2c3-1230")).toBe("Загрузка 12:30");
    expect(workspaceEntryLabel(root, `${root}/client/inbox/2026-09-15/a1b2c3-1230/фото.jpg`, "фото.jpg")).toBe("фото.jpg");
    // Папка владельца с таким же именем вне inbox остаётся собой.
    expect(workspaceEntryLabel(root, `${root}/Проекты/client`, "client")).toBe("client");
    expect(workspaceEntryLabel(root, `${root}/2026-09-15`, "2026-09-15")).toBe("2026-09-15");
    expect(workspaceEntryLabel(null, `${root}/client`, "client")).toBe("client");
  });

  it("opens the inbox directly from the root and returns to the root from it", () => {
    expect(workspaceEntryTarget(root, `${root}/client`)).toBe(`${root}/client/inbox`);
    expect(workspaceEntryTarget(root, `${root}/Проекты`)).toBe(`${root}/Проекты`);
    expect(workspaceParentPath(root, `${root}/client/inbox`, `${root}/client`)).toBe(root);
    expect(workspaceParentPath(root, `${root}/client/inbox/2026-09-15`, `${root}/client/inbox`)).toBe(`${root}/client/inbox`);
    expect(workspaceParentPath(root, `${root}/Проекты/Иван`, `${root}/Проекты`)).toBe(`${root}/Проекты`);
  });

  it("collapses client/inbox into one readable breadcrumb", () => {
    expect(buildWorkspaceBreadcrumbs(root, `${root}/client/inbox/2026-09-15/a1b2c3-1230`)).toEqual([
      { label: "Мои файлы", path: root },
      { label: "Загрузки из чатов", path: `${root}/client/inbox` },
      { label: "15 сентября 2026", path: `${root}/client/inbox/2026-09-15` },
      { label: "Загрузка 12:30", path: `${root}/client/inbox/2026-09-15/a1b2c3-1230` },
    ]);
    expect(buildWorkspaceBreadcrumbs(root, root)).toEqual([{ label: "Мои файлы", path: root }]);
    expect(buildWorkspaceBreadcrumbs(root, `${root}/Клиенты/Иван`).map((item) => item.label))
      .toEqual(["Мои файлы", "Клиенты", "Иван"]);
  });
});

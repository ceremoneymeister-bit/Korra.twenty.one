// @vitest-environment jsdom
import { beforeEach, describe, expect, it } from "vitest";
import {
  FOLDER_LIMITS,
  folderUploadKey,
  readDirectoryHandle,
  readDroppedFolder,
  selectedFolder,
  validateSelection,
  type FolderSelection,
} from "./folder-select";

beforeEach(() => {
  sessionStorage.clear();
});

/** Дерево для drop: читатель отдаёт записи пачками и потом пустую пачку. */
function entry(name: string, children: unknown[] = []): unknown {
  return {
    name,
    isDirectory: true,
    createReader: () => {
      let pending = true;
      return {
        readEntries: (done: (batch: unknown[]) => void) => {
          done(pending ? children : []);
          pending = false;
        },
      };
    },
  };
}

function fileEntry(file: File): unknown {
  return { name: file.name, isFile: true, file: (done: (value: File) => void) => done(file) };
}

function drop(root: unknown): DataTransferItemList {
  return [{ kind: "file", webkitGetAsEntry: () => root }] as unknown as DataTransferItemList;
}

describe("выбор папки целиком", () => {
  it("собирает все пачки читателя и вложенность за пределами одной пачки", async () => {
    const many = Array.from({ length: 250 }, (_, index) =>
      new File([String(index)], `Кадр ${index}.jpg`));
    let cursor = 0;
    const photos = {
      name: "Фото",
      isDirectory: true,
      createReader: () => ({
        readEntries: (done: (batch: unknown[]) => void) => {
          const batch = many.slice(cursor, cursor + 100).map(fileEntry);
          cursor += 100;
          done(batch);
        },
      }),
    };
    const result = await readDroppedFolder(drop(entry("Референсы", [photos])));
    expect(result.files).toHaveLength(250);
    expect(result.files.every(({ path }) => path.startsWith("Фото/"))).toBe(true);
    expect(result.directories).toEqual(["Фото"]);
  });

  it("сохраняет пустые вложенные папки, которых нет ни в одном пути файла", async () => {
    const root = entry("Проект", [entry("Чертежи", [entry("Корпус")]), entry("Результаты")]);
    const result = await readDroppedFolder(drop(root));
    expect(result.files).toEqual([]);
    expect(result.directories).toEqual(["Результаты", "Чертежи", "Чертежи/Корпус"]);
    expect(result.directoryCapture).toBe("complete");
  });

  it("требует ровно одну папку в drop", async () => {
    await expect(readDroppedFolder(drop({ name: "смета.pdf", isFile: true })))
      .rejects.toThrow("одну папку");
  });

  it("сохраняет имя полностью пустой папки при drop и выборе handle", async () => {
    expect(await readDroppedFolder(drop(entry("Пустая")))).toMatchObject({
      name: "Пустая", files: [], directories: [], directoryCapture: "complete",
    });
    const handle = { name: "Пустая", kind: "directory", async *values() {} } as FileSystemDirectoryHandle;
    expect(await readDirectoryHandle(handle)).toMatchObject({ name: "Пустая", files: [], directories: [] });
  });

  it("читает handle каталога вместе с пустыми подпапками", async () => {
    const file = new File(["jpeg"], "кадр.jpg");
    function handle(name: string, children: unknown[] = []): FileSystemDirectoryHandle {
      return {
        name,
        kind: "directory",
        async *values() {
          yield* children;
        },
      } as FileSystemDirectoryHandle;
    }
    const root = handle("Референсы", [
      handle("Пустая"),
      handle("Фото", [{ name: file.name, kind: "file", getFile: async () => file }]),
    ]);
    const result = await readDirectoryHandle(root);
    expect(result.files).toEqual([{ file, path: "Фото/кадр.jpg" }]);
    expect(result.directories).toEqual(["Пустая", "Фото"]);
  });

  it("picker без drop требует именно папку и отбрасывает её имя из путей", () => {
    expect(() => selectedFolder([new File(["x"], "смета.pdf")])).toThrow("папк");
    const file = new File(["jpeg"], "кадр.jpg");
    Object.defineProperty(file, "webkitRelativePath", { value: "Референсы/Фото/кадр.jpg" });
    const result = selectedFolder([file]);
    expect(result.name).toBe("Референсы");
    expect(result.files[0].path).toBe("Фото/кадр.jpg");
    expect(result.directoryCapture).toBe("files-only");
  });
});

describe("границы выбора", () => {
  it("называет превышенный лимит до первого запроса", () => {
    const huge = { size: FOLDER_LIMITS.fileBytes + 1 } as File;
    expect(() => validateSelection({ name: "П", files: [{ path: "видео.mp4", file: huge }] }))
      .toThrow("до 2 ГБ");
    expect(() => validateSelection({ name: "П", files: [] })).toThrow("пуста");
  });

  it("сортирует файлы по-человечески", () => {
    const selection: FolderSelection = {
      name: "П",
      files: [10, 2, 1].map((index) => ({
        path: `Кадр ${index}.jpg`,
        file: new File(["x"], `Кадр ${index}.jpg`),
      })),
    };
    expect(validateSelection(selection).files.map(({ path }) => path))
      .toEqual(["Кадр 1.jpg", "Кадр 2.jpg", "Кадр 10.jpg"]);
  });
});

describe("ключ возобновления", () => {
  it("возвращает тот же идентификатор для того же выбора и новый — для изменившегося", () => {
    const build = (size: number): FolderSelection => ({
      name: "Референсы",
      files: [{
        path: "кадр.jpg",
        file: new File(["x".repeat(size)], "кадр.jpg", { lastModified: 1_757_650_000_000 }),
      }],
    });
    const first = folderUploadKey(build(1));
    expect(folderUploadKey(build(1))).toBe(first);
    expect(folderUploadKey(build(2))).not.toBe(first);
  });

  it("работает без хранилища: приватный режим не ломает загрузку", () => {
    const storage = sessionStorage;
    Object.defineProperty(globalThis, "sessionStorage", {
      value: {
        getItem() {
          throw new Error("denied");
        },
        setItem() {
          throw new Error("denied");
        },
      },
      configurable: true,
    });
    try {
      expect(folderUploadKey({ name: "П", files: [] })).toMatch(/^[0-9a-f-]{36}$/);
    } finally {
      Object.defineProperty(globalThis, "sessionStorage", { value: storage, configurable: true });
    }
  });
});

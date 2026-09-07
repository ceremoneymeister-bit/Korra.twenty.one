import { describe, expect, it } from "vitest";
import { buildDocumentTree, documentEntries, parentDocumentPath, type CalcOrderDocument } from "./calc-document-tree";

const document = (path: string): CalcOrderDocument => ({
  name: path.split("/").at(-1)!, relative_path: path, bytes: 5, download_url: `/download/${path}`,
});

describe("order document tree", () => {
  it("shows folders first and only immediate children, deriving old order folders", () => {
    const tree = buildDocumentTree([
      document("заявка.xlsx"), document("Чертежи/Сборка/деталь 10.pdf"),
      document("Чертежи/Сборка/деталь 2.pdf"), document("Чертежи/общий.pdf"),
    ]);
    expect(documentEntries(tree, "", "").map((entry) => entry.path)).toEqual(["Чертежи", "заявка.xlsx"]);
    expect(documentEntries(tree, "Чертежи", "").map((entry) => entry.path)).toEqual(["Чертежи/Сборка", "Чертежи/общий.pdf"]);
    expect(documentEntries(tree, "Чертежи/Сборка", "").map((entry) => entry.name)).toEqual(["деталь 2.pdf", "деталь 10.pdf"]);
    expect(documentEntries(tree, "", "")[0]).toMatchObject({ kind: "folder", fileCount: 3 });
    expect(parentDocumentPath("Чертежи/Сборка")).toBe("Чертежи");
    expect(parentDocumentPath("Чертежи")).toBe("");
  });

  it("keeps nested empty directories and deduplicates explicit and derived parents", () => {
    const tree = buildDocumentTree([document("Чертежи/деталь.pdf")], ["Чертежи", "Чертежи", "Пусто/Архив"]);
    expect(documentEntries(tree, "", "")).toHaveLength(2);
    expect(documentEntries(tree, "Пусто", "")).toEqual([{ kind: "folder", path: "Пусто/Архив", name: "Архив", fileCount: 0 }]);
    expect(documentEntries(tree, "Пусто/Архив", "")).toEqual([]);
  });

  it("searches full paths globally and preserves distinct files with the same basename", () => {
    const tree = buildDocumentTree([document("А/деталь.pdf"), document("Б/деталь.pdf"), document("заявка.xlsx")], ["Пусто"]);
    expect(documentEntries(tree, "А", "ДЕТАЛЬ").map((entry) => entry.path)).toEqual(["А/деталь.pdf", "Б/деталь.pdf"]);
    expect(documentEntries(tree, "А", "Б/деталь")).toHaveLength(1);
    expect(documentEntries(tree, "А", "Пусто")[0]).toMatchObject({ kind: "folder", fileCount: 0 });
    expect(documentEntries(tree, "А", " ").map((entry) => entry.path)).toEqual(["А/деталь.pdf"]);
  });
});

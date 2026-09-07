export interface CalcOrderDocument {
  name: string;
  relative_path: string;
  bytes: number;
  download_url: string;
}

export interface DocumentFolder {
  kind: "folder";
  path: string;
  name: string;
  fileCount: number;
}
export interface DocumentFile {
  kind: "file";
  path: string;
  name: string;
  file: CalcOrderDocument;
}
export type DocumentEntry = DocumentFolder | DocumentFile;
export interface DocumentTree {
  children: Map<string, DocumentEntry[]>;
  entries: DocumentEntry[];
}

const names = new Intl.Collator("ru", { numeric: true, sensitivity: "base" });
function compareEntries(a: DocumentEntry, b: DocumentEntry): number {
  if (a.kind !== b.kind) return a.kind === "folder" ? -1 : 1;
  return names.compare(a.name, b.name) || a.path.localeCompare(b.path);
}

export function parentDocumentPath(path: string): string {
  return path.split("/").slice(0, -1).join("/");
}

/** File paths supply folders for older orders; directory metadata preserves empty ones. */
export function buildDocumentTree(files: CalcOrderDocument[], directories: string[] = []): DocumentTree {
  const children = new Map<string, DocumentEntry[]>([["", []]]);
  const folders = new Map<string, DocumentFolder>();
  const entries: DocumentEntry[] = [];
  function addFolder(path: string): void {
    if (!path || folders.has(path)) return;
    const parent = parentDocumentPath(path);
    addFolder(parent);
    const folder: DocumentFolder = { kind: "folder", path, name: path.split("/").at(-1)!, fileCount: 0 };
    folders.set(path, folder);
    children.set(path, []);
    children.get(parent)!.push(folder);
    entries.push(folder);
  }
  for (const path of directories) addFolder(path.replace(/\/+$/, ""));
  for (const file of files) {
    const path = file.relative_path || file.name;
    let parent = parentDocumentPath(path);
    addFolder(parent);
    const entry: DocumentFile = { kind: "file", path, name: path.split("/").at(-1)!, file };
    children.get(parent)!.push(entry);
    entries.push(entry);
    while (parent) {
      folders.get(parent)!.fileCount++;
      parent = parentDocumentPath(parent);
    }
  }
  for (const list of children.values()) list.sort(compareEntries);
  entries.sort(compareEntries);
  return { children, entries };
}

/** Search always covers the full order, even while browsing a nested folder. */
export function documentEntries(tree: DocumentTree, folder: string, query: string): DocumentEntry[] {
  const needle = query.trim().toLocaleLowerCase("ru");
  return needle
    ? tree.entries.filter((entry) => entry.path.toLocaleLowerCase("ru").includes(needle))
    : tree.children.get(folder) ?? [];
}

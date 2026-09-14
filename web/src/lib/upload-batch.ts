import { folderUploadKey, validateSelection, type FolderSelection } from "@/lib/folder-select";
import { optimizeAll } from "@/lib/image-optimize";
import { DEFAULT_PART_BYTES, type ConflictPolicy, type UploadManifest } from "@/lib/upload-session";
import type { StartUploadInput } from "@/store/upload-jobs";

/** Hash bounded chunks, never buffer a multi-GB file in browser memory.
 * The chunk fingerprint protects resume identity even when size/mtime match.
 * Small files additionally get the server's optional full-file dedup hash. */
async function identify(file: File): Promise<{ fingerprint: string; sha256?: string }> {
  if (!crypto.subtle) return { fingerprint: crypto.randomUUID() };
  const hashes: string[] = [];
  for (let offset = 0; offset < file.size || offset === 0; offset += DEFAULT_PART_BYTES) {
    const data = await file.slice(offset, offset + DEFAULT_PART_BYTES).arrayBuffer();
    const digest = await crypto.subtle.digest("SHA-256", data);
    hashes.push(Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, "0")).join(""));
    if (!file.size) break;
  }
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(hashes.join(":")));
  return {
    fingerprint: Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, "0")).join(""),
    sha256: hashes.length === 1 ? hashes[0] : undefined,
  };
}

export interface PrepareBatchOptions {
  origin: "chat" | "files";
  target?: string;
  profile?: string;
  folder?: FolderSelection;
  originals?: boolean;
  conflict?: ConflictPolicy;
  /** Explicit grouping when current chat has fewer free attachment slots. */
  grouped?: boolean;
  onProgress?: (done: number, total: number) => void;
}

export async function prepareUploadBatch(files: File[], options: PrepareBatchOptions): Promise<StartUploadInput> {
  const raw = options.folder ?? { name: "Файлы", files: files.map(file => ({ path: file.name, file })) };
  const selection = validateSelection(raw);
  if (options.origin === "chat" && selection.files.length > 500) throw new Error("В чат можно загрузить до 500 файлов за раз.");
  // Optimise only chat photos. Files always preserves the original bytes.
  const optimized = await optimizeAll(selection.files.map(item => item.file), {
    originals: options.origin === "files" || options.originals,
    onProgress: options.onProgress,
  });
  const paths = new Set<string>();
  const entries = optimized.map((item, index) => {
    const source = selection.files[index].path;
    let path = source.slice(0, source.lastIndexOf("/") + 1) + item.file.name;
    const dot = path.lastIndexOf(".");
    const stem = dot > path.lastIndexOf("/") ? path.slice(0, dot) : path;
    const extension = path.slice(stem.length);
    for (let suffix = 2; paths.has(path); suffix++) path = `${stem} (${suffix})${extension}`;
    paths.add(path);
    return { path, file: item.file };
  });
  const identities: { fingerprint: string; sha256?: string }[] = [];
  for (const [index, entry] of entries.entries()) {
    identities.push(await identify(entry.file));
    options.onProgress?.(index + 1, entries.length);
  }
  const grouped = Boolean(options.folder || options.grouped || (options.origin === "chat" && entries.length > 30));
  const manifest: UploadManifest = {
    upload_id: "", origin: options.origin,
    target: options.target ? { kind: "path", path: options.target } : { kind: "inbox" },
    profile: options.profile, name: grouped ? selection.name : undefined,
    directories: selection.directories,
    on_conflict: options.conflict ?? "copy",
    client: { optimized_images: optimized.some(item => item.optimized), ua: navigator.userAgent },
    files: entries.map((entry, index) => ({ path: entry.path, size: entry.file.size,
      last_modified: entry.file.lastModified, sha256: identities[index].sha256 })),
  };
  const identity = JSON.stringify([manifest.files, manifest.directories, identities]);
  const fingerprint = crypto.subtle ? Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(identity))),
    byte => byte.toString(16).padStart(2, "0")).join("") : crypto.randomUUID();
  manifest.upload_id = folderUploadKey({ ...selection, files: entries },
    JSON.stringify([manifest.origin, manifest.target, manifest.profile, manifest.name, manifest.on_conflict]),
    fingerprint);
  return { manifest, blobs: entries.map(item => item.file), title: grouped ? selection.name : `${entries.length} файлов` };
}

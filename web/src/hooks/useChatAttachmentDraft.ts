import { useCallback, useState, type SetStateAction } from "react";
import { useStore } from "@nanostores/react";
import { atom, type WritableAtom } from "nanostores";
import { MAX_ATTACHMENTS, type PendingAttachment, type UploadedAttachment } from "@/lib/chat-attachments";
import { readChatView, writeChatView } from "@/lib/chat-view-state";

const drafts = new Map<string, WritableAtom<PendingAttachment[]>>();
function draftFor(key: string) {
  let draft = drafts.get(key);
  if (!draft) {
    let restored: PendingAttachment[] = [];
    try {
      const files = JSON.parse(readChatView(`${key}:files`) || "[]") as UploadedAttachment[];
      restored = files.filter(file => file && typeof file.path === "string" && typeof file.name === "string").slice(0, MAX_ATTACHMENTS).map(file => ({
        id: crypto.randomUUID(), name: file.name, size: file.size, kind: file.kind,
        status: "ready", progress: 100, uploaded: file, file: new File([], file.name),
      }));
    } catch { /* A damaged browser draft must not prevent opening the chat. */ }
    draft = atom(restored);
    draft.listen(items => writeChatView(`${key}:files`, JSON.stringify(items.flatMap(item => item.uploaded ? [item.uploaded] : []))));
    drafts.set(key, draft);
  }
  return draft;
}

export function clearChatAttachmentDraft(key: string): void {
  const draft = drafts.get(key);
  draft?.get().forEach(item => { if (item.previewUrl) URL.revokeObjectURL(item.previewUrl); });
  draft?.set([]);
  writeChatView(`${key}:files`, "");
}

/** Upload callbacks continue updating their own conversation after navigation. */
export function useChatAttachmentDraft(key?: string) {
  const [local] = useState(() => atom<PendingAttachment[]>([]));
  const draft = key ? draftFor(key) : local;
  const value = useStore(draft);
  const set = useCallback((next: SetStateAction<PendingAttachment[]>) => {
    draft.set(typeof next === "function" ? next(draft.get()) : next);
  }, [draft]);
  return [value, set] as const;
}


/** Restore sent files without replacing the next message's existing attachments. */
export function restoreChatAttachmentDraft(key: string, files: UploadedAttachment[]): boolean {
  const draft = draftFor(key);
  const existing = draft.get();
  const missing = files.filter(file => !existing.some(item => item.uploaded?.path === file.path));
  if (existing.length + missing.length > MAX_ATTACHMENTS) return false;
  draft.set([...existing, ...missing.map(file => ({
    id: crypto.randomUUID(), name: file.name, size: file.size, kind: file.kind,
    status: "ready" as const, progress: 100, uploaded: file, file: new File([], file.name),
  }))]);
  return true;
}

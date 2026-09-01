/**
 * Owner file attachments for the bubble chat.
 *
 * The server appends an `[вложения]` block to the user message so the agent
 * gets an absolute path plus the tool that must open the file. That block is
 * for the model, not for the human: showing a raw filesystem path in the
 * transcript is how the product starts looking like a terminal. So the UI
 * parses the block back out and renders cards, both live and after a reload
 * (history comes back from the server with the block intact).
 */

import { withBasePath } from "@/lib/api";
import type { AttachmentDisplay } from "@/lib/chat-types";

export interface UploadedAttachment {
  path: string;
  name: string;
  kind: string;
  size: number;
  reader: string;
}

export type PendingStatus = "uploading" | "ready" | "error";

export interface PendingAttachment {
  id: string;
  name: string;
  size: number;
  kind: string;
  status: PendingStatus;
  progress: number;
  error?: string;
  uploaded?: UploadedAttachment;
  previewUrl?: string;
  file: File;
}

export const MAX_ATTACHMENTS = 5;
export const MAX_ATTACHMENT_BYTES = 50 * 1024 * 1024;

const IMAGE_KINDS = new Set(["png", "jpg", "jpeg", "webp", "heic", "gif"]);

export function isImageKind(kind: string): boolean {
  return IMAGE_KINDS.has(kind.toLowerCase());
}

export function kindOf(name: string): string {
  const dot = name.lastIndexOf(".");
  return dot > -1 ? name.slice(dot + 1).toLowerCase() : "bin";
}

export function formatSize(bytes: number): string {
  if (bytes >= 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} МБ`;
  if (bytes >= 1024) return `${Math.round(bytes / 1024)} КБ`;
  return `${bytes} Б`;
}

/** Middle-truncate so the extension stays visible — "Отчёт…КЕДР.pptx". */
export function shortName(name: string, max = 28): string {
  if (name.length <= max) return name;
  const dot = name.lastIndexOf(".");
  const ext = dot > -1 ? name.slice(dot) : "";
  const stem = dot > -1 ? name.slice(0, dot) : name;
  const keep = Math.max(4, max - ext.length - 1);
  return `${stem.slice(0, keep)}…${ext}`;
}

/* ------------------------------------------------------------------ */
/*  Parsing the server-side [вложения] block back out of a message     */
/* ------------------------------------------------------------------ */

export interface ParsedAttachment {
  index: number;
  name: string;
  kind: string;
  size: string;
  reader: string;
  path: string;
}

const BLOCK_MARKER = "[вложения]";

/**
 * Split a stored user message into human text and attachment cards.
 *
 * Format produced by the server (one entry, two lines):
 *   1. Отчёт.pdf · pdf · 63 КБ · читать: pdf
 *      /opt/data/home/client/inbox/2026-08-19/xxxx-report.pdf
 */
export function toDisplay(items: UploadedAttachment[]): AttachmentDisplay[] {
  return items.map((item) => ({
    key: item.path,
    name: item.name,
    kind: item.kind,
    sizeLabel: formatSize(item.size),
  }));
}

export function splitAttachments(content: string): {
  text: string;
  attachments: AttachmentDisplay[];
} {
  const at = content.lastIndexOf(BLOCK_MARKER);
  if (at === -1) return { text: content, attachments: [] };

  const text = content.slice(0, at).trimEnd();
  const block = content.slice(at + BLOCK_MARKER.length);
  const attachments: AttachmentDisplay[] = [];

  const entry =
    /(\d+)\.\s*(.+?)\s+·\s+(\S+)\s+·\s+([\d.,]+\s*\S+)\s+·\s+читать:\s*(\S+)\s*\n\s*(\S.*)/g;
  let match: RegExpExecArray | null;
  while ((match = entry.exec(block)) !== null) {
    attachments.push({
      key: match[6].trim(),
      name: match[2].trim(),
      kind: match[3].trim(),
      sizeLabel: match[4].trim(),
    });
  }

  // A block we cannot parse must not be swallowed — better to show the raw
  // text than to silently drop what the owner sent.
  if (attachments.length === 0) return { text: content, attachments: [] };
  return { text, attachments };
}

/* ------------------------------------------------------------------ */
/*  Upload                                                             */
/* ------------------------------------------------------------------ */

/** XHR rather than fetch: we need real upload progress, not a spinner lie. */
export function uploadAttachment(
  file: File,
  onProgress: (percent: number) => void,
): { promise: Promise<UploadedAttachment>; abort: () => void } {
  const xhr = new XMLHttpRequest();
  const promise = new Promise<UploadedAttachment>((resolve, reject) => {
    const form = new FormData();
    form.append("file", file, file.name);

    xhr.open("POST", withBasePath("/api/chat/upload"));
    const token =
      typeof window !== "undefined" ? (window.__HERMES_SESSION_TOKEN__ ?? "") : "";
    if (token) xhr.setRequestHeader("Authorization", `Bearer ${token}`);

    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable) {
        onProgress(Math.round((event.loaded / event.total) * 100));
      }
    };
    xhr.onload = () => {
      let payload: Record<string, unknown> = {};
      try {
        payload = JSON.parse(xhr.responseText || "{}");
      } catch {
        payload = {};
      }
      if (xhr.status >= 200 && xhr.status < 300 && payload.path) {
        resolve(payload as unknown as UploadedAttachment);
      } else {
        reject(new Error(String(payload.detail || `Ошибка ${xhr.status}`)));
      }
    };
    xhr.onerror = () => reject(new Error("Сеть недоступна"));
    xhr.onabort = () => reject(new Error("Отменено"));
    xhr.send(form);
  });

  return { promise, abort: () => xhr.abort() };
}

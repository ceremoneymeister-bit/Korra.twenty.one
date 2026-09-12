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

import { authedFetch, fetchJSON, withBasePath } from "@/lib/api";
import type { AttachmentDisplay } from "@/lib/chat-types";
import { ownerFacingError } from "@/lib/owner-facing-error";

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

const IMAGE_KINDS = new Set(["png", "jpg", "jpeg", "webp", "heic", "heif", "avif", "gif"]);

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

/** Only recognise explicit local file references; the server owns access. */
export function localFilePath(reference: string): string | null {
  let value = reference.trim().replace(/^<|>$/g, "");
  if (value.startsWith("sandbox:")) value = value.slice(8);
  else if (value.startsWith("file://")) {
    if (!value.startsWith("file:///")) return null;
    value = value.slice(7);
  }
  try { value = decodeURIComponent(value); } catch { return null; }
  if (!value.startsWith("/") || value.startsWith("//") || [...value].some(char => char.charCodeAt(0) < 32)) return null;
  if (value.split("/").includes("..")) return null;
  return value;
}

export function describeAttachment(path: string, signal?: AbortSignal): Promise<UploadedAttachment> {
  return fetchJSON(`/api/files/attachment?${new URLSearchParams({ path })}`, { signal });
}

/** Native downloads cannot set a header in a direct, token-authenticated panel.
 * Keep its credential out of URLs; cookie/proxy deployments stream natively. */
export async function downloadWorkspaceFile(path: string, name: string, chat = false): Promise<void> {
  const query = new URLSearchParams({ path });
  if (chat) query.set("chat", "1");
  const endpoint = `/api/files/download?${query}`;
  let url = withBasePath(endpoint);
  let objectUrl = false;
  if (window.__HERMES_SESSION_TOKEN__) {
    const response = await authedFetch(endpoint);
    if (!response.ok) throw new Error("Не удалось скачать файл. Он мог быть удалён или перемещён.");
    url = URL.createObjectURL(await response.blob());
    objectUrl = true;
  }
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  document.body.appendChild(link);
  link.click();
  link.remove();
  if (objectUrl) window.setTimeout(() => URL.revokeObjectURL(url), 60_000);
}

/** Parse at render time so original MEDIA references remain durable in history. */
export function splitFileReferences(content: string): { text: string; paths: string[] } {
  const protectedText: string[] = [];
  let text = content.replace(/(^ {0,3}(`{3,}|~{3,})[^\n]*\n[\s\S]*?^ {0,3}\2[^\n]*(?:\n|$))|(`(?!MEDIA:)[^`\n]+`)/gm, (value) => {
    protectedText.push(value);
    return `\u0000${protectedText.length - 1}\u0000`;
  });
  const paths = new Set<string>();
  const take = (full: string, reference: string): string => {
    const path = localFilePath(reference);
    if (!path) return full;
    paths.add(path);
    return "";
  };
  text = text.replace(/!?\[[^\]\n]*\]\((<[^>\n]+>|(?:[^()\n]|\([^()\n]*\))+)\)/g,
    (full, reference: string) => take(full, reference));
  text = text.replace(/`?MEDIA:\s*(?:"([^"\n]+)"|'([^'\n]+)'|([^\s`]+))`?/g,
    (full, double: string, single: string, bare: string) => take(full, double || single || bare.replace(/[.,;:!?)]+$/, "")));
  text = text.replace(/^\s*((?:sandbox:|file:\/\/)?\/[^\n]*\.[\p{L}\d]{1,12})\s*$/gmu,
    (full, path: string) => take(full, path));
  // Null delimiters cannot occur in a filesystem path; these placeholders
  // restore protected code after extracting file references.
  // eslint-disable-next-line no-control-regex
  text = text.replace(/\u0000(\d+)\u0000/g, (_full, index: string) => protectedText[Number(index)] ?? "");
  return { text: text.trim(), paths: [...paths] };
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
export function chatUploadPath(profile?: string): string {
  const name = profile?.trim();
  return name
    ? `/api/chat/upload?profile=${encodeURIComponent(name)}`
    : "/api/chat/upload";
}

export function uploadAttachment(
  file: File,
  onProgress: (percent: number) => void,
  profile?: string,
): { promise: Promise<UploadedAttachment>; abort: () => void } {
  const xhr = new XMLHttpRequest();
  const promise = new Promise<UploadedAttachment>((resolve, reject) => {
    const form = new FormData();
    form.append("file", file, file.name);

    xhr.open("POST", withBasePath(chatUploadPath(profile)));
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
        reject(new Error(ownerFacingError(
          payload.detail,
          `Не удалось загрузить вложение (HTTP ${xhr.status}).`,
        )));
      }
    };
    xhr.onerror = () => reject(new Error("Сеть недоступна"));
    xhr.onabort = () => reject(new Error("Отменено"));
    xhr.send(form);
  });

  return { promise, abort: () => xhr.abort() };
}

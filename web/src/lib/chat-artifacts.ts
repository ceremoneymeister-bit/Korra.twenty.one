/**
 * Артефакты агента в ленте чата.
 *
 * Агент, создав файл, дописывает в ответ строку `[артефакт] <абсолютный путь>`.
 * Чат вынимает такие строки из текста и показывает результат: картинку —
 * картинкой, документ — карточкой с просмотром. Владелец не должен видеть путь
 * в файловой системе и уж тем более ходить за ним в другой раздел.
 *
 * Почему маркер в тексте, а не отдельное поле ответа: ровно та же причина, что
 * у вложений. Ответ приходит потоком по SSE и хранится как текст сессии —
 * маркер переживает перезагрузку страницы и возобновление разговора, а
 * дополнительное поле пришлось бы протаскивать через api-сервер и оно
 * терялось бы в истории.
 */

import { withBasePath } from "@/lib/api";
import { isImageKind, kindOf, shortName } from "@/lib/chat-attachments";
import { isProductUiMode } from "@/lib/dashboard-flags";

export interface ChatArtifact {
  /** Абсолютный путь в контуре — ключ и источник ссылки. */
  path: string;
  name: string;
  kind: string;
  isImage: boolean;
}

const MARKER = /^\s*\[артефакт\]\s+(\S.*?)\s*$/gim;

/** Разделить ответ агента на текст и артефакты. */
export function splitArtifacts(content: string): {
  text: string;
  artifacts: ChatArtifact[];
} {
  const artifacts: ChatArtifact[] = [];
  const seen = new Set<string>();

  const text = content
    .replace(MARKER, (_full, rawPath: string) => {
      const path = rawPath.trim().replace(/^`|`$/g, "");
      if (!path || seen.has(path)) return "";
      seen.add(path);
      const name = path.split("/").pop() || path;
      const kind = kindOf(name);
      artifacts.push({ path, name, kind, isImage: isImageKind(kind) });
      return "";
    })
    .replace(/\n{3,}/g, "\n\n")
    .trim();

  return { text, artifacts };
}

/** Ссылка на файл контура через прокси кабинета. */
export function artifactUrl(
  path: string,
  inline = true,
  expectedSha256?: string | null,
): string {
  const q = new URLSearchParams({ path });
  if (inline) q.set("inline", "1");
  if (expectedSha256) q.set("expected_sha256", expectedSha256);
  const token =
    typeof window !== "undefined" ? (window.__HERMES_SESSION_TOKEN__ ?? "") : "";
  // The client cabinet authenticates media upstream with a private header.
  // Putting the full dashboard token in the query string would copy it into
  // browser history and reverse-proxy access logs. Direct/admin dashboards do
  // not have that proxy header, so they retain the loopback-token fallback.
  if (token && !isProductUiMode()) q.set("token", token);
  return withBasePath(`/api/files/download?${q.toString()}`);
}

export { shortName };

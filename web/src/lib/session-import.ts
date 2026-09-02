import type { SessionImportResponse } from "@/lib/api";

export type ImportableSession = Record<string, unknown>;
type Translate = (message: string, values?: Record<string, string | number>) => string;
const identityTranslate: Translate = (message, values) =>
  Object.entries(values ?? {}).reduce(
    (text, [name, value]) => text.replaceAll(`{${name}}`, String(value)),
    message,
  );

function normalizeImportSessions(value: unknown, translate: Translate): ImportableSession[] {
  const candidate =
    value &&
    typeof value === "object" &&
    !Array.isArray(value) &&
    Array.isArray((value as { sessions?: unknown }).sessions)
      ? (value as { sessions: unknown[] }).sessions
      : Array.isArray(value)
        ? value
        : [value];

  const sessions = candidate.filter(
    (item): item is ImportableSession =>
      !!item && typeof item === "object" && !Array.isArray(item),
  );
  if (sessions.length !== candidate.length) {
    throw new Error(translate("Expected exported session JSON or JSONL"));
  }
  return sessions;
}

export function parseImportSessions(
  text: string,
  translate: Translate = identityTranslate,
): ImportableSession[] {
  const trimmed = text.trim();
  if (!trimmed) throw new Error(translate("File is empty"));

  try {
    return normalizeImportSessions(JSON.parse(trimmed), translate);
  } catch (jsonError) {
    const lines = trimmed.split(/\r?\n/).filter((line) => line.trim());
    if (lines.length <= 1) {
      if (jsonError instanceof SyntaxError) throw new Error(translate("Invalid JSON or JSONL"));
      throw jsonError;
    }
    return normalizeImportSessions(lines.map((line) => JSON.parse(line)), translate);
  }
}

export function importSummary(
  result: SessionImportResponse,
  translate: Translate = identityTranslate,
): string {
  const parts = [translate("{count} imported", { count: result.imported })];
  if (result.skipped > 0) parts.push(translate("{count} skipped", { count: result.skipped }));
  if (result.detached > 0) {
    parts.push(translate("{count} detached from missing parents", { count: result.detached }));
  }
  return parts.join("; ");
}

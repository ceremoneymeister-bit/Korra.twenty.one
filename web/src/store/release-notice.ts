import { atom } from "nanostores";
import { HERMES_BASE_PATH } from "@/lib/api";

const STORAGE_KEY = `korra.seen-releases:${HERMES_BASE_PATH}`;

function readSeen(): string[] {
  try {
    const value: unknown = JSON.parse(localStorage.getItem(STORAGE_KEY) ?? "[]");
    return Array.isArray(value) ? value.filter((id): id is string => typeof id === "string") : [];
  } catch { return []; }
}

export const $seenReleaseIds = atom<string[]>(readSeen());

export function markReleaseSeen(releaseId: string): void {
  if (!releaseId || $seenReleaseIds.get().includes(releaseId)) return;
  const seen = [...$seenReleaseIds.get(), releaseId];
  $seenReleaseIds.set(seen);
  try { localStorage.setItem(STORAGE_KEY, JSON.stringify(seen)); } catch { /* Browser storage is unavailable. */ }
}

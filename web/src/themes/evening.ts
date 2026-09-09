import { preferenceScope, type ThemePreference } from './preference';
export function isEvening(now: Date): boolean { return now.getHours() >= 19; }
export function eveningEligible(p: ThemePreference | null, now: Date): boolean {
  return !!p?.known && !!p.installation_id && !!p.evening && p.theme === 'light' && isEvening(now) &&
    p.evening?.disabled !== true && (p.evening?.snooze_until ?? 0) * 1000 <= now.getTime();
}
export function localDay(now: Date): string { return `${now.getFullYear()}-${now.getMonth()}-${now.getDate()}`; }
export function episodeKey(p: ThemePreference): string { return `${preferenceScope(p)}:evening-episode`; }
/** Web Locks makes the read/claim atomic across tabs. If durable browser state
 * or the lock is unavailable we quietly skip this optional offer. */
export async function claimEvening(p: ThemePreference, now: Date): Promise<boolean> {
  if (!p.installation_id || !navigator.locks) return false;
  try {
    return await navigator.locks.request(episodeKey(p), async () => {
      const key = episodeKey(p);
      const raw = localStorage.getItem(key);
      const previous = raw ? JSON.parse(raw) : null;
      if (previous && (typeof previous.at !== 'number' || previous.at > now.getTime() || previous.day === localDay(now))) return false;
      const claim = { version: 1, day: localDay(now), at: now.getTime() };
      localStorage.setItem(key, JSON.stringify(claim));
      return localStorage.getItem(key) === JSON.stringify(claim);
    });
  } catch { return false; }
}

/** Server metadata is authoritative. A cache is only an optimization within
 * this installation, process owner, cabinet path and acknowledged revision. */
export interface ThemePreference {
  version: 1;
  known: boolean;
  theme: 'light' | 'dark';
  installation_id: string | null;
  owner: string;
  base_path: string;
  revision: string;
  evening?: { disabled: boolean; snooze_until: number };
}
declare global { interface Window { __KORRA_THEME_PREF__?: unknown } }
export function validPreference(value: unknown): ThemePreference | null {
  if (!value || typeof value !== 'object') return null;
  const p = value as Partial<ThemePreference>;
  if (p.evening !== undefined && (!p.evening || typeof p.evening !== 'object' ||
    typeof p.evening.disabled !== 'boolean' || typeof p.evening.snooze_until !== 'number' ||
    !Number.isFinite(p.evening.snooze_until) || p.evening.snooze_until < 0)) return null;
  return p.version === 1 && p.known === true &&
    (p.theme === 'light' || p.theme === 'dark') &&
    (p.installation_id === null || (typeof p.installation_id === 'string' && /^[a-f0-9]{32}$/.test(p.installation_id))) &&
    typeof p.owner === 'string' && p.owner.length > 0 &&
    typeof p.base_path === 'string' && (p.base_path === '' || p.base_path.startsWith('/')) &&
    typeof p.revision === 'string' && p.revision.length > 0 ? p as ThemePreference : null;
}
export function preferenceScope(p: ThemePreference): string {
  return ['korra-dashboard-v1', p.installation_id, p.owner, encodeURIComponent(p.base_path)].join(':');
}
export function readBootstrap(): ThemePreference | null {
  return typeof window === 'undefined' ? null : validPreference(window.__KORRA_THEME_PREF__);
}
export function cachePreference(p: ThemePreference): void {
  if (!p.installation_id) return; // Identity unavailable: honor the server, skip the cache.
  try {
    const key = `${preferenceScope(p)}:theme`;
    // A single bounded record per owner. The revision is part of the record's
    // identity; stale, malformed and global legacy records never seed a theme.
    const cached = validPreference(JSON.parse(localStorage.getItem(key) ?? 'null'));
    if (cached?.revision !== p.revision || cached.theme !== p.theme) localStorage.setItem(key, JSON.stringify(p));
  } catch { /* Private browsing/quota must not prevent a server-backed render. */ }
}

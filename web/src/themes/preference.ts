/** Server metadata is authoritative. A cache is only an optimization within
 * this installation, process owner, cabinet path and acknowledged revision. */
export interface ThemePreference {
  version: 1;
  known: boolean;
  theme: 'light' | 'dark';
  installation_id: string;
  owner: string;
  base_path: string;
  revision: string;
}
declare global { interface Window { __KORRA_THEME_PREF__?: unknown } }
export function validPreference(value: unknown): ThemePreference | null {
  if (!value || typeof value !== 'object') return null;
  const p = value as Partial<ThemePreference>;
  return p.version === 1 && p.known === true &&
    (p.theme === 'light' || p.theme === 'dark') &&
    typeof p.installation_id === 'string' && /^[a-f0-9]{32}$/.test(p.installation_id) &&
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
  try {
    const key = `${preferenceScope(p)}:theme`;
    // A single bounded record per owner. The revision is part of the record's
    // identity; stale, malformed and global legacy records never seed a theme.
    const cached = validPreference(JSON.parse(localStorage.getItem(key) ?? 'null'));
    if (cached?.revision !== p.revision || cached.theme !== p.theme) localStorage.setItem(key, JSON.stringify(p));
  } catch { /* Private browsing/quota must not prevent a server-backed render. */ }
}
export function safeRead(key: string): string | null {
  try { return localStorage.getItem(key); } catch { return null; }
}
export function safeWrite(key: string, value: string): void {
  try { localStorage.setItem(key, value); } catch { /* optional cache */ }
}

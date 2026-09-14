/** Cabinet owns /c/<slug>; its session is independent from dashboard OAuth. */
export function cabinetLogoutPath(basePath: string): string | null {
  const match = /^(\/[a-zA-Z0-9_/-]*)?\/c\/[a-z0-9][a-z0-9_-]*$/.exec(basePath.replace(/\/+$/, ""));
  if (!match || basePath.startsWith("//") || basePath.includes("..")) return null;
  return `${match[1] ?? ""}/cab/logout`;
}

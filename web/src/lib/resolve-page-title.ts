import type { Translations } from "@/i18n/types";
import { productUiMode } from "./dashboard-flags";
import { productNavLabel } from "./product-nav";
import { russianInterfaceText } from "./russian-interface-text";

const BUILTIN: Record<string, keyof Translations["app"]["nav"]> = {
  "/chat": "chat",
  "/sessions": "sessions",
  "/analytics": "analytics",
  "/models": "models",
  "/logs": "logs",
  "/cron": "cron",
  "/skills": "skills",
  "/plugins": "plugins",
  "/profiles": "profiles",
  "/config": "config",
  "/env": "keys",
  "/docs": "documentation",
};

// Built-in routes without an i18n nav key. Keep these in sync with the
// sidebar labels in App.tsx — the naive capitalize fallback below mangles
// initialisms ("/mcp" → "Mcp") and can't match multi-word labels.
const BUILTIN_LITERAL: Record<string, string> = {
  "/files": "Файлы",
  "/mcp": "MCP",
  "/channels": "Каналы",
  "/webhooks": "Вебхуки",
  "/pairing": "Подключения",
  "/system": "Система",
};

export function resolvePageTitle(
  pathname: string,
  t: Translations,
  pluginTabs: { path: string; label: string }[],
): string {
  const normalized = pathname.replace(/\/$/, "") || "/";
  if (normalized === "/") {
    return t.app.nav.sessions;
  }
  const plugin = pluginTabs.find((p) => p.path === normalized);
  if (plugin) {
    return russianInterfaceText(plugin.label, "Плагин Korra");
  }
  // Продуктовая подпись — раньше админской: в продукте у экрана своё имя, и
  // заголовок обязан совпадать с пунктом меню, по которому на него пришли.
  const product = productNavLabel(productUiMode(), normalized);
  if (product) {
    return product;
  }
  const key = BUILTIN[normalized];
  if (key) {
    return t.app.nav[key];
  }
  const literal = BUILTIN_LITERAL[normalized];
  if (literal) {
    return literal;
  }
  if (normalized.slice(1)) return "Раздел Korra";
  return t.app.webUi;
}

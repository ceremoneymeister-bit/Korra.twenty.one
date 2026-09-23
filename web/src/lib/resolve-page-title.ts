import type { Translations } from "@/i18n/types";
import { productUiMode } from "./dashboard-flags";
import { SHIPPED_PLUGIN_LABELS, productNavLabel } from "./product-nav";
import { russianInterfaceLabel } from "./russian-interface-text";

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
  "/dashboard": "Дашборд",
  "/files": "Файлы",
  "/mcp": "MCP",
  "/channels": "Каналы",
  "/webhooks": "Вебхуки",
  "/pairing": "Подключения",
  "/system": "Система",
  "/profiles/new": "Создать агента",
  "/help": "Помощь",
};

// Плагины поставки: их подпись задана продуктом, а не манифестом, поэтому она
// нужна и здесь — на случай, если у вкладки не оказалось русского имени.
// Источник подписей один, общий с сайдбаром (см. product-nav).
const PLUGIN_LITERAL = SHIPPED_PLUGIN_LABELS;

export function resolvePageTitle(
  pathname: string,
  t: Translations,
  pluginTabs: { path: string; label: string }[],
): string {
  const normalized = pathname.replace(/\/$/, "") || "/";
  if (normalized === "/") {
    return t.app.nav.sessions;
  }
  if (normalized === "/profiles/new") return BUILTIN_LITERAL[normalized];
  if (PLUGIN_LITERAL[normalized]) return PLUGIN_LITERAL[normalized];
  const plugin = pluginTabs.find((p) => p.path === normalized);
  if (plugin) {
    // Заголовок обязан совпадать с пунктом меню, по которому сюда пришли, —
    // и подпись плагина это ровно он. Раньше здесь стоял `russianInterfaceText`,
    // и короткое английское имя («Kanban») схлопывалось в безликое «Плагин
    // Korra», хотя в сайдбаре пункт назывался нормально (QA 03.09).
    return russianInterfaceLabel(
      plugin.label,
      PLUGIN_LITERAL[normalized] ?? "Плагин Korra",
    );
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
  const literal = BUILTIN_LITERAL[normalized] ?? PLUGIN_LITERAL[normalized];
  if (literal) {
    return literal;
  }
  if (normalized.slice(1)) return "Раздел не найден";
  return t.app.webUi;
}

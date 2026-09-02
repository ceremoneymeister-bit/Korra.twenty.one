/**
 * Состав сайдбара в продуктовых сборках панели.
 *
 * Флотовый режим берёт полный админский список и раскладывает его на основной
 * рабочий маршрут, редкие настройки и служебные экраны.
 *
 * Лежит отдельным модулем, а не в App: выбор состава здесь чистый и
 * проверяется тестом, тогда как внутри useMemo он проверяется только глазами.
 */

import type { ProductUiMode } from "./dashboard-flags";

/** Пункт сайдбара глазами этого модуля: иконка и остальное ему не нужны. */
export interface NavEntry {
  label: string;
  labelKey?: string;
  path: string;
}

/** Что видит пользователь продукта. Порядок задаёт и порядок в сайдбаре. */
const PRODUCT_NAV_PATHS: Record<ProductUiMode, string[]> = {
  fleet: ["/agents", "/files", "/sessions", "/cron"],
};

/** Язык продукта, а не панели администратора. */
const PRODUCT_NAV_LABELS: Record<ProductUiMode, Record<string, string>> = {
  fleet: {
    "/agents": "Агенты",
    "/files": "Материалы",
    "/sessions": "История",
    "/cron": "Задачи",
  },
};

/**
 * Настройки — свёрнутая группа внизу сайдбара: нужны редко, а место занимают
 * постоянно. Группа общая для обоих продуктов: ключи и модель заводит тот же
 * человек, независимо от того, что он купил.
 */
export const CLIENT_SETTINGS_PATHS = ["/env", "/models", "/logs", "/help"];

export const CLIENT_SETTINGS_LABELS: Record<string, string> = {
  "/env": "Ключи и доступы",
  "/models": "Модель",
  "/logs": "Журналы",
  "/help": "Помощь",
};

export const SERVICE_PATHS = [
  "/skills",
  "/plugins",
  "/mcp",
  "/channels",
  "/webhooks",
  "/pairing",
  "/profiles",
  "/config",
  "/system",
  "/docs",
];

export const SERVICE_LABELS: Record<string, string> = {
  "/skills": "Навыки",
  "/plugins": "Плагины",
  "/mcp": "MCP",
  "/channels": "Каналы",
  "/webhooks": "Вебхуки",
  "/pairing": "Подключения",
  "/profiles": "Мультиагенты",
  "/config": "Конфигурация",
  "/system": "Система",
  "/docs": "Документация",
};

/**
 * Подпись пункта в продуктовом режиме — для тех, кому нужен не список, а
 * одно название: заголовок экрана берёт его здесь же, а не держит вторую
 * копию.
 */
export function productNavLabel(
  mode: ProductUiMode | null,
  path: string,
): string | undefined {
  if (!mode) return undefined;
  return (
    PRODUCT_NAV_LABELS[mode][path] ??
    CLIENT_SETTINGS_LABELS[path] ??
    SERVICE_LABELS[path] ??
    undefined
  );
}

/** Куда уводить `/` и неизвестный маршрут. */
const PRODUCT_HOME_PATHS: Record<ProductUiMode, string> = {
  fleet: "/agents",
};

/** Домашний экран режима; для админской панели (`null`) — прежние /sessions. */
export function productHomePath(mode: ProductUiMode | null): string {
  return mode ? PRODUCT_HOME_PATHS[mode] : "/sessions";
}

/**
 * Главные пункты сайдбара для продуктового режима.
 *
 * `labelKey` снимаем: подписи продукта заданы здесь и не должны подменяться
 * переводом админской панели.
 */
export function selectProductNav<T extends NavEntry>(
  items: T[],
  mode: ProductUiMode,
): T[] {
  const order = PRODUCT_NAV_PATHS[mode];
  const labels = PRODUCT_NAV_LABELS[mode];
  return items
    .filter((item) => order.includes(item.path))
    .sort((a, b) => order.indexOf(a.path) - order.indexOf(b.path))
    .map((item) => ({
      ...item,
      label: labels[item.path] ?? item.label,
      labelKey: undefined,
    }));
}

/** Пункты свёрнутой группы «НАСТРОЙКИ» — в порядке CLIENT_SETTINGS_PATHS. */
export function selectProductSettingsNav<T extends NavEntry>(items: T[]): T[] {
  return CLIENT_SETTINGS_PATHS.map((path) => {
    const found = items.find((item) => item.path === path);
    return found
      ? {
          ...found,
          label: CLIENT_SETTINGS_LABELS[path] ?? found.label,
          labelKey: undefined,
        }
      : null;
  }).filter(Boolean) as T[];
}

/** Пункты второй свёрнутой группы «СЛУЖЕБНОЕ». */
export function selectServiceNav<T extends NavEntry>(items: T[]): T[] {
  return SERVICE_PATHS.map((path) => {
    const found = items.find((item) => item.path === path);
    return found
      ? {
          ...found,
          label: SERVICE_LABELS[path] ?? found.label,
          labelKey: undefined,
        }
      : null;
  }).filter(Boolean) as T[];
}

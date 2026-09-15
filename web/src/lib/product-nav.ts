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
  fleet: ["/agents", "/files", "/cron"],
};

/** Язык продукта, а не панели администратора. */
const PRODUCT_NAV_LABELS: Record<ProductUiMode, Record<string, string>> = {
  fleet: {
    "/agents": "Агенты",
    "/files": "Файлы",
    "/cron": "Задачи",
  },
};

/**
 * Подписи плагинов поставки: продукт называет их сам, а не манифестом, и одно
 * и то же имя нужно сайдбару, заголовку экрана и вкладке плагина. Держим их
 * здесь, чтобы переименование не приходилось повторять в трёх местах.
 */
export const SHIPPED_PLUGIN_LABELS: Record<string, string> = {
  "/kanban": "Канбан-доска",
  "/achievements": "Достижения",
};

/**
 * Плагины, которые в продукте остаются в главном списке. «Канбан-доска» живёт
 * под «Задачами» (решение владельца 03.09); остальные плагины поставки и
 * сторонние вкладки уходят в «Служебное».
 */
export const MAIN_PLUGIN_PATHS = ["/kanban"];

/** Пункт главного списка, после которого встают плагины из MAIN_PLUGIN_PATHS. */
const MAIN_PLUGIN_ANCHOR = "/cron";

/**
 * Настройки — свёрнутая группа внизу сайдбара: нужны редко, а место занимают
 * постоянно. Группа общая для обоих продуктов: ключи и модель заводит тот же
 * человек, независимо от того, что он купил.
 */
export const CLIENT_SETTINGS_PATHS = [
  "/env",
  "/models",
  "/updates",
  "/logs",
  "/help",
];

export const CLIENT_SETTINGS_LABELS: Record<string, string> = {
  "/env": "Ключи и доступы",
  "/models": "Модель",
  "/updates": "Обновления",
  "/logs": "Журналы",
  "/help": "Помощь",
};

/**
 * «Служебное» — вторая свёрнутая группа. «История» и «Достижения» перенесены
 * сюда из главного меню (решение владельца 15.09) и стоят первыми: они ближе
 * к повседневной работе, чем остальные служебные экраны. Список смешанный —
 * `/achievements` даёт плагин поставки, остальные пункты встроенные.
 */
export const SERVICE_PATHS = [
  "/sessions",
  "/achievements",
  "/skills",
  "/plugins",
  "/mcp",
  "/channels",
  "/webhooks",
  "/pairing",
  "/profiles",
  "/config",
  "/system",
];

export const SERVICE_LABELS: Record<string, string> = {
  "/sessions": "История",
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
    SHIPPED_PLUGIN_LABELS[path] ??
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

/**
 * Пункты второй свёрнутой группы «СЛУЖЕБНОЕ».
 *
 * `pluginItems` — вкладки плагинов контура. Часть из них названа в
 * SERVICE_PATHS и встаёт на своё место в порядке группы, остальные добавляются
 * в конец. Пункт, которого в контуре нет (плагин не установлен или скрыт),
 * просто не появляется: мёртвых ссылок группа не создаёт.
 */
export function selectServiceNav<T extends NavEntry>(
  items: T[],
  pluginItems: T[] = [],
): T[] {
  const pool = [...items, ...pluginItems];
  const known = SERVICE_PATHS.map((path) => {
    const found = pool.find((item) => item.path === path);
    return found
      ? {
          ...found,
          label: SERVICE_LABELS[path] ?? SHIPPED_PLUGIN_LABELS[path] ?? found.label,
          labelKey: undefined,
        }
      : null;
  }).filter(Boolean) as T[];
  const rest = pluginItems.filter(
    (item) =>
      !SERVICE_PATHS.includes(item.path) &&
      !MAIN_PLUGIN_PATHS.includes(item.path),
  );
  return [...known, ...rest];
}

/** Готовые группы продуктового сайдбара в порядке отрисовки. */
export interface ProductSidebarGroups<T extends NavEntry> {
  main: T[];
  settings: T[];
  service: T[];
}

/**
 * Итоговый состав сайдбара продукта: главный список, «Настройки», «Служебное».
 *
 * Собираем все три группы здесь, а не в App: пункт плагина иначе приходится
 * вставлять в один список и вычитать из другого руками, и правка одного
 * массива оставляет либо дубль, либо пункт сразу в двух группах.
 */
export function selectProductSidebar<T extends NavEntry>(
  items: T[],
  pluginItems: T[],
  mode: ProductUiMode,
): ProductSidebarGroups<T> {
  const main = selectProductNav(items, mode);
  const mainPlugins = MAIN_PLUGIN_PATHS.flatMap((path) =>
    pluginItems.filter((item) => item.path === path),
  );
  const anchor = main.findIndex((item) => item.path === MAIN_PLUGIN_ANCHOR);
  main.splice(anchor < 0 ? main.length : anchor + 1, 0, ...mainPlugins);
  return {
    main,
    settings: selectProductSettingsNav(items),
    service: selectServiceNav(items, pluginItems),
  };
}

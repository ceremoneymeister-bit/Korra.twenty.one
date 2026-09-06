/**
 * Состав вкладок рабочего места агентов.
 *
 * Источник правды — реальные профили контура (`GET /api/profiles`), а не
 * конфиг и не bootstrap HTML: владелец создал профиль в панели — появилась
 * вкладка этого агента с его чатами. Здесь только чистая функция
 * «список профилей → вкладки», чтобы порядок, предел и защита от мусора
 * проверялись тестом, а хук и страница ею лишь пользовались.
 */

import type { ProfileInfo } from "@/lib/api";

export interface AgentTabConfig {
  /** Имя профиля Korra. Оно же уезжает в `?profile=` на каждом запросе
   *  чата и истории. "" — собственный профиль процесса панели, то есть
   *  главный агент. */
  profile: string;
  /** Подпись на вкладке — имя агента, а не техническое имя профиля,
   *  когда владелец его задал. */
  label: string;
  /** Описание роли профиля. На вкладку не выносится (это одно-два
   *  предложения, а не имя) — уходит во всплывающую подсказку. */
  description?: string;
}

/** Главный агент — всегда первая вкладка и всегда «Корра». */
export const MAIN_AGENT_TAB: AgentTabConfig = { profile: "", label: "Корра" };

/** Предел вкладок, включая главную: дальше полоса перестаёт читаться. */
export const MAX_AGENT_TABS = 10;

/** Та же грамматика имени, что у движка (`_PROFILE_ID_RE` в profiles.py):
 *  имя уходит в URL и в путь на диске, чужое сюда попасть не должно. */
const PROFILE_ID_RE = /^[a-z0-9][a-z0-9_-]{0,63}$/;

type ProfileLike = Partial<
  Pick<ProfileInfo, "name" | "is_default" | "display_name" | "description">
>;

function cleanText(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
}

/**
 * Построить вкладки из ответа `/api/profiles`.
 *
 * Гарантии: никогда не бросает; первая вкладка — главный агент «Корра»;
 * профиль `default` — это и есть главная вкладка, второй раз не добавляется;
 * порядок именованных профилей — как отдал сервер (по имени); дубли и
 * имена вне грамматики отбрасываются; не больше `MAX_AGENT_TABS`.
 * Остановленный профиль, профиль без бота или без модели — всё равно
 * вкладка: чат к нему адресуется по имени, а не по состоянию шлюза.
 */
export function buildAgentTabs(profiles: unknown): AgentTabConfig[] {
  const tabs: AgentTabConfig[] = [{ ...MAIN_AGENT_TAB }];
  if (!Array.isArray(profiles)) return tabs;

  const seen = new Set<string>();
  for (const item of profiles as unknown[]) {
    if (tabs.length >= MAX_AGENT_TABS) break;
    if (!item || typeof item !== "object") continue;
    const profile = item as ProfileLike;
    const name = cleanText(profile.name);
    if (!name || name === "default" || profile.is_default === true) continue;
    if (!PROFILE_ID_RE.test(name) || seen.has(name)) continue;
    seen.add(name);

    const displayName = cleanText(profile.display_name);
    const description = cleanText(profile.description);
    tabs.push({
      profile: name,
      label: displayName || name,
      ...(description ? { description } : {}),
    });
  }
  return tabs;
}

/** Одинаковый ли состав — хук не трогает состояние, если сервер вернул то же. */
export function sameAgentTabs(
  a: readonly AgentTabConfig[],
  b: readonly AgentTabConfig[],
): boolean {
  if (a.length !== b.length) return false;
  return a.every((tab, index) => {
    const other = b[index];
    return (
      tab.profile === other.profile &&
      tab.label === other.label &&
      (tab.description ?? "") === (other.description ?? "")
    );
  });
}

/** Что можно открыть для конкретного агента из меню его вкладки. */
export type AgentSettingsKind = "role" | "model" | "skills" | "schedule";

/**
 * Адрес настроек выбранного агента.
 *
 * Роль и модель — редактор в «Настройках агентов»
 * (`/profiles?agent=<профиль>&edit=role|model`, договорённость с Астрой 05.09);
 * навыки и расписание — их разделы с явным `?profile=` (оба в
 * `PROFILE_SCOPED_ROUTES` контекста профилей, поэтому адрес выбирает именно
 * этого агента, а не запомненного в разделе).
 *
 * Пустой профиль — главная вкладка, то есть профиль самой панели; в списке
 * профилей он значится как `default`.
 */
export function agentSettingsHref(
  profile: string,
  edit: AgentSettingsKind,
): string {
  const target = encodeURIComponent(profile || "default");
  if (edit === "skills") return `/skills?profile=${target}`;
  if (edit === "schedule") return `/cron?profile=${target}`;
  return `/profiles?agent=${target}&edit=${edit}`;
}

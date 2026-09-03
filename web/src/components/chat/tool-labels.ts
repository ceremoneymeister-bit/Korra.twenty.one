/**
 * Имена инструментов движка → человеческий русский и тип значка.
 *
 * Словарь собран по фактическому списку вызовов в базе сессий, а не по
 * догадкам: `SELECT tool_name, count(*) FROM messages GROUP BY tool_name`.
 * Подписи — существительные («Чтение файла», а не «Прочитал файл»): строка
 * живёт и пока вызов идёт, и после него, а прошедшее время во время работы
 * врёт.
 *
 * Неизвестный инструмент показываем его собственным именем — выдумывать
 * перевод для того, чего мы не знаем, хуже, чем показать сырое имя.
 */

/** Тип вызова — определяет только значок слева. */
export type ToolKind =
  | "read"
  | "write"
  | "run"
  | "search"
  | "web"
  | "browser"
  | "think"
  | "delegate"
  | "memory"
  | "message"
  | "plan"
  | "skill"
  | "schedule"
  | "image"
  | "ask"
  | "other";

export interface ToolMeta {
  label: string;
  kind: ToolKind;
}

const TOOLS: Record<string, ToolMeta> = {
  // Терминал и код
  terminal: { label: "Команда в терминале", kind: "run" },
  execute_code: { label: "Запуск кода", kind: "run" },
  process: { label: "Фоновый процесс", kind: "run" },

  // Файлы
  read_file: { label: "Чтение файла", kind: "read" },
  write_file: { label: "Запись файла", kind: "write" },
  patch: { label: "Правка файла", kind: "write" },
  search_files: { label: "Поиск по файлам", kind: "search" },

  // Поиск и сеть
  session_search: { label: "Поиск по истории", kind: "search" },
  web_search: { label: "Поиск в интернете", kind: "web" },
  web_extract: { label: "Чтение веб-страницы", kind: "web" },

  // Браузер
  browser_navigate: { label: "Переход по адресу", kind: "browser" },
  browser_click: { label: "Клик в браузере", kind: "browser" },
  browser_type: { label: "Ввод в браузере", kind: "browser" },
  browser_scroll: { label: "Прокрутка страницы", kind: "browser" },
  browser_back: { label: "Шаг назад в браузере", kind: "browser" },
  browser_snapshot: { label: "Снимок страницы", kind: "browser" },
  browser_console: { label: "Консоль браузера", kind: "browser" },
  browser_exec: { label: "Скрипт в браузере", kind: "browser" },
  browser_vision: { label: "Взгляд на страницу", kind: "browser" },
  browser_get_images: { label: "Картинки со страницы", kind: "browser" },

  // Скиллы и планирование
  skill_view: { label: "Чтение скилла", kind: "skill" },
  skills_list: { label: "Список скиллов", kind: "skill" },
  skill_manage: { label: "Правка скилла", kind: "skill" },
  todo: { label: "План работы", kind: "plan" },
  kanban_show: { label: "Доска задач", kind: "plan" },
  kanban_complete: { label: "Закрытие задачи", kind: "plan" },

  // Память и расписание
  memory: { label: "Память", kind: "memory" },
  cronjob: { label: "Расписание", kind: "schedule" },

  // Делегирование и вопросы
  delegate_task: { label: "Задача субагенту", kind: "delegate" },
  clarify: { label: "Уточняющий вопрос", kind: "ask" },

  // Медиа
  vision_analyze: { label: "Разбор изображения", kind: "image" },
  image_generate: { label: "Генерация изображения", kind: "image" },
  text_to_speech: { label: "Синтез речи", kind: "image" },

  // Сообщения
  send_message: { label: "Отправка сообщения", kind: "message" },
  tg_userbot_send: { label: "Отправка в Telegram", kind: "message" },
  tg_userbot_read_chat: { label: "Чтение чата Telegram", kind: "message" },
  tg_userbot_search: { label: "Поиск в Telegram", kind: "search" },
  tg_userbot_get_chat_info: { label: "Сведения о чате Telegram", kind: "message" },
  tg_userbot_get_me: { label: "Аккаунт Telegram", kind: "message" },
  tg_userbot_join: { label: "Вход в чат Telegram", kind: "message" },

  // Внутренние
  _thinking: { label: "Размышление", kind: "think" },
};

/** Подпись и тип значка для имени инструмента. */
export function toolMeta(name: string): ToolMeta {
  const known = TOOLS[name];
  if (known) return known;
  // Семейства растут быстрее словаря: новый `browser_*` или `tg_userbot_*`
  // должен получить правильный значок, даже если подписи для него ещё нет.
  if (name.startsWith("browser_")) return { label: name, kind: "browser" };
  if (name.startsWith("tg_userbot_")) return { label: name, kind: "message" };
  if (name.startsWith("web_")) return { label: name, kind: "web" };
  if (name.startsWith("skill")) return { label: name, kind: "skill" };
  return { label: name, kind: "other" };
}

/** Главный аргумент вызова — тот же выбор, что делает `build_tool_preview`
 *  в agent/display.py. Живой поток присылает готовое превью в `label`, а у
 *  истории есть только сырой JSON аргументов, и превью приходится собирать
 *  здесь. Список ключей держим согласованным с движком. */
const PRIMARY_ARG: Record<string, string> = {
  terminal: "command",
  execute_code: "code",
  browser_exec: "code",
  read_file: "path",
  write_file: "path",
  patch: "path",
  search_files: "pattern",
  web_search: "query",
  web_extract: "urls",
  browser_navigate: "url",
  browser_click: "ref",
  browser_type: "text",
  image_generate: "prompt",
  text_to_speech: "text",
  vision_analyze: "question",
  skill_view: "name",
  skill_manage: "name",
  skills_list: "category",
  cronjob: "action",
  delegate_task: "goal",
  clarify: "question",
  session_search: "query",
  memory: "target",
  process: "action",
  todo: "action",
};

const PREVIEW_MAX = 160;

function flatten(value: unknown): string {
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  if (Array.isArray(value)) return value.map(flatten).filter(Boolean).join(", ");
  return "";
}

/** Собрать однострочное превью аргументов вызова из истории сессии.
 *  `raw` — строка `function.arguments` (JSON). Возвращает пустую строку,
 *  если показать нечего: пустой чип лучше выдуманного. */
export function previewArguments(name: string, raw: string | undefined): string {
  if (!raw) return "";
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    // Аргументы бывают недописаны (оборванный ход) — показываем как есть.
    return oneLine(raw);
  }
  if (parsed === null || typeof parsed !== "object") return oneLine(flatten(parsed));
  const args = parsed as Record<string, unknown>;
  const primary = PRIMARY_ARG[name];
  if (primary && args[primary] !== undefined) {
    const value = oneLine(flatten(args[primary]));
    if (value) return value;
  }
  for (const value of Object.values(args)) {
    const flat = oneLine(flatten(value));
    if (flat) return flat;
  }
  return "";
}

/** Результат инструмента из истории. Движок кладёт туда JSON вида
 *  `{"output": …}` / `{"error": …}` — вынимаем читаемое поле, а не
 *  показываем владельцу фигурные скобки. */
export function previewToolResult(raw: string | null | undefined): string {
  if (!raw) return "";
  const text = raw.trim();
  if (!text.startsWith("{")) return text.slice(0, 1_200);
  try {
    const parsed = JSON.parse(text) as Record<string, unknown>;
    for (const key of ["output", "error", "message", "result", "content"]) {
      const value = parsed[key];
      if (typeof value === "string" && value.trim()) return value.slice(0, 1_200);
    }
  } catch {
    // Не JSON — покажем сырой текст.
  }
  return text.slice(0, 1_200);
}

function oneLine(value: string): string {
  const collapsed = value.replace(/\s+/g, " ").trim();
  return collapsed.length > PREVIEW_MAX
    ? `${collapsed.slice(0, PREVIEW_MAX - 1)}…`
    : collapsed;
}

/** Русское склонение счётчика вызовов: 1 вызов, 2 вызова, 5 вызовов. */
export function pluralCalls(count: number): string {
  const tens = count % 100;
  if (tens >= 11 && tens <= 14) return `${count} вызовов`;
  switch (count % 10) {
    case 1:
      return `${count} вызов`;
    case 2:
    case 3:
    case 4:
      return `${count} вызова`;
    default:
      return `${count} вызовов`;
  }
}

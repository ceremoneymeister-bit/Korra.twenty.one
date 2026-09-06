import { russianInterfaceText } from "./russian-interface-text";

/** Названия разделов и совместимых значений; технические ключи на диске сохраняются. */
const CATEGORY_NAMES: Record<string, string> = {
  bedrock: "Amazon Bedrock", curator: "Обслуживание навыков", database: "База данных",
  desktop: "Приложение для компьютера", gateway: "Подключение каналов", kanban: "Доска задач",
  loops: "Повторные запуски", lsp: "Работа с кодом", matrix: "Matrix", mattermost: "Mattermost",
  moa: "Совместная работа моделей", model_catalog: "Каталог моделей", monitoring: "Контроль работы",
  openrouter: "OpenRouter", proxy: "Прокси-сервер", secrets: "Хранение ключей", sessions: "История разговоров",
  session: "Текущий разговор", slack: "Slack", streaming: "Постепенный вывод ответа",
  tool_loop_guardrails: "Защита от зацикливания", tool_output: "Результаты инструментов",
  tools: "Инструменты", vertex: "Google Vertex AI", wake_word: "Голосовая активация",
  web: "Поиск в интернете", x_search: "Поиск в X", updates: "Обновления", approvals: "Разрешения",
  checkpoints: "Точки восстановления", plugins: "Плагины", skills: "Навыки", cron: "Расписание",
  smart_model_routing: "Автоматический выбор модели", profiles: "Агенты", dashboard: "Панель управления",
  telegram: "Telegram", whatsapp: "WhatsApp", onboarding: "Первое знакомство", timezone: "Часовой пояс",
};

export function configCategoryName(category: string, translated?: string): string {
  return translated || CATEGORY_NAMES[category] || "Дополнительные настройки";
}

const FIELD_NAMES: Record<string, string> = {
  model: "Модель для ответов", model_context_length: "Объём контекста модели",
  fallback_providers: "Резервные сервисы ответов", toolsets: "Наборы инструментов",
  max_concurrent_sessions: "Одновременные разговоры", max_live_sessions: "Открытые разговоры",
  session: "Текущий разговор", "session.terminal_continue": "Продолжать работу терминала",
  context_file_max_chars: "Максимальный объём инструкций из файлов",
  file_read_max_chars: "Максимальный объём чтения файла",
  mcp_discovery_timeout: "Время ожидания списка внешних инструментов",
  mcp_single_query_discovery_timeout: "Время ожидания одного внешнего инструмента",
  prefill_messages_file: "Файл начального контекста", timezone: "Часовой пояс",
  command_allowlist: "Разрешённые команды", hooks_auto_accept: "Автоматически принимать обработчики событий",
  doctor: "Диагностика", "doctor.live_probe_timeout": "Время ожидания проверки сервиса",
  updates: "Обновления", "updates.pre_update_backup": "Сохранять копию перед обновлением",
  "updates.backup_keep": "Количество резервных копий",
  "updates.non_interactive_local_changes": "Действие с локальными изменениями при обновлении",
  "updates.auto_switch_parked_branch": "Переключать ветку при обновлении",
  "updates.parked_branch_strategy": "Способ переключения ветки",
  "updates.refresh_cua_driver": "Обновлять управление компьютером",
  paste_collapse_threshold: "Сворачивать длинные вставки",
  paste_collapse_threshold_fallback: "Резервный порог сворачивания вставки",
  paste_collapse_char_threshold: "Длина вставки для сворачивания",
};

export function configFieldLabel(key: string, title?: unknown): string {
  return FIELD_NAMES[key] || russianInterfaceText(title, key);
}

/** Ищем по тем же русским подписям, которые читает человек, и по техническим ключам. */
export function configFieldMatches(key: string, schema: Record<string, unknown>, query: string): boolean {
  const category = String(schema.category ?? "general");
  const text = [key, key.replace(/_/g, " "), configFieldLabel(key, schema.title),
    category, configCategoryName(category), schema.description].join(" ").toLocaleLowerCase("ru-RU");
  return text.includes(query.trim().toLocaleLowerCase("ru-RU"));
}

const BASE_TOOLS = "Основные инструменты Korra";
export function presentToolsets(value: unknown): string {
  return (Array.isArray(value) ? value : String(value ?? "").split(","))
    .map((item) => item === "hermes-cli" ? BASE_TOOLS : item).join(", ");
}
export function parseToolsets(value: string): string[] {
  return value.split(",").map((item) => item.trim()).filter(Boolean)
    .map((item) => item === BASE_TOOLS ? "hermes-cli" : item);
}

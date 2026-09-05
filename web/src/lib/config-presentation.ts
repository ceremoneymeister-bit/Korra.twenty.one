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

const BASE_TOOLS = "Основные инструменты Korra";
export function presentToolsets(value: unknown): string {
  return (Array.isArray(value) ? value : String(value ?? "").split(","))
    .map((item) => item === "hermes-cli" ? BASE_TOOLS : item).join(", ");
}
export function parseToolsets(value: string): string[] {
  return value.split(",").map((item) => item.trim()).filter(Boolean)
    .map((item) => item === BASE_TOOLS ? "hermes-cli" : item);
}

import { learningArticle } from './learning';
import { workArticles } from './work';
import { settingsArticles } from './settings';
import { serviceArticles } from './service';

export const HELP_ARTICLES = [...workArticles, learningArticle, ...settingsArticles, ...serviceArticles];
export const HELP_GROUPS = ['Начало и работа', 'Настройки и связь', 'Служебные разделы'] as const;
export const QUICK_HELP = [
  { title: 'Создать агента', detail: 'Имя, роль и первый ответ', to: '/help/agents#create' },
  { title: 'Дать агенту поручение', detail: 'Доска и проверка результата', to: '/help/kanban#create' },
  { title: 'Подключить Telegram', detail: 'QR-код или свой бот', to: '/help/telegram' },
  { title: 'Загрузить файлы', detail: 'Исходники и готовые материалы', to: '/help/files#upload' },
  { title: 'Настроить регулярную работу', detail: 'Запрос, время и доставка', to: '/help/tasks#create' },
  { title: 'Агент молчит', detail: 'Проверить причину и продолжить', to: '/help/troubleshooting#silent' },
];

import { atom } from 'nanostores'
import { DASHBOARD_WIDGET_IDS } from '@/components/dashboard/widget-catalog'

export type Scenario = 'first' | 'day' | 'attention'
export const SCENARIOS: { id: Scenario; label: string }[] = [
  { id: 'first', label: 'Первый вход' },
  { id: 'day', label: 'Рабочий день' },
  { id: 'attention', label: 'Нужно решение' }
]
export const WIDGETS = [
  { id: 'attention', title: 'Внимание', detail: 'Решения, которые ждут тебя', kind: 'attention' },
  { id: 'recent-results', title: 'Артефакты', detail: 'Всё, что создают твои агенты', kind: 'artifacts' },
  { id: 'metrics', title: 'Показатель', detail: 'Главное число и его динамика', kind: 'metrics' },
  { id: 'agents', title: 'Агенты', detail: 'Кто работает и над чем', kind: 'agents' },
  { id: 'upcoming-tasks', title: 'Лента дня', detail: 'Ближайшие запуски и встречи', kind: 'timeline' }
] as const
export type WidgetId = (typeof WIDGETS)[number]['id']
export interface PreviewLayout {
  order: WidgetId[]
  hidden: WidgetId[]
}
export const DEFAULT_LAYOUT: PreviewLayout = { order: WIDGETS.map(widget => widget.id), hidden: [] }
const STORAGE_KEY = 'korra.dashboard.visual-v1.layout'

export function normalizeLayout(value: unknown): PreviewLayout {
  const source = value && typeof value === 'object' ? (value as Partial<PreviewLayout>) : {}
  const valid = (list: unknown): WidgetId[] =>
    Array.isArray(list)
      ? [
          ...new Set(
            list.filter((id): id is WidgetId => typeof id === 'string' && WIDGETS.some(widget => widget.id === id))
          )
        ]
      : []
  const order = valid(source.order)
  return { order: [...order, ...DEFAULT_LAYOUT.order.filter(id => !order.includes(id))], hidden: valid(source.hidden) }
}
export function moveWidget(layout: PreviewLayout, id: WidgetId, direction: -1 | 1): PreviewLayout {
  const order = [...layout.order],
    index = order.indexOf(id),
    next = index + direction
  if (index < 0 || next < 0 || next >= order.length) return layout
  ;[order[index], order[next]] = [order[next], order[index]]
  return { ...layout, order }
}
function readLayout(): PreviewLayout {
  try {
    return normalizeLayout(JSON.parse(localStorage.getItem(STORAGE_KEY) || 'null'))
  } catch {
    return normalizeLayout(null)
  }
}
export const $layout = atom<PreviewLayout>(readLayout())
export const $storageNotice = atom('')
export function saveLayout(layout: PreviewLayout) {
  const next = normalizeLayout(layout)
  $layout.set(next)
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(next))
    $storageNotice.set('Сохранено в этом браузере')
  } catch {
    $storageNotice.set('Хранилище недоступно — настройки действуют до закрытия страницы')
  }
}
const initialScenario = new URLSearchParams(location.search).get('scene')
export const $scenario = atom<Scenario>(
  SCENARIOS.some(s => s.id === initialScenario) ? (initialScenario as Scenario) : 'day'
)
export const $approved = atom(false)
export function selectScenario(scenario: Scenario) {
  $scenario.set(scenario)
  $approved.set(false)
}
// Keep the catalog's stable IDs for the future source integration, including
// recent-results: the user-facing name changes without losing saved identity.
export const catalogCompatible = WIDGETS.every(widget => DASHBOARD_WIDGET_IDS.includes(widget.id))

export interface Artifact {
  id: string
  title: string
  kind: 'deck' | 'plan' | 'sheet'
  format: string
  author: string
  time: string
  summary: string[]
}
export const ARTIFACTS: Artifact[] = [
  {
    id: 'growth',
    title: 'Стратегия роста',
    kind: 'deck',
    format: 'Презентация',
    author: 'Дизайнер',
    time: '09:38',
    summary: [
      'Один продукт. Одна понятная ценность.',
      'Собрать три коротких сценария для первого знакомства.',
      'Проверить путь от первого вопроса до готового артефакта.'
    ]
  },
  {
    id: 'launch',
    title: 'План запуска',
    kind: 'plan',
    format: 'Документ',
    author: 'Корра',
    time: '09:24',
    summary: [
      'Сегодня — проверить макет с командой.',
      'Среда — собрать тексты и материалы.',
      'Пятница — пройти первый пользовательский сценарий.'
    ]
  },
  {
    id: 'sales',
    title: 'Продажи за неделю',
    kind: 'sheet',
    format: 'Таблица',
    author: 'Аналитик',
    time: '09:10',
    summary: [
      'Демонстрационный оборот: 284 500 ₽.',
      'Предыдущая неделя: 241 100 ₽. Изменение: +18% (округлено).',
      'Числа вымышлены и служат только для оценки макета.'
    ]
  }
]
export const AGENTS = [
  { id: 'designer', name: 'Дизайнер', role: 'Презентация продукта', state: 'Работает', mark: 'design' },
  { id: 'analytics', name: 'Аналитик', role: 'Обзор рынка', state: 'Работает', mark: 'analysis' },
  { id: 'default', name: 'Корра', role: 'Готова к поручению', state: 'Свободна', mark: 'korra' }
] as const

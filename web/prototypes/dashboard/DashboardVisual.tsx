import { useEffect, useState } from 'react'
import type { ReactNode } from 'react'
import { useStore } from '@nanostores/react'
import { useNavigate } from 'react-router'
import {
  ArrowDown,
  ArrowRight,
  ArrowUp,
  ArrowUpRight,
  Check,
  CheckCheck,
  ChevronRight,
  Clock3,
  Download,
  FileText,
  Layers3,
  Plus,
  RotateCcw,
  SlidersHorizontal,
  Sparkles,
  Sun
} from 'lucide-react'
import { DashboardWidgetBoundary } from '@/components/dashboard/DashboardWidgetBoundary'
import {
  $approved,
  $layout,
  $scenario,
  $storageNotice,
  AGENTS,
  ARTIFACTS,
  DEFAULT_LAYOUT,
  RESIZABLE_WIDGET_IDS,
  SCENARIOS,
  WIDGETS,
  moveWidget,
  resizeWidget,
  saveLayout,
  selectScenario
} from './model'
import type { Artifact, WidgetId, WidgetSize } from './model'
import { AgentMark, ArtifactCover, FirstVisitVisual, MetricChart } from './visuals'
import { Modal } from './Modal'
import { SizePicker } from './SizePicker'

type Overlay =
  | { kind: 'catalog' }
  | { kind: 'artifact'; artifact: Artifact }
  | { kind: 'gallery' }
  | { kind: 'approval' }
  | { kind: 'team' }
  | { kind: 'events' }
  | { kind: 'agent'; id: string }
  | { kind: 'event'; index: number }
const EVENTS = [
  {
    time: '10:00',
    title: 'Обзор рынка',
    agent: 'Аналитик',
    detail: 'Собрать главные изменения рынка и короткую сводку с источниками.',
    mark: 'analysis'
  },
  {
    time: '12:30',
    title: 'Контент на неделю',
    agent: 'Дизайнер',
    detail: 'Подготовить три обложки и структуру публикаций на неделю.',
    mark: 'design'
  },
  {
    time: '17:00',
    title: 'Итоги дня',
    agent: 'Корра',
    detail: 'Собрать готовые артефакты и перенести незавершённые поручения.',
    mark: 'korra'
  }
]

function Panel({
  id,
  size,
  title,
  meta,
  action,
  children
}: {
  id: WidgetId
  size: WidgetSize
  title: string
  meta?: string
  action?: ReactNode
  children: ReactNode
}) {
  return (
    <section className={`dv-panel dv-panel--${id}`} data-widget={id} data-size={size} aria-labelledby={`dv-${id}`}>
      <header className="dv-panel-heading">
        <div>
          <h3 id={`dv-${id}`}>{title}</h3>
          {meta && <span className="dv-meta">{meta}</span>}
        </div>
        {action}
      </header>
      <div className="dv-panel-body">
        <DashboardWidgetBoundary title={title} widgetId={id}>
          {children}
        </DashboardWidgetBoundary>
      </div>
    </section>
  )
}

function ArtifactButton({
  artifact,
  onOpen,
  detailed = false,
  compact = false
}: {
  artifact: Artifact
  onOpen: () => void
  detailed?: boolean
  compact?: boolean
}) {
  return (
    <button className="dv-artifact" onClick={onOpen} aria-label={`Открыть артефакт «${artifact.title}»`}>
      <div className="dv-artifact-stage">
        <ArtifactCover kind={artifact.kind} compact={compact} />
        <span className="dv-artifact-open">
          <ArrowUpRight size={18} />
        </span>
      </div>
      <strong>{artifact.title}</strong>
      <span className="dv-artifact-meta">
        {artifact.format}
        <span>·</span>
        {artifact.time}
      </span>
      {detailed && (
        <span className="dv-artifact-author">
          {artifact.author}
          <span>
            Готово к просмотру <Check size={12} />
          </span>
        </span>
      )}
    </button>
  )
}

function CatalogPreview({ kind }: { kind: string }) {
  return (
    <span className={`dv-catalog-preview dv-catalog-preview--${kind}`} aria-hidden="true">
      {kind === 'artifacts' ? (
        <>
          <i />
          <i />
          <i />
        </>
      ) : kind === 'metrics' ? (
        <>
          <b>
            284<span>₽</span>
          </b>
          <span className="dv-catalog-bars">
            <i />
            <i />
            <i />
            <i />
            <i />
          </span>
        </>
      ) : kind === 'agents' ? (
        <>
          <AgentMark kind="design" />
          <AgentMark kind="analysis" />
          <AgentMark kind="korra" />
        </>
      ) : kind === 'attention' ? (
        <>
          <span className="dv-notice-symbol">!</span>
          <i />
          <i />
        </>
      ) : (
        <>
          <i />
          <i />
          <i />
        </>
      )}
    </span>
  )
}

export default function DashboardVisual() {
  const scenario = useStore($scenario),
    approved = useStore($approved),
    layout = useStore($layout),
    storageNotice = useStore($storageNotice)
  const [overlay, setOverlay] = useState<Overlay | null>(null)
  const [monthly, setMonthly] = useState(false)
  const [notice, setNotice] = useState('')
  const navigate = useNavigate()
  useEffect(() => {
    if (!notice) return
    const timer = window.setTimeout(() => setNotice(''), 4500)
    return () => clearTimeout(timer)
  }, [notice])
  const close = () => setOverlay(null)
  const needsDecision = scenario === 'attention' && !approved
  const visible = layout.order.filter(id => !layout.hidden.includes(id) && (id !== 'attention' || needsDecision))
  const openArtifact = (artifact: Artifact) => setOverlay({ kind: 'artifact', artifact })
  const agent = overlay?.kind === 'agent' ? AGENTS.find(item => item.id === overlay.id) : undefined
  const downloadArtifact = (artifact: Artifact) => {
    const blob = new Blob(
      [
        `# ${artifact.title}\n\nДемонстрационный артефакт Korra. Вымышленные данные.\n\n${artifact.summary.map(line => `- ${line}`).join('\n')}\n`
      ],
      { type: 'text/markdown;charset=utf-8' }
    )
    const url = URL.createObjectURL(blob),
      link = document.createElement('a')
    link.href = url
    link.download = `${artifact.title} — демо.md`
    link.click()
    window.setTimeout(() => URL.revokeObjectURL(url), 1000)
  }
  const renderWidget = (id: WidgetId) => {
    const size = layout.sizes[id]
    switch (id) {
      case 'attention':
        return (
          <section key={id} data-widget={id} className="dv-decision" aria-label="Требуется решение">
            <span className="dv-decision-icon">
              <FileText size={22} />
            </span>
            <div>
              <span className="dv-eyebrow">Одно решение — и дальше</span>
              <h3>Письмо клиенту готово</h3>
              <p>Корра ждёт подтверждения отправки</p>
            </div>
            <button className="dv-button dv-button--accent" onClick={() => setOverlay({ kind: 'approval' })}>
              Посмотреть <ArrowUpRight size={18} />
            </button>
          </section>
        )
      case 'recent-results':
        return (
          <Panel
            key={id}
            id={id}
            size={size}
            title="Артефакты"
            meta="3 новых сегодня"
            action={
              <button className="dv-text-button" onClick={() => setOverlay({ kind: 'gallery' })}>
                Все <ArrowUpRight size={17} />
              </button>
            }
          >
            <div className="dv-artifacts">
              {(size === 's' ? ARTIFACTS.slice(0, 1) : ARTIFACTS).map(artifact => (
                <ArtifactButton
                  key={artifact.id}
                  artifact={artifact}
                  detailed={size === 'l'}
                  compact={size === 'm'}
                  onOpen={() => openArtifact(artifact)}
                />
              ))}
            </div>
            <footer className="dv-artifact-footer">
              <span className="dv-mini-check">
                <Check size={12} />
              </span>
              {size === 's' ? 'Последний артефакт' : 'Идеи становятся осязаемыми'}
              <span className="dv-stack-count">
                <Layers3 size={14} /> 3
              </span>
            </footer>
          </Panel>
        )
      case 'metrics':
        return (
          <Panel
            key={id}
            id={id}
            size={size}
            title="Оборот"
            action={
              <button
                className="dv-period"
                onClick={() => setMonthly(value => !value)}
                aria-label={`Период: ${monthly ? 'месяц' : 'неделя'}. Переключить`}
              >
                {monthly ? 'Месяц' : 'Неделя'}
                <RotateCcw size={12} />
              </button>
            }
          >
            <div className="dv-metric-value" key={monthly ? 'month' : 'week'}>
              {size === 's' ? (monthly ? '1,14' : '284,5') : monthly ? '1 142 800' : '284 500'}
              <span>{size === 's' ? (monthly ? 'млн ₽ · месяц' : 'тыс. ₽ · неделя') : '₽'}</span>
            </div>
            <div className="dv-metric-comparison">
              <span>
                <ArrowUpRight size={14} />
                {monthly ? '+12,4%' : '+18%'}
              </span>
              <small>{monthly ? 'к прошлому периоду' : 'к прошлой неделе'}</small>
            </div>
            {size !== 's' && <MetricChart monthly={monthly} />}
            {size !== 's' && (
              <div className="dv-chart-labels">
                <span>{monthly ? '1 сен' : '14 сен'}</span>
                <span>{monthly ? '21 сен' : '20 сен'}</span>
              </div>
            )}
            {size === 'l' && (
              <div className="dv-metric-detail">
                <span>
                  {monthly ? '1–21 августа' : 'Прошлая неделя'}
                  <strong>{monthly ? '1 016 726' : '241 100'} ₽</strong>
                </span>
                <span>
                  {monthly ? '1–21 сентября' : 'Эта неделя'}
                  <strong>{monthly ? '1 142 800' : '284 500'} ₽</strong>
                </span>
              </div>
            )}
            <footer className="dv-metric-source">
              <span />
              Демо-показатель
            </footer>
          </Panel>
        )
      case 'agents':
        return (
          <Panel
            key={id}
            id={id}
            size={size}
            title={size === 's' ? 'Команда' : 'Твоя команда'}
            meta="2 агента в работе"
            action={<span className="dv-team-count">3</span>}
          >
            {size === 's' && (
              <div className="dv-team-summary">
                <strong>
                  2<span>/ 3</span>
                </strong>
                <span>агента в работе</span>
              </div>
            )}
            {size === 's' ? (
              <button
                className="dv-team-short"
                onClick={() => setOverlay({ kind: 'team' })}
                aria-label="Открыть команду"
              >
                <span className="dv-team-avatars">
                  {AGENTS.map(item => (
                    <AgentMark key={item.id} kind={item.mark} />
                  ))}
                </span>
                <span>
                  Вся команда <ArrowUpRight size={14} />
                </span>
              </button>
            ) : (
              <div className="dv-agent-list">
                {AGENTS.map(item => (
                  <button
                    key={item.id}
                    className="dv-agent-row"
                    onClick={() => setOverlay({ kind: 'agent', id: item.id })}
                    aria-label={`Открыть агента ${item.name}`}
                  >
                    <AgentMark kind={item.mark} />
                    <span className="dv-agent-copy">
                      <strong>{item.name}</strong>
                      <span>{item.role}</span>
                      {size === 'l' && (
                        <small>
                          {item.id === 'designer'
                            ? 'Собирает слайды'
                            : item.id === 'analytics'
                              ? 'Проверяет источники'
                              : 'Можно дать новое поручение'}
                        </small>
                      )}
                    </span>
                    <span className={`dv-agent-status ${item.state === 'Работает' ? 'dv-agent-status--working' : ''}`}>
                      {item.state === 'Работает' ? (
                        <span className="dv-activity">
                          <i />
                          <i />
                          <i />
                        </span>
                      ) : (
                        <span className="dv-idle-dot" />
                      )}
                      <span>{item.state}</span>
                    </span>
                    <ChevronRight className="dv-row-arrow" size={17} />
                  </button>
                ))}
              </div>
            )}
          </Panel>
        )
      case 'upcoming-tasks':
        return (
          <Panel
            key={id}
            id={id}
            size={size}
            title={size === 's' ? 'День' : 'Лента дня'}
            action={
              <button
                className="dv-text-button"
                aria-label="Все события дня"
                onClick={() => setOverlay({ kind: 'events' })}
              >
                Все <ArrowUpRight size={15} />
              </button>
            }
          >
            {size === 'l' && (
              <div className="dv-week-strip" aria-label="Неделя с 21 сентября">
                <span className="dv-week-today">
                  Пн<strong>21</strong>
                </span>
                {['Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс'].map((day, index) => (
                  <span key={day}>
                    {day}
                    <strong>{22 + index}</strong>
                  </span>
                ))}
              </div>
            )}
            <div className="dv-timeline">
              {EVENTS.map((event, index) => (
                <button
                  key={event.time}
                  className={`dv-event ${index === 0 ? 'dv-event--next' : ''}`}
                  onClick={() => setOverlay({ kind: 'event', index })}
                  aria-label={`${event.time} — ${event.title}`}
                >
                  <span className="dv-event-time">{event.time}</span>
                  <span className="dv-event-track">
                    <i />
                  </span>
                  <span className="dv-event-copy">
                    <strong>{event.title}</strong>
                    <span>
                      {event.agent}
                      {index === 0 && <span className="dv-next-tag">Через 19 мин</span>}
                    </span>
                  </span>
                  <ArrowUpRight size={15} />
                </button>
              ))}
            </div>
          </Panel>
        )
    }
  }

  return (
    <div className="dv-root">
      <header className="dv-greeting">
        <div>
          <div className="dv-date">
            <Sun size={17} strokeWidth={1.5} />
            <span>Понедельник, 21 сентября</span>
            <span className="dv-date-divider" />
            <span>09:41</span>
          </div>
          <h2 tabIndex={-1}>
            {scenario === 'first' ? (
              'Всё начинается с тебя.'
            ) : (
              <>
                Хороший день, <span>Дмитрий.</span>
              </>
            )}
          </h2>
          <p>
            {scenario === 'first' ? (
              'Твоё пространство для больших идей.'
            ) : needsDecision ? (
              <>
                <span className="dv-state-dot dv-state-dot--decision" />
                Одно письмо ждёт твоего решения
              </>
            ) : (
              <>
                <span className="dv-state-dot" />
                {approved ? 'Решение принято. Можно двигаться дальше.' : 'Команда в работе. Всё идёт своим чередом.'}
              </>
            )}
          </p>
        </div>
        <button className="dv-button dv-button--quiet" onClick={() => setOverlay({ kind: 'catalog' })}>
          <SlidersHorizontal size={17} />
          Настроить
        </button>
      </header>

      {scenario === 'first' ? (
        <div className="dv-first-scene">
          <section className="dv-first-card">
            <div className="dv-first-copy">
              <span className="dv-eyebrow">Место, где идеи обретают форму</span>
              <h3>
                Твоя команда.
                <br />
                Твои возможности.
              </h3>
              <p>
                Поручи первый шаг агенту.
                <br />
                Готовые артефакты появятся здесь.
              </p>
              <button
                className="dv-button dv-button--accent"
                onClick={() => setOverlay({ kind: 'agent', id: 'default' })}
              >
                Начать с Коррой <ArrowUpRight size={18} />
              </button>
            </div>
            <FirstVisitVisual />
          </section>
          <div className="dv-first-bottom">
            <span>
              <Sparkles size={18} />
              От первого поручения — к готовому артефакту
            </span>
            <button className="dv-text-button" onClick={() => selectScenario('day')}>
              Посмотреть пример <ArrowRight size={17} />
            </button>
          </div>
        </div>
      ) : (
        <div className="dv-board" key={scenario}>
          {visible.includes('attention') && renderWidget('attention')}
          <div className="dv-grid">
            {visible.filter(id => id !== 'attention').map(renderWidget)}
            {visible.filter(id => id !== 'attention').length === 0 && (
              <section className="dv-empty">
                <Layers3 size={36} strokeWidth={1.2} />
                <h3>Место для твоих виджетов</h3>
                <button className="dv-button dv-button--accent" onClick={() => setOverlay({ kind: 'catalog' })}>
                  <Plus size={18} />
                  Выбрать виджеты
                </button>
              </section>
            )}
          </div>
        </div>
      )}

      <div className="dv-demo-dock" role="group" aria-label="Сценарии макета">
        <span className="dv-demo-label">
          <span />
          Макет<span className="dv-demo-label-detail"> · вымышленные данные</span>
        </span>
        <div className="dv-demo-tabs">
          {SCENARIOS.map(item => (
            <button key={item.id} aria-pressed={scenario === item.id} onClick={() => selectScenario(item.id)}>
              {item.label}
            </button>
          ))}
        </div>
      </div>
      <div className={`dv-toast ${notice ? 'dv-toast--visible' : ''}`} role="status" aria-live="polite">
        {notice && (
          <>
            <CheckCheck size={18} />
            {notice}
          </>
        )}
      </div>

      {overlay?.kind === 'catalog' && (
        <Modal title="Твой дашборд" onClose={close} drawer>
          <p className="dv-modal-description">Оставь то, что важно тебе.</p>
          <div className="dv-catalog-list">
            {layout.order.map(id => {
              const widget = WIDGETS.find(item => item.id === id)!
              const shown = !layout.hidden.includes(id)
              return (
                <div className="dv-catalog-item" key={id}>
                  <CatalogPreview kind={widget.kind} />
                  <div className="dv-catalog-info">
                    <h3>{widget.title}</h3>
                    <p>{widget.detail}</p>
                  </div>
                  <button
                    role="switch"
                    aria-checked={shown}
                    className="dv-switch"
                    aria-label={`Показывать: ${widget.title}`}
                    onClick={() =>
                      saveLayout({
                        ...layout,
                        hidden: shown ? [...layout.hidden, id] : layout.hidden.filter(item => item !== id)
                      })
                    }
                  >
                    <span>{shown && <Check size={12} />}</span>
                  </button>
                  <div className="dv-catalog-order">
                    <button
                      className="dv-icon-button"
                      aria-label={`Выше: ${widget.title}`}
                      disabled={
                        id === 'attention' || layout.order.filter(item => item !== 'attention').indexOf(id) === 0
                      }
                      onClick={() => saveLayout(moveWidget(layout, id, -1))}
                    >
                      <ArrowUp size={15} />
                    </button>
                    <button
                      className="dv-icon-button"
                      aria-label={`Ниже: ${widget.title}`}
                      disabled={id === 'attention' || layout.order.filter(item => item !== 'attention').at(-1) === id}
                      onClick={() => saveLayout(moveWidget(layout, id, 1))}
                    >
                      <ArrowDown size={15} />
                    </button>
                  </div>
                  {RESIZABLE_WIDGET_IDS.includes(id) && (
                    <SizePicker
                      id={id}
                      title={widget.title}
                      value={layout.sizes[id]}
                      onChange={size => saveLayout(resizeWidget(layout, id, size))}
                    />
                  )}
                </div>
              )
            })}
          </div>
          <div className="dv-catalog-note">
            <span role="status" aria-live="polite">
              {storageNotice || 'Настройки макета хранятся только в этом браузере'}
            </span>
            <small>S · 1×1, M · 2×1, L · 2×2. «Внимание» — отдельная полоса над плитками.</small>
          </div>
          <div className="dv-modal-actions">
            <button className="dv-text-button" onClick={() => saveLayout(DEFAULT_LAYOUT)}>
              <RotateCcw size={16} />
              Сбросить
            </button>
            <button className="dv-button dv-button--accent" onClick={close}>
              Готово <Check size={17} />
            </button>
          </div>
        </Modal>
      )}

      {overlay?.kind === 'artifact' && (
        <Modal title={overlay.artifact.title} onClose={close}>
          <div className="dv-artifact-detail">
            <ArtifactCover kind={overlay.artifact.kind} />
            <div>
              <span className="dv-eyebrow">
                {overlay.artifact.author} · сегодня, {overlay.artifact.time}
              </span>
              <h3>{overlay.artifact.format}</h3>
              <ul>
                {overlay.artifact.summary.map(line => (
                  <li key={line}>{line}</li>
                ))}
              </ul>
            </div>
          </div>
          <div className="dv-modal-actions">
            <span className="dv-small-note">Демонстрационный артефакт</span>
            <button className="dv-button dv-button--accent" onClick={() => downloadArtifact(overlay.artifact)}>
              <Download size={17} />
              Скачать текст .md
            </button>
          </div>
        </Modal>
      )}
      {overlay?.kind === 'gallery' && (
        <Modal title="Артефакты" onClose={close}>
          <p className="dv-modal-description">Сегодня · 3 готовых работы</p>
          <div className="dv-artifacts dv-artifacts--gallery">
            {ARTIFACTS.map(artifact => (
              <ArtifactButton key={artifact.id} artifact={artifact} onOpen={() => openArtifact(artifact)} />
            ))}
          </div>
        </Modal>
      )}
      {overlay?.kind === 'approval' && (
        <Modal title="Можно отправлять?" onClose={close}>
          <div className="dv-mail-meta">
            <AgentMark kind="korra" />
            <div>
              <strong>Корра подготовила письмо</strong>
              <span>Кому: команде проекта · пример</span>
            </div>
          </div>
          <div className="dv-letter">
            <span className="dv-eyebrow">Тема · План запуска готов</span>
            <h3>Следующий шаг — за нами.</h3>
            <p>Добрый день! Подготовили план запуска и собрали материалы для первого обсуждения.</p>
            <p>Предлагаем встретиться в среду, пройти по ключевым шагам и согласовать сроки.</p>
            <span className="dv-letter-file">
              <FileText size={17} />
              План запуска · пример вложения
            </span>
          </div>
          <p className="dv-small-note">Это макет: письмо никуда не отправится.</p>
          <div className="dv-modal-actions">
            <button className="dv-text-button" onClick={close}>
              Пока оставить
            </button>
            <button
              className="dv-button dv-button--accent"
              onClick={() => {
                $approved.set(true)
                close()
                setNotice('Решение принято в макете. Ничего не отправлено.')
              }}
            >
              Подтвердить в макете <Check size={17} />
            </button>
          </div>
        </Modal>
      )}
      {overlay?.kind === 'team' && (
        <Modal title="Твоя команда" onClose={close}>
          <div className="dv-agent-list">
            {AGENTS.map(item => (
              <button
                key={item.id}
                className="dv-agent-row"
                onClick={() => setOverlay({ kind: 'agent', id: item.id })}
                aria-label={`Открыть агента ${item.name}`}
              >
                <AgentMark kind={item.mark} />
                <span className="dv-agent-copy">
                  <strong>{item.name}</strong>
                  <span>{item.role}</span>
                </span>
                <span className="dv-agent-status">{item.state}</span>
                <ChevronRight size={16} />
              </button>
            ))}
          </div>
        </Modal>
      )}
      {overlay?.kind === 'agent' && agent && (
        <Modal title={agent.name} onClose={close}>
          <div className="dv-agent-detail">
            <AgentMark kind={agent.mark} />
            <div>
              <span className="dv-eyebrow">{scenario === 'first' ? 'Готова к первому поручению' : agent.state}</span>
              <h3>{scenario === 'first' ? 'С чего начнём?' : agent.role}</h3>
            </div>
          </div>
          <p className="dv-modal-description">
            {scenario === 'first'
              ? 'Попробуй: «Собери план запуска моего проекта».'
              : 'В рабочем кабинете здесь будет текущий шаг и переход прямо к поручению.'}
          </p>
          {scenario !== 'first' && (
            <div className="dv-agent-steps">
              {agent.state === 'Работает' && (
                <span>
                  <Check size={16} />
                  Контекст собран
                </span>
              )}
              <span>
                <Clock3 size={16} />
                {agent.state === 'Работает' ? 'Подготовка артефакта' : 'Ожидание поручения'}
              </span>
            </div>
          )}
          <div className="dv-modal-actions">
            <span className="dv-small-note">Диалог тоже демонстрационный</span>
            <button
              className="dv-button dv-button--accent"
              onClick={() => {
                close()
                navigate(`/agents?agent=${agent.id}`)
              }}
            >
              Открыть диалог <ArrowUpRight size={17} />
            </button>
          </div>
        </Modal>
      )}
      {overlay?.kind === 'events' && (
        <Modal title="Все события дня" onClose={close}>
          <div className="dv-timeline">
            {EVENTS.map((event, index) => (
              <button className="dv-event" key={event.time} onClick={() => setOverlay({ kind: 'event', index })}>
                <span className="dv-event-time">{event.time}</span>
                <span className="dv-event-copy">
                  <strong>{event.title}</strong>
                  <span>{event.agent}</span>
                </span>
                <ArrowUpRight size={15} />
              </button>
            ))}
          </div>
        </Modal>
      )}
      {overlay?.kind === 'event' && (
        <Modal title={EVENTS[overlay.index].title} onClose={close}>
          <div className="dv-event-detail">
            <span>{EVENTS[overlay.index].time}</span>
            <AgentMark kind={EVENTS[overlay.index].mark} />
          </div>
          <p className="dv-modal-description">Сегодня · Москва, UTC+3 · {EVENTS[overlay.index].agent}</p>
          <p className="dv-event-description">{EVENTS[overlay.index].detail}</p>
          <div className="dv-modal-actions">
            <span className="dv-small-note">Пример расписания. Автозапуска нет.</span>
            <button className="dv-button dv-button--quiet" onClick={close}>
              Понятно <Check size={17} />
            </button>
          </div>
        </Modal>
      )}
    </div>
  )
}

import { Check, Sparkles } from 'lucide-react'
import type { Artifact } from './model'

export function AgentMark({ kind }: { kind: string }) {
  return (
    <span className={`dv-agent-mark dv-agent-mark--${kind}`} aria-hidden="true">
      {kind === 'design' ? (
        <span className="dv-flower">✳</span>
      ) : kind === 'analysis' ? (
        <span className="dv-bars">
          <i />
          <i />
          <i />
          <i />
        </span>
      ) : (
        <Sparkles size={24} strokeWidth={1.5} />
      )}
    </span>
  )
}

export function ArtifactCover({ kind, compact = false }: { kind: Artifact['kind']; compact?: boolean }) {
  if (kind === 'deck')
    return (
      <div className="dv-cover dv-cover--deck" aria-hidden="true">
        <span className="dv-cover-brand">Korra studio</span>
        <strong>
          Точка
          <br />
          роста.
        </strong>
        <span className="dv-growth-orbit">
          <i />
          <i />
          <i />
        </span>
        <span className="dv-cover-foot">
          Стратегия · 2026 <span>↗</span>
        </span>
      </div>
    )
  if (kind === 'plan')
    return (
      <div className="dv-cover dv-cover--plan" aria-hidden="true">
        <span className="dv-paper-tab" />
        <span className="dv-cover-brand">Проект / запуск</span>
        <strong>
          {compact ? (
            <>
              План
              <br />
              запуска.
            </>
          ) : (
            <>
              Всё начинается
              <br />с плана.
            </>
          )}
        </strong>
        <span className="dv-paper-rule" />
        <span className="dv-paper-row">
          <Check />
          Идея и цель
        </span>
        <span className="dv-paper-row">
          <Check />
          Первые шаги
        </span>
        <span className="dv-paper-row">
          <span className="dv-empty-check" />
          Запуск
        </span>
        <span className="dv-cover-foot">
          Сентябрь 2026 <span>01</span>
        </span>
      </div>
    )
  return (
    <div className="dv-cover dv-cover--sheet" aria-hidden="true">
      <span className="dv-cover-brand">Неделя в цифрах</span>
      <strong>
        Хорошая
        <br />
        динамика.
      </strong>
      <span className="dv-mini-chart">
        {[32, 47, 42, 65, 56, 76, 95].map((height, i) => (
          <i key={i} style={{ height: `${height}%` }} />
        ))}
      </span>
      <span className="dv-cover-foot">
        14 — 20 сентября <span>↗</span>
      </span>
    </div>
  )
}

export function MetricChart({ monthly = false }: { monthly?: boolean }) {
  const values = monthly ? [25, 32, 28, 41, 47, 40, 55, 62, 54, 72, 78, 90] : [30, 48, 40, 62, 53, 76, 96]
  return (
    <div
      className="dv-metric-chart"
      aria-label={
        monthly
          ? 'Оборот за месяц: демонстрационный растущий тренд'
          : 'Оборот за неделю: демонстрационный растущий тренд'
      }
      role="img"
    >
      <span className="dv-chart-grid" />
      {values.map((value, i) => (
        <span
          key={i}
          className={i === values.length - 1 ? 'dv-bar dv-bar--last' : 'dv-bar'}
          style={{ height: `${value}%` }}
        />
      ))}
    </div>
  )
}

export function FirstVisitVisual() {
  return (
    <div className="dv-first-visual" aria-hidden="true">
      <span className="dv-first-orbit dv-first-orbit--a" />
      <span className="dv-first-orbit dv-first-orbit--b" />
      <div className="dv-first-paper">
        <ArtifactCover kind="plan" />
      </div>
      <div className="dv-first-token dv-first-token--design">
        <AgentMark kind="design" />
      </div>
      <div className="dv-first-token dv-first-token--analysis">
        <AgentMark kind="analysis" />
      </div>
      <div className="dv-first-token dv-first-token--done">
        <Check size={26} />
      </div>
      <span className="dv-first-dot" />
    </div>
  )
}

// @vitest-environment jsdom
import { beforeEach, describe, expect, it } from 'vitest'
import {
  $layout,
  $storageNotice,
  DEFAULT_LAYOUT,
  WIDGETS,
  catalogCompatible,
  moveWidget,
  normalizeLayout,
  resizeWidget,
  saveLayout
} from '../../prototypes/dashboard/model'

describe('dashboard visual prototype layout', () => {
  beforeEach(() => {
    localStorage.clear()
    $layout.set(DEFAULT_LAYOUT)
  })
  it('uses the existing catalog IDs while naming results artifacts', () => {
    expect(catalogCompatible).toBe(true)
    expect(WIDGETS.find(widget => widget.id === 'recent-results')?.title).toBe('Артефакты')
  })
  it('repairs stale, duplicated and untrusted stored layouts', () => {
    const result = normalizeLayout({
      order: ['metrics', 'unknown', 'metrics'],
      hidden: ['agents', 'unknown', 'agents']
    })
    expect(result.order[0]).toBe('metrics')
    expect(new Set(result.order)).toEqual(new Set(DEFAULT_LAYOUT.order))
    expect(result.order).toHaveLength(new Set(result.order).size)
    expect(result.hidden).toEqual(['agents'])
    expect(normalizeLayout({ order: {}, hidden: 'agents' })).toEqual(DEFAULT_LAYOUT)
    expect(normalizeLayout(null)).toEqual(DEFAULT_LAYOUT)
  })
  it('moves a widget without losing IDs, mutating the source or hidden state', () => {
    const result = moveWidget(DEFAULT_LAYOUT, 'agents', -1)
    expect(result.order.indexOf('agents')).toBe(DEFAULT_LAYOUT.order.indexOf('agents') - 1)
    expect(new Set(result.order)).toEqual(new Set(DEFAULT_LAYOUT.order))
    expect(result.hidden).toBe(DEFAULT_LAYOUT.hidden)
    expect(DEFAULT_LAYOUT.order).toEqual(WIDGETS.map(widget => widget.id))
    expect(moveWidget(DEFAULT_LAYOUT, DEFAULT_LAYOUT.order[0], -1)).toBe(DEFAULT_LAYOUT)
    expect(moveWidget(DEFAULT_LAYOUT, DEFAULT_LAYOUT.order.at(-1)!, 1)).toBe(DEFAULT_LAYOUT)
  })
  it('keeps the demo layout in its own namespaced browser storage', () => {
    saveLayout({ ...DEFAULT_LAYOUT, hidden: ['metrics'] })
    expect($layout.get().hidden).toEqual(['metrics'])
    expect(JSON.parse(localStorage.getItem('korra.dashboard.visual-v1.layout')!).hidden).toEqual(['metrics'])
    expect($storageNotice.get()).toContain('этом браузере')
    expect(localStorage.length).toBe(1)
  })
  it('migrates v1 settings without resetting order or hidden widgets', () => {
    const order = [...DEFAULT_LAYOUT.order].reverse()
    const result = normalizeLayout({ order, hidden: ['agents'] })
    expect(result.order).toEqual(order)
    expect(result.hidden).toEqual(['agents'])
    expect(result.sizes).toEqual(DEFAULT_LAYOUT.sizes)
  })
  it('validates each stored size and keeps the attention strip fixed', () => {
    const result = normalizeLayout({
      sizes: { agents: 's', metrics: 'l', attention: 's', 'recent-results': 'huge', injected: 'l' }
    })
    expect(result.sizes.agents).toBe('s')
    expect(result.sizes.metrics).toBe('l')
    expect(result.sizes.attention).toBe('m')
    expect(result.sizes['recent-results']).toBe('m')
    expect(Object.keys(result.sizes)).toEqual(Object.keys(DEFAULT_LAYOUT.sizes))
  })
  it('changes only the selected widget size, without mutating accepted defaults', () => {
    const result = resizeWidget(DEFAULT_LAYOUT, 'metrics', 'l')
    expect(result.sizes.metrics).toBe('l')
    expect(DEFAULT_LAYOUT.sizes.metrics).toBe('m')
    expect(result.order).toBe(DEFAULT_LAYOUT.order)
    expect(result.hidden).toBe(DEFAULT_LAYOUT.hidden)
    expect(resizeWidget(DEFAULT_LAYOUT, 'attention', 's')).toBe(DEFAULT_LAYOUT)
  })
  it('persists and restores different sizes independently', () => {
    const result = resizeWidget(resizeWidget(DEFAULT_LAYOUT, 'metrics', 's'), 'agents', 'l')
    saveLayout(result)
    expect(normalizeLayout(JSON.parse(localStorage.getItem('korra.dashboard.visual-v1.layout')!))).toEqual(result)
    saveLayout(DEFAULT_LAYOUT)
    expect($layout.get().sizes).toEqual(DEFAULT_LAYOUT.sizes)
  })
})

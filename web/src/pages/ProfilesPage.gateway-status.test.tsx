// @vitest-environment jsdom

/**
 * K21-088 — карточка агента различает «ведёт общий шлюз» и «не работает».
 *
 * Панель знала только `gateway_running`: у агента под общим шлюзом своего
 * процесса нет, и карточка выглядела так же, как у выключенного. Владелец
 * после создания агентов видел «остановлен» у тех, кто уже отвечал в
 * Telegram, и приёмка миграции упиралась в объяснение ложного статуса.
 */

import { act, useState, type ReactNode } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { MemoryRouter } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { PageHeaderContext } from '@/contexts/page-header-context'

const apiMocks = vi.hoisted(() => ({
  getProfiles: vi.fn(),
  getActiveProfile: vi.fn(),
  getModelOptions: vi.fn(),
  getProfileSoul: vi.fn(),
  getProfileMemory: vi.fn(),
  getProfileMaterials: vi.fn()
}))

vi.mock('@/contexts/useProfileScope', () => ({
  useProfileScope: () => ({ setProfile: vi.fn(), refreshProfiles: vi.fn() })
}))

vi.mock('@/lib/api', async () => {
  const actual = await vi.importActual<typeof import('@/lib/api')>('@/lib/api')
  return { ...actual, api: { ...actual.api, ...apiMocks } }
})

vi.mock('thinking-orbs', () => ({
  ThinkingOrb: ({ state }: { state: string }) => <span data-orb={state} data-testid="orb" />
}))

import ProfilesPage from './ProfilesPage'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

let container: HTMLDivElement
let root: Root

const BASE = {
  path: '/opt/data/profiles/x',
  is_default: false,
  model: 'claude-opus-4-5',
  provider: 'custom:dario',
  has_env: true,
  skill_count: 3,
  description: '',
  description_auto: false,
  distribution_name: null,
  distribution_version: null,
  distribution_source: null,
  has_alias: false
}

function PageHeaderHost({ children }: { children: ReactNode }) {
  const [end, setEnd] = useState<ReactNode>(null)
  const [title, setTitle] = useState<string | null>(null)
  return (
    <PageHeaderContext.Provider value={{ setAfterTitle: () => {}, setEnd, setTitle }}>
      <h1>{title}</h1>
      <div data-testid="page-header-end">{end}</div>
      {children}
    </PageHeaderContext.Provider>
  )
}

async function openPage() {
  container = document.createElement('div')
  document.body.append(container)
  root = createRoot(container)
  await act(async () =>
    root.render(
      <MemoryRouter initialEntries={['/profiles']}>
        <PageHeaderHost>
          <ProfilesPage />
        </PageHeaderHost>
      </MemoryRouter>
    )
  )
  await act(async () => {
    await Promise.resolve()
    await Promise.resolve()
  })
}

/** Карточка агента по видимому имени: самый глубокий подходящий узел. */
function card(name: string) {
  const matches = Array.from(container.querySelectorAll<HTMLElement>('div')).filter(
    node =>
      Array.from(node.querySelectorAll('span')).some(s => s.textContent === name) &&
      node.textContent?.includes('Открыть чат')
  )
  const found = matches[matches.length - 1]
  expect(found, `карточка «${name}» не найдена`).toBeTruthy()
  return found
}

beforeEach(() => {
  vi.stubGlobal(
    'matchMedia',
    vi.fn((query: string) => ({
      addEventListener: vi.fn(),
      dispatchEvent: vi.fn(),
      matches: false,
      media: query,
      onchange: null,
      removeEventListener: vi.fn()
    }))
  )
  Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', {
    configurable: true,
    value: vi.fn()
  })
  apiMocks.getActiveProfile.mockResolvedValue({ active: 'default', current: 'default' })
  apiMocks.getModelOptions.mockResolvedValue({ providers: [], provider: null, model: null })
  apiMocks.getProfileSoul.mockResolvedValue({ content: '', exists: false })
  apiMocks.getProfileMemory.mockResolvedValue({
    memory: [],
    user: [],
    limits: { memory: 2200, user: 1375 },
    used: { memory: 0, user: 0 },
    enabled: { memory: true, user: true }
  })
  apiMocks.getProfileMaterials.mockResolvedValue({ materials: [] })
  apiMocks.getProfiles.mockResolvedValue({
    profiles: [
      {
        ...BASE,
        name: 'secretary',
        display_name: 'Секретарь',
        gateway_running: false,
        gateway_status: 'served'
      },
      {
        ...BASE,
        name: 'archive',
        display_name: 'Архивариус',
        gateway_running: false,
        gateway_status: 'stopped'
      },
      {
        ...BASE,
        name: 'solo',
        display_name: 'Одиночка',
        gateway_running: true,
        gateway_status: 'running'
      }
    ]
  })
})

afterEach(async () => {
  await act(async () => root?.unmount())
  container?.remove()
  vi.unstubAllGlobals()
  vi.clearAllMocks()
})

describe('ProfilesPage — состояние шлюза агента', () => {
  it('агента под общим шлюзом не показывает выключенным', async () => {
    await openPage()
    const served = card('Секретарь')
    expect(served.textContent).toContain('На связи — общий шлюз')
    expect(served.textContent).not.toContain('Не на связи')
    expect(served.textContent).toContain('Шлюз: общий шлюз основного профиля')
  })

  it('остановленный агент остаётся остановленным', async () => {
    await openPage()
    const stopped = card('Архивариус')
    expect(stopped.textContent).toContain('Не на связи')
    expect(stopped.textContent).toContain('Шлюз: не работает')
  })

  it('агент со своим шлюзом отличается от обслуживаемого', async () => {
    await openPage()
    const own = card('Одиночка')
    expect(own.textContent).toContain('На связи')
    expect(own.textContent).not.toContain('общий шлюз')
    expect(own.textContent).toContain('Шлюз: собственный')
  })

  it('без нового поля читает старый gateway_running', async () => {
    apiMocks.getProfiles.mockResolvedValue({
      profiles: [{ ...BASE, name: 'legacy', display_name: 'Старый', gateway_running: true }]
    })
    await openPage()
    expect(card('Старый').textContent).toContain('На связи')
  })
})

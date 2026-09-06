// @vitest-environment jsdom

import { act, useState, type ReactNode } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { MemoryRouter, useLocation } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { PageHeaderContext } from '@/contexts/page-header-context'

const apiMocks = vi.hoisted(() => ({
  getProfiles: vi.fn(),
  getActiveProfile: vi.fn(),
  getModelOptions: vi.fn(),
  createProfile: vi.fn(),
  renameProfile: vi.fn(),
  updateProfileDisplayName: vi.fn(),
  deleteProfile: vi.fn(),
  getProfileSoul: vi.fn(),
  getProfileMemory: vi.fn(),
  getProfileMaterials: vi.fn(),
  setActiveProfile: vi.fn(),
  updateProfileSoul: vi.fn()
}))

const scopeMocks = vi.hoisted(() => ({ setProfile: vi.fn(), refreshProfiles: vi.fn() }))
vi.mock('@/contexts/useProfileScope', () => ({ useProfileScope: () => scopeMocks }))

vi.mock('@/lib/api', async () => {
  const actual = await vi.importActual<typeof import('@/lib/api')>('@/lib/api')
  return { ...actual, api: { ...actual.api, ...apiMocks } }
})

// Орбита рисует себя через requestAnimationFrame и canvas — в jsdom это шум.
vi.mock('thinking-orbs', () => ({
  ThinkingOrb: ({ state }: { state: string }) => <span data-orb={state} data-testid="orb" />
}))

import ProfilesPage from './ProfilesPage'

let container: HTMLDivElement
let root: Root

const PROFILES = [
  {
    name: 'default',
    path: '/root/.korra',
    is_default: true,
    model: 'claude-opus-4-5',
    provider: 'custom:dario',
    has_env: true,
    skill_count: 3,
    gateway_running: true,
    description: 'Главный агент',
    description_auto: false,
    distribution_name: null,
    distribution_version: null,
    distribution_source: null,
    has_alias: false
  }
]

const PROVIDERS = [
  {
    name: 'Anthropic',
    slug: 'anthropic',
    models: ['claude-opus-4-5'],
    authenticated: false
  },
  {
    name: 'Nous',
    slug: 'nous',
    models: ['hermes-4-70b'],
    authenticated: true
  },
  {
    name: 'Dario',
    slug: 'custom:dario',
    models: ['claude-opus-4-5', 'claude-sonnet-4-5'],
    authenticated: true
  }
]

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

/** Кнопка «Создать» живёт в шапке страницы — отдаём её через тот же контекст,
 *  что и приложение, и рисуем рядом. */
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

function LocationProbe() {
  const { pathname, search } = useLocation()
  return <output data-testid="location">{`${pathname}${search}`}</output>
}

async function render(ui: ReactNode) {
  container = document.createElement('div')
  document.body.append(container)
  root = createRoot(container)
  await act(async () => root.render(ui))
}

async function flush() {
  await act(async () => {
    await Promise.resolve()
    await Promise.resolve()
  })
}

function findButton(text: string, scope: ParentNode = container) {
  return Array.from(scope.querySelectorAll('button')).find(button => button.textContent?.includes(text))
}

async function click(element: Element | undefined) {
  expect(element).toBeTruthy()
  await act(async () => {
    ;(element as HTMLElement).click()
  })
}

async function enterText(input: HTMLTextAreaElement, value: string) {
  const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')?.set
  await act(async () => {
    setter?.call(input, value)
    input.dispatchEvent(new Event('input', { bubbles: true }))
  })
}

async function openPage(path = '/profiles') {
  await render(
    <MemoryRouter initialEntries={[path]}>
      <PageHeaderHost>
        <ProfilesPage />
      </PageHeaderHost>
      <LocationProbe />
    </MemoryRouter>
  )
  await flush()
}

const dialog = () => document.querySelector<HTMLElement>('[role="dialog"]')!
const editor = () => document.querySelector<HTMLTextAreaElement>('#learning-role')!
const save = () => findButton('Сохранить', dialog())!

beforeEach(() => {
  scopeMocks.refreshProfiles.mockResolvedValue(undefined)
  apiMocks.updateProfileDisplayName.mockImplementation(async (_name: string, displayName: string) => ({
    ok: true,
    display_name: displayName.trim()
  }))
  apiMocks.deleteProfile.mockResolvedValue({ ok: true })
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
  apiMocks.getProfiles.mockResolvedValue({
    profiles: [...PROFILES, { ...PROFILES[0], name: 'secretary', display_name: 'Секретарь', is_default: false }]
  })
  apiMocks.getActiveProfile.mockResolvedValue({
    active: 'default',
    current: 'default'
  })
  apiMocks.getModelOptions.mockResolvedValue({
    providers: PROVIDERS,
    provider: 'custom:dario',
    model: 'claude-opus-4-5'
  })
  apiMocks.createProfile.mockResolvedValue({
    ok: true,
    name: 'buhgalter',
    path: '/root/.korra-buhgalter',
    model_set: true
  })
  apiMocks.setActiveProfile.mockResolvedValue({ active: 'secretary' })
  apiMocks.getProfileMemory.mockResolvedValue({
    memory: [],
    user: [],
    limits: { memory: 2200, user: 1375 },
    used: { memory: 0, user: 0 },
    enabled: { memory: true, user: true }
  })
  apiMocks.getProfileMaterials.mockResolvedValue({ materials: [] })
  apiMocks.getProfileSoul.mockResolvedValue({ content: 'Готовь повестки встреч.', exists: true })
  apiMocks.updateProfileSoul.mockResolvedValue({ ok: true })
})

afterEach(async () => {
  await act(async () => root?.unmount())
  container?.remove()
  vi.unstubAllGlobals()
  vi.clearAllMocks()
})

describe('ProfilesPage — создание и роль агента', () => {
  it('меняет русское имя, сохраняя адрес, разговоры и роль агента', async () => {
    await openPage()
    await click(container.querySelectorAll<HTMLButtonElement>('button[aria-haspopup="menu"]')[1])
    await click(findButton('Изменить имя', document.querySelector('[role="menu"]')!))
    const input = container.querySelector<HTMLInputElement>('input[aria-label="Имя агента"]')!
    expect(input.value).toBe('Секретарь')
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(input, '  Менеджер магазина  ')
      input.dispatchEvent(new Event('input', { bubbles: true }))
    })
    await click(findButton('Сохранить'))
    expect(apiMocks.updateProfileDisplayName).toHaveBeenCalledWith('secretary', 'Менеджер магазина')
    expect(apiMocks.renameProfile).not.toHaveBeenCalled()
    expect(apiMocks.updateProfileSoul).not.toHaveBeenCalled()
    expect(scopeMocks.refreshProfiles).toHaveBeenCalledTimes(1)
    expect(container.textContent).toContain('Менеджер магазина')
    const chatButtons = [...container.querySelectorAll('button')].filter(b => b.textContent === 'Открыть чат')
    await click(chatButtons[1])
    expect(container.querySelector('[data-testid="location"]')?.textContent).toBe('/agents?agent=secretary')
  })

  it('при отказе сохранения имени оставляет введённый текст для повторной попытки', async () => {
    apiMocks.updateProfileDisplayName.mockRejectedValueOnce(new Error('503: недоступно'))
    await openPage()
    await click(container.querySelectorAll<HTMLButtonElement>('button[aria-haspopup="menu"]')[1])
    await click(findButton('Изменить имя', document.querySelector('[role="menu"]')!))
    const input = container.querySelector<HTMLInputElement>('input[aria-label="Имя агента"]')!
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(input, 'Помощник')
      input.dispatchEvent(new Event('input', { bubbles: true }))
    })
    await click(findButton('Сохранить'))
    expect(input.value).toBe('Помощник')
    expect(scopeMocks.refreshProfiles).not.toHaveBeenCalled()
    await click(findButton('Сохранить'))
    expect(apiMocks.updateProfileDisplayName).toHaveBeenLastCalledWith('secretary', 'Помощник')
    expect(scopeMocks.refreshProfiles).toHaveBeenCalledOnce()
  })

  it('подтверждает удаление человеческим именем и обновляет каталог после успеха', async () => {
    await openPage()
    await click(container.querySelectorAll<HTMLButtonElement>('button[aria-haspopup="menu"]')[1])
    await click(findButton('Удалить', document.querySelector('[role="menu"]')!))
    const confirmation = document.querySelector('[role="alertdialog"]') || dialog()
    expect(confirmation?.textContent).toContain('Удалить агента «Секретарь»?')
    expect(confirmation?.textContent).toContain('разговоры, сохранённые знания')
    expect(apiMocks.deleteProfile).not.toHaveBeenCalled()
    await click(findButton('Удалить', confirmation!))
    expect(apiMocks.deleteProfile).toHaveBeenCalledWith('secretary')
    expect(scopeMocks.refreshProfiles).toHaveBeenCalledOnce()
  })

  it('ведёт кнопку создания в единый мастер', async () => {
    await openPage()
    await click(findButton('Создать агента'))
    expect(container.querySelector('[data-testid="location"]')?.textContent).toBe('/profiles/new')
    expect(document.querySelector('[role="dialog"]')).toBeNull()
    expect(apiMocks.createProfile).not.toHaveBeenCalled()
  })

  it.each(['default', 'secretary'])('из карточки открывает чат агента %s', async name => {
    await openPage()
    const buttons = Array.from(container.querySelectorAll('button')).filter(
      button => button.textContent === 'Открыть чат'
    )
    await click(buttons[name === 'default' ? 0 : 1])
    expect(container.querySelector('[data-testid="location"]')?.textContent).toBe(`/agents?agent=${name}`)
  })

  it('по ссылке открывает роль нужного агента и сохраняет её ему', async () => {
    await openPage('/profiles?agent=secretary&edit=role')
    expect(apiMocks.getProfileSoul).toHaveBeenCalledWith('secretary')
    expect(dialog().textContent).toContain('Секретарь')
    expect(dialog().textContent).toContain('Изменения применятся в новом разговоре')
    expect(editor().value).toBe('Готовь повестки встреч.')
    await enterText(editor(), 'Запрашивай сроки и собирай повестку.')
    await click(save())
    expect(apiMocks.updateProfileSoul).toHaveBeenCalledWith('secretary', 'Запрашивай сроки и собирай повестку.')
    expect(dialog().textContent).toContain('Сохранено. Изменения применятся в новом разговоре.')
    await click(findButton('Закрыть', dialog()))
    expect(document.querySelector('[role="dialog"]')).toBeNull()
    expect(container.querySelector('[data-testid="location"]')?.textContent).toBe('/profiles?agent=secretary')
  })

  it('не позволяет стереть роль во время её загрузки', async () => {
    let resolve!: (value: { content: string; exists: boolean }) => void
    apiMocks.getProfileSoul.mockReturnValueOnce(
      new Promise(done => {
        resolve = done
      })
    )
    await openPage('/profiles?agent=secretary&edit=role')
    expect(editor().disabled).toBe(true)
    expect(save().disabled).toBe(true)
    await click(save())
    expect(apiMocks.updateProfileSoul).not.toHaveBeenCalled()
    await act(async () => resolve({ content: 'Существующая роль', exists: true }))
    expect(editor().value).toBe('Существующая роль')
    await enterText(editor(), 'Существующая роль с уточнением')
    expect(save().disabled).toBe(false)
  })

  it('после ошибки чтения предлагает загрузить роль заново до сохранения', async () => {
    apiMocks.getProfileSoul.mockRejectedValueOnce(new Error('HTTP 500'))
    await openPage('/profiles?agent=secretary&edit=role')
    expect(dialog().querySelector('[role="alert"]')?.textContent).toContain('Не удалось загрузить роль')
    expect(save().disabled).toBe(true)
    await click(save())
    expect(apiMocks.updateProfileSoul).not.toHaveBeenCalled()
    await click(findButton('Повторить загрузку', dialog()))
    expect(editor().value).toBe('Готовь повестки встреч.')
    await enterText(editor(), 'Готовь повестки и протоколы встреч.')
    expect(save().disabled).toBe(false)
  })

  it('не заменяет свежий текст ответом закрытого редактора того же агента', async () => {
    let oldResponse!: (value: { content: string; exists: boolean }) => void
    apiMocks.getProfileSoul.mockReturnValueOnce(
      new Promise(resolve => {
        oldResponse = resolve
      })
    )
    await openPage('/profiles?agent=secretary&edit=role')
    await click(findButton('Закрыть', dialog()))
    const actionButtons = container.querySelectorAll<HTMLButtonElement>('button[aria-haspopup="menu"]')
    await click(actionButtons[1])
    await click(findButton('Обучение и настройки', document.querySelector('[role="menu"]')!))
    expect(editor().value).toBe('Готовь повестки встреч.')
    await enterText(editor(), 'Мои новые инструкции')
    await act(async () => oldResponse({ content: 'Устаревшие инструкции', exists: true }))
    expect(editor().value).toBe('Мои новые инструкции')
  })

  it.each(['memory', 'materials'])(
    'открывает адресованный раздел обучения %s и убирает адрес при закрытии',
    async section => {
      await openPage(`/profiles?agent=secretary&edit=learning&section=${section}`)
      expect(dialog().getAttribute('aria-label')).toBe('Обучение агента «Секретарь»')
      if (section === 'memory') expect(apiMocks.getProfileMemory).toHaveBeenCalledWith('secretary')
      else expect(apiMocks.getProfileMaterials).toHaveBeenCalledWith('secretary')
      expect(apiMocks.getProfileSoul).not.toHaveBeenCalled()
      await click(findButton('Закрыть', dialog()))
      expect(container.querySelector('[data-testid="location"]')?.textContent).toBe('/profiles?agent=secretary')
    }
  )

  it('на карточке открывает общую панель обучения выбранного агента', async () => {
    await openPage()
    const buttons = [...container.querySelectorAll('button')].filter(b => b.textContent === 'Обучение и настройки')
    await click(buttons[1])
    expect(apiMocks.getProfileSoul).toHaveBeenCalledWith('secretary')
    expect(container.querySelector('[data-testid="location"]')?.textContent).toBe(
      '/profiles?agent=secretary&edit=learning'
    )
    expect(dialog().querySelectorAll('#learning-role')).toHaveLength(1)
  })

  it('выбор агента по умолчанию относится к терминалу и не обещает смену чата', async () => {
    await openPage()
    await click(container.querySelectorAll<HTMLButtonElement>('button[aria-haspopup="menu"]')[1])
    await click(findButton('По умолчанию в терминале', document.querySelector('[role="menu"]')!))
    expect(apiMocks.setActiveProfile).toHaveBeenCalledWith('secretary')
    expect(scopeMocks.setProfile).not.toHaveBeenCalled()
    expect(document.body.textContent).toContain('Для новых запусков терминала выбран агент «Секретарь».')
  })

  it('по ссылке на модель выбирает модель адресованного агента', async () => {
    await openPage('/profiles?agent=secretary&edit=model')
    expect(dialog().textContent).toContain('Секретарь')
    expect(dialog().textContent).toContain('Dario · claude-opus-4-5')
    expect(apiMocks.getProfileSoul).not.toHaveBeenCalled()
  })
})

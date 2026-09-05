// @vitest-environment jsdom

import { act, useEffect } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { MemoryRouter, useLocation, useNavigate } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ProfileProvider } from '@/contexts/ProfileProvider'
import { useProfileScope } from '@/contexts/useProfileScope'
import { api, getManagementProfile, setManagementProfile, type ProfileInfo } from '@/lib/api'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

const main = { name: 'default', display_name: 'Корра' } as ProfileInfo
const created = { name: 'new-agent', display_name: 'Помощник магазина' } as ProfileInfo
let container: HTMLDivElement
let root: Root

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>(done => {
    resolve = done
  })
  return { promise, resolve }
}

function Harness() {
  const { profile, profiles, refreshProfiles } = useProfileScope()
  const navigate = useNavigate()
  const { pathname } = useLocation()
  useEffect(() => {
    if (pathname === '/skills') void api.getSkills()
  }, [pathname, profile])
  return (
    <>
      <span data-scope>{profile}</span>
      <span data-names>{profiles.map(p => p.display_name).join(', ')}</span>
      <button onClick={() => navigate('/skills?profile=new-agent')}>Навыки нового</button>
      <button onClick={() => navigate('/env?profile=new-agent')}>Ключи нового</button>
      <button onClick={() => void refreshProfiles().catch(() => {})}>Обновить каталог</button>
      <button onClick={() => void api.toggleSkill('example', false)}>Изменить навык</button>
    </>
  )
}

async function render(path = '/profiles/new') {
  container = document.createElement('div')
  document.body.append(container)
  root = createRoot(container)
  await act(async () =>
    root.render(
      <MemoryRouter initialEntries={[path]}>
        <ProfileProvider>
          <Harness />
        </ProfileProvider>
      </MemoryRouter>
    )
  )
}

async function click(text: string) {
  const button = [...container.querySelectorAll('button')].find(b => b.textContent === text)
  if (!button) throw new Error(`Нет кнопки ${text}`)
  await act(async () => button.click())
}

beforeEach(() => {
  localStorage.clear()
  setManagementProfile('')
  vi.spyOn(api, 'getProfiles').mockResolvedValue({ profiles: [main] })
  vi.spyOn(api, 'getActiveProfile').mockResolvedValue({ active: 'default', current: 'default' })
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => new Response('[]', { status: 200 }))
  )
})

afterEach(async () => {
  await act(async () => root?.unmount())
  container?.remove()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
  setManagementProfile('')
})

describe('адрес настроек после создания агента', () => {
  it.each(['Навыки нового', 'Ключи нового'])('%s сохраняют явный профиль при старом каталоге', async button => {
    await render()
    expect(container.querySelector('[data-names]')?.textContent).toBe('Корра')
    await click(button)
    expect(getManagementProfile()).toBe('new-agent')
    expect(container.querySelector('[data-scope]')?.textContent).toBe('new-agent')
  })

  it('реальный клиент API читает и изменяет навыки выбранного нового агента', async () => {
    await render()
    await click('Навыки нового')
    expect(fetch).toHaveBeenCalledWith('/api/skills?profile=new-agent', expect.anything())
    await click('Изменить навык')
    expect(fetch).toHaveBeenLastCalledWith(
      '/api/skills/toggle?profile=new-agent',
      expect.objectContaining({ method: 'PUT' })
    )
  })

  it('обновление каталога приносит человеческое имя без смены выбранного агента', async () => {
    await render()
    await click('Навыки нового')
    vi.mocked(api.getProfiles).mockResolvedValue({ profiles: [main, created] })
    await click('Обновить каталог')
    expect(container.querySelector('[data-names]')?.textContent).toContain('Помощник магазина')
    expect(getManagementProfile()).toBe('new-agent')
  })

  it('поздняя первоначальная загрузка не стирает созданного агента из каталога', async () => {
    const initial = deferred<{ profiles: ProfileInfo[] }>()
    vi.mocked(api.getProfiles).mockReturnValueOnce(initial.promise)
    await render()
    vi.mocked(api.getProfiles).mockResolvedValue({ profiles: [main, created] })
    await click('Обновить каталог')
    await act(async () => initial.resolve({ profiles: [main] }))
    expect(container.querySelector('[data-names]')?.textContent).toContain('Помощник магазина')
  })

  it('сбой обновления не сбрасывает явный адрес или уже загруженные имена', async () => {
    await render()
    await click('Ключи нового')
    vi.mocked(api.getProfiles).mockRejectedValue(new Error('Нет сети'))
    await click('Обновить каталог')
    expect(getManagementProfile()).toBe('new-agent')
    expect(container.querySelector('[data-names]')?.textContent).toBe('Корра')
  })

  it('сохранённый выбор удалённого агента по-прежнему очищается без явной ссылки', async () => {
    localStorage.setItem('korra.profileScope./env', 'deleted-agent')
    await render('/env')
    expect(getManagementProfile()).toBe('')
    expect(localStorage.getItem('korra.profileScope./env')).toBeNull()
  })
})

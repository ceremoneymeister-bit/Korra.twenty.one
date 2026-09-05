import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { useLocation, useSearchParams } from 'react-router'
import { api, setManagementProfile } from '@/lib/api'
import type { ProfileInfo } from '@/lib/api'
import { ProfileContext } from '@/contexts/profile-context'

/**
 * Routes whose data belongs to one isolated agent home. Deliberately excludes
 * Agents (each tab supplies an explicit profile), Files, System and Channels.
 */
const PROFILE_SCOPED_ROUTES = new Set([
  '/analytics',
  '/config',
  '/cron',
  '/env',
  '/mcp',
  '/models',
  '/pairing',
  '/sessions',
  '/skills'
])

const PROFILE_SCOPE_STORAGE_PREFIX = 'korra.profileScope.'

function normalizedProfileRoute(pathname: string): string | null {
  const route = pathname.replace(/\/+$/, '') || '/'
  return PROFILE_SCOPED_ROUTES.has(route) ? route : null
}

function profileScopeStorageKey(route: string): string {
  return `${PROFILE_SCOPE_STORAGE_PREFIX}${route}`
}

function readStoredProfile(route: string | null): string {
  if (!route || typeof localStorage === 'undefined') return ''
  try {
    return localStorage.getItem(profileScopeStorageKey(route))?.trim() ?? ''
  } catch {
    return ''
  }
}

function storeProfile(route: string, profile: string): void {
  if (typeof localStorage === 'undefined') return
  try {
    localStorage.setItem(profileScopeStorageKey(route), profile)
  } catch {
    // Storage can be unavailable in privacy mode; the URL still preserves
    // the selection for this visit.
  }
}

/**
 * Per-section management-profile scope.
 *
 * The active route synchronously resolves its own saved profile before child
 * effects run, then mirrors that target into the API module. Bare navigation
 * therefore restores the destination section's choice instead of carrying
 * the previous section's target across the dashboard. "" means the dashboard
 * process's own profile. An explicit `?profile=` remains a deep-link override
 * and is saved for that section.
 */
export function ProfileProvider({ children }: { children: ReactNode }) {
  const [searchParams, setSearchParams] = useSearchParams()
  const { pathname } = useLocation()
  const [profiles, setProfiles] = useState<ProfileInfo[]>([])
  const [currentProfile, setCurrentProfile] = useState('default')
  const profilesRequest = useRef(0)
  const refreshProfiles = useCallback(async () => {
    const request = ++profilesRequest.current
    const result = await api.getProfiles()
    if (request === profilesRequest.current) setProfiles(result.profiles)
  }, [])
  const route = normalizedProfileRoute(pathname)
  const urlProfile = route ? searchParams.get('profile') : null
  const selectedProfile = route ? (urlProfile !== null ? urlProfile.trim() : readStoredProfile(route)) : ''
  // Явный адрес может вести к только что созданному агенту, которого ещё нет
  // в каталоге. Никогда не подменяем его основным агентом. Проверка каталога
  // нужна лишь для старого выбора из localStorage после удаления профиля.
  const selectionKnown =
    urlProfile !== null ||
    !selectedProfile ||
    profiles.length === 0 ||
    profiles.some(item => item.name === selectedProfile)
  const profile = selectionKnown && selectedProfile && selectedProfile !== currentProfile ? selectedProfile : ''
  useEffect(() => {
    if (route && selectedProfile && !selectionKnown && typeof localStorage !== 'undefined') {
      try {
        localStorage.removeItem(profileScopeStorageKey(route))
      } catch {
        // storage может быть недоступен — просто не запоминаем
      }
    }
  }, [route, selectedProfile, selectionKnown])

  // This must happen during the provider render: child page effects in the
  // same commit immediately issue their profile-scoped reads.
  setManagementProfile(profile)

  // Deep links become the remembered choice for this section.
  useEffect(() => {
    if (route && urlProfile?.trim()) {
      storeProfile(route, urlProfile.trim())
    }
  }, [route, urlProfile])

  useEffect(() => {
    let cancelled = false

    void refreshProfiles().catch(() => {})

    api
      .getActiveProfile()
      .then(info => {
        if (cancelled) return
        setCurrentProfile(info.current || 'default')
      })
      .catch(() => {})

    return () => {
      cancelled = true
      profilesRequest.current += 1
    }
  }, [refreshProfiles])

  const setProfile = useCallback(
    (name: string) => {
      if (!route) return
      const nextProfile = name.trim()
      if (!nextProfile) return

      storeProfile(route, nextProfile)
      setManagementProfile(nextProfile === currentProfile ? '' : nextProfile)
      setSearchParams(
        prev => {
          const next = new URLSearchParams(prev)
          next.set('profile', nextProfile)
          return next
        },
        { replace: true }
      )
    },
    [currentProfile, route, setSearchParams]
  )

  const value = useMemo(
    () => ({ profile, currentProfile, profiles, setProfile, refreshProfiles }),
    [profile, currentProfile, profiles, setProfile, refreshProfiles]
  )

  return <ProfileContext.Provider value={value}>{children}</ProfileContext.Provider>
}

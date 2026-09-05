import { createContext } from 'react'
import type { ProfileInfo } from '@/lib/api'

export interface ProfileContextValue {
  /** Profile every management surface reads/writes ("" = the dashboard
   *  process's own profile). */
  profile: string
  /** The profile the dashboard process itself runs under. */
  currentProfile: string
  /** Known profiles (includes "default"). */
  profiles: ProfileInfo[]
  setProfile: (name: string) => void
  /** Reload names and display names after creating or changing an agent. */
  refreshProfiles: () => Promise<void>
}

export const ProfileContext = createContext<ProfileContextValue>({
  profile: '',
  currentProfile: 'default',
  profiles: [],
  setProfile: () => {},
  refreshProfiles: async () => {}
})

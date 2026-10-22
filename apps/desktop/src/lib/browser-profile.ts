import type { DesktopProfileRoute } from '@/global'
import { requireBrowserConnection } from '@/lib/browser-connection'

import { readJson } from './storage'

interface BrowserProfileOptions {
  basePath: string
  currentProfile: () => string | null
  openWindow: (target: URL) => Promise<{ ok: true }>
}

// A tab without `?profile=` boots on this origin's saved default, so a named
// profile — Default included — is always written out; only null clears it.
function setUrlProfile(url: URL, profile: string | null): void {
  if (profile) {
    url.searchParams.set('profile', profile)
  } else {
    url.searchParams.delete('profile')
  }
}

interface BrowserProfileBridge extends Pick<Window['hermesDesktop'], 'openWindow' | 'profile'> {
  /** The bridge's own preferences follow a rename or delete the server accepted. */
  profileChanged: (oldName: string, newName: null | string) => void
}

export function createBrowserProfileBridge({
  basePath,
  currentProfile,
  openWindow
}: BrowserProfileOptions): BrowserProfileBridge {
  // localStorage supplies origin isolation; the base path separates colocated servers.
  const storageKey = `hermes:webapp:default-profile:${basePath || '/'}`
  const listeners = new Set<(route: DesktopProfileRoute | null) => void>()

  const requireRoute = (value: unknown): DesktopProfileRoute => {
    if (!value || typeof value !== 'object') {
      throw new Error('A profile route is required.')
    }

    const { connectionId, profile } = value as Record<string, unknown>

    if (typeof profile !== 'string' || !/^[a-z0-9][a-z0-9_-]{0,63}$/.test(profile)) {
      throw new Error('Invalid profile name.')
    }

    if (connectionId !== null && typeof connectionId !== 'string') {
      throw new Error('Invalid connection id.')
    }

    requireBrowserConnection(connectionId)

    return { connectionId, profile }
  }

  const readDefault = (): DesktopProfileRoute | null => {
    try {
      return requireRoute(readJson(storageKey))
    } catch {
      return null
    }
  }

  const remember = async (profile: string | null) => {
    const selected = profile?.trim() || null
    const url = new URL(window.location.href)
    setUrlProfile(url, selected)
    // Keep react-router's entry state (idx/key/usr): only the URL changes.
    window.history.replaceState(window.history.state, '', `${url.pathname}${url.search}${url.hash}`)

    return { profile: selected }
  }

  // Native Desktop's profileChanged (electron/desktop-profile.ts), run once the
  // server has renamed or deleted `oldName`: this tab's `?profile=` and the
  // saved startup route follow the new name, or fall back to Default on delete,
  // so the next reload or bare launch link never boots a profile that is gone.
  // The browser has one source, so a saved route's connection (null or
  // 'local') always matches. Other tabs hear about the route via `storage`.
  const profileChanged = (oldName: string, newName: null | string) => {
    const url = new URL(window.location.href)

    if (url.searchParams.get('profile')?.trim() === oldName) {
      setUrlProfile(url, newName || 'default')
      window.history.replaceState(window.history.state, '', `${url.pathname}${url.search}${url.hash}`)
    }

    const route = readDefault()

    if (route?.profile !== oldName) {
      return
    }

    const next = newName ? { ...route, profile: newName } : null

    if (next) {
      window.localStorage.setItem(storageKey, JSON.stringify(next))
    } else {
      window.localStorage.removeItem(storageKey)
    }

    listeners.forEach(listener => listener(next))
  }

  return {
    openWindow: async options => {
      const url = new URL(window.location.href)
      setUrlProfile(url, options === undefined ? currentProfile() : requireRoute(options).profile)

      url.searchParams.delete('win')
      url.searchParams.delete('watch')
      url.hash = '/'

      return openWindow(url)
    },
    profile: {
      forgetProfile: name => profileChanged(name, null),
      get: async () => ({ profile: currentProfile() }),
      getDefault: async () => readDefault(),
      setDefault: async value => {
        const route = requireRoute(value)
        window.localStorage.setItem(storageKey, JSON.stringify(route))
        listeners.forEach(listener => listener(route))

        return route
      },
      onDefaultChanged: callback => {
        listeners.add(callback)

        const onStorage = (event: StorageEvent) => {
          if (event.storageArea === window.localStorage && (event.key === storageKey || event.key === null)) {
            callback(readDefault())
          }
        }

        window.addEventListener('storage', onStorage)

        return () => {
          listeners.delete(callback)
          window.removeEventListener('storage', onStorage)
        }
      },
      remember,
      set: async profile => {
        const result = await remember(profile)
        window.location.reload()

        return result
      }
    },
    profileChanged
  }
}

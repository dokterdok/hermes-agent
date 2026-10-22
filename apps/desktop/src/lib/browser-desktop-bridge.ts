import { externalUrlTarget } from '@hermes/shared'

import type { HermesApiRequest } from '@/global'
import { BROWSER_BRIDGE_STUBS } from '@/lib/browser-bridge-stubs'
import { createBrowserClipboardBridge } from '@/lib/browser-clipboard'
import { createBrowserConnectionBridge, requireBrowserConnection } from '@/lib/browser-connection'
import { createBrowserFilesBridge } from '@/lib/browser-files'
import { watchWebappLaunchLink } from '@/lib/browser-launch-session'
import { createBrowserProfileBridge } from '@/lib/browser-profile'
import { createBrowserSettings } from '@/lib/browser-settings'
import { createBrowserTerminal } from '@/lib/browser-terminal'
import { authenticatedWebsocketUrl, browserApi, browserBootstrap, endpointUrl, socketTicket } from '@/lib/browser-transport'
import { createBrowserUploadsBridge } from '@/lib/browser-uploads'
import { createBrowserWindowOpener, sessionWindowTarget } from '@/lib/browser-window'
import { createBrowserZoom } from '@/lib/browser-zoom'
import { $connection } from '@/store/session'
import { windowProfileOverride } from '@/store/windows'

import { resolveGatewayVersion } from '../../electron/gateway-version'
import { profileNameFromDeleteRequest } from '../../electron/profile-delete-routing'
import { type ProfileRename, profileRenameFromRequest } from '../../electron/profile-rename-routing'

/** The rename (or delete: `newName` null) a profile request asks for, parsed as native Desktop does. */
function profileChangeFromRequest(request: HermesApiRequest): null | { newName: null | string; oldName: string } {
  const renamed: null | ProfileRename = profileRenameFromRequest(request)
  const deleted = renamed ? null : profileNameFromDeleteRequest(request)

  return renamed ?? (deleted ? { newName: null, oldName: deleted } : null)
}

/** Native Desktop's failure test for a 2xx profile reply (`afterProfileRequest`). */
function profileRequestRefused(response: unknown): boolean {
  if (!response || typeof response !== 'object') {return false}
  const result = response as Record<string, unknown>

  return result.ok === false || result.success === false || Boolean(result.error)
}

/**
 * Install a capability-limited Desktop bridge when the real renderer is served
 * directly by Hermes' authenticated web server.
 *
 * Electron remains authoritative everywhere it exists: no injected dashboard
 * token means this is a normal Vite/Electron renderer, and an existing preload
 * bridge is never replaced. Browser-hosted mode maps the Desktop's backend
 * contract onto the same-origin /api + /api/ws surface and deliberately exposes
 * only safe browser equivalents for machine-level capabilities.
 */
export function installBrowserDesktopBridge(): boolean {
  if (window.hermesDesktop) {return false}

  const bootstrap = browserBootstrap()

  if (!bootstrap) {return false}

  if (bootstrap.privateSession) {
    watchWebappLaunchLink(() => window.location.reload())
  }

  const currentProfile = () => $connection.get()?.profile?.trim() || windowProfileOverride()

  const profileBridge = createBrowserProfileBridge({
    basePath: bootstrap.basePath,
    currentProfile,
    openWindow: target => openWindow(target)
  })

  // Native Desktop's afterProfileRequest: once the server has renamed or
  // deleted a profile, what this bridge keeps under the old name follows it.
  // A refused request changes nothing, and bookkeeping that fails must not
  // turn a rename the server accepted into one the caller reports as failed.
  const afterProfileChange = (change: { newName: null | string; oldName: string }, response: unknown) => {
    if (profileRequestRefused(response)) {return}

    try {
      profileBridge.profileChanged(change.oldName, change.newName)
    } catch {
      // Storage refused: the server-side change still stands.
    }
  }

  const api = <T>(request: HermesApiRequest): Promise<T> => {
    const response = browserApi<T>(bootstrap, request)
    const change = profileChangeFromRequest(request)

    return change
      ? response.then(result => {
          afterProfileChange(change, result)

          return result
        })
      : response
  }

  const objectUrls = new Set<string>()
  const previewUrls = new Set<string>()

  window.addEventListener(
    'beforeunload',
    () => {
      objectUrls.forEach(url => URL.revokeObjectURL(url))
      objectUrls.clear()
      previewUrls.clear()
    },
    { once: true }
  )

  const openWindow = createBrowserWindowOpener({
    api,
    basePath: bootstrap.basePath,
    privateSession: bootstrap.privateSession
  })

  // Electron's opener rule: agent- or tool-supplied links must not run script
  // or custom schemes on this origin, whose cookie authenticates the API. A
  // `file:` URL names the server's disk, and this browser cannot open it.
  const openExternal = async (url: string) => {
    const target = externalUrlTarget(url)

    if (target?.kind === 'web') {window.open(target.url, '_blank', 'noopener,noreferrer')}
  }

  const uploads = createBrowserUploadsBridge({ api, bootstrap, currentProfile, objectUrls, previewUrls })

  const bridge: Window['hermesDesktop'] = {
    ...BROWSER_BRIDGE_STUBS,
    ...createBrowserClipboardBridge({ saveBuffer: uploads.saveImageBuffer }),
    ...createBrowserConnectionBridge({ api, bootstrap }),
    ...createBrowserFilesBridge({ api, bootstrap, currentProfile }),
    ...uploads,
    api,
    // This renderer is built from the serving checkout, so its version is the
    // server's, read the way native Desktop reads its runtime's.
    getVersion: async () => ({
      appVersion: await resolveGatewayVersion(path => api({ path, timeoutMs: 5000 })),
      electronVersion: '',
      hermesRoot: '',
      nodeVersion: '',
      platform: 'browser'
    }),
    findInPage: async (query: string) => {
      const find = (window as Window & { find?: (value: string) => boolean }).find

      return { count: query && find?.call(window, query) ? 1 : 0 }
    },
    notify: async ({ title, body }: { title?: string; body?: string }) => {
      if (!('Notification' in window)) {return false}

      if (Notification.permission === 'default') {await Notification.requestPermission()}

      if (Notification.permission !== 'granted') {return false}
      new Notification(title || 'Hermes', { body })

      return true
    },
    openExternal,
    // Each plugin socket dial gets a one-use ticket bound to its path and profile, as Electron's
    // native grant does: the session token never rides a socket URL.
    getPluginWsUrl: async ({ connectionId, path, pluginId, profile }) => {
      requireBrowserConnection(connectionId)
      const url = endpointUrl(`/api/plugins/${pluginId}${path}`, bootstrap.basePath, profile)
      // The ticket names the path the server routes (decoded, without the proxy's base path).
      const route = decodeURIComponent(url.pathname.slice(bootstrap.basePath.length))
      url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:'
      url.searchParams.set('ticket', await socketTicket(bootstrap, route, url.searchParams.get('profile')))

      return url.toString()
    },
    openPreviewInBrowser: async url => {
      // Only this install's sandbox wrappers may open as same-origin blobs.
      if (previewUrls.has(url)) {
        window.open(url, '_blank', 'noopener,noreferrer')
      } else {
        await openExternal(url)
      }
    },
    openSessionWindow: async (sessionId, opts) => {
      const target = sessionWindowTarget(window.location.href, sessionId, opts)

      return target ? openWindow(target) : { error: 'invalid_session_id', ok: false }
    },
    settings: createBrowserSettings({ basePath: bootstrap.basePath, currentProfile }),
    requestMicrophoneAccess: async () => {
      if (!navigator.mediaDevices?.getUserMedia) {return false}
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
      stream.getTracks().forEach(track => track.stop())

      return true
    },
    terminal: createBrowserTerminal({
      basePath: bootstrap.basePath,
      currentProfile,
      websocketUrl: profile => authenticatedWebsocketUrl(bootstrap, '/api/host-terminal', profile),
      defaultCwd: async profile => (await api<{ cwd?: string }>({ path: '/api/fs/default-cwd', profile })).cwd || ''
    }),
    openWindow: profileBridge.openWindow,
    profile: profileBridge.profile,
    zoom: createBrowserZoom(bootstrap.basePath)
  }

  window.hermesDesktop = bridge
  document.documentElement.dataset.hermesDesktopHost = 'browser'

  return true
}

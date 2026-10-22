import { cleanup, render, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

import { installBrowserDesktopBridge } from '@/lib/browser-desktop-bridge'
import { $desktopBoot } from '@/store/boot'
import { closeSecondaryGateways } from '@/store/gateway'
import { $notifications, clearNotifications } from '@/store/notifications'
import { $connection } from '@/store/session'
import { clearWebappSession, seedWebappSession } from '@/test/webapp-session'

import { FakeWebSocket } from '../../../test/fake-gateway-socket'

import { takeGatewaySurvivor } from './gateway-hmr-survivor'
import { useGatewayBoot } from './use-gateway-boot'

vi.mock(import('@/store/terminal-backend-warning'), () => ({
  warnIfTerminalBackendUnavailable: vi.fn(async () => false)
}))

// A Webapp whose `ops` profile was renamed or deleted outside this tab (another tab, the CLI).
const tickets: string[] = []
let rosterStatus = 200

const reply = (body: unknown, status = 200) => Promise.resolve(new Response(JSON.stringify(body), { status }))

function fakeServer(input: RequestInfo | URL): Promise<Response> {
  const url = new URL(String(input instanceof Request ? input.url : input), window.location.origin)
  const profile = url.searchParams.get('profile') || 'default'

  if (url.pathname === '/api/profiles') {
    return reply({ profiles: [{ name: 'default' }] }, rosterStatus)
  }

  if (url.pathname !== '/api/webapp/gateway-ticket') {
    return reply({})
  }

  tickets.push(profile)

  return profile === 'default'
    ? reply({ ticket: 'T'.repeat(43) })
    : reply({ detail: `Profile '${profile}' does not exist.` }, 404)
}

const ignore = () => undefined

function Boot() {
  useGatewayBoot({
    beforeConnectionSwitch: ignore,
    handleGatewayEvent: ignore,
    handleServerRequest: () => false,
    onConnectionReady: ignore,
    onGatewayReady: ignore,
    refreshHermesConfig: async () => undefined,
    refreshSessions: async () => undefined
  })

  return null
}

async function boot(url: string) {
  window.history.replaceState(null, '', url)
  expect(installBrowserDesktopBridge()).toBe(true)
  const { profile } = window.hermesDesktop
  await profile.setDefault({ connectionId: null, profile: 'ops' })
  render(<Boot />)
  await waitFor(() => expect($desktopBoot.get().running).toBe(false), { timeout: 10_000 })
  const notices = $notifications.get().map(notice => notice.message)
  const urlProfile = new URLSearchParams(window.location.search).get('profile')

  return { error: $desktopBoot.get().error, notices, saved: await profile.getDefault(), tickets, urlProfile }
}

beforeEach(() => {
  tickets.length = 0
  rosterStatus = 200
  localStorage.clear()
  seedWebappSession('S'.repeat(43))
  vi.stubGlobal('fetch', vi.fn(fakeServer))
  vi.stubGlobal('WebSocket', FakeWebSocket)
  FakeWebSocket.mode = 'open'
  FakeWebSocket.pingMode = 'pong'
  $desktopBoot.set({ ...$desktopBoot.get(), error: null, phase: 'init', running: true, visible: true })
})

afterEach(() => {
  cleanup()
  takeGatewaySurvivor()?.gateway.close()
  closeSecondaryGateways()
  clearNotifications()
  $connection.set(null)
  Reflect.deleteProperty(window, 'hermesDesktop')
  clearWebappSession()
  localStorage.clear()
  window.history.replaceState(null, '', '/#/')
  vi.unstubAllGlobals()
})

it.each([
  ['/#/', null],
  ['/?profile=ops#/', 'default']
])('boots Default with a notice when the startup profile is gone (%s)', async (url, urlProfile) => {
  expect(await boot(url)).toEqual({
    error: null,
    notices: ['Profile "ops" no longer exists, so Hermes opened the default profile.'],
    saved: null,
    tickets: ['ops', 'default'],
    urlProfile
  })
})

it('keeps the boot failure when the roster cannot prove the profile gone', async () => {
  rosterStatus = 503
  expect(await boot('/#/')).toMatchObject({
    error: expect.stringContaining("Profile 'ops' does not exist."),
    saved: { connectionId: null, profile: 'ops' },
    tickets: ['ops']
  })
})

import { afterEach, describe, expect, it, vi } from 'vitest'

import type { ProfileInfo } from '@/types/hermes'

vi.mock('@/app/open-session', () => ({ openSession: vi.fn() }))
vi.mock('@/components/pane-shell/tree/store', async () => {
  const { atom } = await import('nanostores')

  return { $narrowViewport: atom(false) }
})
vi.mock('@/contrib/events', () => ({ onGatewayEvent: vi.fn() }))
vi.mock('@/hermes', () => ({ getLogs: vi.fn(), getStatus: vi.fn() }))
vi.mock('@/store/notifications', () => ({ notify: vi.fn(), notifyError: vi.fn() }))
vi.mock('@/store/system-actions', () => ({ runGatewayRestart: vi.fn() }))
vi.mock('@/store/session', async () => {
  const { atom } = await import('nanostores')

  return {
    $activeSessionId: atom(null),
    $currentCwd: atom(''),
    $currentModel: atom(''),
    $gatewayState: atom('open')
  }
})
vi.mock('@/store/profile', async () => {
  const { atom } = await import('nanostores')

  const profiles = atom([
    {
      has_env: false,
      is_default: false,
      model: null,
      name: 'cached-only',
      path: '/profiles/cached-only',
      provider: null,
      skill_count: 0
    }
  ])

  return {
    $activeGatewayProfile: atom('remote-worker'),
    $profiles: profiles,
    ensureGatewayProfile: vi.fn(),
    newSessionInProfile: vi.fn(),
    refreshProfiles: vi.fn(async () => profiles.get()),
    setShowAllProfiles: vi.fn()
  }
})
vi.mock('@/store/gateway', async () => {
  const { atom } = await import('nanostores')

  return {
    $gateway: atom(null),
    requestGatewayForProfile: vi.fn(async (profile: string, method: string, params: Record<string, unknown>) => ({
      method,
      params,
      profile
    }))
  }
})

const { host } = await import('./index')
const { requestGatewayForProfile } = await import('@/store/gateway')
const { $profiles, refreshProfiles } = await import('@/store/profile')

const profile = (name: string): ProfileInfo => ({
  has_env: false,
  is_default: name === 'default',
  model: null,
  name,
  path: `/profiles/${name}`,
  provider: null,
  skill_count: 0
})

afterEach(() => {
  vi.clearAllMocks()
  $profiles.set([profile('cached-only')])
  delete (window as unknown as { hermesDesktop?: unknown }).hermesDesktop
})

describe('connection-aware plugin host APIs', () => {
  it('refreshes the profile inventory before asking Electron for routes', async () => {
    const getProfileRoutes = vi.fn(async () => [
      { connectionId: 'connection-local', mode: 'local', profile: 'desktop-primary', targetProfile: 'desktop-primary' },
      {
        connectionId: 'connection-remote',
        mode: 'remote',
        profile: 'remote-worker',
        targetProfile: 'backend-worker'
      }
    ])

    vi.mocked(refreshProfiles).mockResolvedValueOnce([profile('desktop-primary'), profile('remote-worker')])
    ;(window as unknown as { hermesDesktop: unknown }).hermesDesktop = { getProfileRoutes }

    const routes = await host.profileRoutes()

    expect(routes).toEqual([
      { connectionId: 'connection-local', mode: 'local', profile: 'desktop-primary', targetProfile: 'desktop-primary' },
      {
        connectionId: 'connection-remote',
        mode: 'remote',
        profile: 'remote-worker',
        targetProfile: 'backend-worker'
      }
    ])
    expect(refreshProfiles).toHaveBeenCalledOnce()
    expect(getProfileRoutes).toHaveBeenCalledWith(['desktop-primary', 'remote-worker'])
  })

  it('falls back to the cached profile inventory when refresh fails', async () => {
    const getProfileRoutes = vi.fn(async () => [
      { connectionId: 'connection-cached', mode: 'remote', profile: 'cached-worker', targetProfile: 'cached-worker' }
    ])

    $profiles.set([profile('cached-worker')])
    vi.mocked(refreshProfiles).mockRejectedValueOnce(new Error('profile backend unavailable'))
    ;(window as unknown as { hermesDesktop: unknown }).hermesDesktop = { getProfileRoutes }

    await expect(host.profileRoutes()).resolves.toEqual([
      { connectionId: 'connection-cached', mode: 'remote', profile: 'cached-worker', targetProfile: 'cached-worker' }
    ])
    expect(getProfileRoutes).toHaveBeenCalledWith(['cached-worker'])
  })

  it('forwards a targeted request without changing foreground state', async () => {
    const result = await host.requestProfile('remote-worker', 'profiles.list', { include_sessions: true })

    expect(result).toEqual({
      method: 'profiles.list',
      params: { include_sessions: true },
      profile: 'remote-worker'
    })
    expect(requestGatewayForProfile).toHaveBeenCalledWith('remote-worker', 'profiles.list', { include_sessions: true })
  })
})

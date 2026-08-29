import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { pluginSdkMock, scriptedStorage } from './group-test-utils'

const { host } = vi.hoisted(() => ({
  host: {} as Record<string, unknown>
}))

vi.mock('@hermes/plugin-sdk', async () => pluginSdkMock(host))

async function loadRuntime() {
  vi.resetModules()

  for (const key of Object.keys(host)) {
    delete host[key]
  }

  Object.assign(host, {
    activeConnectionId: () => 'gateway-a',
    onEvent: vi.fn(() => () => undefined),
    profileRoutes: async () => [],
    request: vi.fn(async () => ({})),
    requestProfile: vi.fn(async () => ({})),
    retainProfileSocket: vi.fn(() => () => undefined),
    state: {
      connectionId: {
        get: () => 'gateway-a',
        listen: () => () => undefined
      }
    }
  })

  const [chat, runtime] = await Promise.all([import('./group-chat'), import('./desktop-room-command-runtime')])

  return {
    chat,
    runtime
  }
}

beforeEach(() => {
  vi.useFakeTimers()
})

afterEach(() => {
  vi.clearAllTimers()
  vi.useRealTimers()
})

describe('classic Group Chat command runtime', () => {
  it('mints one private authority and publishes only its hash', async () => {
    const loaded = await loadRuntime()
    const stored = new Map<string, unknown>()

    await loaded.runtime.startDesktopRoomCommandRuntime(scriptedStorage(stored).storage)
    const authority = await loaded.runtime.prepareDesktopRoomAuthority()

    expect(authority.desktopCoordinatorId).toMatch(/^desktop:/)
    expect(authority.desktopAuthorityToken).toMatch(/^authority:/)
    expect(authority.desktopAuthorityHash).toMatch(/^[a-f0-9]{64}$/)
    expect(authority.desktopAuthorityHash).not.toContain(authority.desktopAuthorityToken)
    expect(stored.get('desktop-room-command-consumer-v1')).toBe(authority.desktopCoordinatorId)

    loaded.runtime.stopDesktopRoomCommandRuntime()
  })

  it('adopts only a classic room with local execution evidence', async () => {
    const loaded = await loadRuntime()
    const stored = new Map<string, unknown>()

    loaded.chat.$groupChats.set({
      Active: {
        log: [],
        sessions: {
          research: 'session-1'
        },
        watermarks: {}
      },
      Silent: {
        log: [],
        watermarks: {}
      },
      Hosted: {
        hosted: 'install:home',
        log: [],
        sessions: {
          research: 'session-2'
        },
        watermarks: {}
      }
    })

    await loaded.runtime.startDesktopRoomCommandRuntime(scriptedStorage(stored).storage)

    expect(loaded.chat.$groupChats.get().Active).toMatchObject({
      desktopAuthorityHash: expect.stringMatching(/^[a-f0-9]{64}$/),
      desktopAuthorityToken: expect.stringMatching(/^authority:/),
      desktopCoordinatorId: expect.stringMatching(/^desktop:/)
    })
    expect(loaded.chat.$groupChats.get().Silent.desktopCoordinatorId).toBeUndefined()
    expect(loaded.chat.$groupChats.get().Hosted.desktopCoordinatorId).toBeUndefined()

    await expect(loaded.runtime.ensureDesktopRoomAuthority('Silent')).resolves.toBe(true)
    expect(loaded.chat.$groupChats.get().Silent).toMatchObject({
      desktopAuthorityHash: expect.stringMatching(/^[a-f0-9]{64}$/),
      desktopAuthorityToken: expect.stringMatching(/^authority:/),
      desktopCoordinatorId: expect.stringMatching(/^desktop:/)
    })

    loaded.runtime.stopDesktopRoomCommandRuntime()
  })
})

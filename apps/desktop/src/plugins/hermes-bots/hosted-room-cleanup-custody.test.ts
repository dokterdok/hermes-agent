import { beforeEach, expect, it, vi } from 'vitest'

import { pluginSdkMock, scriptedStorage } from './group-test-utils'

const mocks = vi.hoisted(() => ({ host: {} as Record<string, unknown>, request: vi.fn(), release: vi.fn() }))
vi.mock('@hermes/plugin-sdk', async () => pluginSdkMock(mocks.host))

const route = { connectionId: 'home', profile: 'default', targetProfile: 'default', mode: 'remote' as const }

const operation = (id = 'room') => ({
  operationId: `${id}:rollback`,
  setupId: id,
  kind: 'home-disband' as const,
  connectionId: 'home',
  installationId: 'install:home',
  profile: 'default',
  roomId: id
})

beforeEach(() => {
  vi.resetModules()
  vi.resetAllMocks()
  Object.assign(mocks.host, {
    profileRoutes: async () => [route],
    requestProfile: (_route: unknown, method: string, params: unknown) => mocks.request(method, params),
    acquireProfileRoute: async () => ({
      route,
      generation: 1,
      assertCurrent: () => undefined,
      release: mocks.release,
      request: (method: string, params: unknown = {}) => mocks.request(method, params)
    })
  })
})

it.each(['home-disband', 'peer-revoke', 'peer-revoke-exact', 'peer-reconnect'] as const)(
  'retains %s compensation without sending a secret or mutation to a replacement installation',
  async kind => {
    const cleanup = await import('./hosted-room-cleanup')
    const durable = new Map<string, unknown>()
    await cleanup.startHostedRoomCleanup(scriptedStorage(durable).storage)
    await cleanup.addHostedRoomCleanup({
      ...operation(),
      kind,
      grant: 'synthetic-bearer',
      grantSha256: 'a'.repeat(64),
      homeConnectionId: 'home',
      homeInstallationId: 'install:home',
      homeProfile: 'default',
      memberId: 'member',
      catalog: { installation_id: 'install:home', catalog_digest: 'digest' },
      targetUrl: 'https://fixture.invalid'
    })
    await cleanup.armHostedRoomCleanup('room')
    mocks.request.mockImplementation(async (method: string) => {
      if (method === 'groups.capabilities') {
        return { driver: true, persistent_process: true, authority_gateway_id: 'install:replacement' }
      }

      if (method === 'groups.disband') {
        throw { code: 4113, message: 'hosted room not found' }
      }

      return {}
    })
    await cleanup.dispatchHostedRoomCleanup()
    expect(mocks.request.mock.calls.every(([method]) => method === 'groups.capabilities')).toBe(true)
    expect(JSON.stringify(mocks.request.mock.calls)).not.toContain('synthetic-bearer')
    expect(cleanup.hostedRoomCleanupPending('room')).toBe(true)
    expect((durable.get(cleanup.HOSTED_ROOM_CLEANUP_KEY) as { operations: unknown[] }).operations).toHaveLength(1)
    cleanup.stopHostedRoomCleanup()
  }
)

it('joins a busy cleanup pass then processes compensation armed after its snapshot', async () => {
  const cleanup = await import('./hosted-room-cleanup')
  const durable = new Map<string, unknown>()
  await cleanup.startHostedRoomCleanup(scriptedStorage(durable).storage)
  await cleanup.addHostedRoomCleanup(operation('older'))
  await cleanup.addHostedRoomCleanup(operation('newer'))
  await cleanup.armHostedRoomCleanup('older')
  let entered!: () => void
  let release!: () => void

  const started = new Promise<void>(resolve => {
    entered = resolve
  })

  const held = new Promise<void>(resolve => {
    release = resolve
  })

  mocks.request.mockImplementation(async (method: string, params: { room_id?: string }) => {
    if (method === 'groups.capabilities') {
      return { driver: true, persistent_process: true, authority_gateway_id: 'install:home' }
    }

    if (params.room_id === 'older') {
      entered()
      await held
    }

    return { tombstone: { room_id: params.room_id } }
  })
  const first = cleanup.dispatchHostedRoomCleanup()
  await started
  await cleanup.armHostedRoomCleanup('newer')
  const second = cleanup.dispatchHostedRoomCleanup()
  release()
  await Promise.all([first, second])
  expect(cleanup.hostedRoomCleanupPending('older')).toBe(false)
  expect(cleanup.hostedRoomCleanupPending('newer')).toBe(false)
  expect(
    mocks.request.mock.calls.filter(([method]) => method === 'groups.disband').map(([, params]) => params.room_id)
  ).toEqual(['older', 'newer'])
  cleanup.stopHostedRoomCleanup()
})

it('keeps unbound historical compensation instead of guessing an installation', async () => {
  const cleanup = await import('./hosted-room-cleanup')

  const durable = new Map<string, unknown>([
    [
      cleanup.HOSTED_ROOM_CLEANUP_KEY,
      {
        version: 1,
        operations: [{ ...operation(), installationId: undefined, armed: true, ownerId: '', ownerLeaseUntil: 0 }]
      }
    ]
  ])

  mocks.request.mockResolvedValue({ tombstone: { room_id: 'room' } })
  await cleanup.startHostedRoomCleanup(scriptedStorage(durable).storage)
  expect(mocks.request).not.toHaveBeenCalled()
  expect(cleanup.hostedRoomCleanupPending('room')).toBe(true)
  cleanup.stopHostedRoomCleanup()
})

it.each(['probe-race', 'lost-ack', 'owner-replacement'] as const)('retains compensation after %s', async failure => {
  const cleanup = await import('./hosted-room-cleanup')
  const durable = new Map<string, unknown>()
  const storage = scriptedStorage(durable).storage
  await cleanup.startHostedRoomCleanup(storage)
  await cleanup.addHostedRoomCleanup(operation())
  await cleanup.armHostedRoomCleanup('room')
  let current = true
  Object.assign(mocks.host, {
    acquireProfileRoute: async () => ({
      route,
      generation: 1,
      release: mocks.release,
      assertCurrent: () => {
        if (!current) {
          throw new Error('route retired')
        }
      },
      request: async (method: string, params: unknown) => {
        if (method === 'groups.capabilities') {
          if (failure === 'probe-race') {
            current = false
          }

          return { driver: true, persistent_process: true, authority_gateway_id: 'install:home' }
        }

        mocks.request(method, params)

        if (failure === 'owner-replacement') {
          cleanup.stopHostedRoomCleanup()
        } else {
          throw new Error('lost acknowledgement')
        }

        return { tombstone: { room_id: 'room' } }
      }
    })
  })
  await cleanup.dispatchHostedRoomCleanup()
  expect(cleanup.hostedRoomCleanupPending('room')).toBe(true)
  expect(mocks.request).toHaveBeenCalledTimes(failure === 'probe-race' ? 0 : 1)
  expect(mocks.release).toHaveBeenCalledTimes(1)
  cleanup.stopHostedRoomCleanup()
  vi.resetModules()
  const reloaded = await import('./hosted-room-cleanup')
  current = false
  await reloaded.startHostedRoomCleanup(storage)
  expect(reloaded.hostedRoomCleanupPending('room')).toBe(true)
  reloaded.stopHostedRoomCleanup()
})

it('accepts not-found only from the matching still-current leased installation', async () => {
  const cleanup = await import('./hosted-room-cleanup')
  await cleanup.startHostedRoomCleanup(scriptedStorage(new Map()).storage)
  await cleanup.addHostedRoomCleanup(operation())
  await cleanup.armHostedRoomCleanup('room')
  mocks.request.mockImplementation(async (method: string) => {
    if (method === 'groups.capabilities') {
      return { driver: true, persistent_process: true, authority_gateway_id: 'install:home' }
    }

    throw { code: 4113, message: 'hosted room not found' }
  })
  await cleanup.dispatchHostedRoomCleanup()
  expect(cleanup.hostedRoomCleanupPending('room')).toBe(false)
  expect(mocks.release).toHaveBeenCalledTimes(1)
  cleanup.stopHostedRoomCleanup()
})

it('does not settle a newer same-room compensation with an older in-flight receipt', async () => {
  const cleanup = await import('./hosted-room-cleanup')
  const durable = new Map<string, unknown>()
  await cleanup.startHostedRoomCleanup(scriptedStorage(durable).storage)
  await cleanup.addHostedRoomCleanup(operation())
  await cleanup.armHostedRoomCleanup('room')
  mocks.request.mockImplementation(async (method: string) => {
    if (method === 'groups.capabilities') {
      return { driver: true, persistent_process: true, authority_gateway_id: 'install:home' }
    }

    await cleanup.addHostedRoomCleanup({ ...operation(), installationId: 'install:next-incarnation' })
    await cleanup.armHostedRoomCleanup('room')

    return { tombstone: { room_id: 'room' } }
  })
  await cleanup.dispatchHostedRoomCleanup()
  expect(cleanup.hostedRoomCleanupPending('room')).toBe(true)
  expect(
    (durable.get(cleanup.HOSTED_ROOM_CLEANUP_KEY) as { operations: { installationId: string }[] }).operations[0]
      .installationId
  ).toBe('install:next-incarnation')
  cleanup.stopHostedRoomCleanup()
})

it('never forwards a reconnect bearer to a replacement home even when its peer still matches', async () => {
  const cleanup = await import('./hosted-room-cleanup')
  const peer = { ...route, connectionId: 'peer' }
  Object.assign(mocks.host, {
    profileRoutes: async () => [route, peer],
    acquireProfileRoute: async (target: typeof route) => ({
      route: target,
      generation: 1,
      assertCurrent: () => undefined,
      release: mocks.release,
      request: async (method: string, params: unknown = {}) => {
        mocks.request(method, params)

        return {
          driver: true,
          persistent_process: true,
          authority_gateway_id: target.connectionId === 'peer' ? 'install:peer' : 'install:replacement'
        }
      }
    })
  })
  await cleanup.startHostedRoomCleanup(scriptedStorage(new Map()).storage)
  await cleanup.addHostedRoomCleanup({
    ...operation(),
    kind: 'peer-reconnect',
    connectionId: 'peer',
    installationId: 'install:peer',
    homeConnectionId: 'home',
    homeProfile: 'default',
    homeInstallationId: 'install:home',
    grant: 'synthetic-bearer',
    grantSha256: 'a'.repeat(64),
    memberId: 'member',
    targetUrl: 'https://fixture.invalid',
    catalog: { installation_id: 'install:peer', catalog_digest: 'digest' }
  })
  await cleanup.armHostedRoomCleanup('room')
  await cleanup.dispatchHostedRoomCleanup()
  expect(mocks.request.mock.calls.map(([method]) => method)).toEqual(['groups.capabilities', 'groups.capabilities'])
  expect(JSON.stringify(mocks.request.mock.calls)).not.toContain('synthetic-bearer')
  expect(cleanup.hostedRoomCleanupPending('room')).toBe(true)
  expect(mocks.release).toHaveBeenCalledTimes(2)
  cleanup.stopHostedRoomCleanup()
})

it('holds both leased routes until reciprocal cleanup finishes its asynchronous owner verification', async () => {
  const cleanup = await import('./hosted-room-cleanup')
  const peer = { ...route, connectionId: 'peer' }
  const mutations: string[] = []
  Object.assign(mocks.host, {
    profileRoutes: async () => [route, peer],
    acquireProfileRoute: async (target: typeof route) => {
      let released = false

      const assertCurrent = () => {
        if (released) {
          throw new Error('released route')
        }
      }

      return {
        route: target,
        generation: 1,
        assertCurrent,
        release: () => {
          released = true
          mocks.release()
        },
        request: async (method: string) => {
          assertCurrent()
          await Promise.resolve()
          assertCurrent()

          if (method === 'groups.capabilities') {
            return {
              driver: true,
              persistent_process: true,
              authority_gateway_id: `install:${target.connectionId}`,
              features: ['reciprocal_room_control_setup'],
              room_link: {
                enabled: true,
                endpoint: { available: true, url: `https://${target.connectionId}.fixture.invalid` }
              }
            }
          }

          if (method === 'groups.state') {
            return {
              room: {
                room_id: 'room',
                authority_gateway_id: 'install:home',
                authority_epoch: 1,
                members: [
                  {
                    member_id: 'member',
                    profile: 'default',
                    target: { kind: 'peer', installation_id: 'install:peer', profile: 'default' }
                  }
                ]
              }
            }
          }

          mutations.push(method)

          if (method === 'groups.control.invite') {
            return {
              room_id: 'room',
              member_id: 'member',
              authority_gateway_id: 'install:home',
              authority_epoch: 1,
              home_url: 'https://home.fixture.invalid',
              control_token: 'c'.repeat(43),
              expires_at: 253402300799
            }
          }

          return { registered: true, room_id: 'room' }
        }
      }
    }
  })
  await cleanup.startHostedRoomCleanup(scriptedStorage(new Map()).storage)
  await cleanup.addHostedRoomCleanup({
    ...operation(),
    kind: 'peer-reconnect',
    connectionId: 'peer',
    installationId: 'install:peer',
    homeConnectionId: 'home',
    homeProfile: 'default',
    homeInstallationId: 'install:home',
    grant: 'synthetic-bearer',
    grantSha256: 'a'.repeat(64),
    memberId: 'member',
    reciprocalControl: true,
    controlAuthorityId: 'install:home',
    controlAuthorityEpoch: 1,
    targetUrl: 'https://peer.fixture.invalid',
    catalog: { installation_id: 'install:peer', catalog_digest: 'digest' }
  })
  await cleanup.armHostedRoomCleanup('room')
  await cleanup.dispatchHostedRoomCleanup()
  expect(mutations).toEqual(['groups.peer.register', 'groups.control.invite', 'groups.control.register'])
  expect(cleanup.hostedRoomCleanupPending('room')).toBe(false)
  expect(mocks.release).toHaveBeenCalledTimes(2)
  cleanup.stopHostedRoomCleanup()
})

it('shows pending cleanup without replacing it with a generic creation failure', async () => {
  const { describeHostedRoomCreationError } = await import('./hosted-room-client')
  expect(
    describeHostedRoomCreationError({ cleanupPending: true, message: 'synthetic secret must not appear' })
  ).toMatch(/cleanup.*pending/i)
})

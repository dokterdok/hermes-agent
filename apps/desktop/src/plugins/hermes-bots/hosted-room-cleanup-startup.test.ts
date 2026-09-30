import { afterEach, beforeEach, expect, it, vi } from 'vitest'

// These are integration subjects, not plugin runtime dependencies.
// eslint-disable-next-line no-restricted-imports
import { createPluginContext } from '@/contrib/plugin'
// eslint-disable-next-line no-restricted-imports
import { onPersistenceEvent, type PersistenceEvent } from '@/lib/storage'

import { pluginSdkMock } from './group-test-utils'

const mocks = vi.hoisted(() => ({ host: {} as Record<string, unknown>, request: vi.fn() }))
vi.mock('@hermes/plugin-sdk', async () => pluginSdkMock(mocks.host))

const KEY = 'hosted-room-cleanup-v1'

const homeOperation = (roomId: string) => ({
  operationId: `${roomId}:home-disband`,
  setupId: roomId,
  kind: 'home-disband' as const,
  connectionId: 'home',
  roomId,
  cancelId: `rollback-${roomId}`
})

let stop: () => void = () => undefined
let unsubscribe: () => void = () => undefined

beforeEach(() => {
  vi.resetModules()
  vi.resetAllMocks()
  localStorage.clear()
  Object.assign(mocks.host, {
    state: {},
    activeConnectionId: () => 'home',
    profileRoutes: async () => [],
    requestProfile: mocks.request
  })
})

afterEach(() => {
  stop()
  unsubscribe()
  localStorage.clear()
})

// Fault only the first journal get through the real plugin storage adapter's
// persistence observation seam. Later reads and writes use the real adapter.
function failFirstJournalRead(pluginId: string) {
  const failure = new Error('cleanup journal temporarily unreadable')
  const events: PersistenceEvent[] = []
  let failed = false
  unsubscribe = onPersistenceEvent(event => {
    if (event.key !== `hermes.plugin.${pluginId}.${KEY}`) {
      return
    }

    events.push(event)

    if (event.op === 'read' && !failed) {
      failed = true
      throw failure
    }
  })

  return { events, failure }
}

it('preserves a populated journal and existing recovery state after the first adapter read rejects', async () => {
  const cleanup = await import('./hosted-room-cleanup')
  stop = cleanup.stopHostedRoomCleanup
  const pluginId = 'cleanup-startup-recovery'
  const storage = createPluginContext(pluginId).storage

  const original = cleanup.normalizeHostedRoomCleanup({
    version: 1,
    operations: [
      homeOperation('original-home'),
      ...(['peer-revoke', 'peer-revoke-exact'] as const).map(kind => ({
        operationId: `original-${kind}`,
        setupId: `original-${kind}`,
        kind,
        connectionId: 'peer',
        profile: 'builder',
        grant: `synthetic-fixture-${kind}`
      }))
    ]
  })

  storage.set(KEY, original)
  const backing = localStorage.getItem(`hermes.plugin.${pluginId}.${KEY}`)
  const existing = cleanup.normalizeHostedRoomCleanup({ operations: [homeOperation('already-in-memory')] })
  cleanup.$hostedRoomCleanup.set(existing)
  const { events, failure } = failFirstJournalRead(pluginId)

  const result = await cleanup.startHostedRoomCleanup(storage).then(
    () => null,
    error => error
  )

  expect(events.filter(event => event.op !== 'read')).toEqual([])
  expect(result).toBe(failure)
  expect(localStorage.getItem(`hermes.plugin.${pluginId}.${KEY}`)).toBe(backing)
  expect(cleanup.$hostedRoomCleanup.get()).toBe(existing)
  expect(mocks.request).not.toHaveBeenCalled()

  await cleanup.startHostedRoomCleanup(storage)
  const recovered = cleanup.$hostedRoomCleanup.get()

  const withoutLease = ({
    ownerId: _owner,
    ownerLeaseUntil: _until,
    ...operation
  }: (typeof original.operations)[number]) => operation

  expect(recovered.operations.map(withoutLease)).toEqual(original.operations.map(withoutLease))
  expect(storage.get(KEY, null)).toEqual(recovered)
  expect(recovered.operations.map(operation => operation.operationId)).toEqual(
    original.operations.map(operation => operation.operationId)
  )
})

it('blocks real autonomous setup after cleanup startup fails even when later storage I/O succeeds', async () => {
  const runtime = await import('./hosted-room-runtime')
  const cleanup = await import('./hosted-room-cleanup')
  stop = runtime.stopHostedRoomRuntime
  const pluginId = 'cleanup-startup-admission'
  const storage = createPluginContext(pluginId).storage
  storage.set(KEY, cleanup.normalizeHostedRoomCleanup({ operations: [homeOperation('original-home')] }))
  const { failure } = failFirstJournalRead(pluginId)

  const result = await runtime.startHostedRoomRuntime(storage).then(
    () => null,
    error => error
  )

  expect(result).toBe(failure)
  expect(mocks.request).not.toHaveBeenCalled()

  Object.assign(mocks.host, {
    profileRoutes: async () => [{ connectionId: 'home', profile: 'default', targetProfile: 'default', mode: 'remote' }]
  })
  mocks.request.mockImplementation(async (_route: unknown, method: string, params: Record<string, unknown>) => {
    if (method === 'groups.capabilities') {
      return { driver: true, persistent_process: true, authority_gateway_id: 'install:home' }
    }

    if (method === 'groups.create') {
      return { room: { room_id: params.room_id, authority_gateway_id: 'install:home', authority_epoch: 1 } }
    }

    if (method === 'groups.list') {
      return { rooms: [] }
    }

    return {}
  })

  const members = [
    { name: 'research', connectionId: 'home', targetProfile: 'research' },
    { name: 'builder', connectionId: 'home', targetProfile: 'builder' }
  ]

  const probe = await runtime.probeHostedRoomMembers(members)
  expect(probe.eligible).toBe(true)
  mocks.request.mockClear()
  const beforeSetup = storage.get(KEY, null)

  await expect(
    runtime.createAutonomousHostedGroupChat({
      probe,
      roomId: 'new-room',
      name: 'New room',
      members: members.map(member => ({ member, handle: member.name, profile: member.targetProfile }))
    })
  ).rejects.toThrow('cleanup')
  expect(mocks.request).not.toHaveBeenCalled()
  expect(storage.get(KEY, null)).toEqual(beforeSetup)

  // A successful recovery reopens admission; stop still permits journaling
  // late responses from an already admitted setup, as existing tests require.
  await cleanup.startHostedRoomCleanup(storage)
  await cleanup.addHostedRoomCleanup(homeOperation('recovered-admission'))
  expect(cleanup.hostedRoomCleanupPending('recovered-admission')).toBe(true)
})

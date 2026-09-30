import type { PluginContext } from '@hermes/plugin-sdk'
import { afterEach, expect, it, vi } from 'vitest'

import { pluginSdkMock, scriptedStorage } from './group-test-utils'
import type { HostedRoomCommand, HostedRoomOutbox } from './hosted-room-client'
import type { ProfileRoute } from './types'

const host = vi.hoisted(() => ({}) as Record<string, unknown>)
vi.mock('@hermes/plugin-sdk', async () => pluginSdkMock(host))

const route: ProfileRoute = {
  connectionId: 'owner',
  mode: 'remote',
  profile: 'default',
  targetProfile: 'default'
}

const capability = { driver: true, persistent_process: true, authority_gateway_id: 'install:home' }
const KEY = 'hosted-room-outbox-v1'

type Boundary = 'read' | 'routes' | 'discovery' | 'claim' | 'acquire' | 'revalidation' | 'request' | 'ack'

function barrier() {
  let release!: () => void
  let entered!: () => void
  const reached = new Promise<void>(resolve => (entered = resolve))
  const pending = new Promise<void>(resolve => (release = resolve))

  return { reached, release, wait: () => (entered(), pending) }
}

function command(commandId = 'old', overrides: Partial<HostedRoomCommand> = {}): HostedRoomCommand {
  return {
    commandId,
    authorityId: 'install:home',
    connectionId: 'owner',
    kind: 'retry',
    roomId: 'room',
    payload: { task_id: 'task' },
    status: 'pending',
    attempts: 0,
    possibleAdmission: false,
    failureCode: null,
    ...overrides
  }
}

async function load() {
  vi.resetModules()

  for (const key of Object.keys(host)) {
    delete host[key]
  }

  let routes: ProfileRoute[] = []
  let incarnation = 0
  let authorityId = 'install:home'
  let hold: { boundary: Boundary; gate: ReturnType<typeof barrier> } | null = null
  let failure: unknown = null
  const leases: Array<{ released: number; incarnation: number }> = []
  const calls: Array<{ leased: boolean; method: string; params: Record<string, unknown>; incarnation: number }> = []
  const values = new Map<string, unknown>()
  const writes: HostedRoomOutbox[] = []

  const pause = async (boundary: Boundary) => {
    if (hold?.boundary !== boundary) {
      return
    }

    const { gate } = hold
    hold = null
    await gate.wait()
  }

  // JSON round-trips at the external storage seam. All normalization, locking,
  // claim, failure, recovery and ACK transitions are the production modules.
  const storage = {
    get: async <T>(key: string, fallback?: T): Promise<T> => {
      if (key === KEY) {
        await pause('read')
      }

      return JSON.parse(JSON.stringify(values.get(key) ?? fallback ?? null)) as T
    },
    set: async (key: string, value: unknown) => {
      if (key === KEY) {
        const next = value as HostedRoomOutbox

        if (next.commands.some(entry => entry.commandId === 'old' && entry.status === 'in-flight')) {
          await pause('claim')
        } else if (!next.commands.some(entry => entry.commandId === 'old')) {
          await pause('ack')
        }

        writes.push(structuredClone(next))
      }

      values.set(key, JSON.parse(JSON.stringify(value)))
    },
    remove: (key: string) => values.delete(key)
    // The consumer deliberately awaits storage. Only this inert adapter makes
    // those boundaries controllable; the SDK's synchronous ABI is unchanged.
  } as unknown as PluginContext['storage']

  Object.assign(host, {
    activeConnectionId: () => 'owner',
    notify: vi.fn(),
    profileRoutes: async () => {
      const snapshot = [...routes]
      await pause('routes')

      return snapshot
    },
    requestProfile: async (_route: ProfileRoute, method: string, params: Record<string, unknown>) => {
      calls.push({ leased: false, method, params, incarnation })

      if (method === 'groups.capabilities') {
        const snapshot = { ...capability, authority_gateway_id: authorityId }
        await pause('discovery')

        return snapshot
      }

      throw new Error(`Unexpected unleased request: ${method}`)
    },
    acquireProfileRoute: async () => {
      const owner = incarnation
      const state = { released: 0, incarnation: owner }
      leases.push(state)

      const assertCurrent = () => {
        if (owner !== incarnation) {
          throw new Error('Retired SDK route owner')
        }
      }

      const lease = {
        assertCurrent,
        release: () => state.released++,
        request: async <T>(method: string, params: Record<string, unknown> = {}): Promise<T> => {
          assertCurrent()
          calls.push({ leased: true, method, params, incarnation: owner })

          if (method === 'groups.capabilities') {
            const snapshot = { ...capability, authority_gateway_id: authorityId }
            await pause('revalidation')

            return snapshot as T
          }

          await pause('request')

          if (failure) {
            throw failure
          }

          return {
            retried: true,
            task: { room_id: params.room_id, task_id: params.task_id, execution_generation: 2 }
          } as T
        }
      }

      await pause('acquire')

      return lease
    },
    state: {
      connectionId: { get: () => 'owner', listen: () => () => undefined },
      gateway: { get: () => 'open', listen: () => () => undefined },
      profile: { get: () => 'default', listen: () => () => undefined }
    }
  })

  const [runtime, outbox, chat, shared] = await Promise.all([
    import('./hosted-room-runtime'),
    import('./hosted-room-outbox'),
    import('./group-chat'),
    import('./shared')
  ])

  shared.setPluginCtx({ storage } as PluginContext)
  await runtime.startHostedRoomRuntime(storage)
  routes = [route]
  chat.$groupChats.set({
    Room: {
      roomId: 'room',
      hosted: 'install:home',
      hostedConnectionId: 'owner',
      continuityMode: 'gateway',
      members: [],
      log: [],
      watermarks: {}
    }
  })

  return {
    runtime,
    outbox,
    storage,
    values,
    writes,
    leases,
    calls,
    mutations: () => calls.filter(call => call.method !== 'groups.capabilities'),
    hold: (boundary: Boundary) => {
      const gate = barrier()
      hold = { boundary, gate }

      return gate
    },
    replace: (authority = 'install:replacement') => {
      incarnation++
      authorityId = authority
    },
    fail: (error: unknown) => (failure = error),
    noRoutes: () => (routes = []),
    seed: async (...commands: HostedRoomCommand[]) => {
      for (const entry of commands) {
        await outbox.mutateHostedRoomOutbox(storage, { type: 'enqueue', command: entry })
      }

      runtime.$hostedRoomOutbox.set(await outbox.readHostedRoomOutbox(storage))
    }
  }
}

let active: Awaited<ReturnType<typeof load>> | null = null
afterEach(() => {
  active?.runtime.stopHostedRoomRuntime()
  active = null
  vi.clearAllTimers()
})

it.each(['discovery', 'claim', 'revalidation'] as const)(
  'revalidates the actual outbox claim after route replacement during %s and preserves later input',
  async boundary => {
    const loaded = (active = await load())
    await loaded.seed(command(), command('later'))
    const gate = loaded.hold(boundary)
    const dispatch = loaded.runtime.dispatchHostedRoomOutbox()
    await gate.reached
    loaded.replace()
    gate.release()
    await dispatch

    expect(loaded.mutations()).toEqual([])
    expect(loaded.leases.every(lease => lease.released === 1)).toBe(true)
    const saved = await loaded.outbox.readHostedRoomOutbox(loaded.storage)
    expect(saved.commands.map(entry => entry.commandId)).toEqual(['old', 'later'])
    expect(saved.commands[1]).toEqual(command('later'))
    expect(loaded.runtime.$hostedRoomOutbox.get()).toEqual(saved)

    // A lease loss is conservatively unknown for unkeyed Retry; a fresh
    // independent command must progress without releasing its blocked tail.
    loaded.replace('install:home')

    if (boundary === 'revalidation') {
      await loaded.seed(command('fresh', { roomId: 'other-room', payload: { task_id: 'fresh-task' } }))
      await loaded.runtime.dispatchHostedRoomOutbox()
      expect((await loaded.outbox.readHostedRoomOutbox(loaded.storage)).commands).toEqual(saved.commands)
      expect(loaded.mutations().map(call => call.params.task_id)).toEqual(['fresh-task'])
    } else {
      await loaded.outbox.mutateHostedRoomOutbox(loaded.storage, { type: 'retry', commandId: 'old' })
      await loaded.runtime.dispatchHostedRoomOutbox()
      expect((await loaded.outbox.readHostedRoomOutbox(loaded.storage)).commands).toEqual([])
      expect(loaded.mutations().map(call => call.params.task_id)).toEqual(['task', 'task'])
    }
  }
)

it.each(['read', 'routes', 'discovery', 'claim', 'acquire', 'revalidation', 'request'] as const)(
  'does not admit additional work from a disposed outbox lifetime held at %s',
  async boundary => {
    const loaded = (active = await load())
    await loaded.seed(command(), command('later'))
    const gate = loaded.hold(boundary)
    const dispatch = loaded.runtime.dispatchHostedRoomOutbox().catch(error => error)
    await gate.reached
    loaded.runtime.stopHostedRoomRuntime()

    if (boundary === 'request') {
      loaded.fail(new Error('response lost after disposal'))
    }

    gate.release()
    await dispatch

    expect(loaded.mutations()).toHaveLength(boundary === 'request' ? 1 : 0)
    expect(loaded.leases.every(lease => lease.released === 1)).toBe(true)
    const saved = await loaded.outbox.readHostedRoomOutbox(loaded.storage)
    expect(saved.commands.map(entry => entry.commandId)).toEqual(['old', 'later'])
    expect(saved.commands[1]).toEqual(command('later'))
    expect(saved.commands[0].status).toBe(
      ['claim', 'acquire', 'revalidation', 'request'].includes(boundary) ? 'in-flight' : 'pending'
    )
  }
)

it.each(['read', 'routes', 'discovery', 'claim', 'acquire', 'revalidation', 'request', 'ack'] as const)(
  'does not let an old %s continuation publish or mutate the restarted storage owner',
  async boundary => {
    const loaded = (active = await load())
    await loaded.seed(command(), command('later'))
    const gate = loaded.hold(boundary)
    const dispatch = loaded.runtime.dispatchHostedRoomOutbox().catch(error => error)
    await gate.reached
    loaded.runtime.stopHostedRoomRuntime()
    loaded.noRoutes()
    const nextValues = new Map<string, unknown>([[KEY, { version: 1, commands: [command('successor')] }]])
    const nextStorage = scriptedStorage(nextValues).storage
    const restart = loaded.runtime.startHostedRoomRuntime(nextStorage)
    const publications: string[][] = []

    const unlisten = loaded.runtime.$hostedRoomOutbox.listen(state => {
      publications.push(state.commands.map(entry => entry.commandId))
    })

    // An ACK already owns the real mutation lock, so a restart cannot publish
    // its initial storage snapshot until that old write settles.
    if (!['read', 'claim', 'ack'].includes(boundary)) {
      await vi.waitFor(() => expect(loaded.runtime.$hostedRoomOutbox.get().commands[0]?.commandId).toBe('successor'))
    }

    gate.release()
    await dispatch
    await restart
    unlisten()

    expect(publications.every(ids => ids.every(id => id === 'successor'))).toBe(true)
    expect(nextValues.get(KEY)).toEqual({ version: 1, commands: [command('successor')] })
    expect(loaded.runtime.$hostedRoomOutbox.get()).toEqual(await loaded.outbox.readHostedRoomOutbox(nextStorage))
    expect(loaded.leases.every(lease => lease.released === 1)).toBe(true)
    expect(loaded.mutations()).toHaveLength(['request', 'ack'].includes(boundary) ? 1 : 0)
    const old = await loaded.outbox.readHostedRoomOutbox(loaded.storage)
    expect(old.commands.map(entry => entry.commandId)).toEqual(boundary === 'ack' ? ['later'] : ['old', 'later'])

    if (boundary !== 'ack') {
      expect(old.commands[0].status).toBe(['read', 'routes', 'discovery'].includes(boundary) ? 'pending' : 'in-flight')
    }
  }
)

it.each(['replacement', 'failure'] as const)(
  'retains possible admission and later commands after an uncertain %s, without replaying Retry',
  async outcome => {
    const loaded = (active = await load())
    await loaded.seed(command())
    const gate = loaded.hold('request')
    const dispatch = loaded.runtime.dispatchHostedRoomOutbox()
    await gate.reached
    await loaded.seed(command('later'))

    if (outcome === 'replacement') {
      loaded.replace()
    } else {
      loaded.fail(new Error('response lost after possible remote admission'))
    }

    gate.release()
    await dispatch

    const saved = await loaded.outbox.readHostedRoomOutbox(loaded.storage)
    expect(saved.commands[0]).toMatchObject({
      commandId: 'old',
      status: 'unknown',
      possibleAdmission: true,
      attempts: 1
    })
    expect(saved.commands[1]).toEqual(command('later'))
    expect(loaded.leases[0].released).toBe(1)
    expect(loaded.mutations()).toHaveLength(1)
    await loaded.outbox.recoverHostedRoomOutbox(loaded.storage)
    await loaded.runtime.dispatchHostedRoomOutbox()
    expect(loaded.mutations()).toHaveLength(1)
    expect(loaded.runtime.$hostedRoomOutbox.get()).toEqual(saved)
  }
)

it.each(['success', 'rejection'] as const)('does not let a late %s Retry retire uncertain work after restarting the same storage object', async outcome => {
  const loaded = (active = await load())
  await loaded.seed(command())
  const gate = loaded.hold('request')
  const dispatch = loaded.runtime.dispatchHostedRoomOutbox()
  await gate.reached
  await loaded.seed(command('later'))
  loaded.runtime.stopHostedRoomRuntime()
  loaded.noRoutes()
  const restart = loaded.runtime.startHostedRoomRuntime(loaded.storage)
  await vi.waitFor(() => expect(loaded.runtime.$hostedRoomOutbox.get().commands[0]?.status).toBe('unknown'))

  if (outcome === 'rejection') {
    loaded.fail({ code: -32602, message: 'late refusal from retired owner' })
  }

  gate.release()
  await dispatch
  await restart

  const saved = await loaded.outbox.readHostedRoomOutbox(loaded.storage)
  expect(saved.commands[0]).toMatchObject({ commandId: 'old', status: 'unknown', possibleAdmission: true, attempts: 1 })
  expect(saved.commands[1]).toEqual(command('later'))
  expect(loaded.runtime.$hostedRoomOutbox.get()).toEqual(saved)
  expect(loaded.mutations()).toHaveLength(1)
  expect(loaded.leases[0].released).toBe(1)
})

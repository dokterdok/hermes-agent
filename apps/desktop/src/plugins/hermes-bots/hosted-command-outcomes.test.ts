import { afterEach, expect, it, vi } from 'vitest'

import { pluginSdkMock, scriptedStorage } from './group-test-utils'
import type { HostedRoomCommand } from './hosted-room-client'

const { host } = vi.hoisted(() => ({ host: {} as Record<string, unknown> }))
vi.mock('@hermes/plugin-sdk', async () => pluginSdkMock(host))

async function setup(
  command: Partial<HostedRoomCommand>,
  dispatch: (method: string, params: Record<string, unknown>) => unknown
) {
  vi.resetModules()
  vi.useFakeTimers()
  const calls: Array<{ method: string; params: Record<string, unknown> }> = []
  Object.assign(host, {
    notify: vi.fn(),
    activeConnectionId: () => 'owner',
    profileRoutes: async () => [
      { connectionId: 'owner', profile: 'default', targetProfile: 'default', mode: 'remote' }
    ],
    requestProfile: async (_route: unknown, method: string, params: Record<string, unknown>) => {
      if (method === 'groups.capabilities') {
        return { driver: true, persistent_process: true, authority_gateway_id: 'install:home' }
      }

      if (method === 'groups.list') {
        return { rooms: [] }
      }

      if (method === 'groups.state') {
        throw new Error('read temporarily unavailable')
      }

      calls.push({ method, params })

      return dispatch(method, params)
    },
    acquireProfileRoute: async (route: unknown) => ({
      assertCurrent: () => undefined,
      release: () => undefined,
      request: <T>(method: string, params: Record<string, unknown> = {}) =>
        (host.requestProfile as (route: unknown, method: string, params: Record<string, unknown>) => Promise<T>)(route, method, params)
    }),
    state: {
      connectionId: { get: () => 'owner', listen: () => () => undefined },
      gateway: { get: () => 'open', listen: () => () => undefined },
      profile: { get: () => 'default', listen: () => () => undefined }
    }
  })
  const values = new Map<string, unknown>()
  values.set('hosted-room-outbox-v1', {
    version: 1,
    commands: [
      {
        commandId: 'intent',
        roomId: 'room',
        connectionId: 'owner',
        authorityId: 'install:home',
        kind: 'retry',
        status: 'pending',
        attempts: 0,
        payload: { task_id: 'task' },
        ...command
      }
    ]
  })

  const [runtime, chat, rounds, shared] = await Promise.all([
    import('./hosted-room-runtime'),
    import('./group-chat'),
    import('./group-rounds'),
    import('./shared')
  ])

  const ctx = scriptedStorage(values)
  shared.setPluginCtx(ctx)
  chat.$groupChats.set({
    Room: {
      roomId: 'room',
      hosted: 'install:home',
      hostedConnectionId: 'owner',
      log: [],
      watermarks: {},
      running: true
    }
  })
  await runtime.startHostedRoomRuntime(ctx.storage)

  return { runtime, chat, rounds, values, calls, storage: ctx.storage }
}

afterEach(async () => {
  const runtime = await import('./hosted-room-runtime')
  runtime.stopHostedRoomRuntime()
  vi.clearAllTimers()
  vi.useRealTimers()
})

it('sends the older Retry schema exactly and never replays an uncertain execution after reload', async () => {
  const loaded = await setup({}, () => {
    throw new Error('ACK lost after accepted Retry')
  })

  expect(loaded.calls).toEqual([{ method: 'groups.retry', params: { room_id: 'room', task_id: 'task' } }])
  await loaded.runtime.dispatchHostedRoomOutbox()
  loaded.runtime.stopHostedRoomRuntime()
  await loaded.runtime.startHostedRoomRuntime(loaded.storage)
  await loaded.runtime.retryFailedHostedRoomCommand('Room', 'intent')
  expect(loaded.calls).toHaveLength(1)
  expect(loaded.values.get('hosted-room-outbox-v1')).toMatchObject({
    commands: [{ commandId: 'intent', status: 'unknown' }]
  })
  expect(loaded.chat.$groupChats.get().Room.hostedStatus).toMatchObject({ state: 'indeterminate', canRetry: false })
})

it.each(['queued', 'settled', 'indeterminate-generation-2'] as const)(
  'does not repeat a lost-ACK Retry when the supplier later reports %s',
  async state => {
    const loaded = await setup({}, () => {
      throw new Error('ACK lost after accepted Retry')
    })

    const observed = state === 'indeterminate-generation-2'
      ? { status: 'indeterminate', execution_generation: 2 }
      : { status: state, execution_generation: 1 }

    const request = host.requestProfile as (...args: unknown[]) => Promise<unknown>

    host.requestProfile = async (...args: unknown[]) => {
      if (args[1] === 'groups.state') {
        return { room: { room_id: 'room', name: 'Room', authority_gateway_id: 'install:home', authority_epoch: 1 },
          runtime: { running: true, counts: { [observed.status]: 1 }, pending_actions: [{ kind: 'retry', task_id: 'task' }] },
          ...observed }
      }

      return request(...args)
    }

    await loaded.runtime.refreshHostedRooms()
    await loaded.runtime.dispatchHostedRoomOutbox()
    loaded.runtime.stopHostedRoomRuntime()
    await loaded.runtime.startHostedRoomRuntime(loaded.storage)
    await loaded.runtime.retryFailedHostedRoomCommand('Room', 'intent')
    // Dispatch count is the causal oracle before any new presentation/control API.
    expect(loaded.calls).toHaveLength(1)
    expect(await loaded.runtime.retryHostedGroupChat('Room', 'task')).toBe(false)
    expect(loaded.calls).toEqual([{ method: 'groups.retry', params: { room_id: 'room', task_id: 'task' } }])
    expect(loaded.values.get('hosted-room-outbox-v1')).toMatchObject({ commands: [{ commandId: 'intent', status: 'unknown' }] })
  }
)

it('retains a recovered in-flight Retry without submitting it again', async () => {
  const loaded = await setup({ status: 'in-flight', attempts: 1 }, () => ({ retried: true }))
  expect(loaded.calls).toEqual([])
  expect(loaded.values.get('hosted-room-outbox-v1')).toMatchObject({ commands: [{ status: 'unknown' }] })
})

it('bounds unknown destructive outcomes without reporting rejection or losing intent', async () => {
  const loaded = await setup({ kind: 'disband', payload: {} }, () => {
    throw new Error('lost disband ACK')
  })

  for (let i = 0; i < 8; i++) {
    await loaded.runtime.dispatchHostedRoomOutbox()
  }

  expect(loaded.calls).toHaveLength(1)
  expect(loaded.values.get('hosted-room-outbox-v1')).toMatchObject({
    commands: [{ status: 'unknown', kind: 'disband' }]
  })
  expect(loaded.chat.$groupChats.get().Room.hostedStatus?.state).toBe('indeterminate')
})

it.each(['stop', 'disband'] as const)('does not accept a malformed %s reply as success', async kind => {
  const loaded = await setup({ kind }, () => ({ acknowledged: true }))
  expect(loaded.values.get('hosted-room-outbox-v1')).toMatchObject({ commands: [{ status: 'unknown' }] })
  expect(loaded.chat.$groupChats.get().Room.tombstone).not.toBe(true)
})

it('retains a retry with a lost ACK across fresh storage rehydration without dispatching again', async () => {
  const loaded = await setup({}, () => {
    throw new Error('Lost ACK')
  })

  const persisted = new Map<string, unknown>(JSON.parse(JSON.stringify([...loaded.values])))
  loaded.runtime.stopHostedRoomRuntime()
  vi.resetModules()
  const ctx = scriptedStorage(persisted)
  const shared = await import('./shared')
  shared.setPluginCtx(ctx)
  const runtime = await import('./hosted-room-runtime')
  await runtime.startHostedRoomRuntime(ctx.storage)
  expect(persisted.get('hosted-room-outbox-v1')).toMatchObject({ commands: [{ status: 'unknown' }] })
  expect(loaded.calls).toHaveLength(1)
})
it('refuses to skip or freshly retry unknown non-idempotent work', async () => {
  const loaded = await setup({}, () => {
    throw new Error('lost retry acknowledgement')
  })

  const outbox = await import('./hosted-room-outbox')
  await outbox.mutateHostedRoomOutbox(loaded.storage, { type: 'dismiss', commandId: 'intent' })
  expect(loaded.values.get('hosted-room-outbox-v1')).toMatchObject({ commands: [{ status: 'unknown' }] })
  expect(await loaded.runtime.retryHostedGroupChat('Room', 'task')).toBe(false)
  expect(loaded.calls).toHaveLength(1)
})

it('retains a malformed Retry receipt as unknown rather than retiring the intent', async () => {
  const loaded = await setup({}, () => ({
    retried: true,
    task: { room_id: 'foreign', task_id: 'task', execution_generation: 2 }
  }))

  expect(loaded.values.get('hosted-room-outbox-v1')).toMatchObject({ commands: [{ status: 'unknown' }] })
  await loaded.runtime.dispatchHostedRoomOutbox()
  expect(loaded.calls).toHaveLength(1)
})

it('accepts the matching older Retry receipt without requiring invented deduplication fields', async () => {
  const loaded = await setup({}, () => ({
    retried: true,
    task: {
      room_id: 'room',
      task_id: 'task',
      thread_id: 'thread',
      turn_id: 'turn',
      status: 'queued',
      execution_generation: 2,
      cancel_generation: 0
    }
  }))

  expect(loaded.values.get('hosted-room-outbox-v1')).toMatchObject({ commands: [] })
  expect(loaded.calls).toEqual([{ method: 'groups.retry', params: { room_id: 'room', task_id: 'task' } }])
})
it.each(
  ['stop', 'disband', 'retry'].flatMap(kind =>
    [null, '4123'].map(failureCode => ({ kind: kind as HostedRoomCommand['kind'], failureCode }))
  )
)(
  'holds attempted legacy pending $kind ($failureCode) without a complete refusal history',
  async ({ kind, failureCode }) => {
    const loaded = await setup({ kind, attempts: 2, status: 'pending', failureCode }, () => ({ cancelled: 1 }))
    expect(loaded.calls).toEqual([])
    expect(loaded.values.get('hosted-room-outbox-v1')).toMatchObject({
      commands: [{ kind, status: 'unknown', payload: { task_id: 'task' } }]
    })
  }
)

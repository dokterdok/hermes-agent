import type { PluginContext } from '@hermes/plugin-sdk'
import { afterEach, expect, it, vi } from 'vitest'

import { host } from '@/sdk'
import {
  closeSecondaryGateways,
  configureGatewayRegistry,
  disposeSecondariesForConnection,
  setPrimaryGateway,
  setPrimaryGatewayConnection
} from '@/store/gateway'

import type * as Runtime from '../plugins/hermes-bots/hosted-room-runtime'
import type { GroupChat, GroupMessage } from '../plugins/hermes-bots/types'

// Actual producer -> SDK -> registry; only native discovery and decoded wire
// responses are inert. No gateway, socket server, provider or native launch.
const wire = vi.hoisted(() => ({
  installation: 'install:home',
  sockets: 0,
  canonical: false,
  uploadFailure: '' as '' | 'transport' | 'receipt',
  pause: null as null | { method: string; index: number; entered: () => void; pending: Promise<void> },
  calls: [] as Array<{ installation: string; socket: number; method: string; params: Record<string, unknown> }>
}))

vi.mock('@/hermes', async importActual => ({
  ...(await importActual<Record<string, unknown>>()),
  HermesGateway: class {
    connectionState = 'closed'
    installation = ''
    socket = 0
    onEvent = () => () => undefined
    onState = () => () => undefined
    close = () => (this.connectionState = 'closed')
    connect = async () => {
      this.installation = wire.installation
      this.socket = ++wire.sockets
      this.connectionState = 'open'
    }

    request = async <T>(method: string, params: Record<string, unknown> = {}): Promise<T> => {
      wire.calls.push({ installation: this.installation, socket: this.socket, method, params: structuredClone(params) })
      const index = wire.calls.filter(call => call.method === method).length

      if (wire.pause?.method === method && wire.pause.index === index) {
        const gate = wire.pause
        wire.pause = null
        gate.entered()
        await gate.pending
      }

      if (method === 'groups.capabilities') {
        return {
          driver: true,
          persistent_process: true,
          authority_gateway_id: this.installation,
          methods: ['groups.attachment.put', 'groups.attachment.read'],
          features: wire.canonical ? ['canonical_session_owner'] : []
        } as T
      }

      if (method === 'groups.list') {
        return { rooms: [] } as T
      }

      if (method === 'groups.attachment.put') {
        if (wire.uploadFailure === 'transport') {
          throw new Error('Inert upload transport failed')
        }

        return {
          attachment: {
            attachment_id: `att_${(params.name === 'first.png' ? 'a' : 'b').repeat(32)}`,
            kind: params.kind,
            mime: params.mime,
            name: params.name,
            size: wire.uploadFailure === 'receipt' ? 2 : 1
          }
        } as T
      }

      if (method === 'groups.send') {
        return {
          accepted: true,
          client_event_id: params.event_id,
          event: { room_id: params.room_id, kind: 'message.user' }
        } as T
      }

      throw new Error(`Unexpected wire method: ${method}`)
    }
  }
}))

function barrier() {
  let entered!: () => void
  let release!: () => void
  const reached = new Promise<void>(resolve => (entered = resolve))
  const pending = new Promise<void>(resolve => (release = resolve))

  return { reached, pending, entered, release }
}

const route = { connectionId: 'background', profile: 'default', targetProfile: 'default', mode: 'remote' as const }
const KEY = 'hosted-room-outbox-v1'
let runtime: typeof Runtime | null = null
const originalDesktop = window.hermesDesktop

function storageFixture() {
  const values = new Map<string, unknown>()

  const storage = {
    get: <T>(key: string, fallback?: T) => structuredClone(values.get(key) ?? fallback ?? null) as T,
    set: async (key: string, value: unknown) => {
      values.set(key, structuredClone(value))
    },
    remove: (key: string) => values.delete(key)
  } as unknown as PluginContext['storage']

  return { storage, values }
}

async function setup() {
  vi.useFakeTimers()
  wire.installation = 'install:home'
  wire.sockets = 0
  wire.canonical = false
  wire.uploadFailure = ''
  wire.calls = []
  wire.pause = null
  configureGatewayRegistry({ onEvent: vi.fn() })
  closeSecondaryGateways()
  setPrimaryGateway({ connectionState: 'open', request: vi.fn() } as never, 'default')
  setPrimaryGatewayConnection({ connectionId: 'foreground' })
  window.hermesDesktop = {
    getConnectionFor: vi.fn(async () => ({ port: 5151, profile: 'default', sharedRemote: false })),
    getGatewayWsUrlFor: vi.fn(async () => ({ ok: true, wsUrl: 'ws://inert.invalid' }))
  } as unknown as typeof window.hermesDesktop
  const acquired: Array<{ release: ReturnType<typeof vi.fn> }> = []
  const acquire = host.acquireProfileRoute
  vi.spyOn(host, 'acquireProfileRoute').mockImplementation(async input => {
    const lease = await acquire(input)
    const release = vi.fn(lease.release)
    acquired.push({ release })

    return { ...lease, release }
  })
  let routes: (typeof route)[] = []
  vi.spyOn(host, 'profileRoutes').mockImplementation(async () => routes)
  const saved = storageFixture()
  runtime = await import('../plugins/hermes-bots/hosted-room-runtime')
  runtime.resetHostedRoomRuntimeForTests()
  const chat = await import('../plugins/hermes-bots/group-chat')
  chat.$groupChats.set({})
  await runtime.startHostedRoomRuntime(saved.storage)
  routes = [route]

  const room: GroupChat = {
    continuityMode: 'gateway',
    hosted: 'install:home',
    hostedConnectionId: 'background',
    roomId: 'room',
    hostedEpoch: 1,
    hostedSeq: 0,
    watermarks: {},
    log: [],
    members: [
      { connectionId: 'background', name: 'worker', sourceScoped: true, targetProfile: 'worker' },
      { connectionId: 'background', name: 'builder', sourceScoped: true, targetProfile: 'builder' }
    ]
  }

  chat.$groupChats.set({ Release: room })

  const message: GroupMessage = {
    id: 'private-send',
    at: 1,
    from: { kind: 'user', name: 'You' },
    text: 'private text',
    thread: 'thread',
    images: [
      { data: 'data:image/png;base64,YQ==', kind: 'image', name: 'first.png' },
      { data: 'data:image/png;base64,Yg==', kind: 'image', name: 'second.png' }
    ]
  }

  return { ...saved, chat, runtime, room, message, acquired }
}

function pause(method: string, index: number) {
  const gate = barrier()
  wire.pause = { method, index, entered: gate.entered, pending: gate.pending }

  return gate
}

function replace(installation = 'install:replacement') {
  disposeSecondariesForConnection('background', { redial: true })
  disposeSecondariesForConnection('background')
  wire.installation = installation
}

function uploads() {
  return wire.calls.filter(call => call.method === 'groups.attachment.put')
}

async function savedCommands(storage: PluginContext['storage']) {
  const outbox = await import('../plugins/hermes-bots/hosted-room-outbox')

  return (await outbox.readHostedRoomOutbox(storage)).commands
}

afterEach(() => {
  runtime?.resetHostedRoomRuntimeForTests()
  closeSecondaryGateways()
  setPrimaryGateway(null)
  vi.restoreAllMocks()
  vi.clearAllTimers()
  vi.useRealTimers()
  window.hermesDesktop = originalDesktop
})

it.each(['discovery', 'revalidation', 'parity', 'upload', 'last-upload'] as const)(
  'fences raw bytes and late manifests on actual registry replacement at %s',
  async boundary => {
    const loaded = await setup()

    const gate = boundary.includes('upload')
      ? pause('groups.attachment.put', boundary === 'last-upload' ? 2 : 1)
      : pause('groups.capabilities', boundary === 'discovery' ? 1 : boundary === 'revalidation' ? 2 : 3)

    const send = loaded.runtime.sendHostedGroupChat('Release', loaded.message, 'thread').then(
      result => ({ result, error: null }),
      error => ({ result: null, error })
    )

    await gate.reached
    replace()
    gate.release()
    const outcome = await send
    expect(uploads().filter(call => call.installation !== 'install:home')).toEqual([])
    expect(uploads()).toHaveLength(boundary === 'last-upload' ? 2 : boundary === 'upload' ? 1 : 0)
    expect(loaded.acquired.every(lease => lease.release.mock.calls.length === 1)).toBe(true)
    expect(outcome.error).toBeInstanceOf(Error)
    expect(await savedCommands(loaded.storage)).toEqual([])
    expect(loaded.chat.$groupChats.get().Release.log).toEqual([])
    expect(loaded.message.images?.map(attachment => attachment.data)).toEqual([
      'data:image/png;base64,YQ==',
      'data:image/png;base64,Yg=='
    ])
  }
)

it('stages an unchanged-owner multipart send on one physical socket and durably sends references only', async () => {
  const loaded = await setup()
  await expect(loaded.runtime.queueHostedGroupChat('Release', loaded.message, 'thread')).resolves.toBe(true)
  expect(uploads().map(call => call.params.content_base64)).toEqual(['YQ==', 'Yg=='])
  expect(new Set(uploads().map(call => call.socket)).size).toBe(1)
  const sends = wire.calls.filter(call => call.method === 'groups.send')
  expect(sends).toHaveLength(1)
  expect(sends[0].params).toMatchObject({
    room_id: 'room',
    event_id: 'private-send',
    payload: {
      text: 'private text',
      thread_id: 'thread',
      attachments: [
        { attachment_id: `att_${'a'.repeat(32)}`, name: 'first.png', size: 1 },
        { attachment_id: `att_${'b'.repeat(32)}`, name: 'second.png', size: 1 }
      ]
    }
  })
  expect(JSON.stringify(sends[0].params)).not.toContain('content_base64')
  expect(await savedCommands(loaded.storage)).toEqual([])
  expect(loaded.acquired.every(lease => lease.release.mock.calls.length === 1)).toBe(true)
})

it.each(['discovery', 'revalidation', 'parity', 'upload'] as const)(
  'rejects same-ID same-installation physical ABA at %s and preserves upload identity for fresh retry',
  async boundary => {
    const loaded = await setup()

    const gate =
      boundary === 'upload'
        ? pause('groups.attachment.put', 1)
        : pause('groups.capabilities', boundary === 'discovery' ? 1 : boundary === 'revalidation' ? 2 : 3)

    const send = loaded.runtime.queueHostedGroupChat('Release', loaded.message, 'thread').catch(error => error)
    await gate.reached
    replace('install:home')
    gate.release()
    expect(await send).toBeInstanceOf(Error)
    const firstUploads = uploads()
    expect(firstUploads).toHaveLength(boundary === 'upload' ? 1 : 0)
    expect(await savedCommands(loaded.storage)).toEqual([])
    await expect(loaded.runtime.queueHostedGroupChat('Release', loaded.message, 'thread')).resolves.toBe(true)

    if (firstUploads.length) {
      expect(uploads()[1].params.upload_id).toBe(firstUploads[0].params.upload_id)
      expect(uploads()[1].params.content_base64).toBe(firstUploads[0].params.content_base64)
      expect(uploads()[1].socket).not.toBe(firstUploads[0].socket)
    }

    expect(wire.calls.filter(call => call.method === 'groups.send')).toHaveLength(1)
    expect(loaded.acquired.every(lease => lease.release.mock.calls.length === 1)).toBe(true)
  }
)

it('does not learn a new owning installation from a reused descriptor or catalog', async () => {
  const loaded = await setup()
  replace()
  await expect(loaded.runtime.queueHostedGroupChat('Release', loaded.message, 'thread')).rejects.toThrow()
  expect(uploads()).toEqual([])
  expect(await savedCommands(loaded.storage)).toEqual([])
  expect(loaded.room.hosted).toBe('install:home')
  expect(loaded.acquired.every(lease => lease.release.mock.calls.length === 1)).toBe(true)
})

it('rejects canonical-only ownership before raw upload rather than relying on the later command gate', async () => {
  const loaded = await setup()
  wire.canonical = true
  await expect(loaded.runtime.queueHostedGroupChat('Release', loaded.message, 'thread')).rejects.toThrow()
  expect(uploads()).toEqual([])
  expect(await savedCommands(loaded.storage)).toEqual([])
  expect(loaded.acquired.every(lease => lease.release.mock.calls.length === 1)).toBe(true)
})

it.each(['unsupported-sdk', 'missing-installation'] as const)(
  'fails closed on %s with bytes and room retained',
  async boundary => {
    const loaded = await setup()

    if (boundary === 'unsupported-sdk') {
      Object.assign(host, { acquireProfileRoute: undefined })
    } else {
      loaded.chat.$groupChats.set({ Release: { ...loaded.room, hosted: '' } })
    }

    const before = structuredClone(loaded.chat.$groupChats.get().Release)
    await expect(loaded.runtime.queueHostedGroupChat('Release', loaded.message, 'thread')).rejects.toThrow()
    expect(uploads()).toEqual([])
    expect(loaded.chat.$groupChats.get().Release).toEqual(before)
    expect(loaded.message.images?.[0].data).toBe('data:image/png;base64,YQ==')
  }
)

it.each(['parity', 'upload', 'last-upload'] as const)(
  'retires staging across runtime Stop-start at %s without adopting the new storage',
  async boundary => {
    const loaded = await setup()

    const gate =
      boundary === 'parity'
        ? pause('groups.capabilities', 3)
        : pause('groups.attachment.put', boundary === 'last-upload' ? 2 : 1)

    const send = loaded.runtime.queueHostedGroupChat('Release', loaded.message, 'thread').catch(error => error)
    await gate.reached
    const replacement = storageFixture()
    loaded.runtime.stopHostedRoomRuntime()
    loaded.chat.$groupChats.set({})
    const inventory = vi.spyOn(host, 'profileRoutes').mockResolvedValue([])
    await loaded.runtime.startHostedRoomRuntime(replacement.storage)
    loaded.chat.$groupChats.set({ Release: loaded.room })
    inventory.mockResolvedValue([route])
    gate.release()
    expect(await send).toBeInstanceOf(Error)
    expect(await savedCommands(loaded.storage)).toEqual([])
    expect(await savedCommands(replacement.storage)).toEqual([])
    expect(uploads()).toHaveLength(boundary === 'last-upload' ? 2 : boundary === 'upload' ? 1 : 0)
    expect(wire.calls.some(call => call.method === 'groups.send')).toBe(false)
    expect(loaded.acquired.every(lease => lease.release.mock.calls.length === 1)).toBe(true)
  }
)

it.each(['new-room', 'logical-aba'] as const)('does not clobber %s on a late upload response', async boundary => {
  const loaded = await setup()
  const gate = pause('groups.attachment.put', 2)
  const send = loaded.runtime.queueHostedGroupChat('Release', loaded.message, 'thread').catch(error => error)
  await gate.reached

  const retained = {
    commandId: 'retained',
    roomId: 'unrelated-room',
    connectionId: 'background',
    authorityId: 'install:home',
    kind: 'send',
    status: 'failed',
    attempts: 5,
    failureCode: 'authority-unavailable',
    possibleAdmission: false,
    payload: { text: 'retained intent', thread_id: 'retained-thread' }
  }

  loaded.values.set(KEY, { version: 1, commands: [retained] })

  const replacement: GroupChat = {
    ...loaded.room,
    roomId: boundary === 'new-room' ? 'replacement-room' : 'room',
    log: [{ id: 'fresh', at: 2, from: { kind: 'user', name: 'You' }, text: 'fresh intent', thread: 'fresh-thread' }],
    continuityIssue: 'fresh guidance'
  }

  loaded.chat.$groupChats.set({})
  loaded.chat.$groupChats.set({ Release: replacement })
  gate.release()
  expect(await send).toBeInstanceOf(Error)
  expect(loaded.chat.$groupChats.get().Release).toBe(replacement)
  expect(await savedCommands(loaded.storage)).toEqual([retained])
  expect(wire.calls.some(call => call.method === 'groups.send')).toBe(false)
  expect(loaded.acquired.every(lease => lease.release.mock.calls.length === 1)).toBe(true)
})

it.each(['physical-aba', 'runtime-stop'] as const)(
  'fences %s while actual lease acquisition is awaiting return',
  async boundary => {
    const loaded = await setup()
    const gate = barrier()
    const acquire = host.acquireProfileRoute
    vi.spyOn(host, 'acquireProfileRoute').mockImplementationOnce(async input => {
      const lease = await acquire(input)
      gate.entered()
      await gate.pending

      return lease
    })
    const send = loaded.runtime.queueHostedGroupChat('Release', loaded.message, 'thread').catch(error => error)
    await gate.reached

    if (boundary === 'physical-aba') {
      replace('install:home')
    } else {
      loaded.runtime.stopHostedRoomRuntime()
    }

    gate.release()
    expect(await send).toBeInstanceOf(Error)
    expect(uploads()).toEqual([])
    expect(await savedCommands(loaded.storage)).toEqual([])
    expect(loaded.acquired).toHaveLength(1)
    expect(loaded.acquired[0].release).toHaveBeenCalledTimes(1)
  }
)

it('keeps the real durable composer caller unpainted after a retired upload and supports a fresh safe retry', async () => {
  const loaded = await setup()
  const rounds = await import('../plugins/hermes-bots/group-rounds')
  const gate = pause('groups.attachment.put', 1)

  const send = rounds
    .sendToGroupChatDurably('Release', loaded.room.members || [], 'private text', 'thread', loaded.message.images)
    .catch(error => error)

  await gate.reached
  replace('install:home')
  gate.release()
  expect(await send).toBeInstanceOf(Error)
  expect(loaded.chat.$groupChats.get().Release.log).toEqual([])
  expect(await savedCommands(loaded.storage)).toEqual([])
  const uploadId = uploads()[0].params.upload_id
  await expect(
    rounds.sendToGroupChatDurably('Release', loaded.room.members || [], 'private text', 'thread', loaded.message.images)
  ).resolves.toBe('thread')
  expect(uploads()[1].params.upload_id).toBe(uploadId)
  expect(loaded.chat.$groupChats.get().Release.log).toHaveLength(1)
  expect(loaded.chat.$groupChats.get().Release.log[0]).toMatchObject({ text: 'private text', thread: 'thread' })
  expect(await savedCommands(loaded.storage)).toEqual([])
  expect(loaded.acquired.every(lease => lease.release.mock.calls.length === 1)).toBe(true)
})

it.each(['physical-aba', 'logical-aba', 'runtime-stop'] as const)(
  'rejects %s before enqueue persistence after the captured storage read yields',
  async boundary => {
    const loaded = await setup()
    const gate = barrier()
    const get = loaded.storage.get.bind(loaded.storage)
    vi.spyOn(loaded.storage, 'get').mockImplementationOnce((key, fallback) => {
      gate.entered()

      return gate.pending.then(() => get(key, fallback))
    })
    const send = loaded.runtime.queueHostedGroupChat('Release', loaded.message, 'thread').catch(error => error)
    await gate.reached

    const retained = {
      commandId: 'retained',
      roomId: 'unrelated-room',
      connectionId: 'background',
      authorityId: 'install:home',
      kind: 'send',
      status: 'failed',
      attempts: 5,
      failureCode: 'authority-unavailable',
      possibleAdmission: false,
      payload: { text: 'retained intent', thread_id: 'retained-thread' }
    }

    loaded.values.set(KEY, { version: 1, commands: [retained] })

    if (boundary === 'physical-aba') {
      replace('install:home')
    } else if (boundary === 'logical-aba') {
      loaded.chat.$groupChats.set({})
      loaded.chat.$groupChats.set({ Release: loaded.room })
    } else {
      loaded.runtime.stopHostedRoomRuntime()
    }

    gate.release()
    expect(await send).toBeInstanceOf(Error)
    expect(await savedCommands(loaded.storage)).toEqual([retained])
    expect(uploads()).toHaveLength(2)
    expect(wire.calls.some(call => call.method === 'groups.send')).toBe(false)
    expect(loaded.acquired.every(lease => lease.release.mock.calls.length === 1)).toBe(true)
  }
)

it('retains already-issued persistence with its original storage without publishing into a restarted runtime', async () => {
  const loaded = await setup()
  const gate = barrier()
  const set = loaded.storage.set.bind(loaded.storage)
  vi.spyOn(loaded.storage, 'set').mockImplementationOnce(async (key, value) => {
    set(key, value)
    gate.entered()
    await gate.pending
  })
  const send = loaded.runtime.queueHostedGroupChat('Release', loaded.message, 'thread').catch(error => error)
  await gate.reached
  const replacement = storageFixture()
  loaded.runtime.stopHostedRoomRuntime()
  loaded.chat.$groupChats.set({})
  const inventory = vi.spyOn(host, 'profileRoutes').mockResolvedValue([])

  // Startup recovery shares the outbox mutation lock with the issued write.
  // Start it, but do not await it before releasing that write's response.
  const restarted = loaded.runtime.startHostedRoomRuntime(replacement.storage)
  const freshRoom = { ...loaded.room, roomId: 'fresh-room', continuityIssue: 'fresh intent' }

  loaded.chat.$groupChats.set({ Release: freshRoom })
  gate.release()
  expect(await send).toBeInstanceOf(Error)
  await restarted
  inventory.mockResolvedValue([route])
  expect(await savedCommands(replacement.storage)).toEqual([])
  expect(await savedCommands(loaded.storage)).toMatchObject([
    {
      commandId: 'private-send',
      roomId: 'room',
      authorityId: 'install:home',
      status: 'pending',
      payload: {
        text: 'private text',
        attachments: [{ attachment_id: `att_${'a'.repeat(32)}` }, { attachment_id: `att_${'b'.repeat(32)}` }]
      }
    }
  ])
  // Startup may legitimately refresh its own status object. It must not adopt
  // the old producer's queued row or repaint the new room's message log.
  expect(loaded.runtime.$hostedRoomOutbox.get().commands).toEqual([])
  expect(loaded.chat.$groupChats.get().Release).toMatchObject({ roomId: 'fresh-room', hosted: 'install:home', log: [] })
  expect(wire.calls.some(call => call.method === 'groups.send')).toBe(false)
  expect(loaded.acquired.every(lease => lease.release.mock.calls.length === 1)).toBe(true)
})

it.each(['transport', 'receipt', 'persistence'] as const)(
  'releases staging ownership after %s failure without consuming source bytes or durable intent',
  async failure => {
    const loaded = await setup()

    if (failure === 'persistence') {
      vi.spyOn(loaded.storage, 'set').mockImplementationOnce(() => {
        throw new Error('Inert storage failed')
      })
    } else {
      wire.uploadFailure = failure
    }

    await expect(loaded.runtime.queueHostedGroupChat('Release', loaded.message, 'thread')).rejects.toThrow()
    expect(await savedCommands(loaded.storage)).toEqual([])
    expect(loaded.message.images?.[0]).toMatchObject({ data: 'data:image/png;base64,YQ==', name: 'first.png' })
    expect(loaded.acquired).toHaveLength(1)
    expect(loaded.acquired[0].release).toHaveBeenCalledTimes(1)
    expect(wire.calls.some(call => call.method === 'groups.send')).toBe(false)
    const firstId = uploads()[0].params.upload_id
    wire.uploadFailure = ''
    await expect(loaded.runtime.queueHostedGroupChat('Release', loaded.message, 'thread')).resolves.toBe(true)
    expect(uploads()[failure === 'persistence' ? 2 : 1].params.upload_id).toBe(firstId)
    expect(await savedCommands(loaded.storage)).toEqual([])
    expect(loaded.acquired.every(lease => lease.release.mock.calls.length === 1)).toBe(true)
  }
)

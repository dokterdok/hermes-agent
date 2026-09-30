import fs from 'node:fs'

import { afterEach, beforeEach, expect, it, vi } from 'vitest'

// Electron uses its own compiler options; load its real modules as in canonical-group-send tests.
const nativeIpcModule = '../../../electron/room-secret-ipc'
const nativeFixtureModule = '../../../electron/room-secret-test-fixture'
const nativePreloadModule = '../../../electron/preload'
const { registerRoomSecretIpc } = await import(/* @vite-ignore */ nativeIpcModule)
const { roomSecretFixture } = await import(/* @vite-ignore */ nativeFixtureModule)
import type { BrowserWindow, IpcMain, IpcMainEvent } from 'electron'

// These are integration-test subjects, not plugin runtime dependencies.
// eslint-disable-next-line no-restricted-imports
import { createPluginContext } from '../../contrib/plugin'
// eslint-disable-next-line no-restricted-imports
import { onPersistenceEvent } from '../../lib/storage'

import { pluginSdkMock } from './group-test-utils'
import type { GroupChat } from './types'

const host = vi.hoisted(() => ({}) as Record<string, unknown>)
vi.mock('@hermes/plugin-sdk', async () => pluginSdkMock(host))

const transport = vi.hoisted(() => ({
  handler: null as null | ((event: IpcMainEvent, request: unknown) => void),
  bridge: {} as Window['hermesDesktop'],
  client: 'A',
  senders: new Map<string, { mainFrame: { url: string }; isDestroyed: () => boolean }>()
}))

vi.mock('electron', () => ({
  app: {},
  BrowserWindow: {},
  ipcMain: {},
  safeStorage: {},
  webFrame: {},
  webUtils: {},
  contextBridge: {
    exposeInMainWorld: (_name: string, value: Window['hermesDesktop']) => {
      transport.bridge = value
    }
  },
  ipcRenderer: {
    sendSync: (channel: string, request: unknown) => {
      if (channel !== 'hermes:room-secrets:exchange') {
        return undefined
      }

      if (!transport.senders.has(transport.client)) {
        transport.senders.set(transport.client, {
          mainFrame: { url: 'file:///synthetic-desktop/index.html' },
          isDestroyed: () => false
        })
      }

      const sender = transport.senders.get(transport.client)!
      const event = { senderFrame: sender.mainFrame, sender } as unknown as IpcMainEvent
      transport.handler!(event, request)

      return event.returnValue
    }
  }
}))
let fixture: ReturnType<typeof roomSecretFixture>

function registerStore() {
  registerRoomSecretIpc({
    rendererUrl: 'file:///synthetic-desktop/index.html',
    installationId: 'synthetic-desktop-A',
    store: fixture.store(),
    ipc: {
      on: (_channel, callback) => {
        transport.handler = callback
      }
    } as Pick<IpcMain, 'on'>,
    windowFor: (() => ({ isDestroyed: () => false })) as unknown as typeof BrowserWindow.fromWebContents
  })
}

beforeEach(async () => {
  Object.assign(host, { profileRoutes: async () => [] })
  transport.client = 'A'
  transport.senders.clear()
  fixture = roomSecretFixture()
  registerStore()
  // Exercise the actual contextBridge exposure, not a pretend SDK method.
  await import(/* @vite-ignore */ nativePreloadModule)
  window.hermesDesktop = transport.bridge
  await import('./room-secret-custody')
  window.localStorage.clear()
  const storage = createPluginContext('hermes-bots').storage
  storage.get('group-chats', null)
  storage.get('hosted-room-cleanup-v1', null)
})

afterEach(async () => {
  const cleanup = await import('./hosted-room-cleanup')
  cleanup.resetHostedRoomCleanupForTests()
  const runtime = await import('./hosted-room-runtime')
  runtime.stopHostedRoomRuntime()
  window.localStorage.clear()
  fixture.dispose()
  vi.restoreAllMocks()
})

it('keeps room bearers out of the actual public storage and both event directions', async () => {
  const context = createPluginContext('hermes-bots')
  const authority = await import('./group-desktop-authority')
  const cleanup = await import('./hosted-room-cleanup')

  const room = authority.ensureClassicDesktopAuthority({
    roomId: 'synthetic-room',
    log: [],
    watermarks: {}
  } as GroupChat)

  const grant = 'synthetic-bearer-for-isolated-custody-test'
  const events: string[] = []

  const unsubscribe = onPersistenceEvent(event => {
    events.push(event.value || '')
  })

  try {
    context.storage.set('group-chats', {
      Room: { roomId: room.roomId, ...authority.storedClassicDesktopAuthority(room) }
    })
    await cleanup.startHostedRoomCleanup(context.storage)
    await cleanup.addHostedRoomCleanup({
      operationId: 'synthetic-revoke',
      setupId: 'synthetic-room',
      kind: 'peer-revoke',
      connectionId: 'synthetic-peer',
      installationId: 'install:synthetic-peer',
      profile: 'default',
      grant
    })
    const loaded = context.storage.get<Record<string, GroupChat>>('group-chats', {})
    expect(loaded.Room.desktopAuthorityToken).toBe(room.desktopAuthorityToken)

    const savedCleanup = context.storage.get<{ operations: Array<{ grant: string }> }>('hosted-room-cleanup-v1', {
      operations: []
    })

    expect(savedCleanup.operations[0].grant).toBe(grant)
    expect(fs.readFileSync(fixture.file, 'utf8')).not.toContain(grant)

    const durable = ['group-chats', 'hosted-room-cleanup-v1'].map(
      key => window.localStorage.getItem(`hermes.plugin.hermes-bots.${key}`) || ''
    )

    expect({
      durableContainsGrant: durable.some(value => value.includes(grant)),
      durableContainsAuthority: durable.some(value => value.includes(String(room.desktopAuthorityToken))),
      eventsContainGrant: events.some(value => value.includes(grant)),
      eventsContainAuthority: events.some(value => value.includes(String(room.desktopAuthorityToken)))
    }).toEqual({
      durableContainsGrant: false,
      durableContainsAuthority: false,
      eventsContainGrant: false,
      eventsContainAuthority: false
    })
  } finally {
    unsubscribe()
  }
})

const key = (name: string) => `hermes.plugin.hermes-bots.${name}`

const legacy = () => ({
  Team: {
    roomId: 'legacy-room',
    desktopAuthorityToken: 'authority:synthetic-old',
    desktopAuthorityHash: 'synthetic-commitment',
    log: [{ at: 1, text: 'History must survive', from: { kind: 'user', name: 'Test' } }],
    watermarks: { bot: 1 },
    desktopCommandSettled: { operation: { state: 'settled' } }
  }
})

it.each(['unavailable', 'rename', 'readback', 'renderer-write'])(
  'retains the only old credential, history and pending journal on %s failure, then migrates on retry',
  async failure => {
    const storage = createPluginContext('hermes-bots').storage
    const old = JSON.stringify(legacy())

    const pending = JSON.stringify({
      version: 1,
      operations: [
        {
          kind: 'peer-revoke',
          operationId: 'old-revoke',
          setupId: 'old-setup',
          connectionId: 'old-peer',
          profile: 'default',
          grant: 'synthetic-old-grant',
          armed: true
        }
      ]
    })

    window.localStorage.setItem(key('group-chats'), old)
    window.localStorage.setItem(key('hosted-room-cleanup-v1'), pending)
    const events: string[] = []
    const off = onPersistenceEvent(event => events.push(event.value || ''))

    if (failure === 'unavailable') {
      fixture.available(false)
    }

    if (failure === 'rename') {
      fixture.failWrite(true)
    }

    if (failure === 'readback') {
      fixture.failReadback(true)
    }

    const write =
      failure === 'renderer-write'
        ? vi.spyOn(window.localStorage, 'setItem').mockImplementation(() => {
            throw new Error('quota')
          })
        : null

    try {
      expect(() => storage.get('group-chats', null)).toThrow()
      const cleanup = await import('./hosted-room-cleanup')
      await expect(cleanup.startHostedRoomCleanup(storage)).rejects.toThrow()
      expect(window.localStorage.getItem(key('group-chats'))).toBe(old)
      expect(window.localStorage.getItem(key('hosted-room-cleanup-v1'))).toBe(pending)
      // A later ordinary save must not clobber the history that failed hydration.
      expect(() => storage.set('group-chats', {})).toThrow()
      expect(() => storage.remove('hosted-room-cleanup-v1')).toThrow()
      expect(events.join('')).not.toContain('synthetic-old')
      fixture.available(true)
      fixture.failWrite(false)
      fixture.failReadback(false)
      write?.mockRestore()
      expect(storage.get('group-chats', null)).toEqual(legacy())
      expect(storage.get('hosted-room-cleanup-v1', null)).toEqual(JSON.parse(pending))
      expect(window.localStorage.getItem(key('group-chats'))).not.toContain('authority:synthetic-old')
      expect(window.localStorage.getItem(key('hosted-room-cleanup-v1'))).not.toContain('synthetic-old-grant')
      expect(events.join('')).not.toContain('synthetic-old')
    } finally {
      off()
      write?.mockRestore()
    }
  }
)

it('reloads opaque records without changing exact runtime readback, and rejects copied refs across incarnations', async () => {
  const storage = createPluginContext('hermes-bots').storage
  const chat = await import('./group-chat')
  const authority = await import('./group-desktop-authority')
  const room = authority.ensureClassicDesktopAuthority({ roomId: 'room-A', log: [], watermarks: {} })
  await chat.persistGroupChatRoomsRequired({ Team: room }, storage)
  const bytes = window.localStorage.getItem(key('group-chats'))!
  expect(bytes).toContain('room-secret:')
  expect(bytes).not.toContain(room.desktopAuthorityToken)
  // New native store instance and public plugin context, no renderer bearer cache.
  registerStore()
  const reloaded = createPluginContext('hermes-bots').storage.get('group-chats', {})
  expect(chat.hydrateGroupChatRooms(reloaded).Team.desktopAuthorityToken).toBe(room.desktopAuthorityToken)
  const copied = JSON.parse(bytes)
  copied.Team.roomId = 'room-replacement'
  window.localStorage.setItem(key('group-chats'), JSON.stringify(copied))
  expect(() => storage.get('group-chats', null)).toThrow()
  window.localStorage.setItem(key('group-chats'), bytes)
  expect(storage.get('group-chats', null)).toEqual(reloaded)
  const replacement = authority.ensureClassicDesktopAuthority({ ...room, roomId: 'room-replacement' }, room)
  await chat.persistGroupChatRoomsRequired({ Team: replacement }, storage)
  expect(window.localStorage.getItem(key('group-chats'))).not.toBe(bytes)
  expect(chat.hydrateGroupChatRooms(storage.get('group-chats', {})).Team.desktopAuthorityToken).toBe(
    replacement.desktopAuthorityToken
  )
})

it('keeps cleanup refs bound to installation, profile, operation and authority incarnation across reload', async () => {
  const storage = createPluginContext('hermes-bots').storage
  const cleanup = await import('./hosted-room-cleanup')
  await cleanup.startHostedRoomCleanup(storage)
  await cleanup.addHostedRoomCleanup({
    operationId: 'pending-original',
    setupId: 'setup-original',
    kind: 'peer-revoke',
    connectionId: 'peer',
    installationId: 'installation-original',
    profile: 'builder',
    grant: 'synthetic-pending'
  })
  const bytes = window.localStorage.getItem(key('hosted-room-cleanup-v1'))!

  for (const field of ['installationId', 'profile', 'operationId', 'setupId', 'connectionId']) {
    const copied = JSON.parse(bytes)
    copied.operations[0][field] = 'replacement'
    window.localStorage.setItem(key('hosted-room-cleanup-v1'), JSON.stringify(copied))
    expect(() => storage.get('hosted-room-cleanup-v1', null)).toThrow()
    window.localStorage.setItem(key('hosted-room-cleanup-v1'), bytes)
    expect(
      storage.get<{ operations: Array<{ grant: string }> }>('hosted-room-cleanup-v1', { operations: [] }).operations[0]
        .grant
    ).toBe('synthetic-pending')
  }

  cleanup.stopHostedRoomCleanup()
  registerStore()
  await cleanup.startHostedRoomCleanup(storage)
  expect(cleanup.$hostedRoomCleanup.get().operations).toEqual([
    expect.objectContaining({
      operationId: 'pending-original',
      installationId: 'installation-original',
      grant: 'synthetic-pending'
    })
  ])
  expect(window.localStorage.getItem(key('hosted-room-cleanup-v1'))).not.toContain('synthetic-pending')
})

it('retains private native candidates through a projection conflict without choosing a claim or leaking to projection', async () => {
  const storage = createPluginContext('hermes-bots').storage
  const authority = await import('./group-desktop-authority')
  const chat = await import('./group-chat')

  const original = {
    roomId: 'contested-room',
    log: [{ at: 1, text: 'retained history', thread: 'thread-1', from: { kind: 'user' as const, name: 'Test' } }],
    watermarks: {}
  }

  const first = authority.ensureClassicDesktopAuthority(original)
  const second = authority.ensureClassicDesktopAuthority(original)
  await chat.persistGroupChatRoomsRequired({ Team: first }, storage)
  const oldRef = JSON.parse(window.localStorage.getItem(key('group-chats'))!).Team.desktopAuthorityTokenRef

  const merged = chat.mergeRemoteGroupChatSnapshotIntoRooms(chat.groupChatSyncSnapshot({ Team: second }), {
    Team: first
  })

  const conflicted = authority.ensureClassicDesktopAuthority(merged.Team, first)
  expect(authority.classicAuthorityClaim(conflicted)).toBeNull()
  await chat.persistGroupChatRoomsRequired({ Team: conflicted }, storage)
  const bytes = window.localStorage.getItem(key('group-chats'))!
  expect(bytes).toContain(oldRef)
  expect(bytes).not.toContain(first.desktopAuthorityToken)
  registerStore()
  const loaded = chat.hydrateGroupChatRooms(storage.get('group-chats', {})).Team
  expect(loaded.log).toEqual(original.log)
  expect(loaded.desktopAuthorityCandidates).toContainEqual({
    hash: first.desktopAuthorityHash,
    token: first.desktopAuthorityToken
  })
  expect(authority.classicAuthorityClaim(loaded)).toBeNull()
  const projection = JSON.stringify(chat.groupChatSyncSnapshot({ Team: loaded }))
  expect(projection).not.toContain(oldRef)
  expect(projection).not.toContain(first.desktopAuthorityToken)
})

it.each(['history', 'remove', 'replace'] as const)('R1 reloads client B %s during client A migration', mode => {
  const a = createPluginContext('hermes-bots').storage
  const b = createPluginContext('hermes-bots').storage
  window.localStorage.setItem(key('group-chats'), JSON.stringify(legacy()))
  const newer = legacy()
  newer.Team.log.push({ at: 2, text: 'B new history', from: { kind: 'user', name: 'B' } })

  if (mode === 'replace') {
    newer.Team.roomId = 'replacement-room'
    newer.Team.desktopAuthorityToken = 'authority:replacement'
  }

  const bridge = window.hermesDesktop.roomSecrets!
  const native = bridge.exchange.bind(bridge)
  let interleaved = false
  let committed: string | null = null
  vi.spyOn(bridge, 'exchange').mockImplementation(request => {
    const result = native(request)

    if (!interleaved && request.action === 'seal') {
      interleaved = true
      transport.client = 'B'

      if (mode === 'remove') {
        b.remove('group-chats')
      } else {
        b.set('group-chats', newer)
      }

      committed = window.localStorage.getItem(key('group-chats'))
      transport.client = 'A'
    }

    return result
  })
  expect(a.get('group-chats', null)).toEqual(mode === 'remove' ? null : newer)
  expect(interleaved).toBe(true)
  expect(window.localStorage.getItem(key('group-chats'))).toBe(committed)
})

async function consumerFixture() {
  const cleanup = await import('./hosted-room-cleanup')
  cleanup.resetHostedRoomCleanupForTests()
  await cleanup.startHostedRoomCleanup(createPluginContext('hermes-bots').storage)
  const runtime = await import('./hosted-room-runtime')
  const chat = await import('./group-chat')
  const reauthorization = await import('./hosted-room-reauthorization')

  const routes = [
    { connectionId: 'home', profile: 'default', targetProfile: 'default', mode: 'remote' as const },
    { connectionId: 'peer', profile: 'builder', targetProfile: 'builder', mode: 'remote' as const }
  ]

  let home = 'install:home'
  let generation = 1
  let failInvitePersistence: () => void = () => undefined
  let failRevoke = true
  let afterRevoke: () => void = () => undefined
  const revoked = new Set<string>()
  let createMode = ''
  const calls: Array<{ installation: string; method: string; params: Record<string, unknown> }> = []

  const capability = (installation: string) => ({
    authority_gateway_id: installation,
    driver: true,
    persistent_process: true,
    methods: ['groups.peer.revoke_exact'],
    room_link: {
      enabled: true,
      endpoint: { available: true, url: 'https://fixture.invalid' },
      catalog: {
        installation_id: installation,
        catalog_digest: 'digest',
        text: true,
        attachments: true,
        persistent_process: true,
        protocol_versions: [2],
        link_modes: ['direct']
      }
    }
  })

  const serverRoom = () => ({
    room_id: 'fresh-room',
    authority_gateway_id: home,
    authority_epoch: 1,
    members: [
      {
        member_id: 'member-builder',
        profile: 'builder',
        handle: 'builder',
        target: {
          kind: 'peer',
          installation_id: 'install:peer',
          peer_id: 'install:peer'
        }
      }
    ]
  })

  const request = async (installation: string, method: string, params: Record<string, unknown> = {}) => {
    calls.push({ installation, method, params })

    if (method === 'groups.capabilities') {
      return capability(installation)
    }

    if (method === 'groups.peer.invite') {
      failInvitePersistence()

      return {
        grant: 'synthetic-fresh-grant',
        target_profile: 'builder',
        expires_at: 3601,
        status_expires_at: 2592001,
        catalog: capability(installation).room_link.catalog
      }
    }

    if (method === 'groups.create') {
      if (createMode.startsWith('lost-ack')) {
        if (createMode === 'lost-ack') {
          home = 'install:replacement'
          generation += 1
        }

        throw new Error('lost create ACK')
      }

      return {
        room: {
          ...serverRoom(),
          authority_gateway_id: createMode === 'wrong-authority' ? 'install:wrong' : installation
        }
      }
    }

    if (method === 'groups.state') {
      return {
        room: serverRoom(),
        driver_status: {
          peer_routes: [{ member_id: 'member-builder', grant_sha256: 'c'.repeat(64), status: 'needs_reauthorization' }]
        }
      }
    }

    if (method === 'groups.disband') {
      throw Object.assign(new Error('hosted room not found'), { code: 4113 })
    }

    if (method === 'groups.peer.revoke' || method === 'groups.peer.revoke_exact') {
      if (failRevoke) {
        throw new Error('synthetic revoke offline')
      }

      const grant = String(params.grant)
      const first = !revoked.has(grant)
      revoked.add(grant)
      afterRevoke()

      return { revoked: first }
    }

    if (method === 'groups.peer.register') {
      return { registered: true }
    }

    throw new Error(`Unexpected ${method}`)
  }

  Object.assign(host, {
    profileRoutes: async () => routes,
    activeConnectionId: () => 'home',
    requestProfile: (route: (typeof routes)[number], method: string, params: Record<string, unknown>) =>
      request(route.connectionId === 'home' ? home : 'install:peer', method, params),
    acquireProfileRoute: async (route: (typeof routes)[number]) => {
      const held = generation
      const installation = route.connectionId === 'home' ? home : 'install:peer'

      return {
        route,
        generation: held,
        release: () => undefined,
        assertCurrent: () => {
          if (route.connectionId === 'home' && held !== generation) {
            throw new Error('route retired')
          }
        },
        request: (method: string, params: Record<string, unknown>) => request(installation, method, params)
      }
    }
  })
  chat.$groupChats.set({})
  await runtime.startHostedRoomRuntime(createPluginContext('hermes-bots').storage)
  const { classifyHostedRoomCapability, HOSTED_ROOM_CLIENT_LIMITATIONS } = await import('./hosted-room-client')

  const homeCapability = {
    ...classifyHostedRoomCapability(capability('install:home')),
    peerGrantRenewal: true,
    routeGrantFingerprint: true
  }

  const peerCapability = classifyHostedRoomCapability(capability('install:peer'))
  runtime.$hostedRoomCapabilities.set({ home: homeCapability, peer: peerCapability })

  const member = {
    name: 'builder',
    handle: 'builder',
    connectionId: 'peer',
    targetProfile: 'builder',
    route: routes[1]
  }

  chat.$groupChats.set({
    Team: {
      roomId: 'fresh-room',
      hosted: 'install:home',
      hostedConnectionId: 'home',
      hostedEpoch: 1,
      continuityMode: 'distributed',
      members: [member],
      log: [],
      watermarks: {}
    }
  })

  const create = () =>
    runtime.createAutonomousHostedGroupChat({
      roomId: 'fresh-room',
      name: 'Team',
      members: [{ member, profile: 'builder', handle: 'builder' }],
      probe: {
        eligible: true,
        attachmentParity: true,
        attachmentUnavailableMembers: [],
        capability: homeCapability,
        capabilities: { home: homeCapability, peer: peerCapability },
        routes: { home: routes[0], peer: routes[1] },
        route: {
          kind: 'multi-gateway',
          connectionId: 'home',
          homeConnectionId: 'home',
          remoteConnectionIds: ['peer'],
          limits: HOSTED_ROOM_CLIENT_LIMITATIONS,
          memberConnectionIds: ['home', 'peer'],
          reason: null
        }
      }
    })

  return {
    cleanup,
    calls,
    create,
    reconnect: () => reauthorization.reconnectHostedGroupChatPeer('Team', 'member-builder'),
    switchHome: () => {
      home = 'install:replacement'
      generation += 1
    },
    mode: (value: string) => {
      createMode = value
    },
    onInvite: (fn: () => void) => {
      failInvitePersistence = fn
    },
    recover: () => {
      failRevoke = false
    },
    onRevoke: (fn: () => void) => {
      afterRevoke = fn
    }
  }
}

it('propagates a real protected localStorage read failure without overwriting encrypted cleanup or history', async () => {
  const storage = createPluginContext('hermes-bots').storage
  const cleanup = await import('./hosted-room-cleanup')
  storage.set('group-chats', legacy())
  await cleanup.startHostedRoomCleanup(storage)
  await cleanup.addHostedRoomCleanup({
    operationId: 'original-read-failure',
    setupId: 'original-read-failure',
    kind: 'peer-revoke-exact',
    connectionId: 'original-peer',
    installationId: 'install:original-peer',
    profile: 'builder',
    grant: 'synthetic-original-read-failure-grant'
  })
  const journalBytes = window.localStorage.getItem(key('hosted-room-cleanup-v1'))
  const historyBytes = window.localStorage.getItem(key('group-chats'))
  expect(journalBytes).toContain('room-secret:')
  const atom = cleanup.$hostedRoomCleanup.get()
  const getItem = window.localStorage.getItem.bind(window.localStorage)
  const failure = new Error('synthetic actual localStorage read denied')

  const read = vi.spyOn(window.localStorage, 'getItem').mockImplementation(name => {
    if (name === key('hosted-room-cleanup-v1')) {
      throw failure
    }

    return getItem(name)
  })

  const writes = vi.spyOn(window.localStorage, 'setItem')
  const events: Array<{ op: string; value: null | string }> = []

  const off = onPersistenceEvent(event => {
    if (event.key === key('hosted-room-cleanup-v1')) {
      events.push(event)
    }
  })

  try {
    await expect(cleanup.startHostedRoomCleanup(storage)).rejects.toBe(failure)
    expect(cleanup.$hostedRoomCleanup.get()).toBe(atom)
    expect(writes).not.toHaveBeenCalled()
    expect(events).toEqual([expect.objectContaining({ op: 'read', value: null })])
    read.mockRestore()
    expect(window.localStorage.getItem(key('hosted-room-cleanup-v1'))).toBe(journalBytes)
    expect(window.localStorage.getItem(key('group-chats'))).toBe(historyBytes)
    expect(() => storage.set('hosted-room-cleanup-v1', { version: 1, operations: [] })).toThrow('must be recovered')
    await cleanup.startHostedRoomCleanup(storage)
    expect(cleanup.hostedRoomCleanupPending('original-read-failure')).toBe(true)
    expect(storage.get('group-chats', null)).toEqual(legacy())
    expect(window.localStorage.getItem(key('hosted-room-cleanup-v1'))).not.toContain('synthetic-original-read-failure-grant')
  } finally {
    off()
    read.mockRestore()
  }
})

it('blocks real reconnect minting after failed protected startup even once later storage reads succeed', async () => {
  const f = await consumerFixture()
  const storage = createPluginContext('hermes-bots').storage
  const failure = new Error('synthetic startup read denied')

  const read = vi.spyOn(window.localStorage, 'getItem').mockImplementationOnce(() => {
    throw failure
  })

  await expect(f.cleanup.startHostedRoomCleanup(storage)).rejects.toBe(failure)
  read.mockRestore()
  // Clear the lower storage failure latch without declaring cleanup recovered.
  storage.get('hosted-room-cleanup-v1', null)
  const before = window.localStorage.getItem(key('hosted-room-cleanup-v1'))
  f.calls.length = 0
  await expect(f.reconnect()).rejects.toThrow('cleanup recovery must finish')
  expect(f.calls.some(call => ['groups.peer.invite', 'groups.peer.register', 'groups.peer.revoke_exact'].includes(call.method))).toBe(false)
  expect(f.cleanup.$hostedRoomVolatileCleanup.get()).toEqual([])
  expect(window.localStorage.getItem(key('hosted-room-cleanup-v1'))).toBe(before)
  await f.cleanup.startHostedRoomCleanup(storage)
  await f.reconnect()
  expect(f.calls.some(call => call.method === 'groups.peer.invite')).toBe(true)
  expect(f.calls.some(call => call.method === 'groups.peer.register')).toBe(true)
})

it.each(['before-create', 'lost-ack', 'wrong-authority'])('R2 binds actual home creation: %s', async mode => {
  const f = await consumerFixture()

  if (mode === 'before-create') {
    f.onInvite(f.switchHome)
  } else {
    f.mode(mode)
  }

  const outcome = await f.create().then(
    () => null,
    error => error
  )

  expect(outcome).toBeInstanceOf(Error)
  expect(
    f.calls
      .filter(call => ['groups.create', 'groups.state', 'groups.peer.register'].includes(call.method))
      .every(call => call.installation === 'install:home')
  ).toBe(true)
  expect(f.calls.some(call => call.method === 'groups.peer.register')).toBe(false)

  if (mode === 'before-create') {
    expect(f.calls.some(call => call.method === 'groups.create')).toBe(false)
  }

  if (mode === 'lost-ack') {
    expect(f.calls.some(call => call.method === 'groups.state')).toBe(false)
  }
})

it.each([
  ['create', 'native'],
  ['create', 'renderer'],
  ['reconnect', 'native'],
  ['reconnect', 'renderer']
] as const)('R3 %s retains retry ownership after %s custody and revoke fail', async (consumer, failure) => {
  const f = await consumerFixture()
  let write: ReturnType<typeof vi.spyOn> | undefined
  f.onInvite(() => {
    if (failure === 'native') {
      fixture.failWrite(true)
    } else {
      write = vi.spyOn(window.localStorage, 'setItem').mockImplementation(() => {
        throw new Error('synthetic renderer quota')
      })
    }
  })

  const error = await f[consumer]().then(
    () => null,
    error => error
  )

  expect(error).toBeInstanceOf(Error)
  expect(f.calls.some(call => call.method === 'groups.peer.invite')).toBe(true)
  expect(f.cleanup.hostedRoomCleanupPending('fresh-room')).toBe(true)
  expect(error).toMatchObject({ cleanupPending: true, cleanupDurability: 'volatile', fallbackSafe: false })
  const { describeHostedRoomCreationError } = await import('./hosted-room-client')
  expect(describeHostedRoomCreationError(error)).toContain('this window only')
  expect(error.cause).toBe(error.custodyCause)
  expect(error.revocationCause.message).toBe('synthetic revoke offline')
  expect(f.cleanup.$hostedRoomVolatileCleanup.get()).toEqual([
    expect.objectContaining({ roomId: 'fresh-room', durability: 'volatile' })
  ])
  expect(window.localStorage.getItem(key('hosted-room-cleanup-v1'))).not.toContain('synthetic-fresh-grant')
  fixture.failWrite(false)
  write?.mockRestore()
  f.recover()
  await f.cleanup.dispatchHostedRoomCleanup()
  expect(f.cleanup.hostedRoomCleanupPending('fresh-room')).toBe(false)
  expect(
    f.calls.some(call => call.method === 'groups.peer.revoke_exact' && call.params.grant === 'synthetic-fresh-grant')
  ).toBe(true)
})

// Fail only native reads AFTER the real protected renderer write has ACKed.
// Seal, native file publication, renderer setItem and its raw readback all run.
function failPublishedJournalReadback() {
  const read = fs.readFileSync.bind(fs)
  let blocked = false
  let committed = ''
  let nativeBytes = ''
  let failedReads = 0

  const spy = vi.spyOn(fs, 'readFileSync').mockImplementation((...args: Parameters<typeof fs.readFileSync>) => {
    if (blocked && args[0] === fixture.file) {
      failedReads += 1
      throw new Error('synthetic post-publication native read failure')
    }

    return read(...args)
  })

  const off = onPersistenceEvent(event => {
    if (
      !committed &&
      event.key === key('hosted-room-cleanup-v1') &&
      event.op === 'write' &&
      event.value?.includes('room-secret:')
    ) {
      committed = event.value
      nativeBytes = read(fixture.file, 'utf8') as string
      blocked = true
    }
  })

  return {
    verify: () => {
      expect(committed).toContain('room-secret:')
      expect(committed).not.toContain('synthetic-fresh-grant')
      expect(nativeBytes).not.toContain('synthetic-fresh-grant')
      expect(nativeBytes.length).toBeGreaterThan(0)
      expect(failedReads).toBeGreaterThan(0)

      return JSON.parse(committed).operations.find((operation: { grantRef?: string }) => operation.grantRef)
    },
    block: () => {
      blocked = true
    },
    restore: () => {
      blocked = false
    },
    dispose: () => {
      off()
      spy.mockRestore()
    }
  }
}

it.each([
  ['reconnect', 'immediate'],
  ['reconnect', 'delayed'],
  ['create', 'immediate'],
  ['create', 'delayed']
] as const)('R3.1 %s settles published journal after %s confirmed revoke without restart', async (consumer, timing) => {
  const f = await consumerFixture()
  const storage = createPluginContext('hermes-bots').storage
  await f.cleanup.addHostedRoomCleanup({
    operationId: 'unrelated',
    setupId: 'unrelated',
    kind: 'home-disband',
    roomId: 'unrelated',
    connectionId: 'home',
    installationId: 'install:home',
    profile: 'default'
  })

  if (timing === 'immediate') {
    f.recover()
  }

  const io = failPublishedJournalReadback()

  try {
    const error = await f[consumer]().catch(error => error)
    const published = io.verify()
    expect(published).toMatchObject({ armed: false })
    expect(published.ownerId).not.toBe('')
    expect(error).toMatchObject({ cleanupPending: true, cleanupDurability: 'volatile', fallbackSafe: false })
    expect(error.cause).toBe(error.custodyCause)

    if (timing === 'delayed') {
      expect(error.revocationCause.message).toBe('synthetic revoke offline')
    } else {
      expect(error.revocationCause).toBeUndefined()
      expect(error.settlementCause).toBeInstanceOf(Error)
    }

    expect(f.cleanup.$hostedRoomVolatileCleanup.get()).toHaveLength(1)
    expect(f.cleanup.hostedRoomCleanupPending('fresh-room')).toBe(true)
    io.restore()
    f.recover()
    await f.cleanup.dispatchHostedRoomCleanup()
    expect(f.cleanup.$hostedRoomVolatileCleanup.get()).toEqual([])
    expect(f.cleanup.hostedRoomCleanupPending('fresh-room')).toBe(false)

    const journal = storage.get<{ operations: Array<{ operationId: string }> }>('hosted-room-cleanup-v1', {
      operations: []
    })

    expect(journal.operations.map(operation => operation.operationId)).toEqual(['unrelated'])
    // Same live lifecycle, no Stop/start: the recovered capacity admits new work.
    const invites = f.calls.filter(call => call.method === 'groups.peer.invite').length
    await expect(f.reconnect()).resolves.toBeUndefined()
    expect(f.calls.filter(call => call.method === 'groups.peer.invite')).toHaveLength(invites + 1)
    expect(
      storage.get<{ operations: unknown[] }>('hosted-room-cleanup-v1', { operations: [] }).operations
    ).toHaveLength(1)
  } finally {
    io.dispose()
  }
})

it.each(['read', 'write', 'readback'] as const)(
  'R3.1 retains confirmed revoke across settlement %s failure',
  async failure => {
    const f = await consumerFixture()
    const io = failPublishedJournalReadback()
    const storage = createPluginContext('hermes-bots').storage
    const rawGet = window.localStorage.getItem.bind(window.localStorage)
    let off: () => void = () => undefined
    let write: ReturnType<typeof vi.spyOn> | undefined

    try {
      await expect(f.reconnect()).rejects.toMatchObject({ cleanupPending: true })
      const published = io.verify()
      io.restore()
      f.onRevoke(() => {
        if (failure === 'read') {
          io.block()
        }

        if (failure === 'write') {
          write = vi.spyOn(window.localStorage, 'setItem').mockImplementation(() => {
            throw new Error('settlement quota')
          })
        }

        if (failure === 'readback') {
          // The removal commits as an empty journal: fail its required renderer read.
          off = onPersistenceEvent(event => {
            if (event.key === key('hosted-room-cleanup-v1') && event.op === 'write') {
              write = vi.spyOn(window.localStorage, 'getItem').mockImplementation(() => {
                throw new Error('settlement readback')
              })
            }
          })
        }
      })
      f.recover()
      await f.cleanup.dispatchHostedRoomCleanup().catch(() => undefined)
      expect(f.cleanup.$hostedRoomVolatileCleanup.get()).toHaveLength(1)
      expect(f.cleanup.hostedRoomCleanupPending('fresh-room')).toBe(true)
      const revokes = f.calls.filter(call => call.method === 'groups.peer.revoke_exact').length
      expect(revokes).toBe(2)

      if (failure !== 'read') {
        expect(write).toHaveBeenCalled()
      }

      const unresolved = JSON.parse(rawGet(key('hosted-room-cleanup-v1'))!).operations
      expect(unresolved).toHaveLength(failure === 'readback' ? 0 : 1)
      io.restore()
      off()
      write?.mockRestore()
      f.onRevoke(() => undefined)
      await f.cleanup.dispatchHostedRoomCleanup()
      await f.cleanup.dispatchHostedRoomCleanup()
      // The server returns revoked:false on repetition; confirmation must not be lost.
      expect(f.calls.filter(call => call.method === 'groups.peer.revoke_exact')).toHaveLength(revokes)
      expect(f.cleanup.$hostedRoomVolatileCleanup.get()).toEqual([])
      expect(
        storage
          .get<{ operations: Array<{ operationId: string }> }>('hosted-room-cleanup-v1', { operations: [] })
          .operations.some(operation => operation.operationId === published.operationId)
      ).toBe(false)
    } finally {
      off()
      write?.mockRestore()
      io.dispose()
    }
  }
)

it('R3.1 keeps confirmed settlement ownership through a cleanup lifecycle rebind', async () => {
  const f = await consumerFixture()
  const io = failPublishedJournalReadback()
  const storage = createPluginContext('hermes-bots').storage

  try {
    await expect(f.reconnect()).rejects.toMatchObject({ cleanupPending: true })
    io.verify()
    io.restore()
    f.recover()
    f.onRevoke(io.block)
    await f.cleanup.dispatchHostedRoomCleanup().catch(() => undefined)
    expect(f.cleanup.$hostedRoomVolatileCleanup.get()).toHaveLength(1)
    const revokes = f.calls.filter(call => call.method === 'groups.peer.revoke_exact').length
    io.restore()
    f.onRevoke(() => undefined)
    f.cleanup.stopHostedRoomCleanup()
    await f.cleanup.startHostedRoomCleanup(storage)
    expect(f.calls.filter(call => call.method === 'groups.peer.revoke_exact')).toHaveLength(revokes)
    expect(f.cleanup.$hostedRoomVolatileCleanup.get()).toEqual([])
    expect(storage.get<{ operations: unknown[] }>('hosted-room-cleanup-v1', { operations: [] }).operations).toEqual([])
  } finally {
    io.dispose()
  }
})

it.each(['absent', 'replacement'] as const)(
  'R3.1 reconciles %s without deleting a newer journal owner',
  async disposition => {
    const f = await consumerFixture()
    const io = failPublishedJournalReadback()
    const storage = createPluginContext('hermes-bots').storage

    try {
      await expect(f.reconnect()).rejects.toMatchObject({ cleanupPending: true })
      const published = io.verify()
      io.restore()

      const journal = storage.get<{ operations: Array<Record<string, unknown>> }>('hosted-room-cleanup-v1', {
        operations: []
      })

      const newer = { ...journal.operations[0], grant: 'synthetic-newer-grant', setupId: 'newer-owner' }
      storage.set('hosted-room-cleanup-v1', { version: 1, operations: disposition === 'absent' ? [] : [newer] })
      f.recover()
      await f.cleanup.dispatchHostedRoomCleanup()
      expect(f.cleanup.$hostedRoomVolatileCleanup.get()).toEqual([])

      const remaining = storage.get<{ operations: Array<Record<string, unknown>> }>('hosted-room-cleanup-v1', {
        operations: []
      }).operations

      expect(remaining).toEqual(
        disposition === 'absent'
          ? []
          : [
              expect.objectContaining({
                operationId: published.operationId,
                setupId: 'newer-owner',
                grant: 'synthetic-newer-grant'
              })
            ]
      )
      expect(
        f.calls
          .filter(call => call.method === 'groups.peer.revoke_exact')
          .every(call => call.params.grant === 'synthetic-fresh-grant')
      ).toBe(true)
    } finally {
      io.dispose()
    }
  }
)

it('R1 excludes a second writer in the compare-to-commit gap, then permits its retry', () => {
  const a = createPluginContext('hermes-bots').storage
  const b = createPluginContext('hermes-bots').storage
  window.localStorage.setItem(key('group-chats'), JSON.stringify(legacy()))
  const set = window.localStorage.setItem.bind(window.localStorage)
  let entered = false

  const spy = vi.spyOn(window.localStorage, 'setItem').mockImplementation((name, value) => {
    if (name === key('group-chats') && !entered) {
      entered = true
      transport.client = 'B'
      expect(() => b.remove('group-chats')).toThrow('unavailable or busy')
      transport.client = 'A'
    }

    set(name, value)
  })

  expect(a.get('group-chats', null)).toEqual(legacy())
  expect(entered).toBe(true)
  spy.mockRestore()
  transport.client = 'B'
  b.remove('group-chats')
  expect(a.get('group-chats', null)).toBeNull()
})

it('R2 recovers lost create ACK on the retained installation before peer registration', async () => {
  const f = await consumerFixture()
  f.mode('lost-ack-stable')
  await expect(f.create()).resolves.toMatchObject({ authorityId: 'install:home' })
  expect(
    f.calls
      .filter(call => ['groups.create', 'groups.state', 'groups.peer.register'].includes(call.method))
      .map(call => [call.installation, call.method])
  ).toEqual([
    ['install:home', 'groups.create'],
    ['install:home', 'groups.state'],
    ['install:home', 'groups.peer.register']
  ])
})

it.each(['create', 'reconnect'] as const)(
  'R3 refuses %s before minting when native custody is unavailable',
  async consumer => {
    const f = await consumerFixture()
    fixture.available(false)
    await expect(f[consumer]()).rejects.toThrow('secure credential storage')
    expect(f.calls.some(call => call.method === 'groups.peer.invite')).toBe(false)
  }
)

it('R3 native preflight refuses exhausted capacity without writing a synthetic reservation', async () => {
  const store = fixture.store()

  for (let batch = 0; batch < 4; batch += 1) {
    store.exchange({
      action: 'seal',
      entries: Array.from({ length: 1024 }, (_, index) => ({
        scope: ['hosted-grant', 'capacity-room', `${batch}:${index}`],
        value: `synthetic:${batch}:${index}`
      }))
    })
  }

  const before = fs.readFileSync(fixture.file, 'utf8')
  const f = await consumerFixture()
  await expect(f.create()).rejects.toThrow('secure credential storage')
  expect(f.calls.some(call => call.method === 'groups.peer.invite')).toBe(false)
  expect(fs.readFileSync(fixture.file, 'utf8')).toBe(before)
}, 30_000)

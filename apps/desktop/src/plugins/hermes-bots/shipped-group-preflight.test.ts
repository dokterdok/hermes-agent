import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { createGroupGateway, drain, runTimersInline, scriptedStorage } from './group-test-utils'
import type { ShippedGroupAdoption } from './types'

const { host } = vi.hoisted(() => ({ host: {} as Record<string, unknown> }))
vi.mock('@hermes/plugin-sdk', async () => {
  const { pluginSdkMock } = await import('./group-test-utils')

  return pluginSdkMock(host)
})

// Transform the real module graph during collection, like static imports.
// Each fixture still gets a fresh module generation and gateway below.
Object.assign(host, createGroupGateway().host)
await Promise.all([
  import('./group-chat'), import('./shipped-group-adoption'), import('./shared'), import('./group-rounds')
])

let loaded: Awaited<ReturnType<typeof startup>>

beforeEach(async () => {
  vi.resetModules()
  runTimersInline()
  loaded = await startup()
})
afterEach(() => vi.unstubAllGlobals())

async function startup(turn: Parameters<typeof createGroupGateway>[0] = { turn: () => '(pass)' }) {
  const gateway = createGroupGateway(turn)
  const capabilities = vi.fn(async (_method: string, _params?: Record<string, unknown>): Promise<unknown> => ({ driver: false, persistent_process: false, methods: [] }))
  Object.assign(host, gateway.host, {
    activeConnectionId: () => 'owner',
    profileRoutes: async () => [{ connectionId: 'owner', mode: 'local', profile: 'default', targetProfile: 'default' }],
    acquireProfileRoute: async (route: unknown) => ({
      generation: 1, route, assertCurrent() {}, release() {}, request: capabilities
    })
  })

  const [chat, adoption, shared, rounds] = await Promise.all([
    import('./group-chat'), import('./shipped-group-adoption'), import('./shared'), import('./group-rounds')
  ])

  const members = [{ name: 'research', connectionId: 'owner' }, { name: 'builder', connectionId: 'owner' }]
  gateway.storage.set('group-chats', {
    Classic: { roomId: 'released-classic', log: [], members, watermarks: {} }
  })
  const ctx = scriptedStorage(gateway.storage)
  shared.setPluginCtx(ctx)
  chat.$groupChats.set(chat.hydrateGroupChatRooms(await ctx.storage.get('group-chats', {})))
  await chat.activateClassicGroupAuthorities()
  // Released member descriptors are remote-capable. Model the completed
  // startup inventory before allowing another classic Send on those routes.
  const { hostedRoomObservations } = await import('./hosted-room-runtime')
  hostedRoomObservations.publish(hostedRoomObservations.capture('owner'), new Set(), true)

  chat.stopGroupChatServerSync()

  return { gateway, capabilities, chat, adoption, rounds, ctx, members }
}

describe('shipped adoption preflight execution boundary', () => {
  it('imports current history after delayed capability discovery, not the preflight snapshot', async () => {
    let release!: () => void
    const held = new Promise<void>(resolve => { release = resolve })
    const imports: Record<string, unknown>[] = []
    loaded.capabilities.mockImplementation(async (method, params) => {
      if (method === 'groups.capabilities') {
        await held
        return { authority_gateway_id: 'original-install', methods: ['groups.import_history'] }
      }
      if (method === 'groups.import_history') { imports.push(params!) }
      throw new Error('Keep the prepared checkpoint for inspection')
    })
    const adopting = loaded.adoption.adoptShippedGroupChats(loaded.ctx.storage)
    try {
      await drain(() => !loaded.capabilities.mock.calls.length)
      expect(loaded.rounds.sendToGroupChat('Classic', loaded.members, 'Sent during discovery')).toBeTruthy()
      await drain(() => Boolean(loaded.chat.$groupChats.get().Classic.running))
      release()
      await adopting
      expect(imports).toHaveLength(1)
      expect(imports[0].history).toEqual(expect.arrayContaining([expect.objectContaining({ text: 'Sent during discovery' })]))
      const persisted = await loaded.ctx.storage.get<Record<string, { log: unknown[] }>>('group-chats', {})
      expect(persisted?.Classic.log).toEqual(loaded.chat.$groupChats.get().Classic.log)
    } finally {
      release()
      await adopting
      loaded.adoption.stopShippedGroupAdoption()
    }
  })

  it('settles the actual active and queued classic drive before preparing or importing', async () => {
    let release!: () => void
    const held = new Promise<void>(resolve => { release = resolve })
    loaded = await startup({ turn: async ({ n }) => {
      if (n === 1) { await held; return 'Reply from active classic work' }
      return '(pass)'
    } })
    const imports: Record<string, unknown>[] = []
    let submitsAtImport = -1
    loaded.capabilities.mockImplementation(async (method, params) => {
      if (method === 'groups.capabilities') {
        return { authority_gateway_id: 'original-install', methods: ['groups.import_history'] }
      }
      if (method === 'groups.import_history') {
        imports.push(params!)
        submitsAtImport = loaded.gateway.calls.length
      }
      throw new Error('Keep the prepared checkpoint for inspection')
    })
    loaded.rounds.sendToGroupChat('Classic', loaded.members, 'First thread')
    await drain(() => !loaded.gateway.calls.length)
    expect(loaded.rounds.sendToGroupChat('Classic', loaded.members, 'Queued thread'),
      JSON.stringify(loaded.chat.$groupChats.get().Classic)).toBeTruthy()
    const adopting = loaded.adoption.adoptShippedGroupChats(loaded.ctx.storage)
    try {
      await drain(() => !loaded.capabilities.mock.calls.length)
      // Let capability + preparation microtasks run while the real submit is held.
      for (let i = 0; i < 30; i++) { await Promise.resolve() }
      expect(imports).toHaveLength(0)
      expect(loaded.chat.$groupChats.get().Classic.shippedAdoption).toBeUndefined()
      release()
      await adopting
      await drain(() => Boolean(loaded.chat.$groupChats.get().Classic.running))
      expect(imports).toHaveLength(1)
      expect(imports[0].history).toEqual(expect.arrayContaining([
        expect.objectContaining({ text: 'Reply from active classic work' }),
        expect.objectContaining({ text: 'Queued thread' })
      ]))
      expect(loaded.gateway.calls.length).toBe(submitsAtImport)
      expect(loaded.rounds.sendToGroupChat('Classic', loaded.members, 'Must stay canonical')).toBeNull()
    } finally {
      release()
      await adopting
      await drain(() => Boolean(loaded.chat.$groupChats.get().Classic.running))
      loaded.adoption.stopShippedGroupAdoption()
    }
  })
  it('startup without import_history leaves no hold and real classic Send runs', async () => {
    try {
      await loaded.adoption.adoptShippedGroupChats(loaded.ctx.storage)
      expect(loaded.capabilities).toHaveBeenCalled()
      expect(loaded.chat.$groupChats.get().Classic.shippedAdoption).toBeUndefined()
      const persisted = await loaded.ctx.storage.get<Record<string, { shippedAdoption?: unknown }>>('group-chats', {})
      expect(persisted?.Classic.shippedAdoption).toBeUndefined()
      const thread = loaded.rounds.sendToGroupChat('Classic', loaded.members, 'A new classic message')
      expect(thread).toBeTruthy()
      await drain(() => Boolean(loaded.chat.$groupChats.get().Classic.running))

      expect(loaded.gateway.rpcFor('prompt.submit').length).toBeGreaterThan(0)
      expect(loaded.chat.$groupChats.get().Classic.log.some(entry => entry.text === 'A new classic message')).toBe(true)
    } finally {
      loaded.adoption.stopShippedGroupAdoption()
      loaded.chat.stopGroupChatServerSync()
    }
  })

  it.each(['prepared', 'uncertain', 'adopted'] as const)('never gives %s imports back to classic execution', async state => {
    const checkpoint: ShippedGroupAdoption = {
      version: 1, state: state === 'adopted' ? 'adopted' : 'prepared',
      sourceId: 'retained-source', roomId: 'released-classic', requestHash: 'retained-request',
      route: { connectionId: 'owner', profile: 'default', authorityGatewayId: 'original-install' },
      ...(state === 'uncertain' ? { issue: { kind: 'offline' as const, message: 'Connection lost after submission' } } : {})
    }

    loaded.chat.$groupChats.set({ Classic: { ...loaded.chat.$groupChats.get().Classic, shippedAdoption: checkpoint } })

    try {
      await loaded.adoption.adoptShippedGroupChats(loaded.ctx.storage)
      expect(loaded.chat.$groupChats.get().Classic.shippedAdoption).toMatchObject({
        state: checkpoint.state, sourceId: checkpoint.sourceId, requestHash: checkpoint.requestHash
      })
      expect(loaded.rounds.sendToGroupChat('Classic', loaded.members, 'Must not run')).toBeNull()
      expect(await loaded.rounds.sendToGroupChatDurably('Classic', loaded.members, 'Must not run either')).toBeNull()
      expect(loaded.gateway.rpcFor('prompt.submit')).toHaveLength(0)
    } finally {
      loaded.adoption.stopShippedGroupAdoption()
      loaded.chat.stopGroupChatServerSync()
    }
  })
})

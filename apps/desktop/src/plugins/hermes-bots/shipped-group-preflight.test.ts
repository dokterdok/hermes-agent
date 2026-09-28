import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { createGroupGateway, drain, runTimersInline, scriptedStorage } from './group-test-utils'
import type { ShippedGroupAdoption } from './types'

const { host } = vi.hoisted(() => ({ host: {} as Record<string, unknown> }))
vi.mock('@hermes/plugin-sdk', async () => {
  const { pluginSdkMock } = await import('./group-test-utils')
  return pluginSdkMock(host)
})

beforeEach(() => {
  vi.resetModules()
  runTimersInline()
})
afterEach(() => vi.unstubAllGlobals())

async function startup() {
  const gateway = createGroupGateway({ turn: () => '(pass)' })
  const capabilities = vi.fn(async () => ({ driver: false, persistent_process: false, methods: [] }))
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
  chat.stopGroupChatServerSync()
  return { gateway, capabilities, chat, adoption, rounds, ctx, members }
}

describe('shipped adoption preflight execution boundary', () => {
  it('startup without import_history leaves no hold and real classic Send runs', async () => {
    const loaded = await startup()
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
    const loaded = await startup()
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

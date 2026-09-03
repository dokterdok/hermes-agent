import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { pluginSdkMock, scriptedStorage } from './group-test-utils'
import type { GroupChat, GroupMember, ProfileRoute, RosterRow } from './types'

const { host } = vi.hoisted(() => ({ host: {} as Record<string, unknown> }))

vi.mock('@hermes/plugin-sdk', async () => pluginSdkMock(host))

const GROUP = 'Decision board field test'
const HOME = 'authority-connection'

function member(memberId: string, profile: string, displayName: string, installationId?: string) {
  return {
    member_id: memberId,
    profile,
    handle: memberId,
    display_name: displayName,
    target: installationId
      ? {
          kind: 'peer',
          installation_id: installationId,
          peer_id: installationId,
          profile,
          capability_digest: 'a'.repeat(64)
        }
      : { kind: 'local', profile }
  }
}

const FOREIGN = [
  member('ux', 'default', 'Remote UX', 'installation-A'),
  member('reviewer', 'default', 'Remote Reviewer', 'installation-B')
]

const LOCAL_DEFAULT: RosterRow = {
  name: 'default',
  connectionId: HOME,
  display_name: 'Local default',
  sourceScoped: true,
  sourceReachable: true
}

async function load(serverMembers = [member('local', 'default', 'Authority Bot'), ...FOREIGN]) {
  vi.resetModules()

  for (const key of Object.keys(host)) {
    delete host[key]
  }

  const connections: Record<string, string> = { [HOME]: 'installation-home' }
  const storage = new Map<string, unknown>()
  const calls: Array<{ connectionId: string; method: string }> = []

  const serverRoom = {
    room_id: 'identity-room',
    name: GROUP,
    authority_gateway_id: 'installation-home',
    authority_epoch: 1,
    revision: 1,
    latest_seq: 1,
    members: serverMembers
  }

  Object.assign(host, {
    activeConnectionId: () => HOME,
    profileRoutes: async () =>
      Object.keys(connections).map(connectionId => ({
        connectionId,
        mode: 'remote',
        profile: 'default',
        targetProfile: 'default'
      })),
    requestProfile: async (route: ProfileRoute, method: string) => {
      calls.push({ connectionId: route.connectionId, method })

      if (method === 'groups.capabilities') {
        return {
          authority_gateway_id: connections[route.connectionId],
          driver: true,
          persistent_process: true
        }
      }

      if (method === 'groups.list') {
        return { rooms: route.connectionId === HOME ? [serverRoom] : [] }
      }

      if (method === 'groups.state') {
        return { room: serverRoom, driver_status: { working: false } }
      }

      if (method === 'groups.log') {
        return {
          events: [
            {
              room_id: serverRoom.room_id,
              seq: 1,
              event_id: 'created',
              kind: 'room.created',
              actor: { kind: 'gateway', id: 'installation-home' },
              payload: { name: GROUP, members: serverMembers },
              created_at: 1
            }
          ],
          latest_seq: 1,
          has_more: false
        }
      }

      throw new Error(`Unexpected RPC: ${method}`)
    },
    state: {
      connectionId: { get: () => HOME },
      gateway: { get: () => 'open' },
      profile: { get: () => 'default' }
    }
  })
  const chat = await import('./group-chat')
  const data = await import('./data')
  const membership = await import('./group-membership')
  const labels = await import('./labels')
  const runtime = await import('./hosted-room-runtime')
  const shared = await import('./shared')
  const context = scriptedStorage(storage)
  shared.setPluginCtx(context)
  data.$lastRoster.set([LOCAL_DEFAULT])

  return { chat, data, membership, labels, runtime, context, storage, calls, connections, serverRoom }
}

let loaded: Awaited<ReturnType<typeof load>>

beforeEach(() => vi.useFakeTimers())
afterEach(() => {
  loaded?.runtime.stopHostedRoomRuntime()
  loaded?.chat.stopGroupChatServerSync()
  vi.clearAllTimers()
  vi.useRealTimers()
})

describe('hosted member identity through normalization and consumers', () => {
  it('keeps all four UAT members when Desktop knows only the authority', async () => {
    loaded = await load([
      member('pm', 't2oracle', 'Project Manager'),
      member('builder', 'oxcoder', 'Builder'),
      ...FOREIGN
    ])
    await loaded.runtime.startHostedRoomRuntime(loaded.context.storage)
    const stored = loaded.chat.$groupChats.get()[GROUP].members!
    expect(stored).toHaveLength(4)
    expect(stored.map(bot => bot.hostedIdentity)).toEqual(
      loaded.serverRoom.members.map(raw => ({
        installationId: raw.target.kind === 'peer' ? raw.target.installation_id : 'installation-home',
        profile: raw.profile,
        roomId: 'identity-room',
        memberId: raw.member_id
      }))
    )
    const seated = loaded.membership.groupChatMemberBots(GROUP, [LOCAL_DEFAULT], {})
    expect(seated.map(bot => bot.handle)).toEqual(['pm', 'builder', 'ux', 'reviewer'])
    expect(new Set(seated.map(loaded.data.botRosterKey)).size).toBe(4)
    expect(seated.slice(2).map(bot => loaded.data.botSourceStatus(bot).available)).toEqual([false, false])
    expect(new Set(loaded.calls.map(call => call.connectionId))).toEqual(new Set([HOME]))
  })

  it('does not resolve foreign defaults as the local default or borrow its display metadata', async () => {
    loaded = await load()
    await loaded.runtime.startHostedRoomRuntime(loaded.context.storage)
    const meta = { [`${HOME}::default`]: { title: 'Private local title', groups: [GROUP] } }
    loaded.data.$botMeta.set(meta)
    const seated = loaded.membership.groupChatMemberBots(GROUP, [LOCAL_DEFAULT], meta)
    expect(seated).toHaveLength(3)
    expect(seated.map(bot => bot.handle)).toEqual(['local', 'ux', 'reviewer'])
    expect(seated.slice(1).map(bot => loaded.labels.displayName(bot))).toEqual(['Remote UX', 'Remote Reviewer'])
    expect(seated.slice(1).every(bot => !bot.connectionId && !bot.route)).toBe(true)
    expect(seated.map(loaded.membership.groupMemberKey)).toEqual(seated.map(loaded.data.botRosterKey))
  })

  it('deduplicates connection aliases of one actual Bot without collapsing the other defaults', async () => {
    loaded = await load()
    loaded.connections['peer-a'] = 'installation-A'
    loaded.connections['peer-a-alias'] = 'installation-A'
    await loaded.runtime.startHostedRoomRuntime(loaded.context.storage)
    const descriptors = loaded.chat.$groupChats.get()[GROUP].members!
    const peer = descriptors.find(bot => bot.handle === 'ux')!

    const alias: GroupMember = {
      ...peer,
      name: 'configured-ux-alias',
      connectionId: 'peer-a-alias',
      connectionLabel: 'Alternate connection',
      route: { connectionId: 'peer-a-alias', mode: 'remote', profile: 'configured-ux-alias', targetProfile: 'default' }
    }

    expect(loaded.data.botRosterKey(alias)).toBe(loaded.data.botRosterKey(peer))
    const roster = [LOCAL_DEFAULT, { name: 'default', connectionId: 'peer-a', remoteSource: true }]
    const seated = loaded.membership.groupChatBotsFromDescriptors([...descriptors, alias], roster)
    expect(seated.map(bot => bot.handle)).toEqual(['local', 'ux', 'reviewer'])
    expect(seated.find(bot => bot.handle === 'ux')).toMatchObject({ title: 'Remote UX', name: 'default' })
    expect(loaded.labels.displayName(peer, { title: 'Connection-only nickname' })).toBe('Remote UX')
  })

  it('preserves identities and display metadata across durable rebuild, projection, reload and replay', async () => {
    loaded = await load()
    await loaded.runtime.startHostedRoomRuntime(loaded.context.storage)
    const original = loaded.chat.$groupChats.get()[GROUP]
    const expectedKeys = original.members!.map(loaded.data.botRosterKey)
    expect(new Set(expectedKeys).size).toBe(3)
    const rebuilt = loaded.membership.durableGroupChatMembers(original.members!)
    expect(rebuilt.map(bot => bot.hostedIdentity)).toEqual(original.members!.map(bot => bot.hostedIdentity))
    loaded.chat.updateGroupChat(GROUP, room => ({ ...room, members: rebuilt }), { sync: false })
    const persisted = JSON.parse(JSON.stringify(loaded.storage.get('group-chats'))) as Record<string, GroupChat>
    loaded.runtime.stopHostedRoomRuntime()
    loaded.chat.$groupChats.set(persisted)
    const snapshot = loaded.chat.groupChatSyncSnapshot(persisted)
    const restored = loaded.chat.mergeRemoteGroupChatSnapshotIntoRooms(JSON.parse(JSON.stringify(snapshot)), {})
    loaded.chat.$groupChats.set(restored)
    const beforeReplay = loaded.membership.groupChatMemberBots(GROUP, [LOCAL_DEFAULT], {})
    expect(beforeReplay.map(loaded.data.botRosterKey)).toEqual(expectedKeys)
    expect(beforeReplay.map(bot => bot.hostedIdentity)).toEqual(original.members!.map(bot => bot.hostedIdentity))
    expect(beforeReplay.slice(1).map(bot => loaded.labels.displayName(bot))).toEqual(['Remote UX', 'Remote Reviewer'])
    expect(beforeReplay.slice(1).every(bot => !bot.route && bot.sourceMissing)).toBe(true)
    await loaded.runtime.startHostedRoomRuntime(loaded.context.storage)
    await loaded.runtime.refreshHostedRooms()
    const afterReplay = loaded.membership.groupChatMemberBots(GROUP, [LOCAL_DEFAULT], {})
    expect(afterReplay.map(loaded.data.botRosterKey)).toEqual(expectedKeys)
    expect(loaded.chat.$groupChats.get()[GROUP].hostedSeq).toBe(1)
    expect(loaded.chat.$groupChats.get()[GROUP].members).toHaveLength(3)
  })

  it('keeps identity stable across connection aliases and discards a route rebound to another installation', async () => {
    loaded = await load()
    loaded.connections['peer-a'] = 'installation-A'
    await loaded.runtime.startHostedRoomRuntime(loaded.context.storage)
    const peer = () => loaded.chat.$groupChats.get()[GROUP].members!.find(bot => bot.handle === 'ux')!
    const key = loaded.data.botRosterKey(peer())
    loaded.runtime.stopHostedRoomRuntime()
    delete loaded.connections['peer-a']
    loaded.connections['peer-alias'] = 'installation-A'
    await loaded.runtime.startHostedRoomRuntime(loaded.context.storage)
    expect(peer().connectionId).toBe('peer-alias')
    expect(loaded.data.botRosterKey(peer())).toBe(key)
    loaded.runtime.stopHostedRoomRuntime()
    loaded.connections['peer-alias'] = 'unrelated-installation'
    await loaded.runtime.startHostedRoomRuntime(loaded.context.storage)
    expect(peer()).toMatchObject({ sourceMissing: true, sourceReachable: false, title: 'Remote UX' })
    expect(peer().route).toBeUndefined()
    expect(peer().connectionId).toBeUndefined()
    expect(loaded.data.botRosterKey(peer())).toBe(key)
  })

  it('refreshes idle membership when a peer connection is added or removed without changing the room', async () => {
    loaded = await load()
    await loaded.runtime.startHostedRoomRuntime(loaded.context.storage)
    const peer = () => loaded.chat.$groupChats.get()[GROUP].members!.find(bot => bot.handle === 'ux')!
    const key = loaded.data.botRosterKey(peer())
    expect(peer().connectionId).toBeUndefined()
    loaded.connections['peer-a'] = 'installation-A'
    await loaded.runtime.refreshHostedRooms()
    expect(peer().connectionId).toBe('peer-a')
    delete loaded.connections['peer-a']
    await loaded.runtime.refreshHostedRooms()
    expect(peer().connectionId).toBeUndefined()
    expect(peer().sourceReachable).toBe(false)
    expect(loaded.data.botRosterKey(peer())).toBe(key)
  })

  it('never runs legacy local-name recovery for a source-scoped descriptor whose connection is absent', async () => {
    loaded = await load()

    const legacyForeign: GroupMember = {
      name: 'default',
      handle: 'ux',
      title: 'Remote UX',
      remoteSource: true,
      sourceScoped: true,
      sourceMissing: true
    }

    expect(loaded.membership.resolveLegacyMemberDescriptor(legacyForeign, [LOCAL_DEFAULT])).toBe(legacyForeign)
    expect(loaded.membership.groupChatBotsFromDescriptors([legacyForeign], [LOCAL_DEFAULT])).toEqual([legacyForeign])
  })
})

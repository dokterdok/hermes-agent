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

function legacyProjection(rooms: Record<string, GroupChat>) {
  const snapshot = loaded.chat.groupChatSyncSnapshot(rooms)

  for (const room of Object.values(snapshot.rooms)) {
    for (const bot of room.members || []) {
      delete bot.hostedIdentity
      delete bot.title
      delete bot.display_name
      delete bot.targetProfile
      delete bot.remoteSource
      delete bot.sourceMissing
      delete bot.sourceReachable
    }
  }

  return snapshot
}

const stateReads = () => loaded.calls.filter(call => call.method === 'groups.state').length
const peer = () => loaded.chat.$groupChats.get()[GROUP].members!.find(bot => bot.handle === 'ux')!

describe('review repairs across authority and display projection', () => {
  it('retains classic projection handle updates at equal revision', async () => {
    loaded = await load()

    const current: Record<string, GroupChat> = {
      Classic: {
        roomId: 'classic-id',
        watermarks: {},
        log: [],
        members: [{ name: 'research', handle: 'old' }, { name: 'builder' }]
      }
    }

    const snapshot = loaded.chat.groupChatSyncSnapshot(current)
    Object.values(snapshot.rooms)[0].members![0].handle = 'new'
    const restored = loaded.chat.mergeRemoteGroupChatSnapshotIntoRooms(snapshot, current)
    expect(restored.Classic.members?.[0].handle).toBe('new')
    expect(restored.Classic.members).toHaveLength(2)
  })
  it('keeps verified membership when a display mirror carries a malformed member collection', async () => {
    loaded = await load()
    await loaded.runtime.startHostedRoomRuntime(loaded.context.storage)
    const current = loaded.chat.$groupChats.get()
    const snapshot = loaded.chat.groupChatSyncSnapshot(current)
    Object.assign(Object.values(snapshot.rooms)[0], { members: { invalid: true } })
    const restored = loaded.chat.mergeRemoteGroupChatSnapshotIntoRooms(snapshot, current)
    expect(restored[GROUP].members).toEqual(current[GROUP].members)
  })
  it.each([false, true])(
    'preserves verified seats against a legacy projection and refreshes authority (newer=%s)',
    async newer => {
      loaded = await load([
        member('pm', 't2oracle', 'Project Manager'),
        member('builder', 'oxcoder', 'Builder'),
        ...FOREIGN
      ])
      await loaded.runtime.startHostedRoomRuntime(loaded.context.storage)
      const current = loaded.chat.$groupChats.get()
      const members = current[GROUP].members
      const legacy = legacyProjection(current)

      if (newer) {
        Object.values(legacy.rooms).forEach(room => {
          room.revision = 100
        })
      }

      loaded.chat.$groupChats.set(loaded.chat.mergeRemoteGroupChatSnapshotIntoRooms(legacy, current))
      expect(loaded.chat.$groupChats.get()[GROUP].members).toBe(members)
      expect(loaded.membership.groupChatMemberBots(GROUP, [], {}).map(bot => bot.handle)).toEqual([
        'pm',
        'builder',
        'ux',
        'reviewer'
      ])
      const before = stateReads()
      await loaded.runtime.refreshHostedRooms()
      expect(stateReads()).toBe(before + 1)
      expect(loaded.chat.$groupChats.get()[GROUP].members?.map(bot => bot.handle)).toEqual([
        'pm',
        'builder',
        'ux',
        'reviewer'
      ])
      await loaded.runtime.refreshHostedRooms()
      expect(stateReads()).toBe(before + 1)
    }
  )

  it.each([false, true])(
    'keeps a coherent hosted roster when merging two display mirrors (reverse=%s)',
    async reverse => {
      loaded = await load()
      loaded.connections['peer-a'] = 'installation-A'
      await loaded.runtime.startHostedRoomRuntime(loaded.context.storage)
      const current = loaded.chat.groupChatSyncSnapshot(loaded.chat.$groupChats.get())
      const legacy = legacyProjection(loaded.chat.$groupChats.get())

      const merged = reverse
        ? loaded.chat.mergeGroupChatSyncSnapshots(current, legacy)
        : loaded.chat.mergeGroupChatSyncSnapshots(legacy, current)

      const restored = loaded.chat.mergeRemoteGroupChatSnapshotIntoRooms(merged, {})
      expect(restored[GROUP].members?.map(bot => bot.handle)).toEqual(['local', 'ux', 'reviewer'])
      expect(restored[GROUP].members?.every(bot => bot.hostedIdentity)).toBe(true)
      expect(restored[GROUP].hostedMembersVerified).not.toBe(true)
    }
  )

  it('never accepts a projected verification flag, and preserves verification through local storage', async () => {
    loaded = await load()
    await loaded.runtime.startHostedRoomRuntime(loaded.context.storage)
    const current = loaded.chat.$groupChats.get()
    const snapshot = loaded.chat.groupChatSyncSnapshot(current)
    const projected = Object.values(snapshot.rooms)[0]
    expect(Object.hasOwn(projected, 'hostedMembersVerified')).toBe(false)
    Object.assign(projected, { hostedMembersVerified: true })
    const restored = loaded.chat.mergeRemoteGroupChatSnapshotIntoRooms(snapshot, {})
    expect(restored[GROUP].hostedMembersVerified).not.toBe(true)
    const saved = JSON.parse(JSON.stringify(loaded.storage.get('group-chats'))) as Record<string, GroupChat>
    expect(saved[GROUP].hostedMembersVerified).toBe(true)
  })

  it('repairs an older contaminated cache even with a previously current idle fingerprint', async () => {
    loaded = await load()
    await loaded.runtime.startHostedRoomRuntime(loaded.context.storage)
    const current = loaded.chat.$groupChats.get()
    const oldMembers = Object.values(legacyProjection(current).rooms)[0].members!
    loaded.chat.$groupChats.set({
      [GROUP]: {
        ...current[GROUP],
        hostedMembersVerified: undefined,
        members: [...current[GROUP].members!, ...oldMembers]
      }
    })
    const before = stateReads()
    await loaded.runtime.refreshHostedRooms()
    expect(stateReads()).toBe(before + 1)
    expect(loaded.chat.$groupChats.get()[GROUP].members?.map(bot => bot.handle)).toEqual(['local', 'ux', 'reviewer'])
  })

  it('recovers projected classic friendly names only within their metadata-confirmed local room', async () => {
    loaded = await load()

    const roster: RosterRow[] = [
      { name: 'research', connectionId: 'local', display_name: 'Researcher', remoteSource: false },
      { name: 'builder', connectionId: 'local', display_name: 'Builder', remoteSource: false }
    ]

    const initial: Record<string, GroupChat> = {
      Classic: { roomId: 'classic-id', log: [], watermarks: {}, members: [{ name: 'Researcher' }, { name: 'Builder' }] }
    }

    const snapshot = loaded.chat.groupChatSyncSnapshot(initial)
    loaded.chat.$groupChats.set(loaded.chat.mergeRemoteGroupChatSnapshotIntoRooms(snapshot, {}))
    const meta = { research: { groups: ['Classic'] }, builder: { groups: ['Classic'] } }
    const seated = loaded.membership.groupChatMemberBots('Classic', roster, meta)
    expect(seated.map(bot => bot.name)).toEqual(['research', 'builder'])
    expect(loaded.membership.durableGroupChatMembers(seated).map(bot => bot.name)).toEqual(['research', 'builder'])
  })

  it('does not borrow an unrelated local friendly-name match for projected classic members', async () => {
    loaded = await load()

    const initial: Record<string, GroupChat> = {
      Classic: { roomId: 'classic-id', log: [], watermarks: {}, members: [{ name: 'Researcher' }, { name: 'Builder' }] }
    }

    loaded.chat.$groupChats.set(
      loaded.chat.mergeRemoteGroupChatSnapshotIntoRooms(loaded.chat.groupChatSyncSnapshot(initial), {})
    )

    const seated = loaded.membership.groupChatMemberBots(
      'Classic',
      [{ name: 'research', display_name: 'Researcher' }],
      {}
    )

    expect(seated.map(bot => bot.name)).toEqual(['Researcher', 'Builder'])
  })

  it.each([
    ['existing', false],
    ['existing', true],
    ['empty', false],
    ['empty', true]
  ] as const)(
    'uses live health and canonical metadata after projection into %s (error=%s)',
    async (destination, error) => {
      loaded = await load()
      loaded.connections['peer-a'] = 'installation-A'
      await loaded.runtime.startHostedRoomRuntime(loaded.context.storage)
      const current = loaded.chat.$groupChats.get()
      const snapshot = loaded.chat.groupChatSyncSnapshot(current)
      expect(Object.values(snapshot.rooms)[0].members?.find(bot => bot.handle === 'ux')?.route).toBeUndefined()
      loaded.chat.$groupChats.set(
        loaded.chat.mergeRemoteGroupChatSnapshotIntoRooms(snapshot, destination === 'empty' ? {} : current)
      )

      const live: RosterRow = {
        name: 'logical-alias',
        connectionId: 'peer-a',
        remoteSource: true,
        route: { connectionId: 'peer-a', mode: 'remote', profile: 'logical-alias', targetProfile: 'default' },
        sourceReachable: false,
        ...(error ? { sourceError: 'connection-refused' } : {}),
        canonical_session: { id: 'canonical-live' },
        has_avatar: true,
        display_name: 'Wrong local name'
      }

      const seated = loaded.membership.groupChatMemberBots(GROUP, [live], {}).find(bot => bot.handle === 'ux')!
      expect(loaded.data.botSourceStatus(seated)).toMatchObject({ key: 'unavailable', available: false })
      expect(seated.sourceReachable).toBe(false)
      expect(seated.canonical_session).toEqual(live.canonical_session)
      expect(seated.has_avatar).toBe(true)
      expect(seated.hostedIdentity?.installationId).toBe('installation-A')
      expect(loaded.labels.displayName(seated)).toBe('Remote UX')
    }
  )

  it('does not enrich a compact projected route after that connection is proven to be another installation', async () => {
    loaded = await load()
    loaded.connections['peer-a'] = 'installation-A'
    await loaded.runtime.startHostedRoomRuntime(loaded.context.storage)
    const snapshot = loaded.chat.groupChatSyncSnapshot(loaded.chat.$groupChats.get())
    loaded.connections['peer-a'] = 'unrelated-installation'
    await loaded.runtime.refreshHostedRooms()
    loaded.chat.$groupChats.set(loaded.chat.mergeRemoteGroupChatSnapshotIntoRooms(snapshot, {}))

    const seated = loaded.membership
      .groupChatMemberBots(
        GROUP,
        [
          {
            name: 'default',
            connectionId: 'peer-a',
            remoteSource: true,
            sourceReachable: true,
            canonical_session: { id: 'wrong-installation-chat' }
          }
        ],
        {}
      )
      .find(bot => bot.handle === 'ux')!

    expect(seated.hostedIdentity?.installationId).toBe('installation-A')
    expect(seated.canonical_session).toBeUndefined()
    expect(seated.route).toBeUndefined()
    expect(loaded.data.botSourceStatus(seated).available).toBe(false)
  })

  it.each([
    ['removed', false],
    ['rebound', false],
    ['removed', true],
    ['rebound', true]
  ] as const)('revokes %s peer ownership before a failed authority read (reload=%s)', async (change, reload) => {
    loaded = await load()
    loaded.connections['peer-a'] = 'installation-A'
    await loaded.runtime.startHostedRoomRuntime(loaded.context.storage)
    const original = peer()

    if (reload) {
      loaded.runtime.stopHostedRoomRuntime()
      const saved = JSON.parse(JSON.stringify(loaded.storage.get('group-chats'))) as Record<string, GroupChat>
      loaded.chat.$groupChats.set(saved)
      loaded.runtime.$hostedRoomCapabilities.set({})
    }

    if (change === 'removed') {
      delete loaded.connections['peer-a']
    } else {
      loaded.connections['peer-a'] = 'unrelated-installation'
    }

    const request = host.requestProfile as (route: ProfileRoute, method: string, params?: unknown) => Promise<unknown>
    let routeAtFailedRead: unknown = 'not-read'

    host.requestProfile = async (route: ProfileRoute, method: string, params?: unknown) => {
      if (route.connectionId === HOME && method === 'groups.state') {
        routeAtFailedRead = peer().route
        throw new Error('authority offline')
      }

      return request(route, method, params)
    }

    if (reload) {
      await loaded.runtime.startHostedRoomRuntime(loaded.context.storage)
    } else {
      await loaded.runtime.refreshHostedRooms()
    }

    expect(routeAtFailedRead).toBeUndefined()
    expect(peer().route).toBeUndefined()
    expect(peer().connectionId).toBeUndefined()
    expect(peer()).toMatchObject({
      hostedIdentity: original.hostedIdentity,
      title: original.title,
      sourceMissing: true,
      sourceReachable: false
    })
    const saved = loaded.storage.get('group-chats') as Record<string, GroupChat>
    expect(saved[GROUP].members?.find(bot => bot.handle === 'ux')?.route).toBeUndefined()
    expect(loaded.chat.$groupChats.get()[GROUP].members?.find(bot => bot.handle === 'local')?.connectionId).toBe(HOME)
  })

  it('does not interpret a failed inventory request as proof that every route was removed', async () => {
    loaded = await load()
    loaded.connections['peer-a'] = 'installation-A'
    await loaded.runtime.startHostedRoomRuntime(loaded.context.storage)
    const original = peer()

    host.profileRoutes = async () => {
      throw new Error('inventory unavailable')
    }

    await expect(loaded.runtime.refreshHostedRooms()).rejects.toThrow('inventory unavailable')
    expect(peer()).toBe(original)
  })
})

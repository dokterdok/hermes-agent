import type { BrowserWindow, IpcMain, IpcMainEvent } from 'electron'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

import type { ShippedGroupImportRequest } from './canonical-groups'
import { createGroupGateway, deferTimers, drain, pluginSdkMock } from './group-test-utils'
import type { GroupChat, ShippedGroupAdoption } from './types'

const nativeIpcModule = '../../../electron/room-secret-ipc'
const nativeFixtureModule = '../../../electron/room-secret-test-fixture'
const nativePreloadModule = '../../../electron/room-secret-preload'
const { registerRoomSecretIpc } = await import(/* @vite-ignore */ nativeIpcModule)
const { roomSecretFixture } = await import(/* @vite-ignore */ nativeFixtureModule)
const { createRoomSecretBridge } = await import(/* @vite-ignore */ nativePreloadModule)
const host = vi.hoisted(() => ({}) as Record<string, unknown>)
vi.mock('@hermes/plugin-sdk', async () => pluginSdkMock(host))
vi.mock('electron', () => ({ app: {}, BrowserWindow: {}, ipcMain: {}, safeStorage: {} }))

let fixture: ReturnType<typeof roomSecretFixture>
let handler: (event: IpcMainEvent, request: unknown) => void
const frames = new Map<string, { mainFrame: { url: string }; isDestroyed: () => boolean }>()
let client = 'A'

function select(id: string) {
  client = id
}

async function renderer(id: string) {
  select(id)
  vi.resetModules()
  vi.doMock('@hermes/plugin-sdk', async () => pluginSdkMock(host))
  // Actual plugin storage and its codec are distinct module generations, but
  // share the real protected IPC registration and localStorage origin.
  const { createPluginContext } = await import('../../contrib/plugin')

  const [chat, turns, rounds, shared, adoption] = await Promise.all([
    import('./group-chat'), import('./group-turns'), import('./group-rounds'), import('./shared'), import('./shipped-group-adoption')
  ])

  const storage = createPluginContext('hermes-bots').storage
  shared.setPluginCtx({ storage } as Parameters<typeof shared.setPluginCtx>[0])
  chat.$groupChats.set(chat.hydrateGroupChatRooms(await storage.get('group-chats', {})))

  return { id, chat, turns, rounds, storage, adoption }
}

const checkpoint = (state: ShippedGroupAdoption['state']): ShippedGroupAdoption => ({
  version: 1, state, sourceId: 'desktop:original', roomId: 'room:original', requestHash: 'a'.repeat(64),
  route: { connectionId: 'owner', profile: 'default', authorityGatewayId: 'install:owner' }
})

const room = (roomId: string): GroupChat => ({
  roomId, log: [{ id: `history:${roomId}`, at: 1, from: { kind: 'user', name: 'You' }, text: 'retained history' }],
  watermarks: {}, members: [{ name: 'helper', title: '' }], sessions: {},
  stranded: { former: { before: 1, thread: 'legacy', turn: 'unknown-accepted' } },
  desktopCommandSettled: { 'receipt:old': 1 }
})

beforeEach(async () => {
  deferTimers()

  for (const key of Object.keys(host)) { delete host[key] }
  Object.assign(host, createGroupGateway().host)
  frames.clear()
  fixture = roomSecretFixture()
  registerRoomSecretIpc({
    rendererUrl: 'file:///synthetic-desktop/index.html', installationId: 'synthetic-desktop-A', store: fixture.store(),
    ipc: { on: (_channel, callback) => { handler = callback } } as Pick<IpcMain, 'on'>,
    windowFor: (() => ({ isDestroyed: () => false })) as unknown as typeof BrowserWindow.fromWebContents
  })
  window.hermesDesktop = { roomSecrets: createRoomSecretBridge((_channel: string, request: unknown) => {
    if (!frames.has(client)) {
      frames.set(client, { mainFrame: { url: 'file:///synthetic-desktop/index.html' }, isDestroyed: () => false })
    }

    const sender = frames.get(client)!
    const event = { sender, senderFrame: sender.mainFrame } as unknown as IpcMainEvent
    handler(event, request)

    return event.returnValue
  }) } as Window['hermesDesktop']
  window.localStorage.clear()
})

afterEach(() => {
  fixture.dispose()
  window.localStorage.clear()
  vi.restoreAllMocks()
})

it('two windows: zero stale classic submissions and exact adoption survives unrelated whole-map save and rename', async () => {
  const gateway = createGroupGateway()
  Object.assign(host, gateway.host)
  const a = await renderer('A')
  a.chat.updateGroupChat('Team', () => room('room:original'), { sync: false })
  a.chat.updateGroupChat('Independent', () => ({ ...room('room:independent'), stranded: {} }), { sync: false })
  const b = await renderer('B')
  const delayed = structuredClone(b.chat.durableGroupChatRooms())

  select('A')
  a.chat.updateGroupChat('Team', current => ({ ...current, shippedAdoption: checkpoint('prepared') }), { sync: false })
  await a.chat.persistGroupChatRoomsRequired()
  a.chat.updateGroupChat('Team', current => ({
    ...current, shippedAdoption: checkpoint('adopted'), hosted: 'install:owner', hostedEpoch: 1,
    hostedConnectionId: 'owner', continuityMode: 'gateway'
  }), { sync: false })
  await a.chat.persistGroupChatRoomsRequired()
  const exact = structuredClone((await a.storage.get<Record<string, GroupChat>>('group-chats', {})).Team)
  const sealedExact = JSON.parse(window.localStorage.getItem('hermes.plugin.hermes-bots.group-chats')!).Team

  select('B')
  const reply = await b.turns.runGroupChatMemberTurn('Team', { name: 'helper', title: '' }, 'stale prompt', 'legacy')
  expect.soft(gateway.rpcFor('prompt.submit')).toHaveLength(0)
  expect.soft(reply).toBeNull()
  b.chat.updateGroupChat('Independent', current => ({ ...current, image: 'independent edit' }), { sync: false })
  const renamed = { ...b.chat.durableGroupChatRooms() }
  renamed.Renamed = renamed.Independent
  delete renamed.Independent
  b.chat.$groupChats.set(renamed)
  await b.chat.persistGroupChatRoomsRequired()
  await b.storage.set('group-chats', { ...delayed, Renamed: renamed.Renamed })
  const saved = await b.storage.get<Record<string, GroupChat>>('group-chats', {})
  expect.soft(saved.Team).toEqual(exact)
  expect.soft(saved.Team.shippedAdoption).toEqual(checkpoint('adopted'))
  expect.soft(saved.Team.hosted).toBe('install:owner')
  expect(JSON.parse(window.localStorage.getItem('hermes.plugin.hermes-bots.group-chats')!).Team).toEqual(sealedExact)

  await b.rounds.sendToGroupChat('Renamed', [{ name: 'helper', title: '' }], 'ordinary independent work')
  await drain(() => Boolean(b.chat.$groupChats.get().Renamed?.running))
  expect(gateway.rpcFor('prompt.submit')).toHaveLength(1)
})

it('two windows: a prepared/unknown handoff fences direct turns and Send before any submission', async () => {
  const gateway = createGroupGateway()
  Object.assign(host, gateway.host)
  const a = await renderer('A')
  a.chat.updateGroupChat('Team', () => room('room:original'), { sync: false })
  const b = await renderer('B')
  select('A')
  a.chat.updateGroupChat('Team', current => ({ ...current, shippedAdoption: checkpoint('prepared') }), { sync: false })
  await a.chat.persistGroupChatRoomsRequired()
  const exact = structuredClone((await a.storage.get<Record<string, GroupChat>>('group-chats', {})).Team)
  select('B')
  const sent = await b.rounds.sendToGroupChat('Team', [{ name: 'helper', title: '' }], 'must not be admitted')
  await drain(() => Boolean(b.chat.$groupChats.get().Team?.running))
  expect.soft(sent).toBeNull()
  expect.soft(gateway.rpcFor('prompt.submit')).toHaveLength(0)
  expect((await b.storage.get<Record<string, GroupChat>>('group-chats', {})).Team).toEqual(exact)
})

it('production adoption producer: prepared import and its acknowledged owner survive stale window edits without classic dispatch', async () => {
  const gateway = createGroupGateway()
  Object.assign(host, gateway.host)
  let entered!: () => void
  let acknowledge!: () => void
  const imported = new Promise<void>(resolve => { entered = resolve })
  const ack = new Promise<void>(resolve => { acknowledge = resolve })
  const requests: ShippedGroupImportRequest[] = []
  host.profileRoutes = async () => [{ connectionId: 'local', mode: 'local', profile: 'default', targetProfile: 'default' }]
  // Inert wire adapter for the published importer contract; no provider or
  // live gateway is started. Persistence and main/preload IPC remain real.
  host.acquireProfileRoute = async (route: Record<string, unknown>) => ({
    route, generation: 1, assertCurrent: () => undefined, release: () => undefined,
    request: async (method: string, params: Record<string, unknown>) => {
      if (method === 'groups.capabilities') {
        return { authority_gateway_id: 'install:owner', methods: ['groups.import_history'] }
      }

      if (method !== 'groups.import_history') { throw new Error(`Unexpected wire method ${method}`) }
      const request = structuredClone(params) as unknown as ShippedGroupImportRequest
      requests.push(request)
      entered()
      await ack

      return {
        source_id: request.source_id, imported_history: request.history.length, held_work: request.held_work.length,
        held_members: 0, retired_members: 0,
        room: { room_id: request.room_id, name: request.name, authority_gateway_id: 'install:owner',
          authority_epoch: 1, revision: 1, members: [], created_at: 1, updated_at: 1 }
      }
    }
  })
  const a = await renderer('A')
  a.chat.updateGroupChat('Team', () => ({ ...room(`r${'1'.repeat(32)}`),
    members: [{ name: 'helper', title: '', connectionId: 'local' }] }), { sync: false })
  const b = await renderer('B')
  const delayed = structuredClone(b.chat.durableGroupChatRooms())
  select('A')
  const adopting = a.adoption.adoptShippedGroupChats(a.storage)
  await imported
  const prepared = structuredClone((await a.storage.get<Record<string, GroupChat>>('group-chats', {})).Team)
  expect(prepared.shippedAdoption?.state).toBe('prepared')
  expect(requests[0].held_work).toHaveLength(1)
  expect(requests[0].history.map(entry => entry.text)).toEqual(['retained history'])

  select('B')
  expect.soft(await b.turns.runGroupChatMemberTurn('Team', { name: 'helper', title: '' }, 'stale', 'legacy')).toBeNull()
  expect.soft(b.rounds.sendToGroupChat('Team', [{ name: 'helper', title: '' }], 'stale send')).toBeNull()
  expect.soft(gateway.rpcFor('prompt.submit')).toHaveLength(0)
  b.chat.updateGroupChat('Independent', () => ({ ...room(`r${'2'.repeat(32)}`), stranded: {}, image: 'changed in B' }), { sync: false })
  const renamed: Record<string, GroupChat> = { ...b.chat.durableGroupChatRooms(), Renamed: b.chat.durableGroupChatRooms().Independent }
  delete renamed.Independent
  b.chat.$groupChats.set(renamed)
  await b.chat.persistGroupChatRoomsRequired()
  const late: Record<string, GroupChat> = { ...delayed, Renamed: renamed.Renamed }
  delete late.Independent
  await b.storage.set('group-chats', late)
  expect.soft((await b.storage.get<Record<string, GroupChat>>('group-chats', {})).Team).toEqual(prepared)

  select('A')
  acknowledge()
  await adopting
  const adopted = (await a.storage.get<Record<string, GroupChat>>('group-chats', {})).Team
  expect(adopted.shippedAdoption?.state).toBe('adopted')
  expect(adopted.hosted).toBe('install:owner')
  expect(adopted.log).toEqual(prepared.log)
  expect(adopted.stranded).toEqual(prepared.stranded)
  expect(adopted.desktopCommandSettled).toEqual(prepared.desktopCommandSettled)
  select('B')
  await b.storage.set('group-chats', late)
  const saved = await b.storage.get<Record<string, GroupChat>>('group-chats', {})
  expect(saved.Team).toEqual(adopted)
  expect(saved.Renamed.image).toBe('changed in B')
  await b.rounds.sendToGroupChat('Renamed', [{ name: 'helper', title: '' }], 'independent still works')
  await drain(() => Boolean(b.chat.$groupChats.get().Renamed?.running))
  expect(gateway.rpcFor('prompt.submit')).toHaveLength(1)
  a.adoption.stopShippedGroupAdoption()
  b.adoption.stopShippedGroupAdoption()
})

it('native per-room admission spans awaited routing and blocks handoff persistence, not independent edits', async () => {
  const gateway = createGroupGateway()
  Object.assign(host, gateway.host)
  let entered!: () => void
  let resume!: () => void
  const retaining = new Promise<void>(resolve => { entered = resolve })
  const route = new Promise<void>(resolve => { resume = resolve })

  host.retainProfile = async () => {
    entered()
    await route

    return () => undefined
  }

  const a = await renderer('A')
  a.chat.updateGroupChat('Team', () => ({ ...room('room:original'), stranded: {} }), { sync: false })
  a.chat.updateGroupChat('Independent', () => ({ ...room('room:independent'), stranded: {} }), { sync: false })
  const b = await renderer('B')

  const working = b.turns.runGroupChatMemberTurn('Team', { name: 'helper', title: '', sourceScoped: true,
    route: { connectionId: 'local', mode: 'local', profile: 'helper', targetProfile: 'helper' } }, 'ordinary', 'legacy')

  await retaining
  select('A')

  const held = { ...a.chat.$groupChats.get(), Team: { ...a.chat.$groupChats.get().Team,
    shippedAdoption: checkpoint('prepared') } }

  await expect(a.chat.persistGroupChatRoomsRequired(held, a.storage, 'Team')).rejects.toThrow('Group Chat secure credential storage is unavailable or busy.')
  a.chat.updateGroupChat('Independent', current => ({ ...current, image: 'parallel safe edit' }), { sync: false })
  expect((await a.storage.get<Record<string, GroupChat>>('group-chats', {})).Independent.image).toBe('parallel safe edit')
  expect(gateway.rpcFor('prompt.submit')).toHaveLength(0)
  select('B')
  resume()
  await working
  expect(gateway.rpcFor('prompt.submit')).toHaveLength(1)
  expect((await b.storage.get<Record<string, GroupChat>>('group-chats', {})).Team.shippedAdoption).toBeUndefined()
})

it('unrecognized retained handoff stays an execution fence and survives an unrelated room write', async () => {
  const gateway = createGroupGateway()
  Object.assign(host, gateway.host)
  const held = { ...room('room:original'), shippedAdoption: { version: 2, state: 'unknown', opaque: 'retain verbatim' } }
  // A pre-existing future/unknown persisted record, not a fabricated successful
  // import. Hydration must not make the native durable owner classic again.
  window.localStorage.setItem('hermes.plugin.hermes-bots.group-chats', JSON.stringify({ Team: held }))
  const b = await renderer('B')
  expect(b.rounds.sendToGroupChat('Team', [{ name: 'helper', title: '' }], 'must remain held')).toBeNull()
  expect(await b.turns.runGroupChatMemberTurn('Team', { name: 'helper', title: '' }, 'must remain held', 'legacy')).toBeNull()
  b.chat.updateGroupChat('Independent', () => ({ ...room('room:independent'), stranded: {} }), { sync: false })
  expect((await b.storage.get<Record<string, GroupChat>>('group-chats', {})).Team).toEqual(held)
  expect(gateway.rpcFor('prompt.submit')).toHaveLength(0)
})

it.each(['prepared', 'adopted'] as const)('checkpoint persistence: %s survives unrelated edit, rename and delayed stale save with no intervening submits', async state => {
  const gateway = createGroupGateway()
  Object.assign(host, gateway.host)
  const a = await renderer('A')
  a.chat.updateGroupChat('Team', () => room('room:original'), { sync: false })
  a.chat.updateGroupChat('Independent', () => ({ ...room('room:independent'), stranded: {} }), { sync: false })
  const b = await renderer('B')
  const delayed = structuredClone(b.chat.durableGroupChatRooms())

  select('A')
  a.chat.updateGroupChat('Team', current => ({ ...current, shippedAdoption: checkpoint(state),
    ...(state === 'adopted' ? { hosted: 'install:owner', hostedEpoch: 3, hostedConnectionId: 'owner' } : {}) }), { sync: false })
  const exact = structuredClone((await a.storage.get<Record<string, GroupChat>>('group-chats', {})).Team)
  const sealed = JSON.parse(window.localStorage.getItem('hermes.plugin.hermes-bots.group-chats')!).Team

  select('B')
  b.chat.updateGroupChat('Independent', current => ({ ...current, image: 'unrelated edit' }), { sync: false })
  expect.soft((await b.storage.get<Record<string, GroupChat>>('group-chats', {})).Team).toEqual(exact)
  const next = { ...b.chat.durableGroupChatRooms() }
  next.Renamed = next.Independent
  delete next.Independent
  b.chat.$groupChats.set(next)
  await b.chat.persistGroupChatRoomsRequired(next, b.storage)
  const renamed = await b.storage.get<Record<string, GroupChat>>('group-chats', {})
  expect.soft(renamed.Team).toEqual(exact)
  expect.soft(renamed.Renamed.image).toBe('unrelated edit')
  expect.soft(renamed.Independent).toBeUndefined()
  b.storage.set('group-chats', { ...delayed, Renamed: next.Renamed })
  const late = await b.storage.get<Record<string, GroupChat>>('group-chats', {})
  expect.soft(late.Team).toEqual(exact)
  expect.soft(late.Renamed.image).toBe('unrelated edit')
  expect.soft(late.Independent).toBeUndefined()
  expect.soft(JSON.parse(window.localStorage.getItem('hermes.plugin.hermes-bots.group-chats')!).Team).toEqual(sealed)
  expect(gateway.rpcFor('prompt.submit')).toHaveLength(0)
})

function installImporter() {
  const requests: ShippedGroupImportRequest[] = []
  host.profileRoutes = async () => [{ connectionId: 'local', mode: 'local', profile: 'default', targetProfile: 'default' }]
  host.acquireProfileRoute = async (route: Record<string, unknown>) => ({
    route, generation: 1, assertCurrent: () => undefined, release: () => undefined,
    request: async (method: string, params: Record<string, unknown>) => {
      if (method === 'groups.capabilities') {
        return { authority_gateway_id: 'install:owner', methods: ['groups.import_history'] }
      }

      if (method !== 'groups.import_history') { throw new Error(`Unexpected wire method ${method}`) }
      const request = structuredClone(params) as unknown as ShippedGroupImportRequest
      requests.push(request)

      return {
        source_id: request.source_id, imported_history: request.history.length, held_work: request.held_work.length,
        held_members: 0, retired_members: 0,
        room: { room_id: request.room_id, name: request.name, authority_gateway_id: 'install:owner',
          authority_epoch: 1, revision: 1, members: [], created_at: 1, updated_at: 1 }
      }
    }
  })

  return requests
}

it('same-checkpoint adopted history and unknown work survive unrelated read/write then whole-map rename', async () => {
  const a = await renderer('A')
  a.chat.updateGroupChat('Team', () => ({ ...room('room:original'),
    shippedAdoption: checkpoint('adopted'), hosted: 'install:owner', hostedEpoch: 1,
    hostedConnectionId: 'owner', continuityMode: 'gateway' }), { sync: false })
  a.chat.updateGroupChat('Independent', () => ({ ...room('room:independent'), stranded: {} }), { sync: false })
  const b = await renderer('B')
  const delayed = b.chat.durableGroupChatRooms()
  select('A')
  a.chat.updateGroupChat('Team', current => ({ ...current,
    log: [...current.log, { id: 'accepted:new', at: 2, from: { kind: 'user', name: 'You' }, text: 'new durable history' }],
    stranded: { ...current.stranded, newer: { before: 2, thread: 'legacy', turn: 'new-unknown-work' } },
    desktopCommandSettled: { ...current.desktopCommandSettled, 'receipt:new': 2 }, hostedSeq: 2
  }), { sync: false })
  const exact = structuredClone(a.storage.get<Record<string, GroupChat>>('group-chats', {}).Team)
  const sealed = JSON.parse(window.localStorage.getItem('hermes.plugin.hermes-bots.group-chats')!).Team
  expect(exact.log.at(-1)?.id).toBe('accepted:new')
  expect(exact.stranded?.newer).toMatchObject({ turn: 'new-unknown-work' })
  expect(exact.desktopCommandSettled?.['receipt:new']).toBe(2)
  expect(exact.hostedSeq).toBe(2)
  select('B')
  b.chat.updateGroupChat('Independent', current => ({ ...current, image: 'unrelated' }), { sync: false })
  expect(b.storage.get<Record<string, GroupChat>>('group-chats', {}).Team).toEqual(exact)
  const renamed = b.chat.durableGroupChatRooms()
  renamed.Renamed = renamed.Independent
  delete renamed.Independent
  b.chat.$groupChats.set(renamed)
  await b.chat.persistGroupChatRoomsRequired(renamed, b.storage)
  const saved = b.storage.get<Record<string, GroupChat>>('group-chats', {})
  expect.soft(saved.Team.log).toEqual(exact.log)
  expect.soft(saved.Team.stranded).toEqual(exact.stranded)
  expect.soft(saved.Team.desktopCommandSettled).toEqual(exact.desktopCommandSettled)
  expect.soft(saved.Team.hostedSeq).toBe(exact.hostedSeq)
  expect.soft(JSON.parse(window.localStorage.getItem('hermes.plugin.hermes-bots.group-chats')!).Team).toEqual(sealed)
  expect(saved.Renamed.image).toBe('unrelated')
  // A fresh observation is not hydration of this older captured payload.
  b.storage.get('group-chats', {})
  await b.chat.persistGroupChatRoomsRequired(delayed, b.storage)
  b.storage.set('group-chats', { ...delayed, Renamed: saved.Renamed })
  const late = b.storage.get<Record<string, GroupChat>>('group-chats', {})
  expect(late.Team).toEqual(exact)
  expect(late.Renamed.image).toBe('unrelated')
  expect(late.Independent).toBeUndefined()
  expect(JSON.parse(window.localStorage.getItem('hermes.plugin.hermes-bots.group-chats')!).Team).toEqual(sealed)
  const stale = { ...late, Team: { ...delayed.Team, image: 'stale same-room edit' } }
  const ownership = await import('./group-room-ownership')
  ownership.inheritGroupRoomSnapshot(delayed.Team, stale.Team)
  await expect(b.chat.persistGroupChatRoomsRequired(stale, b.storage, 'Team')).rejects.toThrow()
  expect(b.storage.get<Record<string, GroupChat>>('group-chats', {}).Team).toEqual(exact)
  const fresh = await renderer('C')
  const freshProjection = fresh.chat.durableGroupChatRooms().Team
  fresh.chat.updateGroupChat('Team', current => ({ ...current, image: 'current hosted edit' }), { sync: false })
  expect(fresh.storage.get<Record<string, GroupChat>>('group-chats', {}).Team).toEqual({ ...freshProjection, image: 'current hosted edit' })
})

it('legacy waiting checkpoint can progress to prepared/adopted', async () => {
  const requests = installImporter()
  const a = await renderer('A')
  a.chat.updateGroupChat('Team', () => ({ ...room(`r${'1'.repeat(32)}`),
    members: [{ name: 'helper', title: '', connectionId: 'local' }] }), { sync: false })
  const current = a.chat.$groupChats.get().Team
  const { createHash } = await import('node:crypto')

  const waiting: ShippedGroupAdoption = {
    version: 1, state: 'waiting', sourceId: `hermes.plugin.hermes-bots.group-chats:${current.roomId}`,
    roomId: current.roomId!, requestHash: createHash('sha256')
      .update(JSON.stringify(['Team', current.members || [], current.log || [], current.stranded || {}])).digest('hex')
  }

  a.chat.updateGroupChat('Team', current => ({ ...current, shippedAdoption: waiting }), { sync: false })
  await a.adoption.adoptShippedGroupChats(a.storage)
  const saved = a.storage.get<Record<string, GroupChat>>('group-chats', {}).Team
  expect.soft(requests).toHaveLength(1)
  expect.soft(saved.shippedAdoption?.state).toBe('adopted')
  expect.soft(saved.hosted).toBe('install:owner')
  a.adoption.stopShippedGroupAdoption()
})

it('legacy hash-only room can retain its acknowledged adopted room identity', async () => {
  const requests = installImporter()
  const a = await renderer('A')
  a.chat.updateGroupChat('Team', () => ({ ...room('unused'), roomId: null,
    members: [{ name: 'helper', title: '', connectionId: 'local' }] }), { sync: false })
  expect(a.chat.$groupChats.get().Team.desktopAuthorityHash).toMatch(/^[0-9a-f]{64}$/)
  await a.adoption.adoptShippedGroupChats(a.storage)
  const saved = a.storage.get<Record<string, GroupChat>>('group-chats', {}).Team
  expect.soft(requests).toHaveLength(1)
  expect.soft(saved.shippedAdoption?.state).toBe('adopted')
  expect.soft(saved.roomId).toBe(requests[0]?.room_id)
  expect.soft(saved.hosted).toBe('install:owner')
  a.adoption.stopShippedGroupAdoption()
})

it('waiting qualification requires the exact native admission and unchanged source snapshot', async () => {
  const a = await renderer('A')
  a.chat.updateGroupChat('Team', () => ({ ...room('room:original'),
    shippedAdoption: { ...checkpoint('waiting'), route: undefined } }), { sync: false })
  const current = a.chat.$groupChats.get().Team
  const expected = a.storage.get<Record<string, GroupChat>>('group-chats', {}).Team
  const ownership = await import('./group-room-ownership')

  const next = { ...a.chat.$groupChats.get(), Team: { ...current,
    shippedAdoption: { ...checkpoint('prepared'), requestHash: 'b'.repeat(64) } } }

  ownership.inheritGroupRoomSnapshot(current, next.Team)
  await expect(a.chat.persistGroupChatRoomsRequired(next, a.storage, 'Team')).rejects.toThrow()
  await expect(a.chat.persistGroupChatRoomsRequired(next, a.storage, 'Team', {
    preparation: { key: ownership.groupRoomOwnerKey('Team', current), token: 'no-admission' }
  })).rejects.toThrow()
  const release = ownership.acquireGroupRoomOwner('Team', current)

  try {
    next.Team.shippedAdoption = { ...next.Team.shippedAdoption, sourceId: 'replacement-source' }
    await expect(a.chat.persistGroupChatRoomsRequired(next, a.storage, 'Team', { preparation: release })).rejects.toThrow()
  } finally { release() }

  expect(a.storage.get<Record<string, GroupChat>>('group-chats', {}).Team).toEqual(expected)
})

it('hash-only acknowledgement rejects unqualified IDs and changed import identity', async () => {
  const a = await renderer('A')
  a.chat.updateGroupChat('Team', () => ({ ...room('unused'), roomId: null,
    shippedAdoption: checkpoint('prepared') }), { sync: false })
  const current = a.chat.$groupChats.get().Team
  const expected = a.storage.get<Record<string, GroupChat>>('group-chats', {}).Team
  const ownership = await import('./group-room-ownership')

  const next = { ...a.chat.$groupChats.get(), Team: { ...current,
    shippedAdoption: checkpoint('adopted'), roomId: 'room:original', hosted: 'install:owner',
    hostedConnectionId: 'owner', hostedEpoch: 1 } }

  ownership.inheritGroupRoomSnapshot(current, next.Team)
  await expect(a.chat.persistGroupChatRoomsRequired(next, a.storage, 'Team')).rejects.toThrow()
  next.Team.roomId = 'arbitrary-replacement'
  await expect(a.chat.persistGroupChatRoomsRequired(next, a.storage, 'Team', { acknowledgement: true })).rejects.toThrow()
  next.Team.roomId = 'room:original'
  next.Team.shippedAdoption.requestHash = 'b'.repeat(64)
  await expect(a.chat.persistGroupChatRoomsRequired(next, a.storage, 'Team', { acknowledgement: true })).rejects.toThrow()
  next.Team.shippedAdoption = { ...checkpoint('adopted'), route: {
    connectionId: 'different-owner', profile: 'default', authorityGatewayId: 'install:owner' } }
  await expect(a.chat.persistGroupChatRoomsRequired(next, a.storage, 'Team', { acknowledgement: true })).rejects.toThrow()
  expect(a.storage.get<Record<string, GroupChat>>('group-chats', {}).Team).toEqual(expected)
})

it('ordinary rename persists the new key through the production entrypoint', async () => {
  const a = await renderer('A')
  a.chat.updateGroupChat('Independent', () => ({ ...room('room:independent'), stranded: {} }), { sync: false })
  const delayed = structuredClone(a.chat.durableGroupChatRooms())
  const view = await import('./group-chat-view')
  expect(await view.renameGroupChat('Independent', 'Renamed', [])).toBe('Renamed')
  expect(a.chat.$groupChats.get().Renamed?.roomId).toBe('room:independent')
  expect(a.chat.$groupChats.get().Independent).toBeUndefined()
  const saved = a.storage.get<Record<string, GroupChat>>('group-chats', {})
  expect.soft(saved.Renamed?.roomId).toBe('room:independent')
  expect.soft(saved.Independent).toBeUndefined()
  await a.chat.persistGroupChatRoomsRequired(delayed, a.storage)
  expect(a.storage.get<Record<string, GroupChat>>('group-chats', {}).Independent).toBeUndefined()
  a.storage.set('group-chats', delayed)
  expect(a.storage.get<Record<string, GroupChat>>('group-chats', {}).Independent).toBeUndefined()
  a.storage.get('group-chats', {})
  a.storage.set('group-chats', { ...delayed, Renamed: saved.Renamed })
  expect(a.storage.get<Record<string, GroupChat>>('group-chats', {}).Independent).toBeUndefined()
  const cold = await renderer('C')
  expect(cold.chat.$groupChats.get().Renamed?.roomId).toBe('room:independent')
  expect(cold.chat.$groupChats.get().Independent).toBeUndefined()
})

it('remote same-ID rename survives the production pull, delayed saves and cold hydration', async () => {
  const gateway = createGroupGateway()
  Object.assign(host, gateway.host)
  const a = await renderer('A')
  a.chat.updateGroupChat('Original', () => ({ ...room('room:sync'), stranded: {}, syncRevision: 5 }), { sync: false })
  const delayed = a.chat.durableGroupChatRooms()
  const before = a.storage.get<Record<string, GroupChat>>('group-chats', {}).Original
  gateway.uiMeta[a.chat.GROUP_CHAT_SYNC_META_KEY] = {
    version: 3,
    rooms: { 'id:room:sync': {
      roomId: 'room:sync', name: 'Renamed', revision: 6, log: before.log,
      members: before.members, desktopAuthorityHash: before.desktopAuthorityHash
    } },
    deleted: {}
  }

  try {
    expect(await a.chat.pullGroupChatServerState()).toBe(true)
    a.chat.stopGroupChatServerSync()
    expect(a.chat.$groupChats.get().Original).toBeUndefined()
    expect(a.chat.$groupChats.get().Renamed?.roomId).toBe('room:sync')
    const saved = a.storage.get<Record<string, GroupChat>>('group-chats', {})
    expect(saved.Original).toBeUndefined()
    expect(saved.Renamed?.roomId).toBe('room:sync')
    expect(saved.Renamed?.syncRevision).toBe(6)
    await a.chat.persistGroupChatRoomsRequired(delayed, a.storage)
    expect(a.storage.get<Record<string, GroupChat>>('group-chats', {}).Original).toBeUndefined()
    const cold = await renderer('C')
    expect(cold.chat.$groupChats.get().Original).toBeUndefined()
    expect(cold.chat.$groupChats.get().Renamed?.roomId).toBe('room:sync')
    expect(cold.chat.$groupChats.get().Renamed?.syncRevision).toBe(6)
    expect(gateway.rpcFor('prompt.submit')).toHaveLength(0)
    cold.chat.stopGroupChatServerSync()
  } finally { a.chat.stopGroupChatServerSync() }
})

it('ordinary preflight waiting remains classic, distinct same-window tasks cannot share an admission lock, and release admits fresh work', async () => {
  const gateway = createGroupGateway()
  Object.assign(host, gateway.host)
  const a = await renderer('A')
  a.chat.updateGroupChat('Team', () => ({ ...room('room:original'), stranded: {},
    shippedPreflight: { ...checkpoint('waiting'), route: undefined } }), { sync: false })
  const { acquireGroupRoomOwner } = await import('./group-room-ownership')
  const release = acquireGroupRoomOwner('Team', a.chat.$groupChats.get().Team)
  expect(await a.turns.runGroupChatMemberTurn('Team', { name: 'helper', title: '' }, 'while locked', 'legacy')).toBeNull()
  expect(gateway.rpcFor('prompt.submit')).toHaveLength(0)
  release()
  await a.turns.runGroupChatMemberTurn('Team', { name: 'helper', title: '' }, 'fresh classic', 'legacy')
  expect(gateway.rpcFor('prompt.submit')).toHaveLength(1)
  const releaseAgain = acquireGroupRoomOwner('Team', a.chat.$groupChats.get().Team)
  releaseAgain()
})

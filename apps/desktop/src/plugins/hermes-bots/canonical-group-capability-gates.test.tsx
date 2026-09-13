import type * as HermesSdk from '@hermes/plugin-sdk'
import { host } from '@hermes/plugin-sdk'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import type { WritableAtom } from 'nanostores'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

import { readCanonicalGroupCreate } from './canonical-group-create'
import { CANONICAL_GROUP_LOCALES } from './canonical-group-locales'
import { $canonicalGroupBindings } from './canonical-group-registry'
import { CreateGroupChatDialog } from './create-dialog'
import { $botMeta } from './data'
import { $groupChats, $groupChatWorkspace, updateGroupChat } from './group-chat'
import type * as GroupChatModule from './group-chat'
import type * as GroupChatParts from './group-chat-parts'
import { GroupChatWorkspace } from './group-chat-view'
import { translateBots } from './i18n-test-helper'
import type { GroupChat } from './types'

const { request, notify, openWorkspace, activation } = vi.hoisted(() => ({ request: vi.fn(), notify: vi.fn(), openWorkspace: vi.fn(), activation: { epoch: 1 } }))
vi.mock('@hermes/plugin-sdk', async importOriginal => {
  const sdk = await importOriginal<typeof HermesSdk>()
  const { en } = await import('@/i18n/en')

  return {
    ...sdk,
    gatewayActivationEpoch: () => activation.epoch,
    host: {
      ...sdk.host, requestProfile: request, notify, openWorkspace,
      request: (method: string, params?: Record<string, unknown>) => request(null, method, params),
      connections: vi.fn(async () => []),
      state: {
        ...sdk.host.state,
        connectionId: sdk.atom<string | null>('local'),
        profile: sdk.atom('default'),
        gateway: sdk.atom('open')
      }
    },
    useI18n: () => ({ locale: 'en', t: en }),
    usePluginI18n: () => translateBots
  }
})
vi.mock('./group-chat', async importOriginal => {
  const actual = await importOriginal<typeof GroupChatModule>()

  return {
    ...actual,
    // Keep actual local creation, but do not schedule the unrelated remote mirror.
    updateGroupChat: vi.fn((group, mutate) => actual.updateGroupChat(group, mutate, { sync: false }))
  }
})
vi.mock('./group-chat-parts', async importOriginal => ({
  ...await importOriginal<typeof GroupChatParts>(),
  // Avatar generation is unrelated to the capability decision; never request a model.
  GroupImageControls: () => null
}))

const state = {
  connectionId: host.state.connectionId as WritableAtom<string | null>,
  profile: host.state.profile as WritableAtom<string>,
  gateway: host.state.gateway as WritableAtom<string>
}

const roster = [{ name: 'alpha', connectionId: 'local' }, { name: 'beta', connectionId: 'local' }]
const unavailable = CANONICAL_GROUP_LOCALES.en.driverUnavailable
const route = { connectionId: 'local', profile: 'default' }
const authorityId = 'installation:prepared-capabilities'
const canonical = { driver: true, persistent_process: true, authority_gateway_id: authorityId }
const originalDesktop = window.hermesDesktop
let journal: Record<string, unknown>

function retainedRoom(): GroupChat {
  return {
    roomId: 'original-retained-room', members: roster, watermarks: {},
    log: [{ id: 'retained-message', at: 1_700_000_000_000,
      from: { kind: 'user', name: 'You' }, text: 'Original retained history' }]
  }
}

function expectReadOnlyHistory() {
  // Prepared UX keeps history/Files available, not F9's removed inline Create
  // action. The security assertion is absence of the legacy mutation surface.
  expect(screen.getByText('Read only')).toBeTruthy()
  expect(screen.getByText('Original retained history')).toBeTruthy()
  expect(screen.queryByRole('textbox')).toBeNull()
  expect(screen.queryByRole('button', { name: /Send|Stop|Start gateway group|Retry|Resume/ })).toBeNull()
}

// Decision-relevant fields emitted by the actual canonical capabilities producer.
const canonicalUnavailable = {
  driver: false, persistent_process: true, features: ['room_identity', 'monotonic_log', 'replayable_disband']
}

// App-managed hosted capabilities keep their protocol/authority when the driver stops.
// Their RoomLink catalog deliberately reports persistent_process:false.
const appManagedUnavailable = {
  driver: false, persistent_process: false, protocol_version: 2,
  authority_gateway_id: 'installation:app-managed',
  features: ['authority_epoch', 'coordinator_fencing', 'room_identity', 'monotonic_log'],
  methods: ['groups.capabilities', 'groups.create', 'groups.state', 'groups.send']
}

const legacy = { driver: false, persistent_process: false }

const refused = [canonicalUnavailable, appManagedUnavailable, { ...legacy, methods: ['groups.create'] }, { driver: 'true', persistent_process: false }, null]

beforeEach(() => {
  activation.epoch = 1
  state.connectionId.set('local')
  state.profile.set('default')
  state.gateway.set('open')
  $canonicalGroupBindings.set({})
  $groupChats.set({ Existing: retainedRoom() })
  $groupChatWorkspace.set(null)
  $botMeta.set({})
  request.mockReset()
  notify.mockReset()
  openWorkspace.mockReset().mockReturnValue(() => undefined)
  vi.mocked(updateGroupChat).mockClear()
  journal = {}
  window.hermesDesktop = { ...originalDesktop, preparedSubmissions: {
    read: async () => JSON.stringify(journal), update: vi.fn(),
    compareAndSet: async (key: string, expected: string | null, entry: string | null) => {
      if (JSON.stringify(journal[key] ?? null) !== (expected ?? 'null')) {return false}

      if (entry === null) {delete journal[key]}
      else {journal[key] = JSON.parse(entry)}

      return true
    }
  } }
  Element.prototype.scrollIntoView = vi.fn()
  Element.prototype.hasPointerCapture = vi.fn(() => false)
  Element.prototype.releasePointerCapture = vi.fn()
  Element.prototype.setPointerCapture = vi.fn()
})
afterEach(() => {
  cleanup()
  window.hermesDesktop = originalDesktop
  $groupChats.set({})
  localStorage.clear()
  vi.restoreAllMocks()
})

function answer(capabilities: unknown) {
  request.mockImplementation(async (_route, method, params) => {
    if (method === 'groups.capabilities') {return capabilities}

    if (method === 'groups.create') {
      const prepared = await readCanonicalGroupCreate(route)
      expect(prepared?.authorityId).toBe(authorityId)
      expect(prepared?.params.room_id).toBe(params.room_id)

      return { room: { ...params, authority_gateway_id: authorityId } }
    }

    if (method === 'profiles.configure') {return {}}
    throw new Error(`Unexpected RPC: ${method}`)
  })
}

async function submitDialog() {
  const onCreated = vi.fn()
  const onClose = vi.fn()
  render(<CreateGroupChatDialog onClose={onClose} onCreated={onCreated} open roster={roster} />)

  for (const checkbox of screen.getAllByRole('checkbox')) {fireEvent.click(checkbox)}
  const create = screen.getByRole('button', { name: 'Create Group (2)' })
  // The real dialog first reads any durable setup. Never click through that
  // readiness gate or replace the production creation helper with a stub.
  await waitFor(() => expect((create as HTMLButtonElement).disabled).toBe(false))
  await act(async () => { fireEvent.click(create) })

  return { onCreated, onClose }
}

function pendingCreation() {
  let finish!: () => void
  const serverRooms = new Map<string, unknown>()
  request.mockImplementation(async (_route, method, params) => {
    if (method === 'groups.capabilities') {return canonical}

    if (method === 'groups.create') {
      const prepared = await readCanonicalGroupCreate(route)
      expect(prepared?.authorityId).toBe(authorityId)
      expect(prepared?.params.room_id).toBe(params.room_id)
      const room = { ...params, authority_gateway_id: authorityId }
      serverRooms.set(room.room_id, room)

      return new Promise(resolve => { finish = () => resolve({ room }) })
    }

    throw new Error(`Unexpected RPC: ${method}`)
  })

  return { serverRooms, finish: () => finish() }
}

it.each(refused)('classifies %j as unavailable on both surfaces: retained read-only history, no legacy creation', async value => {
  const before = $groupChats.get()
  answer(value)
  await act(async () => { render(<GroupChatWorkspace group="Existing" members={roster} />) })
  expectReadOnlyHistory()
  cleanup()

  const { onCreated, onClose } = await submitDialog()
  await waitFor(() => expect(notify).toHaveBeenCalledWith({ kind: 'error', message: unavailable }))
  expect(onCreated).not.toHaveBeenCalled()
  expect(onClose).not.toHaveBeenCalled()
  expect(updateGroupChat).not.toHaveBeenCalled()
  expect($groupChats.get()).toEqual(before)
  expect(journal).toEqual({})
  expect(request.mock.calls.map(call => call[1])).toEqual(['groups.capabilities', 'groups.capabilities'])
})

it('keeps positive classifications working: legacy renders and creates locally, canonical creates a gateway room', async () => {
  answer(legacy)
  await act(async () => { render(<GroupChatWorkspace group="Existing" members={roster} />) })
  expect(screen.getByRole('textbox')).toBeTruthy()
  cleanup()
  const legacyCreated = (await submitDialog()).onCreated
  await waitFor(() => expect(legacyCreated).toHaveBeenCalledOnce())
  expect(updateGroupChat).toHaveBeenCalledOnce()
  cleanup()

  answer(canonical)
  const { onCreated } = await submitDialog()
  await waitFor(() => expect(onCreated).toHaveBeenCalledOnce())
  expect(Object.values($canonicalGroupBindings.get())).toHaveLength(1)
  expect(updateGroupChat).toHaveBeenCalledOnce()
  expect(request.mock.calls.filter(call => call[1] === 'groups.create')).toHaveLength(1)
  expect(await readCanonicalGroupCreate(route)).toBeUndefined()
})

function moveSource(kind: 'profile' | 'gateway' | 'same-route-activation') {
  if (kind === 'profile') {state.profile.set('other')}

  if (kind === 'gateway') {state.gateway.set('closed')}

  if (kind === 'same-route-activation') {
    activation.epoch++
    state.profile.set('default')
  }
}

it.each(['profile', 'gateway', 'same-route-activation'] as const)('dialog: a creation approved before the %s moved is kept on its owner and never published', async kind => {
  const pending = pendingCreation()
  const { onCreated, onClose } = await submitDialog()
  await waitFor(() => expect(pending.serverRooms.size).toBe(1))
  const prepared = await readCanonicalGroupCreate(route)
  expect(prepared?.authorityId).toBe(authorityId)
  await act(async () => { moveSource(kind); pending.finish() })
  expect($canonicalGroupBindings.get()).toEqual({})
  expect(onCreated).not.toHaveBeenCalled()
  expect(onClose).not.toHaveBeenCalled()
  expect(pending.serverRooms.size).toBe(1)
  const creates = request.mock.calls.filter(call => call[1] === 'groups.create')
  expect(creates).toHaveLength(1)
  expect(creates[0][0]).toMatchObject(route)
  expect(creates[0][2].room_id).toBe(prepared?.binding.roomId)
})

it.each(['profile', 'gateway', 'same-route-activation'] as const)('workspace: a capability read before the %s moved cannot enable editing, create or open a room', async kind => {
  const before = $groupChats.get()
  let finish!: (value: unknown) => void
  request.mockImplementationOnce(() => new Promise(resolve => { finish = resolve }))
    .mockResolvedValue(canonicalUnavailable)
  render(<GroupChatWorkspace group="Existing" members={roster} />)
  await waitFor(() => expect(request).toHaveBeenCalledOnce())
  // The prepared workspace has no inline creation action. A positive legacy
  // receipt would enable its editor on the original source, but not after drift.
  await act(async () => { moveSource(kind); finish(legacy) })
  expectReadOnlyHistory()
  expect(request.mock.calls.filter(call => call[1] === 'groups.create')).toHaveLength(0)
  expect(request.mock.calls.every(call => call[1] === 'groups.capabilities')).toBe(true)
  expect(updateGroupChat).not.toHaveBeenCalled()
  expect($groupChats.get()).toEqual(before)
  expect(journal).toEqual({})
  expect(openWorkspace).not.toHaveBeenCalled()
})

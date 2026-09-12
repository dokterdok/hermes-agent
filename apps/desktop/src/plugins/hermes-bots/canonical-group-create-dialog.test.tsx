import type * as HermesSdk from '@hermes/plugin-sdk'
import { host } from '@hermes/plugin-sdk'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import type { WritableAtom } from 'nanostores'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

import { translateBots } from './i18n-test-helper'
const request = vi.hoisted(() => vi.fn())
const notify = vi.hoisted(() => vi.fn())
const activation = vi.hoisted(() => ({ epoch: 1 }))
vi.mock('@hermes/plugin-sdk', async importOriginal => {
  const original = await importOriginal<typeof HermesSdk>()

  return { ...original, gatewayActivationEpoch: () => activation.epoch, usePluginI18n: () => translateBots, host: { ...original.host,
    state: { ...original.host.state, connectionId: original.atom('local'), profile: original.atom('default'), gateway: original.atom('open') },
    connections: vi.fn(async () => []), notify, notifyError: vi.fn(),
    request: vi.fn(async () => ({})), requestProfile: request } }
})
vi.mock('./canonical-groups', async importOriginal => {
  const actual = await importOriginal<typeof CanonicalGroups>()

  return { ...actual, createCanonicalGroup: vi.fn(actual.createCanonicalGroup) }
})
vi.mock('./group-chat-parts', async importOriginal => ({
  ...await importOriginal<typeof GroupChatParts>(), GroupImageControls: () => null
}))
vi.mock('./group-chat', async importOriginal => {
  const actual = await importOriginal<typeof GroupChatModule>()

  return { ...actual, updateGroupChat: (group: string, mutate: Parameters<typeof actual.updateGroupChat>[1]) => actual.updateGroupChat(group, mutate, { sync: false }) }
})

import { readCanonicalGroupCreate } from './canonical-group-create'
import { $canonicalGroupBindings } from './canonical-group-registry'
import { createCanonicalGroup } from './canonical-groups'
import type * as CanonicalGroups from './canonical-groups'
import { CreateGroupChatDialog } from './create-dialog'
import { $botMeta } from './data'
import { $groupChats } from './group-chat'
import type * as GroupChatModule from './group-chat'
import type * as GroupChatParts from './group-chat-parts'

const originalDesktop = window.hermesDesktop
const route = { connectionId: 'local', profile: 'default' }
const roster = [{ name: 'writer', connectionId: 'local' }, { name: 'reviewer', connectionId: 'local' }]

const state = {
  connectionId: host.state.connectionId as WritableAtom<string>,
  profile: host.state.profile as WritableAtom<string>,
  gateway: host.state.gateway as WritableAtom<string>
}

let entries: Record<string, unknown>
beforeEach(() => {
  activation.epoch = 1
  entries = {}
  state.connectionId.set('local')
  state.profile.set('default')
  state.gateway.set('open')
  $canonicalGroupBindings.set({})
  $groupChats.set({})
  $botMeta.set({})
  vi.mocked(createCanonicalGroup).mockClear()
  window.hermesDesktop = { preparedSubmissions: {
    read: async () => JSON.stringify(entries), update: async () => {},
    compareAndSet: async (key: string, expected: string | null, entry: string | null) => {
      if (JSON.stringify(entries[key] ?? null) !== (expected ?? 'null')) {return false}

      if (entry === null) {delete entries[key]} else {entries[key] = JSON.parse(entry)}

      return true
    }
  } } as unknown as typeof window.hermesDesktop
  Element.prototype.scrollIntoView = () => undefined
  Element.prototype.hasPointerCapture = () => false
  Element.prototype.releasePointerCapture = () => undefined
  Element.prototype.setPointerCapture = () => undefined
})
afterEach(() => { cleanup(); window.hermesDesktop = originalDesktop; request.mockReset(); notify.mockReset(); localStorage.clear() })

async function chooseGroup(submit = true) {
  for (const checkbox of screen.getAllByRole('checkbox')) {fireEvent.click(checkbox)}
  fireEvent.change(screen.getByRole('textbox', { name: 'Group name' }), { target: { value: 'Saved team' } })
  const create = screen.getByRole('button', { name: /^Create Group/ })
  await waitFor(() => expect((create as HTMLButtonElement).disabled).toBe(false))

  if (submit) {fireEvent.click(create)}

  return create
}

it('offers the saved setup after failure and reopening, without new selections or an automatic retry', async () => {
  let first = true
  request.mockImplementation(async (_route, method, params) => {
    if (method === 'groups.capabilities') {return { driver: true, authority_gateway_id: 'install:home' }}

    if (method === 'groups.create') {
      if (first) {first = false; throw new Error('Unconfirmed setup')}

      return { room: { ...params, authority_gateway_id: 'install:home' } }
    }

    return {}
  })
  const closed = vi.fn(), created = vi.fn()
  const view = render(<CreateGroupChatDialog onClose={closed} onCreated={created} open roster={roster} />)
  await chooseGroup()
  await screen.findByRole('button', { name: 'Continue setup' })
  const entry = await readCanonicalGroupCreate(route)
  expect(entry?.params.name).toBe('Saved team')
  expect(created).not.toHaveBeenCalled()
  view.unmount()
  render(<CreateGroupChatDialog onClose={closed} onCreated={created} open roster={[]} />)
  const resume = await screen.findByRole('button', { name: 'Continue setup' })
  expect(screen.getByText('Saved team')).toBeTruthy()
  expect(request.mock.calls.filter(call => call[1] === 'groups.create')).toHaveLength(1)
  fireEvent.click(resume)
  await waitFor(() => expect(created).toHaveBeenCalledTimes(1))
  expect(closed).toHaveBeenCalledTimes(1)
  expect(request.mock.calls.filter(call => call[1] === 'groups.create').map(call => call[2].room_id)).toEqual([
    entry?.binding.roomId, entry?.binding.roomId
  ])
  expect(await readCanonicalGroupCreate(route)).toBeUndefined()
})

it.each([{ driver: false, features: ['canonical_session_owner'] }, {},
  { driver: false, persistent_process: false, protocol_version: 1 },
  { driver: false, persistent_process: false, authority_gateway_id: 'install:hosted' },
  { driver: false, persistent_process: false, methods: ['groups.create'] },
  { driver: false, persistent_process: false, protocol_version: 1, authority_gateway_id: 'install:hosted', methods: ['groups.create'] },
  { driver: 'true', authority_gateway_id: 'install:home' },
  { driver: false, persistent_process: true, features: ['room_identity', 'monotonic_log'] }
])('never starts a legacy room from canonical unavailability or an invalid capability reply: %j', async capabilities => {
  const before = $groupChats.get()
  request.mockImplementation(async (_route, method) => method === 'groups.capabilities' ? capabilities : {})
  const created = vi.fn()
  render(<CreateGroupChatDialog onClose={() => {}} onCreated={created} open roster={roster} />)
  await chooseGroup()
  await waitFor(() => expect(notify).toHaveBeenCalledWith(expect.objectContaining({ kind: 'error' })))
  expect(request.mock.calls.some(call => call[1] === 'groups.create')).toBe(false)
  expect($groupChats.get()).toEqual(before)
  expect(created).not.toHaveBeenCalled()
  expect(createCanonicalGroup).not.toHaveBeenCalled()
})

it('passes the initial authority ID to canonical creation without replacing the durable helper', async () => {
  request.mockImplementation(async (_route, method, params) => {
    if (method === 'groups.capabilities') {return { driver: true, authority_gateway_id: 'install:initial' }}

    if (method === 'groups.create') {return { room: { ...params, authority_gateway_id: 'install:initial' } }}

    return {}
  })
  const created = vi.fn()
  render(<CreateGroupChatDialog onClose={() => {}} onCreated={created} open roster={roster} />)
  await chooseGroup()
  await waitFor(() => expect(created).toHaveBeenCalledOnce())
  expect(createCanonicalGroup).toHaveBeenCalledExactlyOnceWith(route, 'Saved team', expect.any(Array), 'install:initial')
  expect(request.mock.calls.filter(call => call[1] === 'groups.create')).toHaveLength(1)
  expect(await readCanonicalGroupCreate(route)).toBeUndefined()
})

it.each([undefined, null, 1, '', '   ', 'x'.repeat(513)])('refuses canonical creation with an invalid initial authority ID: %j', async authorityId => {
  let first = true
  request.mockImplementation(async (_route, method, params) => {
    if (method === 'groups.capabilities') {
      const authority_gateway_id = first ? authorityId : 'install:later'
      first = false

      return { driver: true, authority_gateway_id }
    }

    if (method === 'groups.create') {return { room: { ...params, authority_gateway_id: 'install:later' } }}

    return {}
  })
  const created = vi.fn()
  render(<CreateGroupChatDialog onClose={() => {}} onCreated={created} open roster={roster} />)
  await chooseGroup()
  await waitFor(() => expect(request).toHaveBeenCalled())
  await act(async () => {})
  expect(createCanonicalGroup).not.toHaveBeenCalled()
  expect(created).not.toHaveBeenCalled()
  expect(entries).toEqual({})
  expect(request.mock.calls.filter(call => call[1] === 'groups.create')).toHaveLength(0)
  expect(notify).toHaveBeenCalledWith(expect.objectContaining({ kind: 'error' }))
})

function changeSource(changed: 'profile' | 'connection' | 'gateway') {
  if (changed === 'profile') {state.profile.set('other')}

  if (changed === 'connection') {state.connectionId.set('other')}

  if (changed === 'gateway') {state.gateway.set('closed')}
}

it.each(['profile', 'connection', 'gateway'] as const)('does not submit after %s changes during the initial capability read', async changed => {
  let finish!: (value: unknown) => void
  request.mockImplementationOnce(() => new Promise(resolve => { finish = resolve }))
    .mockResolvedValue({ driver: true, authority_gateway_id: 'install:home' })
  const created = vi.fn()
  render(<CreateGroupChatDialog onClose={() => {}} onCreated={created} open roster={roster} />)
  await chooseGroup()
  await waitFor(() => expect(request).toHaveBeenCalledOnce())
  await act(async () => { changeSource(changed); finish({ driver: true, authority_gateway_id: 'install:home' }) })
  expect(createCanonicalGroup).not.toHaveBeenCalled()
  expect(created).not.toHaveBeenCalled()
  expect(entries).toEqual({})
  expect($groupChats.get()).toEqual({})
})

it.each(['profile', 'connection', 'gateway'] as const)('does not adopt a journaled creation after %s changes while awaiting its result', async changed => {
  let finish!: () => void
  request.mockImplementation(async (_route, method, params) => {
    if (method === 'groups.capabilities') {return { driver: true, authority_gateway_id: 'install:home' }}

    if (method === 'groups.create') {return new Promise(resolve => { finish = () => resolve({ room: { ...params, authority_gateway_id: 'install:home' } }) })}

    return {}
  })
  const created = vi.fn(), closed = vi.fn()
  render(<CreateGroupChatDialog onClose={closed} onCreated={created} open roster={roster} />)
  await chooseGroup()
  await waitFor(() => expect(request.mock.calls.filter(call => call[1] === 'groups.create')).toHaveLength(1))
  const saved = await readCanonicalGroupCreate(route)
  expect(saved?.authorityId).toBe('install:home')
  await act(async () => { changeSource(changed); finish() })
  expect(created).not.toHaveBeenCalled()
  expect(closed).not.toHaveBeenCalled()
  expect($canonicalGroupBindings.get()).toEqual({})
  expect(request.mock.calls.filter(call => call[1] === 'groups.create').map(call => call[2].room_id)).toEqual([saved?.binding.roomId])
  expect(request.mock.calls.every(call => call[0].connectionId === 'local' && call[0].profile === 'default')).toBe(true)
})

it('preserves explicit nonpersistent legacy creation without a canonical journal', async () => {
  request.mockResolvedValue({ driver: false, persistent_process: false })
  const created = vi.fn()
  render(<CreateGroupChatDialog onClose={() => {}} onCreated={created} open roster={roster} />)
  await chooseGroup()
  await waitFor(() => expect(created).toHaveBeenCalledOnce())
  expect(createCanonicalGroup).not.toHaveBeenCalled()
  expect(entries).toEqual({})
  expect(Object.values($groupChats.get())[0].members).toHaveLength(2)
})

it('checks its captured source before a pre-render Create click can mutate anything', async () => {
  request.mockResolvedValue({ driver: false, persistent_process: false })
  render(<CreateGroupChatDialog onClose={() => {}} open roster={roster} />)
  const create = await chooseGroup(false)
  await act(async () => { state.profile.set('other'); fireEvent.click(create) })
  expect(request).not.toHaveBeenCalled()
  expect(entries).toEqual({})
  expect($groupChats.get()).toEqual({})
})

it('keeps a failed original intent without exposing recovery on a changed source', async () => {
  request.mockImplementation(async (_route, method) => {
    if (method === 'groups.capabilities') {return { driver: true, authority_gateway_id: 'install:home' }}

    if (method === 'groups.create') {throw new Error('Unconfirmed setup')}

    return {}
  })
  render(<CreateGroupChatDialog onClose={() => {}} open roster={roster} />)
  await chooseGroup()
  await screen.findByRole('button', { name: 'Continue setup' })
  const saved = await readCanonicalGroupCreate(route)
  await act(async () => { state.profile.set('other') })
  expect(screen.queryByRole('button', { name: 'Continue setup' })).toBeNull()
  expect((screen.getByRole('button', { name: /^Create Group/ }) as HTMLButtonElement).disabled).toBe(true)
  expect(await readCanonicalGroupCreate(route)).toEqual(saved)
  expect(request.mock.calls.filter(call => call[1] === 'groups.create')).toHaveLength(1)
})

it('preserves the saved ID while declining a resumed result after the source changes', async () => {
  let first = true
  let finish!: () => void
  request.mockImplementation(async (_route, method, params) => {
    if (method === 'groups.capabilities') {return { driver: true, authority_gateway_id: 'install:home' }}

    if (method === 'groups.create') {
      if (first) {first = false; throw new Error('Unconfirmed setup')}

      return new Promise(resolve => { finish = () => resolve({ room: { ...params, authority_gateway_id: 'install:home' } }) })
    }

    return {}
  })
  const created = vi.fn(), closed = vi.fn()
  render(<CreateGroupChatDialog onClose={closed} onCreated={created} open roster={roster} />)
  await chooseGroup()
  const resume = await screen.findByRole('button', { name: 'Continue setup' })
  const saved = await readCanonicalGroupCreate(route)
  fireEvent.click(resume)
  await waitFor(() => expect(request.mock.calls.filter(call => call[1] === 'groups.create')).toHaveLength(2))
  await act(async () => { state.profile.set('other') })
  await act(async () => { finish() })
  expect(created).not.toHaveBeenCalled()
  expect(closed).not.toHaveBeenCalled()
  expect($canonicalGroupBindings.get()).toEqual({})
  expect(request.mock.calls.filter(call => call[1] === 'groups.create').map(call => call[2].room_id)).toEqual([
    saved?.binding.roomId, saved?.binding.roomId
  ])
})

it('does not publish a late error or rewrite its journal on a changed source', async () => {
  let fail!: () => void
  request.mockImplementation(async (_route, method) => {
    if (method === 'groups.capabilities') {return { driver: true, authority_gateway_id: 'install:home' }}

    if (method === 'groups.create') {return new Promise((_resolve, reject) => { fail = () => reject(new Error('Unconfirmed setup')) })}

    return {}
  })
  render(<CreateGroupChatDialog onClose={() => {}} open roster={roster} />)
  await chooseGroup()
  await waitFor(() => expect(request.mock.calls.filter(call => call[1] === 'groups.create')).toHaveLength(1))
  const saved = await readCanonicalGroupCreate(route)
  await act(async () => { state.profile.set('other'); fail() })
  expect(notify).not.toHaveBeenCalled()
  expect(await readCanonicalGroupCreate(route)).toEqual(saved)
  expect(screen.queryByRole('button', { name: 'Continue setup' })).toBeNull()
})

it('retains offline journal hydration and reveals recovery only after its source reconnects', async () => {
  request.mockImplementation(async (_route, method) => {
    if (method === 'groups.capabilities') {return { driver: true, authority_gateway_id: 'install:home' }}

    if (method === 'groups.create') {throw new Error('Unconfirmed setup')}

    return {}
  })
  const first = render(<CreateGroupChatDialog onClose={() => {}} open roster={roster} />)
  await chooseGroup()
  await screen.findByRole('button', { name: 'Continue setup' })
  const saved = await readCanonicalGroupCreate(route)
  first.unmount()
  state.gateway.set('closed')
  await act(async () => { render(<CreateGroupChatDialog onClose={() => {}} open roster={[]} />) })
  expect(screen.queryByRole('button', { name: 'Continue setup' })).toBeNull()
  await act(async () => { state.gateway.set('open') })
  expect(screen.getByRole('button', { name: 'Continue setup' })).toBeTruthy()
  expect(await readCanonicalGroupCreate(route)).toEqual(saved)
  expect(request.mock.calls.filter(call => call[1] === 'groups.create')).toHaveLength(1)
})

function reactivate(kind: 'aba' | 'same-route') {
  if (kind === 'aba') {activation.epoch++; state.profile.set('other')}
  activation.epoch++
  state.profile.set('default')
}

it.each(['aba', 'same-route'] as const)('does not prepare a journal from a pre-%s capability', async kind => {
  let finish!: (value: unknown) => void
  request.mockImplementationOnce(() => new Promise(resolve => { finish = resolve }))
    .mockResolvedValue({ driver: true, authority_gateway_id: 'install:home' })
  render(<CreateGroupChatDialog onClose={() => {}} open roster={roster} />)
  await chooseGroup()
  await waitFor(() => expect(request).toHaveBeenCalledOnce())
  await act(async () => { reactivate(kind); finish({ driver: true, authority_gateway_id: 'install:home' }) })
  expect(createCanonicalGroup).not.toHaveBeenCalled()
  expect(entries).toEqual({})
})

it.each(['aba', 'same-route'] as const)('declines a journaled result after %s reactivation without resubmitting', async kind => {
  let finish!: () => void
  request.mockImplementation(async (_route, method, params) => {
    if (method === 'groups.capabilities') {return { driver: true, authority_gateway_id: 'install:home' }}

    if (method === 'groups.create') {return new Promise(resolve => { finish = () => resolve({ room: { ...params, authority_gateway_id: 'install:home' } }) })}

    return {}
  })
  const created = vi.fn(), closed = vi.fn()
  render(<CreateGroupChatDialog onClose={closed} onCreated={created} open roster={roster} />)
  await chooseGroup()
  await waitFor(() => expect(request.mock.calls.filter(call => call[1] === 'groups.create')).toHaveLength(1))
  const saved = await readCanonicalGroupCreate(route)
  await act(async () => { reactivate(kind); finish() })
  expect(created).not.toHaveBeenCalled()
  expect(closed).not.toHaveBeenCalled()
  expect($canonicalGroupBindings.get()).toEqual({})
  expect(request.mock.calls.filter(call => call[1] === 'groups.create').map(call => call[2].room_id)).toEqual([saved?.binding.roomId])
})

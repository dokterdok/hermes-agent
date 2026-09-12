import type * as HermesSdk from '@hermes/plugin-sdk'
import { host } from '@hermes/plugin-sdk'
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import type { WritableAtom } from 'nanostores'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

import { $canonicalGroupBindings } from './canonical-group-registry'
import { $groupChats } from './group-chat'
import { GroupChatWorkspace } from './group-chat-view'
import { translateBots } from './i18n-test-helper'
import type { GroupChat } from './types'

const request = vi.hoisted(() => vi.fn())
const activation = vi.hoisted(() => ({ epoch: 1 }))
vi.mock('@hermes/plugin-sdk', async importOriginal => {
  const sdk = await importOriginal<typeof HermesSdk>()
  const { en } = await import('@/i18n/en')

  return {
    ...sdk,
    gatewayActivationEpoch: () => activation.epoch,
    host: {
      ...sdk.host,
      requestProfile: request,
      request,
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

const state = {
  connectionId: host.state.connectionId as WritableAtom<string | null>,
  profile: host.state.profile as WritableAtom<string>,
  gateway: host.state.gateway as WritableAtom<string>
}

const fixture = (): GroupChat => ({
  roomId: 'old-classic-room',
  members: [{ name: 'writer', connectionId: 'local' }],
  watermarks: {},
  log: [{ id: 'one', at: 1_700_000_000_000, from: { kind: 'user', name: 'You' }, text: 'Saved text' }]
})

function readOnly() {
  expect(screen.getByText('Read only')).toBeTruthy()
  expect(screen.getByText('Saved text')).toBeTruthy()
  expect(screen.queryByRole('textbox')).toBeNull()
  expect(screen.queryByRole('button', { name: /Send|Stop|Start gateway group|Retry|Resume/ })).toBeNull()
  expect(request.mock.calls.every(call => call[1] === 'groups.capabilities')).toBe(true)
}

beforeEach(() => {
  activation.epoch = 1
  state.connectionId.set('local')
  state.profile.set('default')
  state.gateway.set('open')
  $canonicalGroupBindings.set({})
  $groupChats.set({ Workshop: fixture() })
  request.mockReset()
  Element.prototype.scrollIntoView = vi.fn()
})
afterEach(() => {
  cleanup()
  $groupChats.set({})
  localStorage.clear()
  vi.restoreAllMocks()
})

it.each([
  { driver: false, persistent_process: false, protocol_version: 1 },
  { driver: false, persistent_process: false, authority_gateway_id: 'install:hosted' },
  { driver: false, persistent_process: false, methods: ['groups.create'] },
  { driver: false, persistent_process: false, protocol_version: 1, authority_gateway_id: 'install:hosted', methods: ['groups.create'] },
  { driver: false, persistent_process: false, features: ['canonical_session_owner'] },
  { driver: true, persistent_process: false, features: ['canonical_session_owner'] },
  { driver: true, persistent_process: false },
  { persistent_process: false },
  { driver: null, persistent_process: false },
  { driver: 0, persistent_process: false },
  { driver: '', persistent_process: false },
  { driver: 'false', persistent_process: false },
  { driver: false, persistent_process: false, features: 'canonical_session_owner' },
  { driver: false, persistent_process: false, features: null },
  { driver: false, persistent_process: false, features: [null] },
  { driver: false, persistent_process: true, features: ['room_identity', 'monotonic_log', 'replayable_disband'] },
  { driver: false },
  { driver: false, persistent_process: null },
  { driver: false, persistent_process: 0 },
  { driver: false, persistent_process: '' },
  { driver: false, persistent_process: 'false' },
  null
])('never starts a legacy executor from unavailable/unknown capabilities: %j', async capabilities => {
  request.mockResolvedValue(capabilities)
  await act(async () => { render(<GroupChatWorkspace group="Workshop" members={fixture().members!} />) })
  readOnly()
  expect(request).toHaveBeenCalledExactlyOnceWith(
    { connectionId: 'local', profile: 'default', targetProfile: 'default', mode: 'local' },
    'groups.capabilities', { profile: 'default' }
  )
})

it.each([
  { driver: false, persistent_process: false },
  { driver: false, persistent_process: false, features: [] },
  { driver: false, persistent_process: false, features: ['legacy_files'] }
])(
  'preserves the existing legacy workspace for an explicit minimal nonpersistent reply: %j', async capabilities => {
    request.mockResolvedValue(capabilities)
    await act(async () => { render(<GroupChatWorkspace group="Workshop" members={fixture().members!} />) })
    expect(screen.getByRole('textbox')).toBeTruthy()
    expect(screen.queryByText('Read only')).toBeNull()
    expect(request.mock.calls.every(call => call[1] === 'groups.capabilities')).toBe(true)
  }
)

it('keeps a capabilities failure read-only rather than inferring an old server', async () => {
  request.mockRejectedValue(new Error('owner unavailable'))
  await act(async () => { render(<GroupChatWorkspace group="Workshop" members={fixture().members!} />) })
  readOnly()
})

it.each([
  { hosted: 'old-installation' },
  { hostedEpoch: 0 },
  { hostedConnectionId: 'local' },
  { continuityMode: 'hosted' },
  { members: [{ name: 'writer', connectionId: 'other-source' }] }
])('does not promote a hosted or different-source retained cache: %j', async extra => {
  const room = { ...fixture(), ...extra }
  $groupChats.set({ Workshop: room })
  request.mockResolvedValue({ driver: false, persistent_process: false })
  await act(async () => { render(<GroupChatWorkspace group="Workshop" members={room.members!} />) })
  readOnly()
  expect(request).not.toHaveBeenCalled()
})

it('ignores a late old-profile receipt after selecting a canonical authority', async () => {
  let resolve!: (result: unknown) => void
  request.mockImplementationOnce(() => new Promise(done => { resolve = done }))
    .mockResolvedValue({ driver: false, persistent_process: true, features: ['canonical_session_owner'] })
  render(<GroupChatWorkspace group="Workshop" members={fixture().members!} />)
  await waitFor(() => expect(request).toHaveBeenCalledOnce())
  await act(async () => { state.profile.set('named') })
  await act(async () => { resolve({ driver: false, persistent_process: false }) })
  readOnly()
  expect(request.mock.calls[1][0]).toMatchObject({ connectionId: 'local', profile: 'named' })
})

it('does not use one old server receipt to enable a different displayed member source', async () => {
  request.mockResolvedValue({ driver: false, persistent_process: false })
  await act(async () => {
    render(<GroupChatWorkspace group="Workshop" members={[{ name: 'writer', connectionId: 'other-source' }]} />)
  })
  readOnly()
  expect(request).not.toHaveBeenCalled()
})

it('retires the legacy receipt on reconnect and checks the now-canonical owner again', async () => {
  request.mockResolvedValueOnce({ driver: false, persistent_process: false })
    .mockResolvedValue({ driver: false, persistent_process: true, features: ['canonical_session_owner'] })
  await act(async () => { render(<GroupChatWorkspace group="Workshop" members={fixture().members!} />) })
  expect(screen.getByRole('textbox')).toBeTruthy()
  await act(async () => { state.gateway.set('closed') })
  readOnly()
  await act(async () => { state.gateway.set('open') })
  readOnly()
  expect(request).toHaveBeenCalledTimes(2)
})

it('does not bind replacement retained history after leaving a legacy workspace', async () => {
  request.mockResolvedValue({ driver: false, persistent_process: false })
  await act(async () => { render(<GroupChatWorkspace group="Workshop" members={fixture().members!} />) })
  expect(screen.getByRole('textbox')).toBeTruthy()
  await act(async () => { $groupChats.set({ Workshop: { ...fixture(), roomId: 'replacement' } }) })
  expect(screen.getByText('This retained room is no longer available.')).toBeTruthy()
  expect(screen.queryByText('Saved text')).toBeNull()
  expect(screen.queryByRole('textbox')).toBeNull()
})

it.each(['aba', 'same-route'] as const)('keeps a pre-%s legacy capability read-only after reactivation', async kind => {
  let finish!: (value: unknown) => void
  request.mockImplementationOnce(() => new Promise(resolve => { finish = resolve }))
    .mockResolvedValue({ driver: false, persistent_process: false, protocol_version: 1 })
  render(<GroupChatWorkspace group="Workshop" members={fixture().members!} />)
  await waitFor(() => expect(request).toHaveBeenCalledOnce())
  await act(async () => {
    if (kind === 'aba') {activation.epoch++; state.profile.set('other')}
    activation.epoch++
    state.profile.set('default')
    finish({ driver: false, persistent_process: false })
  })
  readOnly()
})

it('keeps retained Files browsable for an unavailable app-managed hosted owner', async () => {
  const room = fixture()
  room.log[0].images = [{ kind: 'file', name: 'report.txt', data: 'data:text/plain;base64,eA==' }]
  $groupChats.set({ Workshop: room })
  request.mockResolvedValue({ driver: false, persistent_process: false, protocol_version: 1,
    authority_gateway_id: 'install:hosted', methods: ['groups.create'] })
  await act(async () => { render(<GroupChatWorkspace group="Workshop" members={room.members!} />) })
  readOnly()
  fireEvent.click(screen.getByRole('button', { name: 'Files' }))
  expect((within(screen.getByRole('dialog')).getByRole('button', { name: 'Download: report.txt' }) as HTMLButtonElement).disabled).toBe(false)
  expect(request.mock.calls.every(call => call[1] === 'groups.capabilities')).toBe(true)
})

import { useStore } from '@nanostores/react'
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { atom } from 'nanostores'
import type { ComponentProps } from 'react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

import type * as DataModule from './data'

const { request, union } = vi.hoisted(() => ({ request: vi.fn(), union: { profiles: [] as unknown[] } }))
vi.mock('@hermes/plugin-sdk', async () => {
  const { pluginSdkMock, createGroupGateway } = await import('./group-test-utils')
  const gateway = createGroupGateway()
  const { en } = await import('@/i18n/en')
  const { CANONICAL_GROUP_LOCALES } = await import('./canonical-group-locales')
  const { captureGroupRequests } = await import('./canonical-group-contract-test-utils')
  const { ConfirmDialog } = await import('@/components/ui/confirm-dialog')

  return { ...await pluginSdkMock(gateway.host), atom, useValue: useStore, ConfirmDialog,
    useI18n: () => ({ t: en }),
    usePluginI18n: () => (key: string) => CANONICAL_GROUP_LOCALES.en[key.replace('canonical.', '') as keyof typeof CANONICAL_GROUP_LOCALES.en] ?? key,
    Button: (p: ComponentProps<'button'>) => <button {...p} />,
    host: { ...gateway.host, notify: vi.fn(), requestProfile: captureGroupRequests(request).request } }
})
vi.mock('./data', async importOriginal => ({ ...await importOriginal<typeof DataModule>(), cachedUnionRoster: () => union }))

import {
  answerGroupRequests, PEER_HOME as HOME, PEER_MINI as MINI, PEER_REGISTERED, peerGroupCapabilities, peerInvitation, peerRosterRow
} from './canonical-group-contract-test-utils'
import { CANONICAL_GROUP_LOCALES } from './canonical-group-locales'
import { $peerJoinErrors, peerJoinKey, peerRenewalSettled, renewPeers } from './canonical-group-peers'
import { $canonicalGroupBindings, registerCanonicalGroup } from './canonical-group-registry'
import { readCanonicalGroupSend } from './canonical-group-send'
import { CanonicalGroupWorkspace } from './canonical-group-workspace'

const words = CANONICAL_GROUP_LOCALES.en
const say = (text: string) => text.replace('{name}', 'Hermes · Mac Mini')
const binding = { connectionId: 'local', profile: 'default', roomId: 'linked' }
const pinned = { kind: 'peer', peer_id: 'install:mini', installation_id: 'install:mini', profile: 'default', capability_digest: MINI.room_link.catalog.catalog_digest }
const mini = peerRosterRow('mini', 'Mac Mini')

const room = {
  room_id: 'linked', name: 'Linked', authority_gateway_id: 'install:home', authority_epoch: 1, latest_seq: 0,
  members: [
    { member_id: 'default', profile: 'default', handle: 'hermes', target: { kind: 'local', profile: 'default' } },
    { member_id: 'coder', profile: 'coder', handle: 'coder', target: { kind: 'local', profile: 'coder' } },
    { member_id: 'hermes-mac-mini', profile: 'default', handle: 'hermes-mac-mini', display_name: 'Hermes · Mac Mini', target: pinned }
  ]
}

type Handler = (method: string, params: Record<string, any>) => unknown

/** The home (`local`) and the Mac Mini's gateway (`mini`), each answering as `HOME` / `MINI` unless its
 *  handler does; `offline` never answers. The home reports `routes`, and the room as ended once `ended`. */
function gateways({ home, peer, elsewhere = {}, routes = [] }: { home?: Handler; peer?: Handler; elsewhere?: Record<string, Handler>; routes?: unknown[] }) {
  const state = { ended: false, routes }

  answerGroupRequests(request, {
    ...elsewhere,
    offline: () => new Promise(() => undefined),
    mini: (method, params) => peer?.(method, params) ?? ({
      'groups.capabilities': MINI, 'groups.peer.invite': peerInvitation(), 'groups.peer.revoke': { revoked: true }
    } as Record<string, unknown>)[method],
    local: (method, params) => home?.(method, params) ?? ({
      'groups.capabilities': HOME, 'groups.log': { events: [] }, 'groups.peer.register': PEER_REGISTERED,
      'groups.state': { room: state.ended ? { ...room, disbanded_at: 2 } : room, driver_status: { running: true, peer_routes: state.routes } }
    } as Record<string, unknown>)[method]
  })

  return state
}

const refusal = (reason: string) => Object.assign(new Error(reason), { code: 4001, data: { reason } })
const status = () => screen.getByText(say(words.peerNotJoined)).closest('[role="status"]') as HTMLElement
const steps = () => request.mock.calls.map(([route, method]) => [route?.connectionId, method]).filter(([, method]) => method.startsWith('groups.peer'))
const SEATED = [['mini', 'groups.peer.invite'], ['local', 'groups.peer.register']]

async function retry() {
  await screen.findByText(say(words.peerNotJoined))
  await act(async () => { fireEvent.click(within(status()).getByRole('button', { name: 'Retry' })) })
}

beforeEach(() => {
  union.profiles = [mini]
  $peerJoinErrors.set({})
  $canonicalGroupBindings.set({})
})
afterEach(() => { cleanup(); request.mockReset() })

it('shows a Bot of another gateway that has not joined, and Retry seats it by invitation there and registration here', async () => {
  const state = gateways({ home: (method, params) => method === 'groups.peer.register'
    ? (state.routes = [{ room_id: 'linked', member_id: params.member_id, status: 'ready' }], PEER_REGISTERED) : undefined })

  render(<CanonicalGroupWorkspace binding={binding} />)
  await screen.findByText(say(words.peerNotJoined))
  expect(within(screen.getByRole('list', { name: words.members })).getByText(`· ${words.peerNotJoinedBadge}`)).toBeTruthy()

  await retry()
  await waitFor(() => expect(screen.queryByText(say(words.peerNotJoined))).toBeNull())
  expect(screen.queryByText(`· ${words.peerNotJoinedBadge}`)).toBeNull()
  expect(steps()).toEqual(SEATED)
  expect(request.mock.calls.find(call => call[1] === 'groups.peer.invite')![2]).toEqual({
    profile: 'default', room_id: 'linked', member_id: 'hermes-mac-mini', home_install_id: 'install:home',
    authority_gateway_id: 'install:home', authority_epoch: 1, ttl_seconds: 86400, status_ttl_seconds: 2592000 })
})

it.each([
  // An ordinary failure never revokes: revocation is scope-wide, and would end a live room's working grant.
  ['the home cannot reach it', { home: (method: string) => method === 'groups.peer.register' ? refusal('peer_unreachable') : undefined },
    words.peerJoinUnreachable, SEATED],
  ['its gateway lends another catalog now', { peer: (method: string) => method === 'groups.capabilities'
    ? { ...MINI, room_link: { ...MINI.room_link, catalog: { ...MINI.room_link.catalog, catalog_digest: 'd'.repeat(64) } } } : undefined },
  words.peerJoinChanged, []],
  ['no connection here reaches it', { peer: (method: string) => method === 'groups.capabilities' ? peerGroupCapabilities('install:other') : undefined },
    words.peerJoinConnect, []]
])('says why a Retry did not seat the Bot when %s', async (_case, handlers, why, asked) => {
  gateways(handlers)
  render(<CanonicalGroupWorkspace binding={binding} />)
  await retry()
  await waitFor(() => expect(within(status()).getByText(say(why))).toBeTruthy())
  expect(steps()).toEqual(asked)
})

it('says when a joined Bot\'s access ended, and offers Retry', async () => {
  gateways({ routes: [{ room_id: 'linked', member_id: 'hermes-mac-mini', status: 'needs_reauthorization' }] })
  render(<CanonicalGroupWorkspace binding={binding} />)
  const row = (await screen.findByText(say(words.peerRejoin))).closest('[role="status"]') as HTMLElement
  expect(within(row).getByRole('button', { name: 'Retry' })).toBeTruthy()
})

it('reaches the Bot\'s gateway on Retry without waiting on another connection that never answers', async () => {
  union.profiles = [peerRosterRow('offline', 'Old laptop'), mini]
  gateways({})
  render(<CanonicalGroupWorkspace binding={binding} />)
  await retry()
  await waitFor(() => expect(steps()).toEqual(SEATED))
})

const later = (answer: unknown) => new Promise(resolve => setTimeout(() => resolve(answer), 0))
const oneDay = { ...MINI, features: MINI.features.filter(feature => feature !== 'peer_renewal_horizon') }
const viaSsh = { ...MINI, room_link: { ...MINI.room_link, catalog: { ...MINI.room_link.catalog, persistent_process: false, catalog_digest: 'e'.repeat(64) } } }

it.each([
  // The same computer, also reached as a `hermes serve` that grants one day only, answering first.
  ['grants the longer renewal horizon', oneDay, MINI, 2592000],
  // The same computer, also reached over SSH through a backend Desktop started, answering first with a
  // catalog the pin can't match; the Mac Mini connection is a Remote gateway `hermes serve`.
  ['can host it, when another connection to that computer cannot', viaSsh, oneDay, undefined]
])('seats the Bot through the connection that %s', async (_case, first, chosen, horizon) => {
  union.profiles = [peerRosterRow('first', 'Mac Mini (first)'), mini]
  gateways({ elsewhere: { first: method => method === 'groups.capabilities' ? first : undefined },
    peer: method => method === 'groups.capabilities' ? later(chosen) : undefined })
  render(<CanonicalGroupWorkspace binding={binding} />)
  await retry()
  await waitFor(() => expect(steps()).toEqual(SEATED))
  expect(request.mock.calls.find(call => call[1] === 'groups.peer.invite')![2].status_ttl_seconds).toBe(horizon)
})

it('never invites the Bot again for a room that has ended', async () => {
  const state = gateways({ peer: method => method === 'groups.capabilities' ? (state.ended = true, MINI) : undefined })
  render(<CanonicalGroupWorkspace binding={binding} />)
  await retry()
  await act(async () => {})
  expect(steps()).toEqual([])
})

it('revokes at its gateway a grant minted for a room that ended before it was registered', async () => {
  const state = gateways({
    home: method => method === 'groups.peer.register' ? refusal('invalid_params') : undefined,
    peer: method => method === 'groups.peer.invite' ? (state.ended = true, undefined) : undefined
  })

  render(<CanonicalGroupWorkspace binding={binding} />)
  await retry()
  await waitFor(() => expect(steps()).toEqual([...SEATED, ['mini', 'groups.peer.revoke']]))
  expect(request.mock.calls.find(call => call[1] === 'groups.peer.revoke')![2].grant).toBe('grant')
})

it('starts the group again without a Bot that can\'t join, then ends this one', async () => {
  const onReplaced = vi.fn()
  gateways({ home: (method, params) => method === 'groups.create'
    ? { room: { ...room, room_id: params.room_id, members: params.members } }
    : method === 'groups.disband' ? { tombstone: { room_id: 'linked', disbanded_at: 1 } } : undefined })
  registerCanonicalGroup({ connectionId: 'local', profile: 'default' }, room)
  render(<CanonicalGroupWorkspace binding={binding} onReplaced={onReplaced} />)
  await screen.findByText(say(words.peerNotJoined))
  // The control says what it does: a new group without that Bot, earlier messages not copied.
  fireEvent.click(within(status()).getByRole('button', { name: 'Start new group without Hermes · Mac Mini' }))
  const dialog = await screen.findByRole('dialog')
  expect(within(dialog).getByText('Start new group without Hermes · Mac Mini')).toBeTruthy()
  expect(within(dialog).getByText(words.peerStartWithoutConfirm)).toBeTruthy()
  expect(words.peerStartWithoutConfirm).toContain('Earlier messages aren’t copied')
  await act(async () => { fireEvent.click(within(dialog).getByRole('button', { name: words.startGatewayGroup })) })
  await waitFor(() => expect(onReplaced).toHaveBeenCalledOnce())

  const create = request.mock.calls.find(call => call[1] === 'groups.create')![2]
  expect(create).toEqual({ profile: 'default', room_id: create.room_id, name: 'Linked', members: room.members.slice(0, 2) })
  expect(request.mock.calls.map(call => call[1]).filter(method => method === 'groups.create' || method === 'groups.disband'))
    .toEqual(['groups.create', 'groups.disband'])
  expect(Object.values($canonicalGroupBindings.get()).map(entry => entry.roomId)).toEqual([create.room_id])
})

it('shows the badge but no setup controls on a gateway that cannot register Bots of other gateways', async () => {
  gateways({ home: method => method === 'groups.capabilities' ? { ...HOME, methods: HOME.methods.filter(name => !name.startsWith('groups.peer')) } : undefined })
  render(<CanonicalGroupWorkspace binding={binding} />)
  await screen.findByText(`· ${words.peerNotJoinedBadge}`)
  await act(async () => {})
  expect(screen.queryByText(words.peerNotJoined.replace('{name}', 'Hermes · Mac Mini'))).toBeNull()
})

const joined = [{ room_id: 'linked', member_id: 'hermes-mac-mini', status: 'ready' }]
const sent = (params: Record<string, any>) => ({ event: {}, client_event_id: params.event_id, accepted: true, driver_started: true })
const calls = (method: string) => request.mock.calls.filter(call => call[1] === method)
const textbox = () => screen.getByRole('textbox') as HTMLTextAreaElement

/** A registration on the home that answers once the test releases it. */
function heldRegistration() {
  let release!: () => void
  const held = new Promise<void>(resolve => { release = resolve })

  return { release, register: () => held.then(() => PEER_REGISTERED) }
}

async function send(text: string) {
  await waitFor(() => expect(textbox().disabled).toBe(false))
  fireEvent.change(textbox(), { target: { value: text } })
  fireEvent.click(screen.getByRole('button', { name: 'Send' }))
}

it('renews a Bot that is due when a message is sent: the send is journaled first, leaves after the renewal, and the seat is fresh', async () => {
  union.profiles = []
  const { release, register } = heldRegistration()
  gateways({ routes: joined, home: (method, params) => method === 'groups.peer.register' ? register() : method === 'groups.send' ? sent(params) : undefined })
  const sending = { ...binding, roomId: 'renew-on-send' }
  render(<CanonicalGroupWorkspace binding={sending} />)
  // Its connection had not loaded when the room opened: nothing was renewed yet.
  await waitFor(() => expect(textbox().disabled).toBe(false))
  expect(calls('groups.peer.invite')).toHaveLength(0)
  union.profiles = [mini]
  await send('Keep this across a restart')
  // Journaled before it waits: a Desktop that quits now keeps it.
  await waitFor(async () => expect((await readCanonicalGroupSend(sending))?.params.payload.text).toBe('Keep this across a restart'))
  expect(calls('groups.send')).toHaveLength(0)
  await act(async () => { release() })
  await waitFor(() => expect(calls('groups.send')).toHaveLength(1))
  expect(request.mock.calls.map(call => call[1]).filter(method => ['groups.peer.register', 'groups.send'].includes(method)))
    .toEqual(['groups.peer.register', 'groups.send'])
  // Fresh now: renewing again asks for nothing.
  renewPeers(sending, room, joined, [mini])
  await peerRenewalSettled(sending)
  expect(steps()).toEqual(SEATED)
})

it('waits for a renewal as long as the home may take to probe the Bot\'s gateway', async () => {
  vi.useFakeTimers()

  try {
    const { release, register } = heldRegistration()
    gateways({ home: method => method === 'groups.peer.register' ? register() : undefined })
    const probing = { ...binding, roomId: 'slow-probe' }
    renewPeers(probing, room, joined, [mini])
    await vi.waitFor(() => expect(calls('groups.peer.register')).toHaveLength(1))
    let waited = false
    void peerRenewalSettled(probing).then(() => { waited = true })
    // The home's probe of the member's gateway may take up to 30 s.
    await vi.advanceTimersByTimeAsync(31_000)
    expect(waited).toBe(false)
    release()
    await vi.advanceTimersByTimeAsync(0)
    expect(waited).toBe(true)
  } finally {
    vi.useRealTimers()
  }
})

it.each([
  // Nothing is attempted, so nothing is noted against the Bot.
  ['has not loaded yet', [], undefined],
  ['has other computers\' Bots, not yet the Mac Mini\'s', [peerRosterRow('studio', 'Studio')], 'peer_connection_missing']
])('renews a Bot as soon as its connection loads, when the roster %s', async (_case, first, noted) => {
  gateways({ elsewhere: { studio: method => method === 'groups.capabilities' ? peerGroupCapabilities('install:studio') : undefined } })
  const loading = { ...binding, roomId: `roster-${first.length}` }
  renewPeers(loading, room, joined, first)
  await peerRenewalSettled(loading)
  expect(($peerJoinErrors.get()[peerJoinKey(loading, 'hermes-mac-mini')] as { reason?: string } | undefined)?.reason).toBe(noted)
  renewPeers(loading, room, joined, [...first, mini])
  await waitFor(() => expect(steps()).toEqual(SEATED))
})

it('holds a Send back when Stop is pressed while it waits for a renewal', async () => {
  const { release, register } = heldRegistration()
  gateways({ routes: joined, home: (method, params) => method === 'groups.peer.register' ? register()
    : method === 'groups.send' ? sent(params) : method === 'groups.stop' ? { cancelled: 0 } : undefined })
  const stopping = { ...binding, roomId: 'stop-while-renewing' }
  render(<CanonicalGroupWorkspace binding={stopping} />)
  // Opening the room renews its Bot.
  await waitFor(() => expect(calls('groups.peer.register')).toHaveLength(1))
  await send('Never mind')
  await waitFor(async () => expect((await readCanonicalGroupSend(stopping))?.params.payload.text).toBe('Never mind'))
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Stop' })) })
  await act(async () => { release() })
  await waitFor(() => expect(textbox().value).toBe('Never mind'))
  expect(calls('groups.send')).toHaveLength(0)
})

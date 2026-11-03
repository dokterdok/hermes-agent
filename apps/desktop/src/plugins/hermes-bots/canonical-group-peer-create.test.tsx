import type * as HermesSdk from '@hermes/plugin-sdk'
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

import {
  answerGroupRequests, CANONICAL_GROUP_CAPABILITIES, PEER_HOME as HOME, PEER_MINI as MINI, PEER_REGISTERED, peerInvitation, peerRosterRow,
  STANDALONE_GROUP_CAPABILITIES
} from './canonical-group-contract-test-utils'
import { CANONICAL_GROUP_LOCALES } from './canonical-group-locales'
import { peerTargetState } from './canonical-group-peers'
import { $canonicalGroupBindings, publishCanonicalGroup } from './canonical-group-registry'
import { canonicalGroupRoster } from './canonical-groups'
import { CreateGroupChatDialog } from './create-dialog'
import { $botMeta } from './data'
import { renderGatewayRooms } from './gateway-group-rooms-test-harness'
import { $groupChats, $groupChatWorkspace, updateGroupChat } from './group-chat'
import type * as GroupChatParts from './group-chat-parts'
import { GroupChatWorkspace } from './group-chat-view'
import { scriptedStorage } from './group-test-utils'
import { translateBots } from './i18n-test-helper'
import { setPluginCtx } from './shared'
import type { RosterRow } from './types'

const { request, notify } = vi.hoisted(() => ({ request: vi.fn(), notify: vi.fn() }))
vi.mock('@hermes/plugin-sdk', async importOriginal => (await import('./canonical-group-contract-test-utils'))
  .localGatewaySdkMock(await importOriginal<typeof HermesSdk>(), request, () => translateBots, { notify }))
vi.mock('./group-chat-parts', async importOriginal => ({ ...await importOriginal<typeof GroupChatParts>(), GroupImageControls: () => null }))

const words = CANONICAL_GROUP_LOCALES.en

const roster: RosterRow[] = [{ name: 'default', connectionId: 'local' }, peerRosterRow('mini', 'Mac Mini'), peerRosterRow('mini', 'Mac Mini', 'coder'),
  peerRosterRow('nas', 'NAS')]

type Answer = (method: string, params: Record<string, any>) => unknown

const home = (overrides: Answer = () => undefined): Answer => (method, params) => overrides(method, params) ?? ({
  'groups.capabilities': HOME,
  'groups.create': { room: { room_id: params.room_id, name: params.name, members: params.members, authority_gateway_id: 'install:a18b0000000000000000000000000000', authority_epoch: 1 } },
  'groups.peer.register': PEER_REGISTERED
} as Record<string, unknown>)[method]

const mini = (capabilities: unknown = MINI, invitation: unknown = peerInvitation('grant-for-linked')): Answer => method =>
  ({ 'groups.capabilities': capabilities, 'groups.peer.invite': invitation } as Record<string, unknown>)[method]

const nas: Answer = method => method === 'groups.capabilities'
  ? { ...STANDALONE_GROUP_CAPABILITIES, room_link: { enabled: false, reason: 'gateway_roomlink_secret_unavailable' } } : undefined

const gateways = (local = home(method => method === 'groups.capabilities' ? { ...HOME, room_link: { enabled: false } } : undefined), peer = mini()) => answerGroupRequests(request, { local, mini: peer, nas })

beforeEach(() => {
  $canonicalGroupBindings.set({})
  $botMeta.set({})
  $groupChats.set({})
  request.mockReset()
  notify.mockReset()
  Element.prototype.scrollIntoView = vi.fn()
})
afterEach(() => { cleanup(); setPluginCtx(null); localStorage.clear() })

const box = (row: Element | undefined) => row?.closest('label')?.querySelector('button[role="checkbox"]') as HTMLButtonElement
const localOnly = () => request.mock.calls.every(call => call[0]?.connectionId === 'local')

async function openDialog(onCreated = vi.fn()) {
  const view = render(<CreateGroupChatDialog onClose={vi.fn()} onCreated={onCreated} open roster={roster} />)
  await waitFor(() => expect(request.mock.calls.some(call => call[0]?.connectionId === 'mini')).toBe(true))
  await act(async () => {})

  return { ...view, onCreated, rows: [...view.baseElement.querySelectorAll('[data-bot-face="default"]')] }
}

/** Pick this connection's and the Mac Mini's default Bots, create, and wait for the room. */
async function createWithMini({ rows, onCreated }: Awaited<ReturnType<typeof openDialog>>) {
  fireEvent.click(box(rows[0]))
  fireEvent.click(box(rows[1]))
  fireEvent.change(screen.getByRole('textbox', { name: 'Group name' }), { target: { value: 'Team' } })
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Create Group (2)' })) })
  await waitFor(() => expect(onCreated).toHaveBeenCalledOnce())
}

it('seats a Bot from another connection: the room on this gateway, the invitation on its own, the registration here', async () => {
  gateways()
  const dialog = await openDialog()
  const { baseElement, rows } = dialog
  expect(baseElement.querySelector('[data-bot-face="coder"]')).toBeTruthy()
  expect(rows).toHaveLength(3)
  expect(box(rows[2]).disabled).toBe(false)
  await createWithMini(dialog)

  const calls = request.mock.calls.map(([route, method, params]) => [route?.connectionId, method, params])
  const create = calls.find(call => call[1] === 'groups.create')![2]
  expect(calls.filter(call => call[1] !== 'groups.capabilities').map(call => [call[0], call[1]])).toEqual([
    ['local', 'groups.create'], ['mini', 'groups.peer.invite'], ['local', 'groups.peer.register']])
  // Two default Bots: the one from Mac Mini joins under a handle and name that say where it runs.
  expect(create.members).toEqual([
    { member_id: 'default', profile: 'default', handle: 'hermes', target: { kind: 'local', profile: 'default' } },
    { member_id: 'hermes-mac-mini', profile: 'default', handle: 'hermes-mac-mini', display_name: 'Hermes · Mac Mini',
      target: { kind: 'peer', peer_id: 'install:3f2a0000000000000000000000000000', installation_id: 'install:3f2a0000000000000000000000000000', profile: 'default',
        capability_digest: MINI.room_link.catalog.catalog_digest } }])
  expect(calls.find(call => call[1] === 'groups.peer.invite')![2]).toEqual({
    profile: 'default', room_id: create.room_id, member_id: 'hermes-mac-mini', home_install_id: 'install:a18b0000000000000000000000000000',
    authority_gateway_id: 'install:a18b0000000000000000000000000000', authority_epoch: 1, ttl_seconds: 86400, status_ttl_seconds: 2592000 })
  // The grant goes only to the endpoint in the catalog the room pinned.
  expect(calls.find(call => call[1] === 'groups.peer.register')![2]).toEqual({
    profile: 'default', room_id: create.room_id, member_id: 'hermes-mac-mini', target_url: MINI.room_link.catalog.endpoint.url,
    target_profile: 'default', grant: 'grant-for-linked', catalog: MINI.room_link.catalog })
  expect(Object.values($canonicalGroupBindings.get()).map(binding => binding.roomId)).toEqual([create.room_id])
  expect(notify).not.toHaveBeenCalled()
  expect($groupChats.get()[create.name].engine).toMatchObject({ kind: 'gateway', connectionId: 'local', roomId: create.room_id })
})

it.each([
  ['the home cannot reach its computer', home(method => method === 'groups.peer.register'
    ? Object.assign(new Error('peer_unreachable'), { code: 4001, data: { reason: 'peer_unreachable' } }) : undefined), mini(),
  words.peerJoinUnreachable, true],
  // The grant goes nowhere but the endpoint in the catalog the room pinned.
  ['its invitation names another endpoint than its catalog', home(), mini(MINI, peerInvitation('grant-for-linked',
    { available: true, url: 'https://other.example.ts.net' })), words.peerJoinChanged, false]
])('opens the room even when a Bot does not join (%s), and says why', async (_case, local, peer, why, registers) => {
  gateways(local, peer)
  await createWithMini(await openDialog())
  expect(notify).toHaveBeenCalledWith({ kind: 'error', message: why.replace('{name}', 'Hermes · Mac Mini') })
  expect(request.mock.calls.some(call => call[1] === 'groups.peer.register')).toBe(registers)
  expect(Object.values($canonicalGroupBindings.get())).toHaveLength(1)
  expect(request.mock.calls.filter(call => call[1] === 'groups.create')).toHaveLength(1)
  expect(Object.values($groupChats.get())).toHaveLength(1)
  expect(Object.values($groupChats.get())[0].engine).toMatchObject({ kind: 'gateway' })
})

it.each([
  ['cannot register peers', CANONICAL_GROUP_CAPABILITIES],
  ['ends with Desktop', { ...HOME, persistent_process: false }]
])('keeps the courier and its full picker when the owner %s', async (_case, capabilities) => {
  gateways(home(method => method === 'groups.capabilities' ? capabilities : undefined))
  const { baseElement } = render(<CreateGroupChatDialog onClose={vi.fn()} open roster={roster} />)
  await waitFor(() => expect(request).toHaveBeenCalled())
  await act(async () => {})
  expect(baseElement.querySelector('[data-bot-face="coder"]')).toBeTruthy()
  const rows = [...baseElement.querySelectorAll('[data-bot-face="default"]')]
  fireEvent.click(box(rows[0]))
  fireEvent.click(box(rows[1]))
  expect(baseElement.querySelector('[data-group-engine="desktop"]')).toBeTruthy()
})

it.each(['owner', 'lender', 'seatable'])('revalidates %s capabilities before any room is written', async kind => {
  let changed = false
  gateways(home(method => method === 'groups.capabilities'
    ? (changed && kind === 'owner' ? CANONICAL_GROUP_CAPABILITIES : changed && kind === 'seatable' ? { ...HOME, seatable_profiles: [] } : HOME) : undefined),
  method => method === 'groups.capabilities' && changed && kind === 'lender' ? CANONICAL_GROUP_CAPABILITIES : mini()(method, {}))
  const { rows, baseElement } = await openDialog()
  fireEvent.click(box(rows[0]))
  fireEvent.click(box(rows[1]))
  expect(baseElement.querySelector('[data-group-engine="gateway-peer"]')).toBeTruthy()
  changed = true
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Create Group (2)' })) })
  expect(request.mock.calls.some(call => call[1] === 'groups.create')).toBe(false)
  expect($groupChats.get()).toEqual({})
  expect(notify).toHaveBeenCalledWith(expect.objectContaining({ kind: 'error' }))
})

it('leaves a classic (Desktop-run) connection\'s mixed selection as it was', async () => {
  answerGroupRequests(request, { local: () => ({ driver: false, persistent_process: false }) })
  const { baseElement } = render(<CreateGroupChatDialog onClose={vi.fn()} open roster={roster} />)
  await waitFor(() => expect(request).toHaveBeenCalled())
  await act(async () => {})
  const rows = [...baseElement.querySelectorAll('[data-bot-face="default"]')]
  fireEvent.click(box(rows[0]))
  fireEvent.click(box(rows[1]))
  expect((screen.getByRole('button', { name: 'Create Group (2)' }) as HTMLButtonElement).disabled).toBe(false)
  expect(localOnly()).toBe(true)
})

const route = { connectionId: 'local', profile: 'default' }
const pin = () => ({ installationId: 'install:3f2a0000000000000000000000000000', catalogDigest: 'c'.repeat(64) })
const peer = (connectionId: string, connectionLabel: string) => ({ name: 'default', handle: 'hermes', connectionId, connectionLabel, remoteSource: true })

it('refuses a roster whose Bots would share a handle or id even after naming their computer', () => {
  const here = { name: 'default', handle: 'hermes', connectionId: 'local' }
  const mac = peer('mini', 'Mac Mini')
  expect(canonicalGroupRoster(route, [here, mac])).toEqual({ problem: 'otherConnection' })
  expect(canonicalGroupRoster(route, [here, { name: 'hermes-mac-mini', connectionId: 'local' }, mac], pin))
    .toEqual({ problem: 'duplicateMember' })
  const seated = canonicalGroupRoster(route, [{ name: 'alpha', connectionId: 'local' }, { ...mac, title: 'Reviewer' }], pin)
  expect('roster' in seated && seated.roster[1]).toMatchObject({ member_id: 'hermes', handle: 'hermes', display_name: 'Reviewer · Mac Mini' })
})

it('gives Bots from distinct connections distinct handles, whatever script their connections are named in', () => {
  for (const [first, second] of [[peer('home-box', '自宅'), peer('office-box', '会社')], [peer('mini', 'Mac Mini'), peer('mini-2', 'Mac-Mini')]]) {
    const seated = canonicalGroupRoster(route, [{ name: 'alpha', connectionId: 'local' }, first, second], pin)
    const handles = 'roster' in seated ? seated.roster.map(member => member.handle) : []
    expect(new Set(handles).size).toBe(3)
    expect(handles.every(handle => /^[a-z0-9][a-z0-9_-]*$/.test(handle))).toBe(true)
  }
})

it('reads whether another gateway can host a Bot here from its own capabilities', () => {
  const catalog = MINI.room_link.catalog
  const link = (change: Record<string, unknown>) => ({ ...MINI, room_link: { ...MINI.room_link, catalog: { ...catalog, ...change } } })
  expect([
    CANONICAL_GROUP_CAPABILITIES, { ...MINI, room_link: { enabled: false, reason: 'api_server_required' } },
    link({ protocol_versions: [1] }), link({ link_modes: ['relay'] }), link({ persistent_process: false }), link({ attachments: true }), link({ execution_policy: { target_profile: 'coder' } }),
    link({ endpoint: { available: false, reason: 'room_link_url_required' } }), link({ installation_id: 'install:other' })
  ].map(answer => (peerTargetState(answer) as { reason?: string }).reason)).toEqual(['peer_invite_unsupported', 'api_server_required',
    'peer_target_unsupported', 'peer_target_unsupported', 'persistent_process_required', 'text_only_required', 'default_profile_required', 'endpoint_required', 'room_link_unavailable'])
  // A lender without the renewal feature gets no extended horizon.
  expect(peerTargetState({ ...MINI, features: STANDALONE_GROUP_CAPABILITIES.features })).toMatchObject({ ready: true, renewal: false })
})


/** Canonical lifecycle behind the creation dialog; all writes use the requested room identity. */
function lifecycleGateway(older: Array<{ room_id: string; name: string; members: never[] }> = [], disband = 'matching') {
  const state = { room: { room_id: '', name: 'Team', members: [], authority_gateway_id: HOME.authority_gateway_id, authority_epoch: 1 }, ended: false }
  gateways(home((method, params) => {
    if (method === 'groups.create') {state.room = { ...state.room, room_id: params.room_id, name: params.name, members: params.members }}

    if (method === 'groups.disband') {
      if (disband === 'rejection') {throw new Error('Disband refused')}

      if (disband === 'empty') {return {}}

      if (disband === 'another room') {return { tombstone: { room_id: 'another-room', disbanded_at: 1 } }}
      state.ended = true
    }

    return ({
      'groups.create': { room: state.room },
      'groups.list': { rooms: state.ended ? [] : [...older, state.room], next_offset: null },
      'groups.state': { room: { ...state.room, room_id: params.room_id, latest_seq: 0 }, driver_status: {} },
      'groups.log': { events: [] },
      'groups.send': { event: {}, client_event_id: params.event_id, accepted: true, driver_started: true },
      'groups.disband': { tombstone: { room_id: params.room_id, disbanded_at: 1 } }
    } as Record<string, unknown>)[method]
  }))

  return state
}

it('opens and sends to the created owner despite a cached same-name alias', async () => {
  const route = { connectionId: 'local', profile: 'default' }
  const old = { room_id: 'old-room', name: 'Team', members: [] }
  const state = lifecycleGateway([old])
  publishCanonicalGroup(route, 'Team', { authority: route, room: old }, 'name')
  const dialog = await openDialog()
  await createWithMini(dialog)
  dialog.unmount()
  render(<GroupChatWorkspace group="Team" members={roster} />)
  await waitFor(() => expect(request.mock.calls.some(call => call[1] === 'groups.state')).toBe(true))
  expect(request.mock.calls.filter(call => call[1] === 'groups.state').map(call => call[2].room_id)).toEqual([state.room.room_id])
  const input = screen.getByRole('textbox', { name: 'Group message' }) as HTMLTextAreaElement
  await waitFor(() => expect(input.disabled).toBe(false))
  fireEvent.change(input, { target: { value: 'To the newly created room' } })
  fireEvent.click(screen.getByRole('button', { name: 'Send' }))
  await waitFor(() => expect(request.mock.calls.find(call => call[1] === 'groups.send')?.[2]).toMatchObject({ room_id: state.room.room_id }))
})

it.each(['header', 'sidebar'].flatMap(entry => ['empty', 'another room', 'rejection', 'matching'].map(answer => [entry, answer])))('retires ownership only after confirmed %s Disband (%s)', async (entry, answer) => {
  const values = new Map<string, unknown>()
  setPluginCtx(scriptedStorage(values))
  lifecycleGateway([], answer)
  const first = await openDialog()
  await createWithMini(first)
  first.unmount()
  const saved = $groupChats.get().Team
  const otherOwner = { ...(saved.engine as object), ...(entry === 'header' ? { connectionId: 'another-gateway' } : { profile: 'another-profile' }) }
  updateGroupChat('Other owner', () => ({ ...saved, engine: otherOwner as typeof saved.engine }), { sync: false })
  const stored = structuredClone((values.get('group-chats') as ReturnType<typeof $groupChats.get>).Team)
  $groupChatWorkspace.set('Team')
  const view = entry === 'header' ? render(<GroupChatWorkspace group="Team" members={roster} />) : renderGatewayRooms()

  if (entry === 'header') {
    fireEvent.click(await screen.findByRole('button', { name: 'Disband' }))
  } else {
    fireEvent.contextMenu(await screen.findByRole('button', { name: 'Team' }))
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Delete' }))
  }

  const confirm = within(await screen.findByRole('dialog')).getByRole('button', { name: 'Disband' })
  await act(async () => { fireEvent.click(confirm) })

  if (answer !== 'matching') {
    expect($groupChats.get().Team).toEqual(saved)
    await within(screen.getByRole('dialog')).findByText(answer === 'rejection' ? 'Disband refused' : words.disbandUnconfirmed)
    expect((values.get('group-chats') as ReturnType<typeof $groupChats.get>).Team).toEqual(stored)
    expect(Object.values($canonicalGroupBindings.get()).some(binding => binding.roomId === saved.roomId)).toBe(true)
    expect($groupChatWorkspace.get()).toBe('Team')
    expect(notify).not.toHaveBeenCalled()

    return
  }

  await waitFor(() => expect($groupChats.get().Team).toBeUndefined())
  view.unmount()
  $canonicalGroupBindings.set({})
  $groupChats.set(structuredClone(values.get('group-chats')) as ReturnType<typeof $groupChats.get>)
  expect($groupChats.get().Team).toBeUndefined()
  expect($groupChats.get()['Other owner'].engine).toEqual(otherOwner)
  const second = await openDialog()
  await createWithMini(second)
  expect(request.mock.calls.filter(call => call[1] === 'groups.create').map(call => call[2].name)).toEqual(['Team', 'Team'])
})

import type * as HermesSdk from '@hermes/plugin-sdk'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

import {
  answerGroupRequests, CANONICAL_GROUP_CAPABILITIES, PEER_HOME as HOME, PEER_MINI as MINI, PEER_REGISTERED, peerInvitation, peerRosterRow,
  STANDALONE_GROUP_CAPABILITIES
} from './canonical-group-contract-test-utils'
import { CANONICAL_GROUP_LOCALES } from './canonical-group-locales'
import { peerTargetState } from './canonical-group-peers'
import { $canonicalGroupBindings } from './canonical-group-registry'
import { canonicalGroupRoster } from './canonical-groups'
import { CreateGroupChatDialog } from './create-dialog'
import { $botMeta } from './data'
import type * as GroupChatParts from './group-chat-parts'
import { translateBots } from './i18n-test-helper'
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
  'groups.create': { room: { room_id: params.room_id, name: params.name, members: params.members, authority_gateway_id: 'install:home', authority_epoch: 1 } },
  'groups.peer.register': PEER_REGISTERED
} as Record<string, unknown>)[method]

const mini = (capabilities: unknown = MINI, invitation: unknown = peerInvitation('grant-for-linked')): Answer => method =>
  ({ 'groups.capabilities': capabilities, 'groups.peer.invite': invitation } as Record<string, unknown>)[method]

const nas: Answer = method => method === 'groups.capabilities'
  ? { ...STANDALONE_GROUP_CAPABILITIES, room_link: { enabled: false, reason: 'gateway_roomlink_secret_unavailable' } } : undefined

const gateways = (local = home(), peer = mini()) => answerGroupRequests(request, { local, mini: peer, nas })

beforeEach(() => {
  $canonicalGroupBindings.set({})
  $botMeta.set({})
  request.mockReset()
  notify.mockReset()
  Element.prototype.scrollIntoView = vi.fn()
})
afterEach(() => cleanup())

const box = (row: Element | undefined) => row?.closest('label')?.querySelector('button[role="checkbox"]') as HTMLButtonElement
const localOnly = () => request.mock.calls.every(call => call[0]?.connectionId === 'local')

async function openDialog(onCreated = vi.fn()) {
  const view = render(<CreateGroupChatDialog onClose={vi.fn()} onCreated={onCreated} open roster={roster} />)
  await waitFor(() => expect(request.mock.calls.filter(call => call[0]?.connectionId === 'mini')).toHaveLength(1))
  await act(async () => {})

  return { ...view, onCreated, rows: [...view.baseElement.querySelectorAll('[data-bot-face="default"]')] }
}

/** Pick this connection's and the Mac Mini's default Bots, create, and wait for the room. */
async function createWithMini({ rows, onCreated }: Awaited<ReturnType<typeof openDialog>>) {
  fireEvent.click(box(rows[0]))
  fireEvent.click(box(rows[1]))
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Create Group (2)' })) })
  await waitFor(() => expect(onCreated).toHaveBeenCalledOnce())
}

it('seats a Bot from another connection: the room on this gateway, the invitation on its own, the registration here', async () => {
  gateways()
  const dialog = await openDialog()
  const { baseElement, rows } = dialog
  expect(screen.getByText(words.peerHint)).toBeTruthy()
  // Only another computer's default Bot can join, and only from a gateway that can host it.
  expect(baseElement.querySelector('[data-bot-face="coder"]')).toBeNull()
  await waitFor(() => expect(screen.getByRole('note').textContent).toBe(
    words.peerTargetSetup.replace('{connection}', 'NAS').replace('{reason}', 'gateway_roomlink_secret_unavailable')))
  expect(rows).toHaveLength(3)
  expect(box(rows[2]).disabled).toBe(true)
  await createWithMini(dialog)

  const calls = request.mock.calls.map(([route, method, params]) => [route?.connectionId, method, params])
  const create = calls.find(call => call[1] === 'groups.create')![2]
  expect(calls.filter(call => call[1] !== 'groups.capabilities').map(call => [call[0], call[1]])).toEqual([
    ['local', 'groups.create'], ['mini', 'groups.peer.invite'], ['local', 'groups.peer.register']])
  // Two default Bots: the one from Mac Mini joins under a handle and name that say where it runs.
  expect(create.members).toEqual([
    { member_id: 'default', profile: 'default', handle: 'hermes', target: { kind: 'local', profile: 'default' } },
    { member_id: 'hermes-mac-mini', profile: 'default', handle: 'hermes-mac-mini', display_name: 'Hermes · Mac Mini',
      target: { kind: 'peer', peer_id: 'install:mini', installation_id: 'install:mini', profile: 'default',
        capability_digest: MINI.room_link.catalog.catalog_digest } }])
  expect(calls.find(call => call[1] === 'groups.peer.invite')![2]).toEqual({
    profile: 'default', room_id: create.room_id, member_id: 'hermes-mac-mini', home_install_id: 'install:home',
    authority_gateway_id: 'install:home', authority_epoch: 1, ttl_seconds: 86400, status_ttl_seconds: 2592000 })
  // The grant goes only to the endpoint in the catalog the room pinned.
  expect(calls.find(call => call[1] === 'groups.peer.register')![2]).toEqual({
    profile: 'default', room_id: create.room_id, member_id: 'hermes-mac-mini', target_url: MINI.room_link.catalog.endpoint.url,
    target_profile: 'default', grant: 'grant-for-linked', catalog: MINI.room_link.catalog })
  expect(Object.values($canonicalGroupBindings.get()).map(binding => binding.roomId)).toEqual([create.room_id])
  expect(notify).not.toHaveBeenCalled()
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
})

it.each([
  ['cannot seat Bots of other gateways', CANONICAL_GROUP_CAPABILITIES],
  // A backend Desktop started (an SSH connection) stops with Desktop, and its peer routes with it.
  ['stops with Desktop', { ...HOME, persistent_process: false }]
])('keeps main\'s picker on a gateway that %s', async (_case, capabilities) => {
  answerGroupRequests(request, { local: method => method === 'groups.capabilities' ? capabilities : undefined })
  const { baseElement } = render(<CreateGroupChatDialog onClose={vi.fn()} open roster={roster} />)
  await waitFor(() => expect(request).toHaveBeenCalled())
  await act(async () => {})
  expect(baseElement.querySelectorAll('[data-bot-face="default"]')).toHaveLength(1)
  expect(screen.queryByText(words.peerHint)).toBeNull()
  expect(localOnly()).toBe(true)
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
  expect(screen.queryByText(words.peerHint)).toBeNull()
  expect(localOnly()).toBe(true)
})

it('reads whether another gateway can host a Bot here from its own capabilities', () => {
  expect(peerTargetState(MINI)).toEqual({ ready: true, gateway: 'install:mini', installationId: 'install:mini',
    catalogDigest: MINI.room_link.catalog.catalog_digest, renewal: true })
  const catalog = MINI.room_link.catalog
  const link = (change: Record<string, unknown>) => ({ ...MINI, room_link: { ...MINI.room_link, catalog: { ...catalog, ...change } } })
  expect([
    CANONICAL_GROUP_CAPABILITIES, { ...MINI, room_link: { enabled: false, reason: 'api_server_required' } },
    link({ persistent_process: false }), link({ attachments: true }), link({ execution_policy: { target_profile: 'coder' } }),
    link({ endpoint: { available: false, reason: 'room_link_url_required' } }), link({ installation_id: 'install:other' })
  ].map(answer => (peerTargetState(answer) as { reason?: string }).reason)).toEqual(['peer_invite_unsupported', 'api_server_required',
    'persistent_process_required', 'text_only_required', 'default_profile_required', 'endpoint_required', 'room_link_unavailable'])
  // `hermes serve` takes no renewal horizon: its invitations are asked for none.
  expect(peerTargetState({ ...MINI, features: STANDALONE_GROUP_CAPABILITIES.features })).toMatchObject({ ready: true, renewal: false })
})

const route = { connectionId: 'local', profile: 'default' }
const pin = () => ({ installationId: 'install:mini', catalogDigest: 'c'.repeat(64) })
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

it('says plainly why a Bot reached through a backend Desktop started cannot join', async () => {
  gateways(home(), mini({ ...MINI, room_link: { ...MINI.room_link, catalog: { ...MINI.room_link.catalog, persistent_process: false } } }))
  await openDialog()
  await waitFor(() => expect(screen.getAllByRole('note').map(note => note.textContent)).toContain(
    words.peerTargetDesktopOwned.replace('{connection}', 'Mac Mini')))
})

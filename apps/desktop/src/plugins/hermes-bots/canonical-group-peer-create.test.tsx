import type * as HermesSdk from '@hermes/plugin-sdk'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

import { CANONICAL_GROUP_CAPABILITIES, peerGroupCapabilities, STANDALONE_GROUP_CAPABILITIES } from './canonical-group-contract-test-utils'
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
vi.mock('@hermes/plugin-sdk', async importOriginal => {
  const sdk = await importOriginal<typeof HermesSdk>()
  const { captureGroupRequests } = await import('./canonical-group-contract-test-utils')
  const { en } = await import('@/i18n/en')

  return {
    ...sdk,
    gatewayActivationEpoch: () => 1,
    host: {
      ...sdk.host, requestProfile: captureGroupRequests(request).request, notify,
      request: (method: string, params?: Record<string, unknown>) => request(null, method, params),
      state: { ...sdk.host.state, connectionId: sdk.atom<string | null>('local'), profile: sdk.atom('default'), gateway: sdk.atom('open') }
    },
    useI18n: () => ({ locale: 'en', t: en }),
    usePluginI18n: () => translateBots
  }
})
vi.mock('./group-chat-parts', async importOriginal => ({
  ...await importOriginal<typeof GroupChatParts>(),
  GroupImageControls: () => null
}))

const words = CANONICAL_GROUP_LOCALES.en
const HOME = peerGroupCapabilities('install:home')
const MINI = peerGroupCapabilities('install:mini')

const remote = (connectionId: string, label: string, name = 'default'): RosterRow => ({
  name, connectionId, connectionLabel: label, remoteSource: true, sourceScoped: true,
  route: { connectionId, mode: 'remote', profile: name, targetProfile: name }
})

const roster: RosterRow[] = [{ name: 'default', connectionId: 'local' }, remote('mini', 'Mac Mini'), remote('mini', 'Mac Mini', 'coder'),
  remote('nas', 'NAS')]

type Answers = Record<string, (method: string, params: Record<string, any>) => unknown>

function gateways(answers: Answers) {
  request.mockImplementation(async (route, method, params) => {
    const answer = answers[route?.connectionId ?? 'local']?.(method, params)

    if (answer === undefined) {throw new Error(`Unexpected RPC: ${route?.connectionId} ${method}`)}

    return answer instanceof Error ? Promise.reject(answer) : answer
  })
}

const home = (overrides: Answers[string] = () => undefined) => (method: string, params: Record<string, any>) => overrides(method, params) ?? ({
  'groups.capabilities': HOME,
  'groups.create': { room: { room_id: params.room_id, name: params.name, members: params.members, authority_gateway_id: 'install:home', authority_epoch: 1 } },
  'groups.peer.register': { registered: true, mode: 'direct', transport_security: 'tls', target_install_id: 'install:mini', target_profile: 'default' }
} as Record<string, unknown>)[method]

const mini = (capabilities: unknown = MINI) => (method: string) => ({
  'groups.capabilities': capabilities,
  'groups.peer.invite': { grant: 'grant-for-linked', target_profile: 'default', catalog: MINI.room_link.catalog, endpoint: MINI.room_link.endpoint }
} as Record<string, unknown>)[method]

const nas = () => (method: string) => method === 'groups.capabilities'
  ? { ...STANDALONE_GROUP_CAPABILITIES, room_link: { enabled: false, reason: 'gateway_roomlink_secret_unavailable' } } : undefined

beforeEach(() => {
  $canonicalGroupBindings.set({})
  $botMeta.set({})
  request.mockReset()
  notify.mockReset()
  Element.prototype.scrollIntoView = vi.fn()
})
afterEach(() => cleanup())

const face = (container: HTMLElement, name: string) => container.querySelector(`[data-bot-face="${name}"]`)?.closest('label')
const box = (row: Element | null | undefined) => row?.querySelector('button[role="checkbox"]') as HTMLButtonElement

async function openDialog(onCreated = vi.fn()) {
  const view = render(<CreateGroupChatDialog onClose={vi.fn()} onCreated={onCreated} open roster={roster} />)
  await waitFor(() => expect(request.mock.calls.filter(call => call[0]?.connectionId === 'mini')).toHaveLength(1))
  await act(async () => {})

  return { ...view, onCreated }
}

it('seats a Bot from another connection: the room on this gateway, the invitation on its own, the registration here', async () => {
  gateways({ local: home(), mini: mini(), nas: nas() })
  const { baseElement, onCreated } = await openDialog()
  expect(screen.getByText(words.peerHint)).toBeTruthy()
  // Only another computer's default Bot can join, and only from a gateway that can host it.
  expect(face(baseElement, 'coder')).toBeFalsy()
  await waitFor(() => expect(screen.getByRole('note').textContent).toBe(
    words.peerTargetSetup.replace('{connection}', 'NAS').replace('{reason}', 'gateway_roomlink_secret_unavailable')))
  const rows = baseElement.querySelectorAll('[data-bot-face="default"]')
  expect(rows).toHaveLength(3)
  expect(box(rows[2].closest('label')).disabled).toBe(true)

  fireEvent.click(box(rows[0].closest('label')))
  fireEvent.click(box(rows[1].closest('label')))
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Create Group (2)' })) })
  await waitFor(() => expect(onCreated).toHaveBeenCalledOnce())

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

it('opens the room even when a Bot does not join, and says why', async () => {
  const unreachable = Object.assign(new Error('peer_unreachable'), { code: 4001, data: { reason: 'peer_unreachable' } })
  gateways({ local: home(method => method === 'groups.peer.register' ? unreachable : undefined), mini: mini(), nas: nas() })
  const { baseElement, onCreated } = await openDialog()
  const rows = baseElement.querySelectorAll('[data-bot-face="default"]')
  fireEvent.click(box(rows[0].closest('label')))
  fireEvent.click(box(rows[1].closest('label')))
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Create Group (2)' })) })
  await waitFor(() => expect(onCreated).toHaveBeenCalledOnce())
  expect(notify).toHaveBeenCalledWith({ kind: 'error', message: words.peerJoinUnreachable.replace('{name}', 'Hermes · Mac Mini') })
  expect(Object.values($canonicalGroupBindings.get())).toHaveLength(1)
})

it('never sends the grant anywhere but the endpoint in the pinned catalog', async () => {
  const elsewhere = (method: string) => method === 'groups.peer.invite'
    ? { grant: 'grant-for-linked', target_profile: 'default', catalog: MINI.room_link.catalog, endpoint: { available: true, url: 'https://other.example.ts.net' } }
    : mini()(method)

  gateways({ local: home(), mini: elsewhere, nas: nas() })
  const { baseElement, onCreated } = await openDialog()
  const rows = baseElement.querySelectorAll('[data-bot-face="default"]')
  fireEvent.click(box(rows[0].closest('label')))
  fireEvent.click(box(rows[1].closest('label')))
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Create Group (2)' })) })
  await waitFor(() => expect(onCreated).toHaveBeenCalledOnce())
  expect(request.mock.calls.map(call => call[1])).not.toContain('groups.peer.register')
  expect(notify).toHaveBeenCalledWith({ kind: 'error', message: words.peerJoinChanged.replace('{name}', 'Hermes · Mac Mini') })
})

it('keeps main\'s picker on a gateway that cannot seat Bots of other gateways', async () => {
  gateways({ local: () => CANONICAL_GROUP_CAPABILITIES })
  const { baseElement } = render(<CreateGroupChatDialog onClose={vi.fn()} open roster={roster} />)
  await waitFor(() => expect(request).toHaveBeenCalled())
  await act(async () => {})
  expect(baseElement.querySelectorAll('[data-bot-face="default"]')).toHaveLength(1)
  expect(screen.queryByText(words.peerHint)).toBeNull()
  expect(request.mock.calls.every(call => call[0]?.connectionId === 'local')).toBe(true)
})

it('leaves a classic (Desktop-run) connection\'s mixed selection as it was', async () => {
  gateways({ local: () => ({ driver: false, persistent_process: false }) })
  const { baseElement } = render(<CreateGroupChatDialog onClose={vi.fn()} open roster={roster} />)
  await waitFor(() => expect(request).toHaveBeenCalled())
  await act(async () => {})
  const rows = baseElement.querySelectorAll('[data-bot-face="default"]')
  fireEvent.click(box(rows[0].closest('label')))
  fireEvent.click(box(rows[1].closest('label')))
  expect((screen.getByRole('button', { name: 'Create Group (2)' }) as HTMLButtonElement).disabled).toBe(false)
  expect(screen.queryByText(words.peerHint)).toBeNull()
  expect(request.mock.calls.every(call => call[0]?.connectionId === 'local')).toBe(true)
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

it('refuses a roster whose Bots would share a handle or id even after naming their computer', () => {
  const route = { connectionId: 'local', profile: 'default' }
  const pin = () => ({ installationId: 'install:mini', catalogDigest: 'c'.repeat(64) })
  const here = { name: 'default', handle: 'hermes', connectionId: 'local' }
  const mac = { name: 'default', handle: 'hermes', connectionId: 'mini', connectionLabel: 'Mac Mini', remoteSource: true }
  expect(canonicalGroupRoster(route, [here, mac])).toEqual({ problem: 'otherConnection' })
  expect(canonicalGroupRoster(route, [here, { name: 'hermes-mac-mini', connectionId: 'local' }, mac], pin))
    .toEqual({ problem: 'duplicateMember' })
  const seated = canonicalGroupRoster(route, [{ name: 'alpha', connectionId: 'local' }, { ...mac, title: 'Reviewer' }], pin)
  expect('roster' in seated && seated.roster[1]).toMatchObject({ member_id: 'hermes', handle: 'hermes', display_name: 'Reviewer · Mac Mini' })
})

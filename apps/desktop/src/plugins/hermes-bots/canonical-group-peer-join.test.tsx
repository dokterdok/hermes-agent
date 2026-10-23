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

import { peerGroupCapabilities } from './canonical-group-contract-test-utils'
import { CANONICAL_GROUP_LOCALES } from './canonical-group-locales'
import { $peerJoinErrors } from './canonical-group-peers'
import { $canonicalGroupBindings, registerCanonicalGroup } from './canonical-group-registry'
import { CanonicalGroupWorkspace } from './canonical-group-workspace'

const words = CANONICAL_GROUP_LOCALES.en
const HOME = peerGroupCapabilities('install:home')
const MINI = peerGroupCapabilities('install:mini')
const binding = { connectionId: 'local', profile: 'default', roomId: 'linked' }
const pinned = { kind: 'peer', peer_id: 'install:mini', installation_id: 'install:mini', profile: 'default', capability_digest: MINI.room_link.catalog.catalog_digest }

const room = {
  room_id: 'linked', name: 'Linked', authority_gateway_id: 'install:home', authority_epoch: 1, latest_seq: 0,
  members: [
    { member_id: 'default', profile: 'default', handle: 'hermes', target: { kind: 'local', profile: 'default' } },
    { member_id: 'coder', profile: 'coder', handle: 'coder', target: { kind: 'local', profile: 'coder' } },
    { member_id: 'hermes-mac-mini', profile: 'default', handle: 'hermes-mac-mini', display_name: 'Hermes · Mac Mini', target: pinned }
  ]
}

const mini = { name: 'default', connectionId: 'mini', connectionLabel: 'Mac Mini', remoteSource: true, sourceScoped: true,
  route: { connectionId: 'mini', mode: 'remote', profile: 'default', targetProfile: 'default' } }

type Handler = (method: string, params: Record<string, any>) => unknown

function gateways({ home = () => undefined, peer = () => undefined, routes = [] as unknown[] }: { home?: Handler; peer?: Handler; routes?: unknown[] }) {
  const state = { routes }
  request.mockImplementation(async (route, method, params) => {
    const handler = route?.connectionId === 'mini' ? peer : home

    const answer = handler(method, params) ?? (route?.connectionId === 'mini' ? {
      'groups.capabilities': MINI,
      'groups.peer.invite': { grant: 'grant', target_profile: 'default', catalog: MINI.room_link.catalog, endpoint: MINI.room_link.endpoint }
    } : {
      'groups.capabilities': HOME,
      'groups.state': { room, driver_status: { running: true, peer_routes: state.routes } },
      'groups.log': { events: [] }
    } as Record<string, unknown>)[method]

    if (answer === undefined) {throw new Error(`Unexpected RPC: ${route?.connectionId} ${method}`)}

    return answer instanceof Error ? Promise.reject(answer) : answer
  })

  return state
}

const refusal = (reason: string) => Object.assign(new Error(reason), { code: 4001, data: { reason } })
const status = () => screen.getByText(words.peerNotJoined.replace('{name}', 'Hermes · Mac Mini')).closest('[role="status"]') as HTMLElement

beforeEach(() => {
  union.profiles = [mini]
  $peerJoinErrors.set({})
  $canonicalGroupBindings.set({})
})
afterEach(() => { cleanup(); request.mockReset() })

it('shows a Bot of another gateway that has not joined, and Retry seats it by invitation there and registration here', async () => {
  const state = gateways({
    home: (method, params) => method === 'groups.peer.register'
      ? (state.routes = [{ room_id: 'linked', member_id: params.member_id, status: 'ready' }],
        { registered: true, mode: 'direct', transport_security: 'tls', target_install_id: 'install:mini', target_profile: 'default' })
      : undefined
  })

  render(<CanonicalGroupWorkspace binding={binding} />)
  await screen.findByText(words.peerNotJoined.replace('{name}', 'Hermes · Mac Mini'))
  expect(within(screen.getByRole('list', { name: words.members })).getByText(`· ${words.peerNotJoinedBadge}`)).toBeTruthy()

  await act(async () => { fireEvent.click(within(status()).getByRole('button', { name: 'Retry' })) })
  await waitFor(() => expect(screen.queryByText(words.peerNotJoined.replace('{name}', 'Hermes · Mac Mini'))).toBeNull())
  expect(screen.queryByText(`· ${words.peerNotJoinedBadge}`)).toBeNull()
  const steps = request.mock.calls.map(([route, method]) => [route?.connectionId, method]).filter(([, method]) => method.startsWith('groups.peer'))
  expect(steps).toEqual([['mini', 'groups.peer.invite'], ['local', 'groups.peer.register']])
  expect(request.mock.calls.find(call => call[1] === 'groups.peer.invite')![2]).toEqual({
    profile: 'default', room_id: 'linked', member_id: 'hermes-mac-mini', home_install_id: 'install:home',
    authority_gateway_id: 'install:home', authority_epoch: 1, ttl_seconds: 86400, status_ttl_seconds: 2592000 })
})

it.each([
  ['the home cannot reach it', { home: (method: string) => method === 'groups.peer.register' ? refusal('peer_unreachable') : undefined },
    words.peerJoinUnreachable],
  ['its gateway lends another catalog now', { peer: (method: string) => method === 'groups.capabilities'
    ? { ...MINI, room_link: { ...MINI.room_link, catalog: { ...MINI.room_link.catalog, catalog_digest: 'd'.repeat(64) } } } : undefined },
  words.peerJoinChanged],
  ['no connection here reaches it', { peer: (method: string) => method === 'groups.capabilities' ? peerGroupCapabilities('install:other') : undefined },
    words.peerJoinConnect]
])('says why a Retry did not seat the Bot when %s', async (_case, handlers, why) => {
  gateways(handlers)
  render(<CanonicalGroupWorkspace binding={binding} />)
  await screen.findByText(words.peerNotJoined.replace('{name}', 'Hermes · Mac Mini'))
  await act(async () => { fireEvent.click(within(status()).getByRole('button', { name: 'Retry' })) })
  await waitFor(() => expect(within(status()).getByText(why.replace('{name}', 'Hermes · Mac Mini'))).toBeTruthy())
})

it('says when a joined Bot\'s access ended, and offers Retry', async () => {
  gateways({ routes: [{ room_id: 'linked', member_id: 'hermes-mac-mini', status: 'needs_reauthorization' }] })
  render(<CanonicalGroupWorkspace binding={binding} />)
  const row = (await screen.findByText(words.peerRejoin.replace('{name}', 'Hermes · Mac Mini'))).closest('[role="status"]') as HTMLElement
  expect(within(row).getByRole('button', { name: 'Retry' })).toBeTruthy()
})

it('starts the group again without a Bot that can\'t join, then ends this one', async () => {
  const onReplaced = vi.fn()
  gateways({ home: (method, params) => method === 'groups.create'
    ? { room: { ...room, room_id: params.room_id, members: params.members } }
    : method === 'groups.disband' ? { tombstone: { room_id: 'linked', disbanded_at: 1 } } : undefined })
  registerCanonicalGroup({ connectionId: 'local', profile: 'default' }, room)
  render(<CanonicalGroupWorkspace binding={binding} onReplaced={onReplaced} />)
  await screen.findByText(words.peerNotJoined.replace('{name}', 'Hermes · Mac Mini'))
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

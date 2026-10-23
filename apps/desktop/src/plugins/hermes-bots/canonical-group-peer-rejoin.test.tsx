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
import { CanonicalGroupWorkspace } from './canonical-group-workspace'

const words = CANONICAL_GROUP_LOCALES.en
const HOME = peerGroupCapabilities('install:home')
const MINI = peerGroupCapabilities('install:mini')
const pinned = { kind: 'peer', peer_id: 'install:mini', installation_id: 'install:mini', profile: 'default', capability_digest: MINI.room_link.catalog.catalog_digest }

const room = {
  room_id: 'linked', name: 'Linked', authority_gateway_id: 'install:home', authority_epoch: 1, latest_seq: 0,
  members: [
    { member_id: 'default', profile: 'default', handle: 'hermes', target: { kind: 'local', profile: 'default' } },
    { member_id: 'hermes-mac-mini', profile: 'default', handle: 'hermes-mac-mini', display_name: 'Hermes · Mac Mini', target: pinned }
  ]
}

const row = (connectionId: string, connectionLabel: string) => ({ name: 'default', connectionId, connectionLabel, remoteSource: true, sourceScoped: true,
  route: { connectionId, mode: 'remote' as const, profile: 'default', targetProfile: 'default' } })

const registered = { registered: true, mode: 'direct', transport_security: 'tls', target_install_id: 'install:mini', target_profile: 'default' }
type Handler = (method: string, params: Record<string, any>) => unknown

/** The home answers groups.state live until `ended`; then (asked with include_disbanded) as disbanded. */
function gateways({ home = () => undefined, peer = () => undefined }: { home?: Handler; peer?: Handler }) {
  const state = { ended: false }
  request.mockImplementation(async (route, method, params) => {
    if (route?.connectionId === 'offline') {return new Promise(() => undefined)}
    const handler = route?.connectionId === 'mini' ? peer : home

    const answer = handler(method, params) ?? (route?.connectionId === 'mini' ? {
      'groups.capabilities': MINI,
      'groups.peer.invite': { grant: 'late-grant', target_profile: 'default', catalog: MINI.room_link.catalog, endpoint: MINI.room_link.endpoint },
      'groups.peer.revoke': { revoked: true }
    } : {
      'groups.capabilities': HOME,
      'groups.state': { room: state.ended ? { ...room, disbanded_at: 2 } : room, driver_status: { running: true, peer_routes: [] } },
      'groups.log': { events: [] },
      'groups.peer.register': registered
    } as Record<string, unknown>)[method]

    if (answer === undefined) {throw new Error(`Unexpected RPC: ${route?.connectionId} ${method}`)}

    return answer instanceof Error ? Promise.reject(answer) : answer
  })

  return state
}

const binding = { connectionId: 'local', profile: 'default', roomId: 'linked' }
const steps = () => request.mock.calls.map(([route, method]) => [route?.connectionId, method]).filter(([, method]) => method.startsWith('groups.peer'))
const notJoined = () => screen.findByText(words.peerNotJoined.replace('{name}', 'Hermes · Mac Mini'))

async function retry() {
  const row = (await notJoined()).closest('[role="status"]') as HTMLElement

  await act(async () => { fireEvent.click(within(row).getByRole('button', { name: 'Retry' })) })
}

beforeEach(() => { union.profiles = [row('mini', 'Mac Mini')]; $peerJoinErrors.set({}) })
afterEach(() => { cleanup(); request.mockReset() })

it('reaches the Bot\'s gateway on Retry without waiting on another connection that never answers', async () => {
  union.profiles = [row('offline', 'Old laptop'), row('mini', 'Mac Mini')]
  gateways({})
  render(<CanonicalGroupWorkspace binding={binding} />)
  await retry()
  await waitFor(() => expect(steps()).toEqual([['mini', 'groups.peer.invite'], ['local', 'groups.peer.register']]))
})

it('never invites the Bot again for a room that has ended', async () => {
  const state = gateways({ peer: method => method === 'groups.capabilities' ? (state.ended = true, MINI) : undefined })
  render(<CanonicalGroupWorkspace binding={binding} />)
  await retry()
  await act(async () => {})
  expect(steps()).toEqual([])
})

it('revokes at its gateway a grant minted for a room that ended before it was registered', async () => {
  const ended = Object.assign(new Error('invalid_params'), { code: 4001, data: { reason: 'invalid_params' } })

  const state = gateways({
    home: method => method === 'groups.peer.register' ? ended : undefined,
    peer: (method, params) => method === 'groups.peer.invite' ? (state.ended = true, undefined) : method === 'groups.peer.revoke'
      ? (expect(params.grant).toBe('late-grant'), { revoked: true }) : undefined
  })

  render(<CanonicalGroupWorkspace binding={binding} />)
  await retry()
  await waitFor(() => expect(steps()).toEqual([['mini', 'groups.peer.invite'], ['local', 'groups.peer.register'], ['mini', 'groups.peer.revoke']]))
})

it('keeps the registered grant of a live room when a later registration fails', async () => {
  gateways({ home: method => method === 'groups.peer.register' ? Object.assign(new Error('peer_unreachable'), { code: 4001, data: { reason: 'peer_unreachable' } }) : undefined })
  render(<CanonicalGroupWorkspace binding={binding} />)
  await retry()
  await waitFor(() => expect(screen.getByText(words.peerJoinUnreachable.replace('{name}', 'Hermes · Mac Mini'))).toBeTruthy())
  expect(steps()).toEqual([['mini', 'groups.peer.invite'], ['local', 'groups.peer.register']])
})

it('seats the Bot through the connection that grants the longer renewal horizon when two reach its gateway', async () => {
  // The same computer, reached as a canonical gateway (answering later) and as a `hermes serve`
  // that grants one day only (answering first).
  union.profiles = [row('serve-mini', 'Mac Mini (serve)'), row('mini', 'Mac Mini')]
  const oneDay = { ...MINI, features: MINI.features.filter(feature => feature !== 'peer_renewal_horizon') }
  gateways({ peer: method => method === 'groups.capabilities' ? new Promise(resolve => setTimeout(() => resolve(MINI), 50)) : undefined })
  const answers = request.getMockImplementation()!
  request.mockImplementation(async (route, method, params) => route?.connectionId === 'serve-mini'
    ? ({ 'groups.capabilities': oneDay, 'groups.peer.invite': { grant: 'one-day', target_profile: 'default', catalog: MINI.room_link.catalog,
      endpoint: MINI.room_link.endpoint } } as Record<string, unknown>)[method]
    : answers(route, method, params))
  render(<CanonicalGroupWorkspace binding={binding} />)
  await retry()
  await waitFor(() => expect(steps()).toEqual([['mini', 'groups.peer.invite'], ['local', 'groups.peer.register']]))
  expect(request.mock.calls.find(call => call[1] === 'groups.peer.invite')![2].status_ttl_seconds).toBe(2592000)
})

it('seats the Bot through a connection whose gateway can host it when another connection to that computer cannot', async () => {
  // The same computer, reached over SSH through a backend Desktop started (answering first, with a
  // catalog the pin can't match) and as a Remote gateway `hermes serve` kept running there (one-day grants).
  union.profiles = [row('ssh-mini', 'Mac Mini (SSH)'), row('mini', 'Mac Mini')]
  const viaSsh = { ...MINI, room_link: { ...MINI.room_link, catalog: { ...MINI.room_link.catalog, persistent_process: false, catalog_digest: 'e'.repeat(64) } } }
  const oneDay = { ...MINI, features: MINI.features.filter(feature => feature !== 'peer_renewal_horizon') }
  gateways({ peer: method => method === 'groups.capabilities' ? new Promise(resolve => setTimeout(() => resolve(oneDay), 50)) : undefined })
  const answers = request.getMockImplementation()!
  request.mockImplementation(async (route, method, params) => route?.connectionId === 'ssh-mini'
    ? ({ 'groups.capabilities': viaSsh } as Record<string, unknown>)[method] : answers(route, method, params))
  render(<CanonicalGroupWorkspace binding={binding} />)
  await retry()
  await waitFor(() => expect(steps()).toEqual([['mini', 'groups.peer.invite'], ['local', 'groups.peer.register']]))
})

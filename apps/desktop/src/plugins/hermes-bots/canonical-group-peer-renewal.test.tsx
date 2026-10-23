import { useStore } from '@nanostores/react'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
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
import { $peerJoinErrors, peerRenewalSettled, renewPeers } from './canonical-group-peers'
import { readCanonicalGroupSend } from './canonical-group-send'
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

const joined = [{ room_id: 'linked', member_id: 'hermes-mac-mini', status: 'ready' }]
const sent = (params: Record<string, any>) => ({ event: {}, client_event_id: params.event_id, accepted: true, driver_started: true })
const calls = (method: string) => request.mock.calls.filter(call => call[1] === method)

/** As `gateways`, with the Bot joined, so opening the room renews it. */
function joinedGateways(handlers: { home?: Handler; peer?: Handler }) {
  const state = gateways({ ...handlers, home: (method, params) => method === 'groups.state' && !params.include_disbanded
    ? { room, driver_status: { running: true, peer_routes: joined } } : handlers.home?.(method, params) })

  return state
}

beforeEach(() => { union.profiles = [row('mini', 'Mac Mini')]; $peerJoinErrors.set({}) })
afterEach(() => { cleanup(); request.mockReset() })

it('journals a clicked Send before it waits for the room\'s renewal, so a Desktop that quits keeps it', async () => {
  let release!: () => void
  const held = new Promise<void>(resolve => { release = resolve })
  joinedGateways({ home: (method, params) => method === 'groups.peer.register' ? held.then(() => registered) : method === 'groups.send' ? sent(params) : undefined })
  const binding = { connectionId: 'local', profile: 'default', roomId: 'journal-first' }
  render(<CanonicalGroupWorkspace binding={binding} />)
  await waitFor(() => expect(calls('groups.peer.register')).toHaveLength(1))
  await waitFor(() => expect((screen.getByRole('textbox') as HTMLTextAreaElement).disabled).toBe(false))
  fireEvent.change(screen.getByRole('textbox'), { target: { value: 'Keep this across a restart' } })
  fireEvent.click(screen.getByRole('button', { name: 'Send' }))
  await waitFor(async () => expect((await readCanonicalGroupSend(binding))?.params.payload.text).toBe('Keep this across a restart'))
  expect(calls('groups.send')).toHaveLength(0)
  await act(async () => { release() })
  await waitFor(() => expect(calls('groups.send')).toHaveLength(1))
})

it('seats nothing when its room ends while a renewal is under way', async () => {
  let answer!: (value: unknown) => void
  const capabilities = new Promise(resolve => { answer = resolve })
  const state = joinedGateways({ peer: method => method === 'groups.capabilities' ? capabilities : undefined })
  render(<CanonicalGroupWorkspace binding={{ connectionId: 'local', profile: 'default', roomId: 'ends-mid-renewal' }} />)
  await waitFor(() => expect(request.mock.calls.some(call => call[0]?.connectionId === 'mini')).toBe(true))
  state.ended = true
  await act(async () => { answer(MINI) })
  await act(async () => {})
  expect(calls('groups.peer.invite')).toHaveLength(0)
  expect(calls('groups.peer.register')).toHaveLength(0)
})

it('waits for a renewal as long as the home may take to probe the Bot\'s gateway', async () => {
  vi.useFakeTimers()

  try {
    let release!: () => void
    const held = new Promise<void>(resolve => { release = resolve })
    joinedGateways({ home: method => method === 'groups.peer.register' ? held.then(() => registered) : undefined })
    const probing = { connectionId: 'local', profile: 'default', roomId: 'slow-probe' }
    renewPeers(probing, room, joined, [row('mini', 'Mac Mini')])
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

it('does not count a roster that has not loaded yet as a failed renewal', async () => {
  joinedGateways({})
  const early = { connectionId: 'local', profile: 'default', roomId: 'roster-not-loaded' }
  renewPeers(early, room, joined, [])
  await act(async () => {})
  renewPeers(early, room, joined, [row('mini', 'Mac Mini')])
  await waitFor(() => expect(calls('groups.peer.register')).toHaveLength(1))
})

it('starts a renewal that is due when a message is sent, and sends after it', async () => {
  union.profiles = []
  joinedGateways({ home: (method, params) => method === 'groups.send' ? sent(params) : undefined })
  render(<CanonicalGroupWorkspace binding={{ connectionId: 'local', profile: 'default', roomId: 'renew-on-send' }} />)
  await waitFor(() => expect((screen.getByRole('textbox') as HTMLTextAreaElement).disabled).toBe(false))
  expect(calls('groups.peer.invite')).toHaveLength(0)
  union.profiles = [row('mini', 'Mac Mini')]
  fireEvent.change(screen.getByRole('textbox'), { target: { value: 'Hello' } })
  fireEvent.click(screen.getByRole('button', { name: 'Send' }))
  await waitFor(() => expect(calls('groups.send')).toHaveLength(1))
  const order = request.mock.calls.map(call => call[1]).filter(method => ['groups.peer.register', 'groups.send'].includes(method))
  expect(order).toEqual(['groups.peer.register', 'groups.send'])
})


it('holds a Send back when Stop is pressed while it waits for a renewal', async () => {
  let release!: () => void
  const held = new Promise<void>(resolve => { release = resolve })
  joinedGateways({ home: (method, params) => method === 'groups.peer.register' ? held.then(() => registered)
    : method === 'groups.send' ? sent(params) : method === 'groups.stop' ? { cancelled: 0 } : undefined })
  const stopping = { connectionId: 'local', profile: 'default', roomId: 'stop-while-renewing' }
  render(<CanonicalGroupWorkspace binding={stopping} />)
  await waitFor(() => expect(calls('groups.peer.register')).toHaveLength(1))
  await waitFor(() => expect((screen.getByRole('textbox') as HTMLTextAreaElement).disabled).toBe(false))
  fireEvent.change(screen.getByRole('textbox'), { target: { value: 'Never mind' } })
  fireEvent.click(screen.getByRole('button', { name: 'Send' }))
  await waitFor(async () => expect((await readCanonicalGroupSend(stopping))?.params.payload.text).toBe('Never mind'))
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Stop' })) })
  await act(async () => { release() })
  await waitFor(() => expect((screen.getByRole('textbox') as HTMLTextAreaElement).value).toBe('Never mind'))
  expect(calls('groups.send')).toHaveLength(0)
})

it('renews a Bot as soon as its connection loads, after a renewal found no connection to its gateway', async () => {
  const STUDIO = peerGroupCapabilities('install:studio')
  joinedGateways({})
  const answers = request.getMockImplementation()!
  request.mockImplementation(async (route, method, params) => route?.connectionId === 'studio' && method === 'groups.capabilities'
    ? STUDIO : answers(route, method, params))
  const partly = { connectionId: 'local', profile: 'default', roomId: 'roster-partly-loaded' }
  // The roster has another computer's Bot, not yet the Mac Mini's.
  renewPeers(partly, room, joined, [row('studio', 'Studio')])
  await peerRenewalSettled(partly)
  expect(calls('groups.peer.invite')).toHaveLength(0)
  renewPeers(partly, room, joined, [row('studio', 'Studio'), row('mini', 'Mac Mini')])
  await waitFor(() => expect(calls('groups.peer.register')).toHaveLength(1))
})

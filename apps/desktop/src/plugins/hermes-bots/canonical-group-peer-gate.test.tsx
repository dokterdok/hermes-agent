import type * as HermesSdk from '@hermes/plugin-sdk'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

import { peerGroupCapabilities } from './canonical-group-contract-test-utils'
import { CANONICAL_GROUP_LOCALES } from './canonical-group-locales'
import { $canonicalGroupBindings } from './canonical-group-registry'
import { $lastRoster } from './data'
import { $groupChats } from './group-chat'
import type * as GroupChatParts from './group-chat-parts'
import { GroupChatWorkspace } from './group-chat-view'
import { translateBots } from './i18n-test-helper'
import type { GroupMember, RosterRow } from './types'

const { request, notify, openWorkspace } = vi.hoisted(() => ({ request: vi.fn(), notify: vi.fn(), openWorkspace: vi.fn() }))
vi.mock('@hermes/plugin-sdk', async importOriginal => {
  const sdk = await importOriginal<typeof HermesSdk>()
  const { captureGroupRequests } = await import('./canonical-group-contract-test-utils')
  const { en } = await import('@/i18n/en')

  return {
    ...sdk,
    gatewayActivationEpoch: () => 1,
    host: {
      ...sdk.host, requestProfile: captureGroupRequests(request).request, notify, openWorkspace,
      request: (method: string, params?: Record<string, unknown>) => request(null, method, params),
      connections: vi.fn(async () => []),
      state: { ...sdk.host.state, connectionId: sdk.atom<string | null>('local'), profile: sdk.atom('default'), gateway: sdk.atom('open') }
    },
    useI18n: () => ({ locale: 'en', t: en }),
    usePluginI18n: () => translateBots
  }
})
vi.mock('./group-chat-parts', async importOriginal => ({ ...await importOriginal<typeof GroupChatParts>(), GroupImageControls: () => null }))

const words = CANONICAL_GROUP_LOCALES.en
const HOME = peerGroupCapabilities('install:home')
const MINI = peerGroupCapabilities('install:mini')

const mini: RosterRow = { name: 'default', handle: 'hermes', connectionId: 'mini', connectionLabel: 'Mac Mini', remoteSource: true, sourceScoped: true,
  route: { connectionId: 'mini', mode: 'remote', profile: 'default', targetProfile: 'default' } }

// A classic room from before #106742: a Bot here and the default Bot of another computer (#142926).
const members: GroupMember[] = [{ name: 'default', handle: 'hermes', connectionId: 'local' }, mini]
const log = [{ at: 1, from: { kind: 'user', name: 'You' }, id: 'u1', text: 'Earlier question' }]

function gateways(miniCapabilities: unknown = MINI) {
  request.mockImplementation(async (route, method, params) => {
    if (route?.connectionId === 'mini') {
      if (method === 'groups.capabilities') {return miniCapabilities}

      if (method === 'groups.peer.invite') {return { grant: 'grant', target_profile: 'default', catalog: MINI.room_link.catalog, endpoint: MINI.room_link.endpoint }}
    } else {
      if (method === 'groups.capabilities') {return HOME}

      if (method === 'groups.list') {return { rooms: [], next_offset: null }}

      if (method === 'groups.create') {return { room: { room_id: params.room_id, name: params.name, members: params.members, authority_gateway_id: 'install:home', authority_epoch: 1 } }}

      if (method === 'groups.peer.register') {return { registered: true, mode: 'direct', transport_security: 'tls', target_install_id: 'install:mini', target_profile: 'default' }}
    }

    throw new Error(`Unexpected RPC: ${route?.connectionId} ${method}`)
  })
}

beforeEach(() => {
  $canonicalGroupBindings.set({})
  $groupChats.set({ Pair: { log, running: false, members } } as never)
  $lastRoster.set([{ name: 'default', connectionId: 'local' }, mini])
  request.mockReset()
  notify.mockReset()
  openWorkspace.mockReset().mockReturnValue(() => undefined)
})
afterEach(() => { cleanup(); $groupChats.set({}); $lastRoster.set([]) })

it('offers a classic room whose Bots are on two computers Start new group, seating the other computer\'s Bot through its gateway', async () => {
  gateways()
  await act(async () => { render(<GroupChatWorkspace group="Pair" members={members} />) })
  await screen.findByText(words.legacyRoomAcross)
  expect(screen.getByRole('region', { name: words.earlierMessages }).textContent).toContain('Earlier question')

  await act(async () => { fireEvent.click(screen.getByRole('button', { name: words.startGatewayGroup })) })
  await waitFor(() => expect(Object.values($canonicalGroupBindings.get())).toHaveLength(1))
  expect(request.mock.calls.map(([route, method]) => [route?.connectionId, method]).filter(([, method]) => !['groups.capabilities', 'groups.list'].includes(method)))
    .toEqual([['local', 'groups.create'], ['mini', 'groups.peer.invite'], ['local', 'groups.peer.register']])
  const create = request.mock.calls.find(call => call[1] === 'groups.create')![2]
  expect(create.name).toBe('Pair')
  expect(create.members[1]).toMatchObject({ member_id: 'hermes-mac-mini', target: { kind: 'peer', installation_id: 'install:mini' } })
  expect(notify).not.toHaveBeenCalled()
})

it('says why when the other computer cannot host its Bot here, and offers nothing it cannot do', async () => {
  gateways({ ...MINI, room_link: { enabled: false, reason: 'api_server_required' } })
  await act(async () => { render(<GroupChatWorkspace group="Pair" members={members} />) })
  await screen.findByText(words.legacyRoomBlocked)
  expect(screen.getByText(words.peerTargetSetup.replace('{connection}', 'Mac Mini').replace('{reason}', 'api_server_required'))).toBeTruthy()
  expect(screen.queryByRole('button', { name: words.startGatewayGroup })).toBeNull()
  expect(screen.getByRole('region', { name: words.earlierMessages })).toBeTruthy()
})

it('never dials a connection no live Bot on this Desktop runs on', async () => {
  gateways()
  $lastRoster.set([{ name: 'default', connectionId: 'local' }])
  await act(async () => { render(<GroupChatWorkspace group="Pair" members={members} />) })
  await screen.findByText(words.legacyRoomElsewhere)
  expect(screen.queryByRole('button', { name: words.startGatewayGroup })).toBeNull()
  expect(request.mock.calls.filter(call => call[0]?.connectionId === 'mini')).toEqual([])
})

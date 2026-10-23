import type * as HermesSdk from '@hermes/plugin-sdk'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

import { answerGroupRequests, PEER_MINI as MINI, PEER_HOME, PEER_REGISTERED, peerInvitation, peerRosterRow } from './canonical-group-contract-test-utils'
import { CANONICAL_GROUP_LOCALES } from './canonical-group-locales'
import { $canonicalGroupBindings } from './canonical-group-registry'
import { $lastRoster } from './data'
import { $groupChats } from './group-chat'
import type * as GroupChatParts from './group-chat-parts'
import { GroupChatWorkspace } from './group-chat-view'
import { translateBots } from './i18n-test-helper'
import type { GroupMember } from './types'

const { request, notify, openWorkspace } = vi.hoisted(() => ({ request: vi.fn(), notify: vi.fn(), openWorkspace: vi.fn() }))
vi.mock('@hermes/plugin-sdk', async importOriginal => (await import('./canonical-group-contract-test-utils'))
  .localGatewaySdkMock(await importOriginal<typeof HermesSdk>(), request, () => translateBots, { notify, openWorkspace, connections: vi.fn(async () => []) }))
vi.mock('./group-chat-parts', async importOriginal => ({ ...await importOriginal<typeof GroupChatParts>(), GroupImageControls: () => null }))

const words = CANONICAL_GROUP_LOCALES.en
const mini = { ...peerRosterRow('mini', 'Mac Mini'), handle: 'hermes' }

// A classic room from before #106742: a Bot here and the default Bot of another computer (#142926).
const members: GroupMember[] = [{ name: 'default', handle: 'hermes', connectionId: 'local' }, mini]
const log = [{ at: 1, from: { kind: 'user', name: 'You' }, id: 'u1', text: 'Earlier question' }]

function gateways(miniCapabilities: unknown = MINI) {
  answerGroupRequests(request, {
    mini: method => ({ 'groups.capabilities': miniCapabilities, 'groups.peer.invite': peerInvitation() } as Record<string, unknown>)[method],
    local: (method, params) => ({
      'groups.capabilities': PEER_HOME, 'groups.list': { rooms: [], next_offset: null }, 'groups.peer.register': PEER_REGISTERED,
      'groups.create': { room: { room_id: params.room_id, name: params.name, members: params.members, authority_gateway_id: 'install:home', authority_epoch: 1 } }
    } as Record<string, unknown>)[method]
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

it.each([
  ['the other computer cannot host its Bot here', { ...MINI, room_link: { enabled: false, reason: 'api_server_required' } }, true,
    words.legacyRoomBlocked, words.peerTargetSetup.replace('{connection}', 'Mac Mini').replace('{reason}', 'api_server_required')],
  // This gateway could seat it: the gate says to connect to that computer, and never dials it.
  ['no live Bot on this Desktop runs on its connection', MINI, false, words.legacyRoomReach, null],
  ['the other computer is still answering', new Promise(() => undefined), true, words.checkingPeers, null]
])('offers nothing it cannot do, and says why, when %s', async (_case, capabilities, live, message, detail) => {
  gateways(capabilities)

  if (!live) {$lastRoster.set([{ name: 'default', connectionId: 'local' }])}

  await act(async () => { render(<GroupChatWorkspace group="Pair" members={members} />) })
  await screen.findByText(message)

  if (detail) {expect(screen.getByText(detail)).toBeTruthy()}

  // Never that a gateway group seats one connection only.
  expect(screen.queryByText(words.legacyRoomElsewhere)).toBeNull()
  expect(screen.queryByRole('button', { name: words.startGatewayGroup })).toBeNull()
  expect(screen.getByRole('region', { name: words.earlierMessages })).toBeTruthy()
  expect(request.mock.calls.some(call => call[0]?.connectionId === 'mini')).toBe(live)
})

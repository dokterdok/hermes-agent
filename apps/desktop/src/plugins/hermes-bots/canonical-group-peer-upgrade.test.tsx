import type * as HermesSdk from '@hermes/plugin-sdk'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

import { answerGroupRequests, PEER_MINI as MINI, PEER_HOME, PEER_REGISTERED, peerInvitation, peerRosterRow } from './canonical-group-contract-test-utils'
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

const mini = { ...peerRosterRow('mini', 'Mac Mini'), handle: 'hermes' }

// A classic room from before #106742: a Bot here and the default Bot of another computer (#142926).
const members: GroupMember[] = [{ name: 'default', handle: 'hermes', connectionId: 'local' }, mini]
const log = [{ at: 1, from: { kind: 'user', name: 'You' }, id: 'u1', text: 'Earlier question', thread: 't1' }]

function gateways(miniCapabilities: unknown = MINI) {
  answerGroupRequests(request, {
    mini: method => ({ 'groups.capabilities': miniCapabilities, 'groups.peer.invite': peerInvitation() } as Record<string, unknown>)[method],
    local: (method, params) => ({
      'groups.capabilities': PEER_HOME, 'groups.list': { rooms: [], next_offset: null }, 'groups.peer.register': PEER_REGISTERED,
      'groups.create': { room: { room_id: params.room_id, name: params.name, members: params.members, authority_gateway_id: 'install:a18b0000000000000000000000000000', authority_epoch: 1 } }
    } as Record<string, unknown>)[method]
  })
}

beforeEach(() => {
  Element.prototype.scrollIntoView = vi.fn()
  $canonicalGroupBindings.set({})
  $groupChats.set({ Pair: { log, running: false, members, sessions: {}, watermarks: {}, engine: 'desktop' } } as never)
  $lastRoster.set([{ name: 'default', connectionId: 'local' }, mini])
  request.mockReset()
  notify.mockReset()
  openWorkspace.mockReset().mockReturnValue(() => undefined)
})
afterEach(() => { cleanup(); $groupChats.set({}); $lastRoster.set([]) })

it.each([
  ['ready', MINI, true],
  ['lender unavailable', { ...MINI, room_link: { enabled: false } }, false],
  ['checks pending', new Promise(() => undefined), false]
])('keeps the Desktop room usable and offers a separate gateway group only when ready (%s)', async (_case, capabilities, canUpgrade) => {
  gateways(capabilities)
  await act(async () => { render(<GroupChatWorkspace group="Pair" members={members} />) })
  await screen.findByText('Earlier question')
  expect(screen.getByRole('textbox', { name: 'Message Pair' })).toBeTruthy()

  if (canUpgrade) {
    await screen.findByRole('button', { name: 'Start a group chat that runs without Desktop' })
    expect(screen.getByText('Earlier messages stay in this group chat.')).toBeTruthy()
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Start a group chat that runs without Desktop' })) })
    await waitFor(() => expect(Object.values($canonicalGroupBindings.get())).toHaveLength(1))
    const create = request.mock.calls.find(call => call[1] === 'groups.create')![2]
    expect(create.name).not.toBe('Pair')
    expect(create.members[1]).toMatchObject({ target: { kind: 'peer', installation_id: 'install:3f2a0000000000000000000000000000' } })
    expect($groupChats.get().Pair).toMatchObject({ engine: 'desktop', log, members })
    expect($groupChats.get()[create.name].engine).toMatchObject({ kind: 'gateway', roomId: create.room_id })
    expect($groupChats.get()[create.name].log).toEqual([])
    expect(notify).not.toHaveBeenCalled()
  } else {expect(screen.queryByRole('button', { name: 'Start a group chat that runs without Desktop' })).toBeNull()}
})

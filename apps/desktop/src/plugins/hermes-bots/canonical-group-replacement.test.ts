import type * as HermesSdk from '@hermes/plugin-sdk'
import { beforeEach, expect, it, vi } from 'vitest'

import { answerGroupRequests, PEER_HOME, PEER_MINI, PEER_REGISTERED, peerInvitation, peerRosterRow, strictGroupCreation } from './canonical-group-contract-test-utils'
import { CANONICAL_GROUP_LOCALES } from './canonical-group-locales'
import type { CanonicalRoom } from './canonical-groups'
import { translateBots } from './i18n-test-helper'

const { request } = vi.hoisted(() => ({ request: vi.fn() }))
vi.mock('@hermes/plugin-sdk', async importOriginal => (await import('./canonical-group-contract-test-utils'))
  .localGatewaySdkMock(await importOriginal<typeof HermesSdk>(), request, () => translateBots))

const binding = { connectionId: 'local', profile: 'default', roomId: 'original' }
const pin = { kind: 'peer', peer_id: PEER_MINI.authority_gateway_id, profile: 'default', installation_id: PEER_MINI.authority_gateway_id, capability_digest: PEER_MINI.room_link.catalog.catalog_digest }

const original: CanonicalRoom = { room_id: binding.roomId, name: 'Team', authority_gateway_id: PEER_HOME.authority_gateway_id, authority_epoch: 1,
  members: [{ member_id: 'a', profile: 'default', handle: 'a', target: { kind: 'local', profile: 'default' } },
    { member_id: 'b', profile: 'default', handle: 'b', target: { ...pin, peer_id: 'install:b', installation_id: 'install:b' } }, { member_id: 'c', profile: 'default', handle: 'c', target: pin }] }

const calls = (method: string) => request.mock.calls.filter(call => call[1] === method)

async function newWindow() {
  vi.resetModules()
  const { setPluginCtx } = await import('./shared')
  const { createPluginContext } = await import('@/contrib/plugin')
  const context = createPluginContext('hermes-bots')
  setPluginCtx(context)
  const { $groupChats, updateGroupChat } = await import('./group-chat')
  $groupChats.set(context.storage.get('group-chats', {}))
  updateGroupChat(original.name, record => ({ ...record, roomId: binding.roomId, engine: { kind: 'gateway', ...binding } }), { sync: false })
  const { startAgainWithout } = await import('./canonical-group-peers')

  return () => startAgainWithout(binding, original, 'b', [peerRosterRow('mini', 'Mini')], CANONICAL_GROUP_LOCALES.en)
}

beforeEach(() => { request.mockReset(); localStorage.clear() })

it.each(['lost reply', 'join failed', 'disband refused', 'renamed', 'ended'])(
  'recovers one replacement after a fresh window (%s)', async failure => {
    const { rooms, create } = strictGroupCreation()
    let recovered = false
    answerGroupRequests(request, {
      mini: method => ({ 'groups.capabilities': PEER_MINI, 'groups.peer.invite': peerInvitation() } as Record<string, unknown>)[method],
      local: (method, params) => {
        if (method === 'groups.create') {
          const answer = create(params)

          if (!recovered && failure === 'lost reply') {throw new Error('reply lost')}

          return answer
        }

        if (method === 'groups.state') {return { room: rooms.get(params.room_id) ?? original }}

        if (method === 'groups.peer.register') {
          if (!recovered && failure !== 'disband refused') {throw new Error('peer_unreachable')}

          return PEER_REGISTERED
        }

        if (method === 'groups.disband') {
          if (!recovered) {throw new Error('room_stop_pending')}

          return { tombstone: { room_id: binding.roomId, disbanded_at: 2 } }
        }
      }
    })
    const first = await newWindow()

    if (failure === 'lost reply') {await expect(first()).rejects.toThrow('reply lost')} else {
      expect((await first()).ended).toBe(false)
    }

    const next = [...rooms.values()][0]

    if (failure === 'renamed') {next.name = 'Renamed by another window'}

    if (failure === 'ended') {next.disbanded_at = 2}
    recovered = true
    const second = ['renamed', 'ended'].includes(failure) ? first : await newWindow()

    if (failure === 'ended') {
      await expect(second()).rejects.toThrow('The replacement group chat has ended. Start again to create a new one.')
      expect(rooms.size).toBe(1)
      expect(calls('groups.disband')).toHaveLength(0)
      expect((await second()).ended).toBe(true)
      expect(rooms.size).toBe(2)

      return
    }

    const result = await second()
    expect(result.ended).toBe(true)
    expect(result.room.room_id).toBe(next.room_id)
    expect(result.room.name).toBe(next.name)
    expect(rooms.size).toBe(1)
    expect(calls('groups.create')).toHaveLength(failure === 'lost reply' ? 2 : 1)

    if (failure === 'lost reply') {expect(calls('groups.create')[1][2]).toEqual(calls('groups.create')[0][2])}
  }
)

it('does not create when plugin storage silently drops the recovery link', async () => {
  const replace = await newWindow()
  const { setPluginCtx } = await import('./shared')
  const { createPluginContext } = await import('@/contrib/plugin')
  const context = createPluginContext('hermes-bots')
  context.storage.set = () => undefined
  setPluginCtx(context)
  await expect(replace()).rejects.toThrow(CANONICAL_GROUP_LOCALES.en.createRefused)
  expect(calls('groups.create')).toHaveLength(0)
})

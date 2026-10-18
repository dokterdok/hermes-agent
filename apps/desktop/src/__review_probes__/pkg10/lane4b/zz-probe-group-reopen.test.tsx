// Review probes (claude) for PR #140972 / #140805: reopening a gateway group by
// name or restored canonical key. Setup mirrors canonical-group-reopen-by-name.test.tsx.
import type * as HermesSdk from '@hermes/plugin-sdk'
import { host } from '@hermes/plugin-sdk'
import { act, cleanup, render, screen } from '@testing-library/react'
import type { WritableAtom } from 'nanostores'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

import { $canonicalGroupBindings } from '@/plugins/hermes-bots/canonical-group-registry'
import { $botMeta } from '@/plugins/hermes-bots/data'
import { $groupChats, $groupChatWorkspace } from '@/plugins/hermes-bots/group-chat'
import type * as GroupChatParts from '@/plugins/hermes-bots/group-chat-parts'
import { GroupChatWorkspace } from '@/plugins/hermes-bots/group-chat-view'
import { translateBots } from '@/plugins/hermes-bots/i18n-test-helper'

const { request, notify, openWorkspace } = vi.hoisted(() => ({ request: vi.fn(), notify: vi.fn(), openWorkspace: vi.fn() }))
vi.mock('@hermes/plugin-sdk', async importOriginal => {
  const sdk = await importOriginal<typeof HermesSdk>()
  const { en } = await import('@/i18n/en')

  return {
    ...sdk,
    gatewayActivationEpoch: () => 1,
    host: {
      ...sdk.host, requestProfile: request, notify, openWorkspace,
      request: (method: string, params?: Record<string, unknown>) => request(null, method, params),
      connections: vi.fn(async () => []),
      state: {
        ...sdk.host.state,
        connectionId: sdk.atom<string | null>('local'),
        profile: sdk.atom('default'),
        gateway: sdk.atom('open')
      }
    },
    useI18n: () => ({ locale: 'en', t: en }),
    usePluginI18n: () => translateBots
  }
})
vi.mock('@/plugins/hermes-bots/group-chat-parts', async importOriginal => ({
  ...await importOriginal<typeof GroupChatParts>(),
  GroupImageControls: () => null
}))

const state = {
  connectionId: host.state.connectionId as WritableAtom<string | null>,
  profile: host.state.profile as WritableAtom<string>,
  gateway: host.state.gateway as WritableAtom<string>
}

const roster = [{ name: 'alpha', connectionId: 'local' }, { name: 'beta', connectionId: 'local' }]
const members = roster.map(m => ({ member_id: m.name, profile: m.name, handle: m.name }))
const room = (id: string) => ({ room_id: id, name: 'Team', members })

beforeEach(() => {
  state.connectionId.set('local')
  state.profile.set('default')
  state.gateway.set('open')
  $canonicalGroupBindings.set({})
  $groupChats.set({})
  $groupChatWorkspace.set(null)
  $botMeta.set({})
  request.mockReset()
  notify.mockReset()
  openWorkspace.mockReset().mockReturnValue(() => undefined)
  Element.prototype.scrollIntoView = vi.fn()
})
afterEach(() => {
  cleanup()
  localStorage.clear()
  vi.restoreAllMocks()
})

interface Route { connectionId: string; profile: string }

/** One gateway per (connection, profile); `list` answers groups.list (value or promise). */
function gateways(list: (route: Route) => unknown) {
  request.mockImplementation(async (route: Route, method: string, params: Record<string, unknown>) => {
    if (method === 'groups.capabilities') {return { driver: true, persistent_process: true }}

    if (method === 'groups.list') {return list(route)}

    if (method === 'groups.create') {return { room: { room_id: params.room_id, name: params.name, members: params.members } }}

    if (method === 'groups.state') {return { room: { ...room(String(params.room_id)), latest_seq: 0 }, driver_status: {} }}

    if (method === 'groups.log') {return { events: [] }}

    throw new Error(`Unexpected RPC: ${method}`)
  })
}

const calls = (method: string) =>
  request.mock.calls.filter(call => call[1] === method).map(call => ({ route: call[0] as Route, params: call[2] }))

it('G2: a lookup that answers after the source moved does not open the old source room', async () => {
  let releaseA: () => void = () => undefined
  gateways(route => route.connectionId === 'conn-a'
    ? new Promise(resolve => { releaseA = () => resolve({ rooms: [room('room-a')], next_offset: null }) })
    : { rooms: [], next_offset: null })
  state.connectionId.set('conn-a')

  await act(async () => { render(<GroupChatWorkspace group="Team" members={roster} />) })
  expect(calls('groups.list').map(c => c.route.connectionId)).toEqual(['conn-a'])

  // The user switches the foreground source while A's lookup is pending; B answers "no such room".
  await act(async () => { state.connectionId.set('conn-b') })
  expect(calls('groups.list').map(c => c.route.connectionId)).toContain('conn-b')

  // A's late answer lands.
  await act(async () => { releaseA() })

  // What the user sees: the workspace (now on B) opens A's room.
  expect(calls('groups.state').map(c => c.route.connectionId)).not.toContain('conn-a')
  expect($canonicalGroupBindings.get().Team).toBeUndefined()
})

it("G2 (overwrite): A's late answer replaces B's already-open room of the same name", async () => {
  let releaseA: () => void = () => undefined
  gateways(route => route.connectionId === 'conn-a'
    ? new Promise(resolve => { releaseA = () => resolve({ rooms: [room('room-a')], next_offset: null }) })
    : { rooms: [room('room-b')], next_offset: null })
  state.connectionId.set('conn-a')

  await act(async () => { render(<GroupChatWorkspace group="Team" members={roster} />) })
  await act(async () => { state.connectionId.set('conn-b') })
  // B's lookup answered first: the workspace is on B's room.
  expect(calls('groups.state').at(-1)).toMatchObject({ route: { connectionId: 'conn-b' }, params: { room_id: 'room-b' } })

  await act(async () => { releaseA() })

  // The user is still on B; the open room must stay B's.
  expect($canonicalGroupBindings.get().Team).toMatchObject({ connectionId: 'conn-b', roomId: 'room-b' })
  expect(calls('groups.state').at(-1)).toMatchObject({ route: { connectionId: 'conn-b' }, params: { room_id: 'room-b' } })
})

it('alias: after a profile switch, the same group name is resolved again, not served from the old profile', async () => {
  gateways(route => ({ rooms: [room(`room-${route.profile}`)], next_offset: null }))
  const first = render(<GroupChatWorkspace group="Team" members={roster} />)
  await act(async () => {})
  expect(calls('groups.state').at(-1)?.params).toMatchObject({ room_id: 'room-default' })
  first.unmount()

  await act(async () => { state.profile.set('work') })
  await act(async () => { render(<GroupChatWorkspace group="Team" members={roster} />) })

  // The PR resolves a name against the foreground route; the alias it wrote is route-less.
  expect(calls('groups.list').map(c => c.route.profile)).toContain('work')
  expect(calls('groups.state').at(-1)?.params).toMatchObject({ room_id: 'room-work' })
})

it('G5: a restored canonical tab of source A opens while the foreground source B is down', async () => {
  gateways(route => (route.connectionId === 'conn-a' ? { rooms: [room('room-a')], next_offset: null } : { rooms: [], next_offset: null }))
  state.connectionId.set('conn-b')
  state.gateway.set('closed')

  await act(async () => { render(<GroupChatWorkspace group="canonical:conn-a:default:room-a" members={[]} />) })

  expect(calls('groups.list').map(c => c.route.connectionId)).toContain('conn-a')
  expect(calls('groups.state').at(-1)?.params).toMatchObject({ room_id: 'room-a' })
})

it('sibling: a group whose gateway room lives on profile default is not offered for re-creation after a restart on profile work', async () => {
  // The room lives on (local, default); after restart the foreground is (local, work).
  gateways(route => (route.profile === 'default' ? { rooms: [room('room-default')], next_offset: null } : { rooms: [], next_offset: null }))
  state.profile.set('work')

  await act(async () => { render(<GroupChatWorkspace group="Team" members={roster} />) })

  const start = screen.queryByRole('button', { name: 'Start gateway group' }) as HTMLButtonElement | null
  // "Never fork": creation must not be offered while a gateway room of this name exists.
  expect(start === null || start.disabled).toBe(true)
})

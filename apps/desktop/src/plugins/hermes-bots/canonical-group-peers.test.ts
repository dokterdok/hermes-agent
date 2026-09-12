import { afterEach, beforeEach, expect, it, vi } from 'vitest'

const rpc = vi.hoisted(() => vi.fn())
vi.mock('@hermes/plugin-sdk', () => ({ host: { requestProfile: rpc } }))
import { readCanonicalGroupCreate, resumeCanonicalGroupCreate } from './canonical-group-create'
import { profileEndpoint } from './canonical-group-peers'
import { createCanonicalGroup } from './canonical-groups'

const originalDesktop = window.hermesDesktop
const home = { connectionId: 'home', profile: 'default' }

const members = [{ name: 'default', handle: 'hermes', display_name: 'Home Bot', connectionId: 'home' },
  { name: 'default', handle: 'hermes', display_name: 'Peer Bot', connectionId: 'peer' }]

let entries: Record<string, unknown>
let room: Record<string, unknown> & { members: unknown[] }
let routes: Array<Record<string, string>>
let failInvite: boolean
let failControl: boolean
let peerId: string

function capabilities(connectionId: string) {
  const installationId = connectionId === 'home' ? 'install:home' : peerId
  const endpoint = { available: true, url: `https://${connectionId}.example`, transport_security: 'tls' }

  return { driver: true, persistent_process: true, authority_gateway_id: installationId,
    features: ['peer_invitation_request_id', 'reciprocal_room_control_setup'],
    methods: ['groups.peer.invite', 'groups.control.register', 'groups.peer.register', 'groups.control.invite'],
    room_link: { enabled: true, profile: 'default', endpoint, catalog: {
      installation_id: installationId, persistent_process: true, text: true, attachments: true,
      protocol_versions: [2], link_modes: ['direct'], catalog_digest: (connectionId === 'home' ? 'a' : 'b').repeat(64),
      endpoint, execution_policy: { policy_digest: 'd'.repeat(64) }
    } }
  }
}

beforeEach(() => {
  entries = {}; room = { members: [] }; routes = []; failInvite = false; failControl = false; peerId = 'install:peer'
  window.hermesDesktop = { preparedSubmissions: {
    read: async () => JSON.stringify(entries), update: async () => {},
    compareAndSet: async (key: string, expected: string | null, entry: string | null) => {
      if (JSON.stringify(entries[key] ?? null) !== (expected ?? 'null')) {return false}

      if (entry === null) {delete entries[key]} else {entries[key] = JSON.parse(entry)}

      return true
    }
  } } as unknown as typeof window.hermesDesktop
  rpc.mockImplementation(async (route, method, params) => {
    expect(route.targetProfile).toBe(params.profile)

    const handlers: Record<string, () => unknown> = {
      'groups.capabilities': () => capabilities(route.connectionId),
      'groups.create': () => {
        expect(route.connectionId).toBe('home')
        expect(Object.values(entries)).toHaveLength(1)

        if (!room.room_id) {room = { ...params, authority_gateway_id: 'install:home', authority_epoch: 1 }}
        expect(params.room_id).toBe(room.room_id)

        return { room }
      },
      'groups.state': () => ({ room, driver_status: { peer_routes: routes } }),
      'groups.peer.invite': () => {
        expect(route.connectionId).toBe('peer')
        expect((Object.values(entries)[0] as { version: number }).version).toBe(2)

        if (failInvite) {failInvite = false; throw new Error('Unconfirmed invitation')}

        return { grant: 'fixture-invitation-bearer', target_profile: 'default',
          catalog: capabilities('peer').room_link.catalog, endpoint: capabilities('peer').room_link.endpoint,
          expires_at: 5000, status_expires_at: 5000 + 30 * 86400 - 3600 }
      },
      'groups.peer.register': () => {
        expect(route.connectionId).toBe('home')
        expect(params.expected_grant_sha256).toBe('')
        expect(params.target_url).toBe('https://peer.example/p/default')
        routes = [{ member_id: params.member_id, status: 'ready', grant_sha256: 'c'.repeat(64) }]

        return { registered: true, target_install_id: peerId, target_profile: 'default' }
      },
      'groups.control.invite': () => {
        expect(params.reuse_existing).toBe(true)

        return { room_id: room.room_id, member_id: params.member_id, authority_gateway_id: 'install:home', authority_epoch: 1,
          home_url: 'https://home.example/p/default', control_token: 'x'.repeat(43), expires_at: 9999999999,
          room_name: room.name, member_count: room.members.length }
      },
      'groups.control.register': () => {
        if (failControl) {failControl = false; throw new Error('Unconfirmed messaging setup')}

        return { registered: true, room_id: room.room_id, member_id: params.member_id }
      }
    }

    if (!handlers[method]) {throw new Error(`Unexpected method: ${method}`)}

    return handlers[method]()
  })
})
afterEach(() => { window.hermesDesktop = originalDesktop; rpc.mockReset() })

it('connects same-named Bots through captured profiles and distinct room member identities', async () => {
  const result = await createCanonicalGroup(home, 'Two hosts', members)
  expect(result.room.members.map(member => member.handle)).toEqual(['hermes', 'hermes-2'])
  expect(new Set(result.room.members.map(member => member.member_id)).size).toBe(2)
  expect(result.room.members[1].target?.installation_id).toBe(peerId)
  expect(await readCanonicalGroupCreate(home)).toBeUndefined()
  expect(rpc.mock.calls.filter(call => call[1] === 'groups.peer.invite')).toHaveLength(1)
})

it('keeps an unconfirmed invitation on the same request and room without storing either bearer', async () => {
  failInvite = true
  await expect(createCanonicalGroup(home, 'Two hosts', members)).rejects.toThrow('Unconfirmed invitation')
  const entry = (await readCanonicalGroupCreate(home))!
  const first = rpc.mock.calls.find(call => call[1] === 'groups.peer.invite')!
  failControl = true
  await expect(resumeCanonicalGroupCreate(home, entry.binding.roomId)).rejects.toThrow('Unconfirmed messaging')
  const invitations = rpc.mock.calls.filter(call => call[1] === 'groups.peer.invite')
  expect(invitations.map(call => call[2])).toEqual([first[2], first[2]])
  const serialized = JSON.stringify(entries)
  expect(serialized).not.toContain('fixture-invitation-bearer')
  expect(serialized).not.toContain('x'.repeat(43))
  routes[0].grant_sha256 = 'e'.repeat(64)
  const result = await resumeCanonicalGroupCreate(home, entry.binding.roomId)
  expect(result.binding.roomId).toBe(entry.binding.roomId)
  expect(rpc.mock.calls.filter(call => call[1] === 'groups.peer.invite')).toHaveLength(2)
  expect(rpc.mock.calls.filter(call => call[1] === 'groups.peer.register')).toHaveLength(1)
  expect(await readCanonicalGroupCreate(home)).toBeUndefined()
})

it('refuses a changed target without minting another invitation or losing the original setup', async () => {
  failInvite = true
  await expect(createCanonicalGroup(home, 'Two hosts', members)).rejects.toThrow('Unconfirmed invitation')
  const entry = (await readCanonicalGroupCreate(home))!
  peerId = 'install:replacement'
  await expect(resumeCanonicalGroupCreate(home, entry.binding.roomId)).rejects.toThrow('gateway changed')
  expect(rpc.mock.calls.filter(call => call[1] === 'groups.peer.invite')).toHaveLength(1)
  expect(await readCanonicalGroupCreate(home)).toEqual(entry)
})

it('does not seat the same installation and Bot twice through two saved connection aliases', async () => {
  peerId = 'install:home'
  await expect(createCanonicalGroup(home, 'Same Bot twice', members)).rejects.toThrow('already selected')
  expect(rpc.mock.calls.every(call => call[1] === 'groups.capabilities')).toBe(true)
  expect(await readCanonicalGroupCreate(home)).toBeUndefined()
})

it('never doubles a profile prefix or accepts credential-bearing endpoints', () => {
  expect(profileEndpoint({ available: true, url: 'https://peer.example/p/reviewer/' }, 'reviewer')).toBe('https://peer.example/p/reviewer')

  for (const url of ['https://peer.example/p/other', 'https://user:secret@peer.example', 'file:///fixture', 'https://peer.example/?token=fixture']) {
    expect(() => profileEndpoint({ available: true, url }, 'reviewer')).toThrow()
  }
})

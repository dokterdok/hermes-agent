import { afterEach, beforeEach, expect, it, vi } from 'vitest'

const request = vi.hoisted(() => vi.fn())
vi.mock('@hermes/plugin-sdk', () => ({ host: { requestProfile: request } }))
import { normalizeCanonicalGroupName, prepareCanonicalGroupCreate, readCanonicalGroupCreate, resumeCanonicalGroupCreate } from './canonical-group-create'

const route = { connectionId: 'source', profile: 'default' }
const members = ['writer', 'reviewer'].map(profile => ({ member_id: profile, profile, handle: profile, target: { kind: 'local', profile } }))
const originalDesktop = window.hermesDesktop
let entries: Record<string, unknown>
let compare: ReturnType<typeof vi.fn>
beforeEach(() => {
  entries = {}
  compare = vi.fn(async (key, expected, entry) => {
    if (JSON.stringify(entries[key] ?? null) !== (expected ?? 'null')) {return false}
    if (entry === null) {delete entries[key]} else {entries[key] = JSON.parse(entry)}
    return true
  })
  window.hermesDesktop = { preparedSubmissions: { read: async () => JSON.stringify(entries), update: async () => {}, compareAndSet: compare } } as unknown as typeof window.hermesDesktop
  request.mockImplementation(async (_route, method, params) => method === 'groups.capabilities'
    ? { driver: true, authority_gateway_id: 'install:source' }
    : { room: { ...params, authority_gateway_id: 'install:source' } })
})
afterEach(() => { window.hermesDesktop = originalDesktop; request.mockReset(); vi.restoreAllMocks() })

it('publishes one frozen intent before create and keeps its original identity after an unconfirmed response', async () => {
  const captured = { ...route }
  const entry = await prepareCanonicalGroupCreate(captured, 'Team', members)
  captured.connectionId = 'another-source'
  expect(request.mock.calls.every(call => call[1] === 'groups.capabilities')).toBe(true)
  request.mockImplementation(async (_route, method, params) => {
    if (method === 'groups.capabilities') {return { driver: true, authority_gateway_id: entry.authorityId }}
    expect(Object.values(entries)).toEqual([entry])
    if (request.mock.calls.filter(call => call[1] === 'groups.create').length === 1) {throw new Error('Unconfirmed response')}
    return { room: { ...params, authority_gateway_id: entry.authorityId } }
  })
  await expect(resumeCanonicalGroupCreate(route, entry.binding.roomId)).rejects.toThrow('Unconfirmed response')
  expect(await readCanonicalGroupCreate(route)).toEqual(entry)
  const result = await resumeCanonicalGroupCreate(route, entry.binding.roomId)
  expect(result.binding).toEqual(entry.binding)
  const creates = request.mock.calls.filter(call => call[1] === 'groups.create')
  expect(creates.map(call => call[2].room_id)).toEqual([entry.params.room_id, entry.params.room_id])
  expect(creates.every(call => call[0].connectionId === route.connectionId)).toBe(true)
  expect(await readCanonicalGroupCreate(route)).toBeUndefined()
})

it('concurrent preparation retains the winner and cannot silently substitute a different group', async () => {
  const [first, second] = await Promise.all([
    prepareCanonicalGroupCreate(route, 'Team', members), prepareCanonicalGroupCreate(route, 'Team', members)
  ])
  expect(first).toEqual(second)
  expect(Object.values(entries)).toEqual([first])
  await expect(prepareCanonicalGroupCreate(route, 'Different', members)).rejects.toThrow('Finish setting up')
  expect(await readCanonicalGroupCreate(route)).toEqual(first)
})

it('a durable-write failure or changed gateway never reaches group creation', async () => {
  compare.mockRejectedValueOnce(new Error('Storage unavailable'))
  await expect(prepareCanonicalGroupCreate(route, 'Team', members)).rejects.toThrow('Storage unavailable')
  const entry = await prepareCanonicalGroupCreate(route, 'Team', members)
  request.mockResolvedValue({ driver: true, authority_gateway_id: 'install:replacement' })
  await expect(resumeCanonicalGroupCreate(route, entry.binding.roomId)).rejects.toThrow('gateway has changed')
  expect(request.mock.calls.every(call => call[1] === 'groups.capabilities')).toBe(true)
  expect(await readCanonicalGroupCreate(route)).toEqual(entry)
})

it('a mismatched response cannot retire the saved group and late success cannot delete its replacement', async () => {
  const entry = await prepareCanonicalGroupCreate(route, 'Team', members)
  request.mockImplementation(async (_route, method, params) => method === 'groups.capabilities'
    ? { driver: true, authority_gateway_id: entry.authorityId }
    : { room: { ...params, room_id: 'different', authority_gateway_id: entry.authorityId } })
  await expect(resumeCanonicalGroupCreate(route, entry.binding.roomId)).rejects.toThrow('could not be confirmed')
  expect(await readCanonicalGroupCreate(route)).toEqual(entry)
  const replacement = { ...entry, binding: { ...entry.binding, roomId: 'replacement' }, params: { ...entry.params, room_id: 'replacement' } }
  request.mockImplementation(async (_route, method, params) => {
    if (method === 'groups.capabilities') {return { driver: true, authority_gateway_id: entry.authorityId }}
    for (const key of Object.keys(entries)) {entries[key] = replacement}
    return { room: { ...params, authority_gateway_id: entry.authorityId } }
  })
  await resumeCanonicalGroupCreate(route, entry.binding.roomId)
  expect(await readCanonicalGroupCreate(route)).toEqual(replacement)
  await expect(resumeCanonicalGroupCreate(route, entry.binding.roomId)).rejects.toThrow('changed in another window')
})

it('normalizes fresh names but retires older raw-name intents by their unchanged journal identity', async () => {
  const entry = await prepareCanonicalGroupCreate(route, 'Team \u0085', members)
  expect(entry.params.name).toBe('Team')
  expect(normalizeCanonicalGroupName('\ufeffTeam\ufeff')).toBe('\ufeffTeam\ufeff')
  const raw = { ...entry, params: { ...entry.params, name: 'Team ' } }
  for (const key of Object.keys(entries)) {entries[key] = raw}
  request.mockImplementation(async (_route, method, params) => method === 'groups.capabilities'
    ? { driver: true, authority_gateway_id: entry.authorityId }
    : { room: { ...params, name: params.name.trim(), authority_gateway_id: entry.authorityId } })
  const result = await resumeCanonicalGroupCreate(route, entry.binding.roomId)
  expect(result.room.name).toBe('Team')
  expect(request.mock.calls.find(call => call[1] === 'groups.create')![2].name).toBe('Team ')
  expect(await readCanonicalGroupCreate(route)).toBeUndefined()
})

it('checks native journal capabilities before new or saved creation without falling back to browser storage', async () => {
  const entry = await prepareCanonicalGroupCreate(route, 'Team', members)
  window.hermesDesktop!.preparedSubmissions!.compareAndSet = undefined
  request.mockClear()
  await expect(resumeCanonicalGroupCreate(route, entry.binding.roomId)).rejects.toThrow('Update Hermes Desktop')
  expect(await readCanonicalGroupCreate(route)).toEqual(entry)
  expect(request).not.toHaveBeenCalled()
  window.hermesDesktop = {} as typeof window.hermesDesktop
  await expect(prepareCanonicalGroupCreate(route, 'Another', members)).rejects.toThrow('Update Hermes Desktop')
  expect(request).not.toHaveBeenCalled()
})

import { readdirSync, readFileSync } from 'node:fs'
import { join } from 'node:path'

import { beforeEach, expect, it, vi } from 'vitest'

import type { captureGroupRequests } from './canonical-group-contract-test-utils'

const doubles = vi.hoisted(() => ({ request: vi.fn(), capture: undefined as ReturnType<typeof captureGroupRequests> | undefined }))
vi.mock('@hermes/plugin-sdk', async () => {
  const { captureGroupRequests } = await import('./canonical-group-contract-test-utils')
  doubles.capture = captureGroupRequests(doubles.request)

  return { host: { requestProfile: doubles.capture.request } }
})

import {
  assertCanonicalGroupCalls,
  CANONICAL_GROUP_CAPABILITIES,
  GROUP_FIELDS,
  GROUP_METHODS
} from './canonical-group-contract-test-utils'
import { actCanonicalGroup, canonicalGroupRequest, createCanonicalGroup, discoverCanonicalGroups } from './canonical-groups'

const route = { connectionId: 'owner', profile: 'team' }
beforeEach(() => doubles.request.mockReset())

it('reads the gateway declarations it checks against', () => {
  expect(GROUP_METHODS['groups.send']).toBe('session:submit')
  expect(GROUP_FIELDS['groups.send']).toEqual(['room_id', 'event_id', 'payload'])
  expect(GROUP_FIELDS['groups.capabilities']).toEqual([])
  expect(Object.keys(GROUP_FIELDS)).toEqual(expect.arrayContaining(Object.keys(GROUP_METHODS)))
})

it('names only declared gateway methods anywhere in the plugin source', () => {
  const dir = join(process.cwd(), 'src', 'plugins', 'hermes-bots')

  const named = readdirSync(dir)
    .filter(file => /\.tsx?$/.test(file) && !/\.test\.|test-utils/.test(file))
    .flatMap(file => [...readFileSync(join(dir, file), 'utf8').matchAll(/['"`](groups\.[\w.]+)['"`]/g)].map(m => m[1]))

  expect(named.length).toBeGreaterThan(0)
  expect([...new Set(named)].filter(method => !Object.hasOwn(GROUP_METHODS, method))).toEqual([])
})

it('checks recorded discovery, creation and exact attempt calls against the gateway contract', async () => {
  doubles.request.mockResolvedValueOnce(CANONICAL_GROUP_CAPABILITIES).mockResolvedValueOnce({ rooms: [], next_offset: null })
  await discoverCanonicalGroups(route)
  doubles.request.mockImplementation(async (_route, _method, params) => ({ room: { ...params } }))
  const { binding } = await createCanonicalGroup(route, 'Team', [{ name: 'alice' }, { name: 'bob' }])

  for (const kind of ['retry', 'discard', 'approval']) {
    await actCanonicalGroup(binding, { kind, member_id: 'alice', task_id: 'task', execution_generation: 7, request_id: 'approval', approval: { choices: ['deny'] } }, 'deny')
  }

  const calls = doubles.capture!.calls
  expect(calls.map(call => call.method)).toEqual(['groups.capabilities', 'groups.list', 'groups.create', 'groups.retry', 'groups.discard', 'groups.approve'])
  expect(calls.every(call => call.params.profile === route.profile)).toBe(true)
  expect(() => assertCanonicalGroupCalls(calls)).not.toThrow()
})

it('rejects a captured undeclared method or field even when the request double accepts it', async () => {
  await canonicalGroupRequest(route, 'groups.not_declared')
  await canonicalGroupRequest(route, 'groups.state', { room_id: 'room', undeclared: true })
  const calls = doubles.capture!.calls.splice(0)
  expect(calls).toEqual([
    { method: 'groups.not_declared', params: { profile: route.profile } },
    { method: 'groups.state', params: { room_id: 'room', undeclared: true, profile: route.profile } }
  ])
  expect(() => assertCanonicalGroupCalls([calls[0]])).toThrow('Undeclared canonical group method: groups.not_declared')
  expect(() => assertCanonicalGroupCalls([calls[1]])).toThrow('Undeclared canonical group field: groups.state.undeclared')
})

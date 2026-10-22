/**
 * The gateway's own `groups.*` declarations, read from the Python source at test
 * time. Nothing in the type system links the Desktop client to
 * `gateway/session_group_controls.py`, so this is the seam: a method or field the
 * gateway renames or drops fails the tests that capture the client's requests.
 */

import { readFileSync } from 'node:fs'
import { join } from 'node:path'

import { afterEach } from 'vitest'

const repoRoot = join(process.cwd(), '..', '..')
const pythonSource = (path: string) => readFileSync(join(repoRoot, path), 'utf8')
const controls = pythonSource('gateway/session_group_controls.py')

function declaration(source: string, name: string, open: string, close: string): string {
  const match = new RegExp(`^${name} = \\${open}([\\s\\S]*?)\\${close}$`, 'm').exec(source)

  if (!match) {
    throw new Error(`The gateway no longer declares ${name}`)
  }

  return match[1]
}

/** `GROUP_METHODS`: every canonical method and the capability it needs. */
export const GROUP_METHODS: Readonly<Record<string, string>> = Object.fromEntries(
  [...declaration(controls, 'GROUP_METHODS', '{', '}').matchAll(/'([\w.]+)': '([\w:]+)'/g)].map(m => [m[1], m[2]])
)

/** `_FIELDS`: the closed parameter set of each method (`profile` is always allowed). */
export const GROUP_FIELDS: Readonly<Record<string, readonly string[]>> = Object.fromEntries(
  [...declaration(controls, '_FIELDS', '{', '}').matchAll(/'([\w.]+)': (?:set\(\)|\{([^}]*)\})/g)].map(m => [
    m[1],
    [...(m[2] ?? '').matchAll(/'(\w+)'/g)].map(field => field[1])
  ])
)

/** The standalone server's `groups.capabilities.methods` (`hermes serve`, `hermes dashboard`). */
export const STANDALONE_GROUP_METHODS: readonly string[] = [
  ...declaration(pythonSource('tui_gateway/methods_groups.py'), '_METHODS', '(', ')').matchAll(/"([\w.]+)"/g)
].map(m => m[1])

/** `groups.capabilities` as `main`'s gateway answers it with a running driver. */
export const CANONICAL_GROUP_CAPABILITIES = {
  protocol_version: 2,
  driver: true,
  persistent_process: true,
  authority_gateway_id: 'install:canonical',
  room_link: { enabled: false, reason: 'canonical_driver_required' },
  features: ['room_identity', 'monotonic_log', 'replayable_disband'],
  methods: Object.keys(GROUP_METHODS),
  max_log_limit: 500
}

/** The standalone server's answer: it starts its own room driver at boot, so `driver` is true too. */
export const STANDALONE_GROUP_CAPABILITIES = {
  protocol_version: 2,
  driver: true,
  persistent_process: false,
  authority_gateway_id: 'install:standalone',
  room_link: { enabled: false, reason: 'gateway_roomlink_secret_unavailable' },
  features: ['authority_epoch', 'coordinator_fencing', 'room_identity', 'monotonic_log', 'idempotent_send',
    'replayable_disband', 'typed_events', 'actor_identity', 'log_replication', 'authority_takeover'],
  methods: [...STANDALONE_GROUP_METHODS],
  max_log_limit: 500
}

export interface GroupRequestCall {
  method: string
  params: Record<string, unknown>
}

export function assertCanonicalGroupCalls(calls: GroupRequestCall[]): void {
  for (const { method, params } of calls) {
    if (!Object.hasOwn(GROUP_METHODS, method)) {
      throw new Error(`Undeclared canonical group method: ${method}`)
    }

    for (const field of Object.keys(params)) {
      if (field !== 'profile' && !GROUP_FIELDS[method].includes(field)) {
        throw new Error(`Undeclared canonical group field: ${method}.${field}`)
      }
    }
  }
}

type ProfileRequest = (route: unknown, method: string, params?: Record<string, unknown>, ...rest: unknown[]) => unknown

/** Wrap a test's `host.requestProfile` double: its answers are unchanged, and
 *  every `groups.*` call it saw is checked against the gateway after each test. */
export function captureGroupRequests(request: ProfileRequest) {
  const calls: GroupRequestCall[] = []
  afterEach(() => assertCanonicalGroupCalls(calls.splice(0)))

  return {
    calls,
    request: (...args: Parameters<ProfileRequest>) => {
      const [, method, params] = args

      if (method.startsWith('groups.')) {
        calls.push({ method, params: { ...params } })
      }

      return request(...args)
    }
  }
}

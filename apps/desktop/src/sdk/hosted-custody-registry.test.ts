import type { PluginContext } from '@hermes/plugin-sdk'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

import type * as Cleanup from '../plugins/hermes-bots/hosted-room-cleanup'

// Production cleanup -> installation adapter -> actual SDK/registry leases.
// Only decoded wire/native discovery and journal I/O are doubles. Real encrypted
// storage/registered IPC is covered separately by room-secret-custody.test.ts.
const wire = vi.hoisted(() => ({
  installations: { home: 'install:home-A', peer: 'install:peer-A' },
  calls: [] as Array<{ installation: string; method: string; params: Record<string, unknown> }>,
  pause: null as null | { entered: () => void; pending: Promise<void> }
}))

vi.mock('@/hermes', async importActual => ({
  ...(await importActual<Record<string, unknown>>()),
  HermesGateway: class {
    connectionState = 'closed'
    installation = ''
    onEvent = () => () => undefined
    onState = () => () => undefined
    close = () => (this.connectionState = 'closed')
    connect = async (url: string) => {
      const connection = new URL(url).hostname.split('.')[0] as 'home' | 'peer'
      this.installation = wire.installations[connection]
      this.connectionState = 'open'
    }

    request = async <T>(method: string, params: Record<string, unknown> = {}): Promise<T> => {
      wire.calls.push({ installation: this.installation, method, params: structuredClone(params) })

      if (method === 'groups.capabilities') {
        if (wire.pause) {
          const gate = wire.pause
          wire.pause = null
          gate.entered()
          await gate.pending
        }

        return { driver: true, persistent_process: true, authority_gateway_id: this.installation } as T
      }

      if (method === 'groups.state') {
        return {
          driver_status: { peer_routes: [{ member_id: 'builder', status: 'ready', grant_sha256: 'a'.repeat(64) }] }
        } as T
      }

      if (this.installation === 'install:replacement-B') {
        throw { code: 4113, message: 'hosted room not found' }
      }

      return { revoked: true, registered: true, tombstone: { room_id: 'room-original' } } as T
    }
  }
}))

const routes = [
  { connectionId: 'home', profile: 'default', targetProfile: 'default', mode: 'remote' as const },
  { connectionId: 'peer', profile: 'builder', targetProfile: 'builder', mode: 'remote' as const }
]

const KEY = 'hosted-room-cleanup-v1'
let cleanup: typeof Cleanup | null = null
let closeRegistry = () => undefined as void
const originalDesktop = window.hermesDesktop

beforeEach(() => {
  vi.resetModules()
  vi.useFakeTimers()
  wire.installations = { home: 'install:home-A', peer: 'install:peer-A' }
  wire.calls = []
  wire.pause = null
})

afterEach(() => {
  cleanup?.stopHostedRoomCleanup()
  closeRegistry()
  vi.restoreAllMocks()
  vi.clearAllTimers()
  vi.useRealTimers()
  window.hermesDesktop = originalDesktop
})

async function load(storage: PluginContext['storage']) {
  const { host } = await import('@/sdk')
  const registry = await import('@/store/gateway')
  registry.configureGatewayRegistry({ onEvent: vi.fn() })
  registry.closeSecondaryGateways()
  registry.setPrimaryGateway({ connectionState: 'open', request: vi.fn() } as never, 'default')
  registry.setPrimaryGatewayConnection({ connectionId: 'foreground' })

  closeRegistry = () => {
    registry.closeSecondaryGateways()
    registry.setPrimaryGateway(null)
  }

  window.hermesDesktop = {
    getConnectionFor: vi.fn(async () => ({ port: 5151, sharedRemote: false })),
    getGatewayWsUrlFor: vi.fn(async (input: { connectionId?: string }) => ({
      ok: true,
      wsUrl: `ws://${input.connectionId}.inert.invalid`
    }))
  } as unknown as typeof window.hermesDesktop
  let currentRoutes: typeof routes = []
  vi.spyOn(host, 'profileRoutes').mockImplementation(async () => currentRoutes)
  cleanup = await import('../plugins/hermes-bots/hosted-room-cleanup')
  await cleanup.startHostedRoomCleanup(storage)
  currentRoutes = routes

  return { cleanup, registry }
}

function storageFixture() {
  const values = new Map<string, unknown>()

  const storage = {
    get: <T>(key: string, fallback?: T) => structuredClone(values.get(key) ?? fallback ?? null) as T,
    set: (key: string, value: unknown) => values.set(key, structuredClone(value)),
    remove: (key: string) => values.delete(key)
  } as unknown as PluginContext['storage']

  return { values, storage }
}

async function prepare(kind: 'home-disband' | 'peer-reconnect' | 'peer-revoke' | 'peer-revoke-exact') {
  const saved = storageFixture()
  const loaded = await load(saved.storage)
  await loaded.cleanup.addHostedRoomCleanup({
    operationId: 'original-operation',
    setupId: 'original-setup',
    kind,
    connectionId: kind === 'home-disband' ? 'home' : 'peer',
    installationId: kind === 'home-disband' ? wire.installations.home : wire.installations.peer,
    homeInstallationId: wire.installations.home,
    profile: kind === 'home-disband' ? 'default' : 'builder',
    roomId: 'room-original',
    cancelId: 'original-cancel',
    grant: 'synthetic-only-original-grant',
    grantSha256: 'a'.repeat(64),
    homeConnectionId: 'home',
    homeProfile: 'default',
    memberId: 'builder',
    targetUrl: 'https://fixture.invalid/p/builder',
    catalog: { installation_id: wire.installations.peer, catalog_digest: 'original-digest' }
  })
  await loaded.cleanup.armHostedRoomCleanup('original-setup')

  return { ...saved, ...loaded }
}

it.each([
  ['home-disband', 'home'],
  ['peer-revoke', 'peer'],
  ['peer-revoke-exact', 'peer'],
  ['peer-reconnect', 'peer'],
  ['peer-reconnect', 'home']
] as const)(
  'holds restarted %s on reused %s through actual SDK, then settles only on restored A',
  async (kind, replacement) => {
    const first = await prepare(kind)
    const original = structuredClone(first.values.get(KEY))
    first.cleanup.stopHostedRoomCleanup()
    closeRegistry()
    vi.restoreAllMocks()
    vi.resetModules()
    wire.installations[replacement] = 'install:replacement-B'
    wire.calls = []
    const restarted = await load(first.storage)
    await restarted.cleanup.dispatchHostedRoomCleanup()
    expect(wire.calls.length).toBeGreaterThan(0)
    expect(wire.calls.every(call => call.method === 'groups.capabilities')).toBe(true)
    expect(JSON.stringify(wire.calls)).not.toContain('synthetic-only-original-grant')
    expect(restarted.cleanup.hostedRoomCleanupPending('original-setup')).toBe(true)
    const held = (first.values.get(KEY) as Cleanup.HostedRoomCleanup).operations[0]
    const initial = (original as Cleanup.HostedRoomCleanup).operations[0]
    expect(held).toMatchObject({
      operationId: initial.operationId,
      installationId: initial.installationId,
      homeInstallationId: initial.homeInstallationId,
      grant: initial.grant,
      catalog: initial.catalog
    })
    restarted.registry.disposeSecondariesForConnection(replacement)
    wire.installations[replacement] = replacement === 'home' ? 'install:home-A' : 'install:peer-A'
    wire.calls = []
    await restarted.cleanup.dispatchHostedRoomCleanup()
    expect(restarted.cleanup.hostedRoomCleanupPending('original-setup')).toBe(false)
    expect((first.values.get(KEY) as Cleanup.HostedRoomCleanup).operations).toEqual([])

    const mutation =
      kind === 'home-disband'
        ? 'groups.disband'
        : kind === 'peer-reconnect'
          ? 'groups.peer.register'
          : kind === 'peer-revoke'
            ? 'groups.peer.revoke'
            : 'groups.peer.revoke_exact'

    expect(wire.calls.some(call => call.method === mutation)).toBe(true)
    expect(wire.calls.some(call => call.installation === 'install:replacement-B')).toBe(false)
  }
)

it.each(['install:peer-A', 'install:replacement-B'])(
  'retains compensation when a physical socket is replaced during capability read (%s)',
  async installation => {
    const loaded = await prepare('peer-revoke-exact')
    let entered!: () => void
    let release!: () => void
    const reached = new Promise<void>(resolve => (entered = resolve))
    const pending = new Promise<void>(resolve => (release = resolve))
    wire.pause = { entered, pending }
    const dispatch = loaded.cleanup.dispatchHostedRoomCleanup()
    await reached
    loaded.registry.disposeSecondariesForConnection('peer', { redial: true })
    loaded.registry.disposeSecondariesForConnection('peer')
    wire.installations.peer = installation
    release()
    await dispatch
    expect(wire.calls.every(call => call.method === 'groups.capabilities')).toBe(true)
    expect(JSON.stringify(wire.calls)).not.toContain('synthetic-only-original-grant')
    expect(loaded.cleanup.hostedRoomCleanupPending('original-setup')).toBe(true)
    expect((loaded.values.get(KEY) as Cleanup.HostedRoomCleanup).operations).toHaveLength(1)
    loaded.registry.disposeSecondariesForConnection('peer')
    wire.installations.peer = 'install:peer-A'
    await loaded.cleanup.dispatchHostedRoomCleanup()
    expect(loaded.cleanup.hostedRoomCleanupPending('original-setup')).toBe(false)
  }
)

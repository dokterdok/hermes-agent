import { type ConnectionState, type GatewayEvent, registryBackendScopeKey, type ServerRequest } from '@hermes/shared'

import type { HermesConnection } from '@/global'
import type { HermesGateway } from '@/hermes'
import { setApiRequestConnection } from '@/hermes'
import { acceptExecutionEvent } from '@/lib/execution-authority'
import { markNativeNotifyBaseline } from '@/store/notify-baseline'
import { setConnection, setGatewayState } from '@/store/session'

import { gatewayState } from './gateway-registry-state'
import {
  applyActive,
  beginGatewayActivation,
  cancelTurnLeaseRelease,
  drainPendingConnectionRedial,
  publishTurnLease,
  reopenAfterRedial,
  scopeHasTurnLease,
  setActive
} from './gateway-route-lifecycle'
import {
  attachedRemoteProbe,
  createSecondary,
  gatewayForProfile,
  openSecondary,
  probeSecondaryLiveness,
  rearmSecondary,
  reconnectSecondary,
  requestOnPrimaryGateway,
  scheduleReconnect,
  sharedPrimaryRoute
} from './gateway-secondary-lifecycle'

// ── Multi-profile gateway routing ──────────────────────────────────────────
// Concurrent sessions across profiles need concurrent sockets: the renderer's
// event handler is already session-keyed, so the only thing stopping two
// profiles streaming at once was the single swapping socket. We keep that one
// socket as the PRIMARY (window) backend — owned by use-gateway-boot, with all
// its boot-progress / sleep-wake machinery — and add one persistent SECONDARY
// socket per *other* profile that has live work. Every socket feeds the same
// handleGatewayEvent, so background sessions keep painting. Single-profile users
// only ever have the primary, so their path is byte-for-byte unchanged.

export const normKey = (profile: string | null | undefined): string => (profile ?? '').trim() || 'default'

export interface GatewayRouteState {
  connectionId: string
  profile: string
  state: ConnectionState
}

export interface GatewayRouteLease {
  readonly connectionId: string
  readonly generation: number
  readonly profile: string
  assertCurrent: () => void
  release: () => void
  request: <T>(method: string, params?: Record<string, unknown>, timeoutMs?: number, signal?: AbortSignal) => Promise<T>
}
// Dial intent callers attach to a user-initiated open. The canonical
// `hermes gateway ensure` path has no local slot pool, so the hint changes
// nothing about the dial itself; it survives as the option shape the SDK,
// Settings scopes and session creation pass so an explicit user gesture stays
// distinguishable from ambient hydration at the call site.
export type SpawnPriority = 'foreground' | 'background'

// Read connection state through a call so TS control-flow analysis doesn't
// narrow the getter to a constant across guards (it genuinely changes).
export const isOpen = (gateway: HermesGateway | null): boolean => gateway?.connectionState === 'open'

export interface RegistryConfig {
  /** Electron's published descriptor is authoritative for a primary gateway's
   * registry identity. Kept as a getter so gateway.ts does not own or duplicate
   * the connection store. */
  activeConnectionId?: () => null | string
  onEvent: (event: GatewayEvent) => void
  /** Server→client request (clarify, approval, …) from ANY socket the registry owns; the
   *  request's `respond` already routes to the socket it came from. `profile` /
   *  `connectionId` tag the source the same way events are tagged. */
  onServerRequest?: (request: ScopedServerRequest) => void
  onActiveConnectionInvalidated?: (fallbackProfile: string, activationEpoch: number) => void
  onActiveConnectionChanged?: (connection: HermesConnection) => void
  /**
   * Fires whenever applyActive() moves the active route to a (possibly
   * different) profile — including registry-internal eviction fallbacks
   * (connection removal, profile delete) that no renderer call
   * initiated. Consumers mirror this into $activeGatewayProfile so the
   * published profile can never diverge from the socket actually selected
   * (#89206: the stale-profile split-brain that stranded bot wake-ups).
   */
  onActiveRouteChanged?: (profile: string) => void
  /** Drop transient profile-pool runtime routes when local profile teardown
   * permanently retires their owning secondary. Exact registry routes are
   * deliberately outside this callback: a remote source may share the name. */
  onLocalProfileRetired?: (profile: string) => void
  /**
   * Scopes a FOREGROUND surface is bound to right now — every mounted
   * session tile's owner and the primary thread's (foregroundSessionScopes in
   * store/session-states; a config hook because that store imports this
   * one). Consulted by EVERY dispose path — the live-work pruner and the
   * dispose-at-refcount-0 request/relay leases alike (#93892): a tile's
   * resume mints its runtime on its owner's socket, and any path that closes
   * that socket makes the backend orphan-reap the runtime, whose
   * `session.reclaimed` unbinds the tile and re-arms its resume — a spinner
   * loop with no terminal state. Read at decision time, never cached: it
   * follows the tile set, so closing the tile releases the socket.
   */
  foregroundScopes?: () => ReadonlySet<string>
  /**
   * Scopes with a running or needs-input session runtime, in the same key
   * language as `foregroundScopes` (composite registry keys, bare profiles
   * for local/legacy entries). The wake-path liveness probe counts these as
   * in-flight work: prompt.submit returns before the turn ends, so
   * `activeRequests` is 0 during most of a turn.
   */
  liveScopes?: () => ReadonlySet<string>
}

// ── Secondary (pool) backends ──────────────────────────────────────────────
export interface Secondary {
  /** Scope key from registryBackendScopeKey(connectionId, profile). */
  scope: string
  profile: string
  /** Registry connection serving this socket; null = the local/legacy path. */
  connectionId: null | string
  connection: HermesConnection | null
  gateway: HermesGateway
  /**
   * Date.now() of the most recent socket 'open'. The live-work pruner's
   * min-lifetime grace reads this: an idle prune can race an on-demand dial
   * (prune → redial → prune) and close freshly opened sockets before their
   * consumer registers in the keep-set, re-triggering the orphan-reap /
   * remount loop (#94769). 0 = never opened.
   */
  lastOpenedAt: number
  /** Date.now() of the CURRENT socket's 'open'; null while not open. Stability
   *  clock for the backoff reset (#83134) — `lastOpenedAt` persists across
   *  closes and would make every failed redial look like a stable session. */
  openedAt: null | number
  activeRequests: number
  connectPromise: Promise<void> | null
  offEvent: () => void
  offRequest: () => void
  offState: () => void
  reconnectTimer: ReturnType<typeof setTimeout> | null
  reconnectAttempt: number
  /**
   * Consecutive unanswered wake-probe pings on this entry's current socket;
   * drives the same streak tolerance the primary's probe applies
   * (decideLivenessForceClose). Reset on every answered probe and on every
   * fresh socket open.
   */
  livenessProbeFailures: number
  /** Pending deferred liveness re-probe after an in-flight-work deferral. */
  livenessReprobeTimer: ReturnType<typeof setTimeout> | null
  /** Consecutive automatic dials that stalled (slot wait / dial timeout)
   *  rather than failing fast; see SECONDARY_STALLED_DIAL_BUDGET. */
  stalledDials: number
  reconnecting: boolean
  /** A material connection edit is waiting for live owners to drain. */
  pendingConnectionRedial: boolean
  /** Advances before every physical socket dial, including reconnect ABA. */
  ownerGeneration: number
  /**
   * True when a foreground/prewarmed consumer owns this entry beyond one RPC.
   * Guards ONLY the dispose-at-refcount-0 paths (request/relay leases), never
   * the live-work pruner: it is a one-way latch that every hover pre-warm and
   * profile switch sets and nothing ever clears, so honoring it in
   * pruneSecondaryGateways would pin every socket ever warmed. A foreground
   * surface that must keep its owner socket (a mounted session tile, the
   * primary thread) is represented in the pruner's keep-set instead — see
   * foregroundSessionScopes in store/session-states (#93892).
   */
  retained: boolean
  /**
   * Bot-relay retainers pinning this socket open across drain ticks (#93594).
   * The relay's drain loop RPCs every registered connection on an interval;
   * without retention each tick dialed and tore down a fresh WebSocket per
   * connection (refcount hit 0 → dispose). Counted, not boolean, so relay
   * retention can never clobber (or be clobbered by) the foreground
   * `retained` flag. Only non-local registry routes are ever counted here —
   * see retainGatewayForRelay.
   */
  relayRetainCount: number
  // While true the entry auto-reconnects on drop; pruning flips it off so a
  // deliberate close doesn't trigger the backoff loop.
  wantOpen: boolean
  /**
   * Main retired this scope's pooled backend for a foreground open elsewhere
   * (electron/pool-retire.ts). A parked-by-stall entry re-arms on the
   * wake/focus nudge; a retired one must not — that nudge would redial into
   * the very slot the retirement freed. Only an explicit open of the scope
   * clears it (rearmSecondary).
   */
  retiredByPool: boolean
  /**
   * Epoch-ms deadline while an activation (prepare/ensure) is mid-dial. The
   * live-work pruner must not dispose an entry the user is switching to: a
   * switch target is not yet the active key, has no live sessions and holds
   * no request lease, so during a cold pool spawn (~3s) every prune recompute
   * saw it as idle garbage and disposed it mid-dial — the root of the dead
   * profile clicks in #89622. Cleared when the activation settles; bounded so
   * an orphaned lease self-heals.
   */
  activationLeaseUntil: number
}

// How long a mid-dial activation holds its prune lease: covers a cold pool
// backend spawn + socket connect with margin, while still letting a leaked
// lease expire quickly enough for the reaper to reclaim the entry.
const ACTIVATION_LEASE_MS = 30_000

export const g = gatewayState()

function connectionRouteGeneration(connectionId: string): number {
  return g.routeOwnerGenerations?.get(connectionId) ?? 0
}

function advanceConnectionRouteGeneration(connectionId: string): void {
  const generations = (g.routeOwnerGenerations ??= new Map<string, number>())
  generations.set(connectionId, connectionRouteGeneration(connectionId) + 1)
}

// Dev HMR can hand a newer module an older state-container shape. Keep the
// generation ledger lazy so an already-open socket still survives the update.
export const openedSecondaryScopes = (): Set<string> => (g.openedSecondaryScopes ??= new Set<string>())
// Dev-HMR states predate this field, so read it through the same lazy accessor pattern.
export const reactivatingScopes = (): Set<string> => (g.reactivatingScopes ??= new Set<string>())

// Re-exported as a stable binding: the atom instance lives in `g`, so every hot
// reload of this module hands back the SAME atom subscribers are already wired
// to. (A fresh `atom()` per reload would orphan existing subscriptions.)
export const $gateway = g.$gateway

// The profile the ACTIVE gateway is actually routed to. Registry-owned: the
// only writer is applyActive(), which sets it in the same synchronous step
// that selects the socket — so a consumer that reads this and then calls
// activeGateway() always gets a matching (profile, socket) pair. Renderer
// surfaces (store/profile.ts's $activeGatewayProfile) mirror this atom
// instead of writing their own copy.
export const $activeGatewayRoute = g.$activeProfile

/** Bare profile name the active gateway serves (never a composite scope). */
export function activeGatewayProfileKey(): string {
  return g.$activeProfile.get()
}

export function configureGatewayRegistry(cfg: RegistryConfig): void {
  g.config = cfg
}

/**
 * Feed a synthetic event through the exact same fan-out a real socket frame
 * takes (`config.onEvent` → the desktop's `handleGatewayEvent`). Used by
 * dev-only tooling to exercise the real event branches (e.g. the credit-notice
 * demo) without a backend that can produce the event on demand. No-op until a
 * registry is configured.
 */
export function emitLocalGatewayEvent(event: GatewayEvent): void {
  g.config?.onEvent(event)
}

/** A server→client request tagged with the registry source it arrived from (like `GatewayEvent.profile`). */
export interface ScopedServerRequest extends ServerRequest {
  connectionId?: string
  profile: string
}

/**
 * Route a server→client request into the registry handler with its source tags.
 * Fail fast, never swallow: the backend blocks on this answer (clarify waits
 * its full 3600s deadline). Without a registry there is nobody to answer —
 * returning `false` lets the channel answer -32601 immediately instead of
 * stalling the turn (it also fires the client's `onUnhandledRequest` sink).
 */
export function dispatchServerRequest(request: ServerRequest, profile: string, connectionId: null | string): boolean {
  if (!g.config?.onServerRequest) {
    return false
  }

  g.config.onServerRequest({ ...request, ...(connectionId ? { connectionId } : {}), profile })

  return true
}

/** Fan a primary-socket server request into the registry handler with the active source tags. */
export function dispatchPrimaryServerRequest(request: ServerRequest, profile: string): boolean {
  return dispatchServerRequest(request, profile, g.config?.activeConnectionId?.() ?? null)
}

export function setPrimaryGateway(gateway: HermesGateway | null, profile = 'default'): void {
  const next = normKey(profile)

  if (g.primaryGateway !== gateway || g.primaryProfile !== next) {
    g.primaryOwnerGeneration = (g.primaryOwnerGeneration ?? 0) + 1
  }

  if (g.primaryGateway !== gateway) {
    g.primaryConnectionId = null
    g.primaryConnectionMode = null
  }

  // Route identity is exact-scope, never bare-name (#93892 follow-up): when
  // the active route IS the primary and the primary re-homes to another
  // profile, the active key must follow it. Leaving the old bare profile name
  // behind lets a later same-named LOCAL secondary inherit the active-route
  // spare in pruneSecondaryGateways — a remote tile keep-set of composite
  // scopes then appears to "pin" that unrelated local socket forever.
  if (g.activeKey === g.primaryProfile) {
    g.activeKey = next
  }

  g.primaryGateway = gateway
  g.primaryProfile = next

  if (g.activeKey === g.primaryProfile) {
    setApiRequestConnection(g.primaryConnectionId)
  }
}

export function setPrimaryGatewayConnectionId(
  connectionId: null | string | undefined,
  mode: 'local' | 'remote' | null | undefined = undefined
): void {
  // Hardening for #95628: while the active route is a secondary scope, the
  // window is looking at a NON-primary socket — any connection id flowing
  // through presentation-layer code at that moment describes the secondary,
  // not the primary. Accepting it would relabel the primary socket, so every
  // ambient API/WebSocket helper (and new-session routing) silently lands on
  // the wrong backend. The primary's own identity is (re)published by its
  // boot/reconnect path, which runs with the primary route active.
  if (!isActivePrimary()) {
    return
  }

  const next = (connectionId ?? '').trim() || null

  if (g.primaryConnectionId !== next) {
    g.primaryOwnerGeneration = (g.primaryOwnerGeneration ?? 0) + 1
  }

  g.primaryConnectionId = next
  g.primaryConnectionMode = mode === 'local' || mode === 'remote' ? mode : null

  if (g.activeKey === g.primaryProfile) {
    setApiRequestConnection(g.primaryConnectionId)
  }
}

/**
 * Mode of the socket this window already dialed for `(connectionId, profile)`,
 * following gatewayForProfile's precedence: the primary socket when it serves
 * that profile, else a secondary's own descriptor. Null until one is dialed.
 */
export function dialedGatewayModeFor(connectionId: null | string, profile: string): 'local' | 'remote' | null {
  const id = String(connectionId ?? '').trim() || null
  const key = normKey(profile)

  if (key === g.primaryProfile && (!id || id === g.primaryConnectionId) && g.primaryConnectionMode) {
    return g.primaryConnectionMode
  }

  const mode = g.secondaries.get(registryBackendScopeKey(id, key))?.connection?.mode

  return mode === 'local' || mode === 'remote' ? mode : null
}

/** Publish the registry source owned by the window primary socket. */
export function setPrimaryGatewayConnection(connection: Pick<HermesConnection, 'connectionId' | 'mode'> | null): void {
  setPrimaryGatewayConnectionId(connection?.connectionId, connection?.mode)
}

export function isPrimaryRegistryRoute(connectionId: null | string, profile: string): boolean {
  const id = String(connectionId ?? '').trim()

  return (
    normKey(profile) === g.primaryProfile &&
    Boolean(id) &&
    Boolean(g.primaryConnectionId) &&
    id === g.primaryConnectionId
  )
}

export interface AttachedRemoteProbe {
  gateway: HermesGateway | null
  sharedRemote: boolean
  assertCurrent: () => void
}

export function isActivePrimary(): boolean {
  return g.activeKey === g.primaryProfile
}

/** Changes on every active route selection, including same-profile source swaps. */
export function gatewayActivationEpoch(): number {
  return Number.isFinite(g.activationEpoch) ? g.activationEpoch : 0
}

export function activeGateway(): HermesGateway | null {
  if (g.activeKey === g.primaryProfile) {
    return g.primaryGateway
  }

  // A named scope resolves to ITS socket or nothing. Falling back to the
  // primary here would silently route calls (sends, session ops, roster
  // requests) to the WRONG backend whenever the scope's entry is gone —
  // teardown sites keep the invariant "activeKey always resolves" by
  // re-pointing the active key at the primary when they evict it.
  return g.secondaries.get(g.activeKey)?.gateway ?? null
}

/** Passive ordering barrier for a runtime's transcript reads. Inspect only
 * existing sockets: waiting must never dial, activate, or retain a backend.
 * Each client names its own replaying runtime IDs; no ambient route is used
 * to decide which session's events are safe to paint over. A pruned or
 * pool-retired secondary keeps its closed socket's watermarks but will never
 * reopen to replay them, so it must not veto reads forever. */
export function pendingSessionReplay(runtimeId: string): Promise<boolean> | undefined {
  const reconnectable = [...g.secondaries.values()].filter(entry => entry.wantOpen && !entry.retiredByPool)
  const clients = new Set([g.primaryGateway, ...reconnectable.map(entry => entry.gateway)])

  // A closed socket's false means only that IT cannot replay yet. When another
  // open socket already serves this runtime, that socket orders the read.
  const servedOpen = [...clients].some(client => isOpen(client) && client?.getSeqWatermarks?.()[runtimeId] != null)

  const pending = [...clients].flatMap(client => {
    if (servedOpen && !isOpen(client)) {
      return []
    }

    // A dev-HMR survivor can predate the barrier method.
    const barrier = client?.sessionReplayBarrier?.(runtimeId)

    return barrier ? [barrier] : []
  })

  return pending.length ? Promise.all(pending).then(results => results.every(Boolean)) : undefined
}

/**
 * The registry connection serving the gateway the user is currently looking
 * at. A registry-backed primary takes its identity from the published primary
 * connection, falling back to Electron's active descriptor until that is set;
 * a true legacy primary (no resolved connectionId) and profile-keyed local
 * secondaries remain null. Event consumers pair this with the event's own
 * `connectionId` tag so "from the active profile" really means "from the active SOURCE":
 * two connected gateways can both expose a 'default' profile, and a bare
 * profile comparison attributed gateway B's 'default' activity to gateway A.
 */
export function activeGatewayConnectionId(): null | string {
  if (g.activeKey === g.primaryProfile) {
    return g.primaryConnectionId ?? (g.config?.activeConnectionId?.()?.trim() || null)
  }

  return g.secondaries.get(g.activeKey)?.connectionId ?? null
}

/**
 * Registry connections currently served by a live (open-socket) secondary.
 * Used by the reconnect path when the restarted primary's own registry
 * identity is unknown: Bot runtimes owned by these connections are provably
 * NOT the restarted backend and keep their bindings; everything else re-resumes.
 */
export function liveSecondaryConnectionIds(): Set<string> {
  const live = new Set<string>()

  for (const entry of g.secondaries.values()) {
    if (entry.connectionId && isOpen(entry.gateway)) {
      live.add(entry.connectionId)
    }
  }

  return live
}

// Mirror a backend's connection state into the global composer state, but only
// when that backend is the one the user is currently looking at. Lets the
// composer reflect the active profile's socket without a background reconnect
// flipping the foreground enabled/disabled state.
export function reportGatewayState(profile: string, state: ConnectionState): void {
  // Any socket opening replays parked prompts; hold OS notifications so a
  // launch/reconnect doesn't alert about state that already existed.
  if (state === 'open') {
    markNativeNotifyBaseline()
  }

  if (normKey(profile) === g.activeKey) {
    setGatewayState(state)
  }

  const entry = g.secondaries.get(profile)
  const connectionId = entry?.connectionId ?? (profile === g.primaryProfile ? g.primaryConnectionId : null)

  if (connectionId) {
    const event = { connectionId, profile: entry?.profile ?? profile, state }

    for (const listener of [...(g.routeStateListeners ?? [])]) {
      listener(event)
    }
  }
}

/** Observe background sockets without changing the foreground gateway atom. */
export function onGatewayRouteState(listener: (event: GatewayRouteState) => void): () => void {
  const listeners = (g.routeStateListeners ??= new Set())
  listeners.add(listener)

  return () => {
    listeners.delete(listener)
  }
}

export function reportPrimaryGatewayState(state: ConnectionState): void {
  if (state !== 'open') {
    g.primaryOwnerGeneration = (g.primaryOwnerGeneration ?? 0) + 1
  }

  reportGatewayState(g.primaryProfile, state)
}

export function publishActiveConnection(connection: HermesConnection): void {
  if (g.config?.onActiveConnectionChanged) {
    g.config.onActiveConnectionChanged(connection)
  } else {
    setConnection(connection)
  }
}

export function clearTimer(entry: Secondary): void {
  if (entry.reconnectTimer !== null) {
    clearTimeout(entry.reconnectTimer)
    entry.reconnectTimer = null
  }
}

// Consecutive STALLED automatic dials (a pool-slot wait or the 20s dial
// timeout, never a fast transport error) before a secondary parks. A
// tile-pinned scope stays wantOpen for the tile's lifetime, so an owner
// backend that keeps losing its slot wait otherwise re-queues a background
// spawn on every backoff tick forever — the queue/timeout treadmill in
// #103375. Fast failures (a gateway restarting, ECONNREFUSED) keep the
// ordinary unbounded backoff: they cost nothing and the socket must come back
// on its own. Parking keeps the entry; any explicit open of the scope
// (requestGatewayForAgent, openGatewayForAgent, ensureGatewayForAgent,
// ensureActiveGatewayOpen) re-arms it with a fresh budget.
export const SECONDARY_STALLED_DIAL_BUDGET = 3

/**
 * Send a gateway RPC through a named Desktop profile without foregrounding it.
 * Global-remote routes share the primary socket and need an explicit profile
 * param; dedicated pooled backends are already scoped by their descriptor.
 */
export async function requestGatewayForProfile<T>(
  profile: string,
  method: string,
  params: Record<string, unknown> = {},
  timeoutMs?: number,
  signal?: AbortSignal,
  { spawnPriority = 'background' }: { spawnPriority?: SpawnPriority } = {}
): Promise<T> {
  const route = await gatewayForProfile(profile, true, spawnPriority)

  try {
    if (!route.gateway) {
      throw new Error(`Hermes gateway unavailable for profile "${route.key}"`)
    }

    const routedParams = route.scopeProfile ? { ...params, profile: route.key } : params

    // Same arity contract as the ambient path in session-request-router: only
    // pass the deadline args through when the caller set them, so a plain
    // profile-routed RPC keeps its two-argument call shape.
    return await (timeoutMs === undefined && signal === undefined
      ? route.gateway.request<T>(method, routedParams)
      : route.gateway.request<T>(method, routedParams, timeoutMs, signal))
  } finally {
    route.release()
  }
}

/**
 * Send a gateway RPC through one registry source without activating it. The
 * composite (connectionId, profile) pool key prevents same-named agents on two
 * sources from sharing a socket. Only null/empty ids retain the v1 profile
 * resolver; explicit `local` is a registry source and must use getConnectionFor.
 *
 * `spawnPriority` marks a user gesture that reaches this RPC path (first send on
 * a fresh chat, "New session", an explicit Bot Chat open) as 'foreground'
 * (#102281 primitive; #105104 symptom). The canonical ensure dial has no slot
 * to reserve, so the tag is carried, not acted on.
 */
export async function requestGatewayForAgent<T>(
  connectionId: null | string,
  profile: string,
  method: string,
  params: Record<string, unknown> = {},
  timeoutMs?: number,
  signal?: AbortSignal,
  { spawnPriority = 'background' }: { spawnPriority?: SpawnPriority } = {}
): Promise<T> {
  const key = normKey(profile)
  const scope = registryBackendScopeKey(connectionId, key)

  if (scope === key) {
    return requestGatewayForProfile<T>(key, method, params, timeoutMs, signal, { spawnPriority })
  }

  // A primary remote selected from the connection registry carries its source
  // id in the active connection descriptor. Requests for that exact
  // (connection, profile) already have an owning socket: the window primary.
  // Dialing a registry secondary here can resolve the same public endpoint to a
  // different backend/profile route, so durable session.resume reports
  // "session not found" while REST history from the primary remains visible.
  // Require both owner identities to agree before collapsing the route; a
  // different source or profile must retain its isolated secondary.
  if (isPrimaryRegistryRoute(connectionId, key)) {
    return requestGatewayForProfile<T>(key, method, params, timeoutMs, signal, { spawnPriority })
  }

  const attached = await attachedRemoteProbe(connectionId, key, signal, spawnPriority)
  signal?.throwIfAborted()
  attached?.assertCurrent()

  if (attached?.sharedRemote) {
    return requestOnPrimaryGateway<T>(attached, method, { ...params, profile: key }, timeoutMs, signal)
  }

  if (!window.hermesDesktop?.getConnectionFor) {
    throw new Error('This Desktop build cannot dial registry connections. Update Hermes Desktop.')
  }

  const entry = g.secondaries.get(scope) ?? createSecondary(key, connectionId)

  // Existing dev-HMR entries predate request leases/ownership.
  if (!Number.isFinite(entry.activeRequests)) {
    entry.activeRequests = 0
  }

  if (typeof entry.retained !== 'boolean') {
    entry.retained = true
  }

  rearmSecondary(entry, spawnPriority)
  entry.activeRequests += 1

  try {
    if (!isOpen(entry.gateway)) {
      await openSecondary(entry)
    }

    return await (timeoutMs === undefined && signal === undefined
      ? entry.gateway.request<T>(method, params)
      : entry.gateway.request<T>(method, params, timeoutMs, signal))
  } finally {
    entry.activeRequests = Math.max(0, entry.activeRequests - 1)

    if (
      !drainPendingConnectionRedial(entry) &&
      entry.activeRequests === 0 &&
      !entry.retained &&
      !relayRetained(entry) &&
      !foregroundPinned(entry) &&
      g.activeKey !== entry.scope
    ) {
      disposeSecondary(entry)

      if (g.secondaries.get(entry.scope) === entry) {
        g.secondaries.delete(entry.scope)
      }
    }
  }
}

/** Retain one resolved registry route and physical socket across a sensitive
 * multi-RPC sequence. Registry edit/remove generations invalidate immediately;
 * a retained old socket may finish bytes already dispatched, but no later
 * request or ownership publication can pass assertCurrent(). */
export async function acquireGatewayRouteLease(connectionId: string, profile: string): Promise<GatewayRouteLease> {
  const id = String(connectionId || '').trim()
  const key = normKey(profile)

  if (!id) {
    throw new Error('Gateway route lease requires an explicit connection')
  }

  const connectionGeneration = connectionRouteGeneration(id)

  const makePrimaryLease = (
    gateway: HermesGateway,
    ownerGeneration: number,
    routedProfile: boolean
  ): GatewayRouteLease => {
    let released = false

    const assertCurrent = () => {
      if (
        released ||
        connectionRouteGeneration(id) !== connectionGeneration ||
        gateway !== g.primaryGateway ||
        ownerGeneration !== (g.primaryOwnerGeneration ?? 0) ||
        id !== g.primaryConnectionId ||
        !isOpen(gateway)
      ) {
        throw new Error('Hermes gateway route lease expired')
      }
    }

    return {
      connectionId: id,
      generation: connectionGeneration,
      profile: key,
      assertCurrent,
      release: () => {
        released = true
      },
      request: async <T>(
        method: string,
        params: Record<string, unknown> = {},
        timeoutMs?: number,
        signal?: AbortSignal
      ) => {
        assertCurrent()
        const routedParams = routedProfile ? { ...params, profile: key } : params

        const result = await (timeoutMs === undefined && signal === undefined
          ? gateway.request<T>(method, routedParams)
          : gateway.request<T>(method, routedParams, timeoutMs, signal))

        assertCurrent()

        return result
      }
    }
  }

  if (isPrimaryRegistryRoute(id, key)) {
    const gateway = g.primaryGateway

    if (!gateway || !isOpen(gateway)) {
      throw new Error('Hermes gateway unavailable')
    }

    return makePrimaryLease(gateway, g.primaryOwnerGeneration ?? 0, false)
  }

  const attached = await attachedRemoteProbe(id, key)
  attached?.assertCurrent()

  if (connectionRouteGeneration(id) !== connectionGeneration) {
    throw new Error('Hermes gateway route lease expired')
  }

  if (attached?.sharedRemote) {
    const gateway = attached.gateway

    if (!gateway || !isOpen(gateway)) {
      throw new Error('Hermes gateway unavailable')
    }

    return makePrimaryLease(gateway, g.primaryOwnerGeneration ?? 0, true)
  }

  if (!window.hermesDesktop?.getConnectionFor) {
    throw new Error('This Desktop build cannot dial registry connections. Update Hermes Desktop.')
  }

  const scope = registryBackendScopeKey(id, key)
  const entry = g.secondaries.get(scope) ?? createSecondary(key, id)
  rearmSecondary(entry)
  entry.activeRequests = Number.isFinite(entry.activeRequests) ? entry.activeRequests + 1 : 1
  let released = false

  const release = () => {
    if (released) {
      return
    }

    released = true
    entry.activeRequests = Math.max(0, entry.activeRequests - 1)

    if (
      !drainPendingConnectionRedial(entry) &&
      entry.activeRequests === 0 &&
      !entry.retained &&
      !relayRetained(entry) &&
      !foregroundPinned(entry) &&
      g.activeKey !== entry.scope &&
      g.secondaries.get(entry.scope) === entry
    ) {
      disposeSecondary(entry)
      g.secondaries.delete(entry.scope)
    }
  }

  try {
    if (!isOpen(entry.gateway)) {
      await openSecondary(entry)
    }
  } catch (error) {
    release()
    throw error
  }

  const socketGeneration = entry.ownerGeneration

  const assertCurrent = () => {
    if (
      released ||
      connectionRouteGeneration(id) !== connectionGeneration ||
      g.secondaries.get(scope) !== entry ||
      entry.ownerGeneration !== socketGeneration ||
      entry.pendingConnectionRedial ||
      !isOpen(entry.gateway)
    ) {
      throw new Error('Hermes gateway route lease expired')
    }
  }

  try {
    assertCurrent()
  } catch (error) {
    release()
    throw error
  }

  return {
    connectionId: id,
    generation: connectionGeneration,
    profile: key,
    assertCurrent,
    release,
    request: async <T>(
      method: string,
      params: Record<string, unknown> = {},
      timeoutMs?: number,
      signal?: AbortSignal
    ) => {
      assertCurrent()

      const result = await (timeoutMs === undefined && signal === undefined
        ? entry.gateway.request<T>(method, params)
        : entry.gateway.request<T>(method, params, timeoutMs, signal))

      assertCurrent()

      return result
    }
  }
}

// ── Bot-relay socket retention (#93594) ─────────────────────────────────────
// The desktop bot relay RPCs EVERY registered connection on its drain loop.
// Each of those calls runs through requestGatewayForAgent's per-request lease,
// so a connection with no other consumer dialed a fresh WebSocket and tore it
// down again on every tick — a connect/disconnect pair per connection per tick
// flooding the gateway logs. While the relay is active, its routes hold a
// counted retention that keeps the pooled socket (and its existing
// scheduleReconnect/backoff machinery) alive across ticks; stopBotRelay (and
// plugin dispose) releases it, restoring the dispose-at-refcount-0 behavior.

/**
 * True when a foreground surface (mounted tile / primary thread) is bound to
 * this entry's scope (#93892). Registry-scoped entries match on their
 * composite key only; local/legacy entries also match on the bare profile —
 * the same key language pruneSecondaryGateways' keep-set speaks.
 */
export function foregroundPinned(entry: Secondary): boolean {
  const scopes = g.config?.foregroundScopes?.()

  if (!scopes) {
    return false
  }

  return scopes.has(entry.scope) || (!entry.connectionId && scopes.has(entry.profile))
}

/** True when the bot relay currently pins this entry open. Number guard:
 *  dev-HMR entries predate the field. */
export function relayRetained(entry: Secondary): boolean {
  return Number.isFinite(entry.relayRetainCount) && entry.relayRetainCount > 0
}

/**
 * Pin the pooled socket for one relay route open across drain ticks. Returns
 * a once-only release. Local routes (null/empty or explicit `local` source)
 * are deliberately EXEMPT and get a no-op release: a relay pin would keep a
 * local socket open past the point where retireLocalProfileGateways means to
 * drop it (see that note). Local relay traffic is
 * either the primary socket (no churn) or a short-lived local dial — never
 * the remote reconnect flood this retention exists to stop.
 */
export function retainGatewayForRelay(connectionId: null | string, profile: string): () => void {
  const key = normKey(profile)
  const connection = String(connectionId ?? '').trim()

  if (!connection || connection === 'local') {
    return () => undefined
  }

  const scope = registryBackendScopeKey(connection, key)
  const entry = g.secondaries.get(scope) ?? createSecondary(key, connection)

  if (!Number.isFinite(entry.relayRetainCount)) {
    entry.relayRetainCount = 0
  }

  entry.relayRetainCount += 1

  if (!g.reauthFailures.has(entry.scope)) {
    rearmSecondary(entry)
  }

  let released = false

  return () => {
    if (released) {
      return
    }

    released = true
    entry.relayRetainCount = Math.max(0, (entry.relayRetainCount || 0) - 1)

    if (
      !drainPendingConnectionRedial(entry) &&
      entry.relayRetainCount === 0 &&
      entry.activeRequests === 0 &&
      !entry.retained &&
      !foregroundPinned(entry) &&
      g.activeKey !== entry.scope &&
      g.secondaries.get(entry.scope) === entry
    ) {
      disposeSecondary(entry)
      g.secondaries.delete(entry.scope)
    }
  }
}

/**
 * Hold `profile`'s socket open across a multi-RPC sequence without activating
 * it (#93602). Every requestGatewayForProfile/requestGatewayForAgent call is a
 * per-request lease: at refcount 0 a non-retained secondary is disposed, so a
 * session-scoped sequence (session.create → attach → prompt.submit) minted a
 * runtime id on a socket that closed between calls — the gateway detached the
 * session on WS disconnect and the next RPC hit 4001 "not in memory". Callers
 * acquire this lease before the first session-scoped RPC and release it in a
 * `finally`; the refcount keeps the socket (and the session it minted) alive
 * for the whole sequence. Primary/shared-primary routes return a no-op release.
 *
 * `spawnPriority` follows requestGatewayForAgent: the retain is the FIRST dial
 * of a session-create gesture, so a user click passes 'foreground' here.
 */
export async function retainGatewayForAgent(
  connectionId: null | string,
  profile: string,
  { spawnPriority = 'background' }: { spawnPriority?: SpawnPriority } = {}
): Promise<() => void> {
  const key = normKey(profile)
  const scope = registryBackendScopeKey(connectionId, key)

  if (scope === key) {
    // Plain-profile route: gatewayForProfile's request lease IS the retain —
    // hold it until the caller releases.
    const route = await gatewayForProfile(key, true, spawnPriority)

    return route.release
  }

  if (isPrimaryRegistryRoute(connectionId, key)) {
    return () => undefined
  }

  const attached = await attachedRemoteProbe(connectionId, key, undefined, spawnPriority)
  attached?.assertCurrent()

  if (attached?.sharedRemote) {
    // Primary socket stays open for the window lifetime — no secondary to hold.
    return () => undefined
  }

  if (!window.hermesDesktop?.getConnectionFor) {
    // No registry dialing in this build — nothing to hold; the request path
    // will throw its own actionable error.
    return () => undefined
  }

  const entry = g.secondaries.get(scope) ?? createSecondary(key, connectionId)

  // Existing dev-HMR entries predate request leases/ownership.
  if (!Number.isFinite(entry.activeRequests)) {
    entry.activeRequests = 0
  }

  if (typeof entry.retained !== 'boolean') {
    entry.retained = true
  }

  rearmSecondary(entry, spawnPriority)
  entry.activeRequests += 1

  let released = false

  const release = () => {
    if (released) {
      return
    }

    released = true
    entry.activeRequests = Math.max(0, entry.activeRequests - 1)

    if (drainPendingConnectionRedial(entry)) {
      return
    }

    if (
      entry.activeRequests === 0 &&
      !entry.retained &&
      !relayRetained(entry) &&
      !foregroundPinned(entry) &&
      g.activeKey !== entry.scope
    ) {
      disposeSecondary(entry)

      if (g.secondaries.get(entry.scope) === entry) {
        g.secondaries.delete(entry.scope)
      }
    }
  }

  try {
    if (!isOpen(entry.gateway)) {
      await openSecondary(entry)
    }
  } catch (error) {
    release()
    throw error
  }

  return release
}

const turnLeaseKey = (scope: string, sessionId: string): string => `${scope}\u0000${sessionId}`
const TURN_LEASE_SETTLE_DELAY_MS = 500
const turnExecutionAuthorities = new Map()

export function releaseTurnLeasesForScope(scope: string): void {
  const prefix = `${scope}\u0000`

  for (const [key, timer] of [...g.turnLeaseReleaseTimers]) {
    if (key.startsWith(prefix)) {
      clearTimeout(timer)
      g.turnLeaseReleaseTimers.delete(key)
    }
  }

  for (const [key, release] of [...g.turnLeases]) {
    if (key.startsWith(prefix)) {
      release()
    }
  }
}

/**
 * Keep a routed Desktop prompt's socket alive after prompt.submit ACKs.
 *
 * Routed requests normally own a per-request lease. prompt.submit ACKs as soon
 * as the background turn starts, so releasing that lease at RPC completion
 * detaches the runtime session while the model is still working; the gateway's
 * 20-second orphan guard then interrupts it as `client_gone`. Hold one lease per
 * (route, runtime session) until message.complete/session.info settles the turn.
 */
export async function retainGatewayForSessionTurn(
  connectionId: null | string,
  profile: string,
  sessionId: string
): Promise<() => void> {
  // Primary events do not flow through a Secondary's terminal-event listener.
  // Registering a no-op lease here would leave a phantom key that can suppress
  // the real hold if this route is later re-homed as a secondary.
  if (isPrimaryRegistryRoute(connectionId, normKey(profile))) {
    return () => undefined
  }

  const scope = registryBackendScopeKey(connectionId, normKey(profile))
  const key = turnLeaseKey(scope, sessionId)

  cancelTurnLeaseRelease(key)

  // A busy-session redirect/queue can submit again while the original turn is
  // still retained. The existing lease owns that turn; the extra submit must
  // not replace or release it. The no-op means "another caller owns the
  // shared lease", not "this caller acquired a separately releasable lease".
  if (g.turnLeases.has(key)) {
    return () => undefined
  }

  const releaseRoute = await retainGatewayForAgent(connectionId, profile)

  // Only a Secondary's own terminal-event listener releases this lease, so a route with no
  // Secondary can never release one: retainGatewayForAgent and gatewayForProfile both hand back a
  // no-op exactly when the route rides the primary socket (shared-remote collapse, shared-primary
  // route, or a build without registry dialing), and none of those creates an entry. Storing the
  // key there leaves the same phantom the primary-profile guard above avoids, and it would suppress
  // the real hold once that route IS dialed as a secondary — which the shared-remote probe
  // explicitly expects ("prefer the primary until a later probe can prove isolation"). Decide by
  // outcome rather than re-probing every no-op case.
  if (!g.secondaries.has(scope)) {
    releaseRoute()

    return () => undefined
  }

  // Re-check after the await: the guard above the retain ran before it, so a second submit for the
  // same (route, session) can arrive while this one suspends and both pass it. Only the release
  // stored in the map is ever invoked — releaseTerminalTurnLease does `g.turnLeases.get(key)?.()` —
  // so the loser's hold would never be released and the socket could never be reclaimed.
  if (g.turnLeases.has(key)) {
    releaseRoute()

    return () => undefined
  }

  let released = false

  const release = () => {
    if (released) {
      return
    }

    released = true

    if (g.turnLeases.get(key) === release) {
      g.turnLeases.delete(key)
    }

    cancelTurnLeaseRelease(key)
    // Another session on the same scope may still hold a lease; report the
    // scope's state, not this lease's.
    publishTurnLease(scope, scopeHasTurnLease(scope))
    releaseRoute()
  }

  g.turnLeases.set(key, release)
  publishTurnLease(scope, true)

  return release
}

export function releaseTerminalTurnLease(scope: string, event: GatewayEvent): void {
  const sessionId = String(event.session_id || '').trim()

  if (!sessionId) {
    return
  }

  const key = turnLeaseKey(scope, sessionId)

  if (!acceptExecutionEvent(turnExecutionAuthorities, key, event.type, event)) {
    return
  }

  if (
    event.type === 'message.start' ||
    (event.type === 'session.info' && (event.payload as Record<string, unknown>)?.running === true)
  ) {
    // The gateway emits settled session.info before immediately chaining a
    // queued/goal follow-up. Keep the same route alive for that next turn.
    cancelTurnLeaseRelease(key)

    return
  }

  if (event.type === 'session.reclaimed') {
    g.turnLeases.get(key)?.()

    return
  }

  const payload = event.payload as Record<string, unknown> | undefined

  if (event.type === 'session.info' && payload?.running === false && !g.turnLeaseReleaseTimers.has(key)) {
    // session.info(false) is the authoritative settled edge, but auto-followup
    // emits message.start immediately after it. A short debounce lets that
    // frame cancel release while still reclaiming ordinary completed turns.
    g.turnLeaseReleaseTimers.set(
      key,
      setTimeout(() => {
        g.turnLeaseReleaseTimers.delete(key)
        g.turnLeases.get(key)?.()
      }, TURN_LEASE_SETTLE_DELAY_MS)
    )
  }
}

// Open `profile`'s socket WITHOUT making it active — the hover-intent pre-warm
// (store/profile). Runs the same spawn + connect chain as a real switch, so by
// click time ensureGatewayForProfile finds an open socket and just activates
// it. No scheduleReconnect on failure: a hover is speculative, so a dead
// backend must not start a background retry loop — the real switch owns retry
// and error UX. An already-open (or primary) profile is a no-op.
export async function openGatewayForProfile(
  profile: string,
  { spawnPriority = 'background' }: { spawnPriority?: SpawnPriority } = {}
): Promise<void> {
  await gatewayForProfile(profile, false, spawnPriority)
}

// ── Connection-scoped agents (multi-source roster) ─────────────────────────
// The (connectionId, profile) analogues of the profile functions above. A
// null connectionId falls straight through to the profile path. An explicit
// `local` id remains registry-scoped so it cannot inherit legacy remote v1
// routing. Feature-detected: without the Electron getConnectionFor door these
// throw, and roster surfaces disable non-local rows instead.

// `activationLease`: hold the same prune lease ensureGatewayForAgent holds for
// the whole dial. Phase one of the two-phase source switch (store/connections
// selectConnection) opens the target here and activates it right after; without
// the lease a live-work recompute during the cold spawn would dispose the entry
// mid-dial and the click would die (#89622). Plain pre-warms stay prunable —
// a hovered-but-never-activated socket must not be pinned off another source's
// live work.
export async function openGatewayForAgent(
  connectionId: null | string,
  profile: string,
  {
    activationLease = false,
    spawnPriority = 'background'
  }: { activationLease?: boolean; spawnPriority?: SpawnPriority } = {}
): Promise<void> {
  const scope = registryBackendScopeKey(connectionId, profile)

  if (scope === normKey(profile) || isPrimaryRegistryRoute(connectionId, profile)) {
    return openGatewayForProfile(profile, { spawnPriority })
  }

  const attached = await attachedRemoteProbe(connectionId, profile)
  attached?.assertCurrent()

  if (attached?.sharedRemote) {
    if (!isOpen(attached.gateway)) {
      throw new Error('Hermes gateway unavailable')
    }

    return
  }

  if (!window.hermesDesktop?.getConnectionFor) {
    throw new Error('This Desktop build cannot dial registry connections. Update Hermes Desktop.')
  }

  const entry = g.secondaries.get(scope) ?? createSecondary(profile, connectionId)
  entry.retained = true
  rearmSecondary(entry, spawnPriority)

  if (activationLease) {
    // Stays held after a successful open: the activation that follows releases
    // it (applyActive path), and one that never comes lets it expire.
    entry.activationLeaseUntil = Date.now() + ACTIVATION_LEASE_MS
  }

  if (isOpen(entry.gateway)) {
    return
  }

  try {
    await openSecondary(entry)
  } catch (error) {
    if (activationLease) {
      entry.activationLeaseUntil = 0
    }

    throw error
  }
}

export async function ensureGatewayForAgent(
  connectionId: null | string,
  profile: string,
  { signal }: { signal?: AbortSignal } = {}
): Promise<boolean> {
  const scope = registryBackendScopeKey(connectionId, profile)

  if (scope === normKey(profile) || isPrimaryRegistryRoute(connectionId, profile)) {
    if (signal?.aborted) {
      return false
    }

    await ensureGatewayForProfile(profile)

    return !signal?.aborted
  }

  const activationEpoch = beginGatewayActivation()
  const attached = await attachedRemoteProbe(connectionId, profile)
  attached?.assertCurrent()

  if (attached?.sharedRemote) {
    return Boolean(isOpen(attached.gateway) && !signal?.aborted && applyActive(g.primaryProfile, activationEpoch))
  }

  if (!window.hermesDesktop?.getConnectionFor) {
    throw new Error('This Desktop build cannot dial registry connections. Update Hermes Desktop.')
  }

  let entry = g.secondaries.get(scope)

  if (!entry) {
    entry = createSecondary(profile, connectionId)
  }

  entry.retained = true
  rearmSecondary(entry)
  // Lease the entry against the live-work pruner for the whole dial: the
  // switch target is not yet active and has no live sessions, so a prune
  // recompute firing mid-spawn would otherwise dispose it and this
  // activation would fail (#89622).
  entry.activationLeaseUntil = Date.now() + ACTIVATION_LEASE_MS

  if (!isOpen(entry.gateway)) {
    clearTimer(entry)
    entry.reconnectAttempt = 0

    try {
      await openSecondary(entry)
    } catch {
      scheduleReconnect(entry)
    }
  }

  // The activation is settling either way — release the prune lease.
  entry.activationLeaseUntil = 0

  // A timed-out owner may leave the dial running, but it no longer has the
  // right to move the foreground route when that work eventually settles.
  if (signal?.aborted) {
    return false
  }

  // A source edit/remove may dispose this entry while its dial is still in
  // flight. Only the still-registered, still-owned activation may publish --
  // and only when the WebSocket actually reached open: entry.connection is
  // set BEFORE the dial completes in openSecondary, so a transient first-dial
  // failure (caught above, left for scheduleReconnect) must not count as a
  // successful activation just because a connection descriptor exists
  // (issue #92265).
  const activated =
    entry.wantOpen &&
    g.secondaries.get(scope) === entry &&
    Boolean(entry.connection) &&
    isOpen(entry.gateway) &&
    applyActive(scope, activationEpoch)

  if (activated && entry.connection) {
    publishActiveConnection(entry.connection)
  }

  return activated
}

// Make `profile` the active gateway, lazily opening its socket if needed. The
// primary is a no-op fast path. Background sockets are never closed here.
export async function ensureGatewayForProfile(profile: string): Promise<void> {
  const key = normKey(profile)
  const activationEpoch = beginGatewayActivation()

  if (key === g.primaryProfile) {
    applyActive(key, activationEpoch)

    return
  }

  // Global-remote share (routing case 3): one remote host serves every
  // profile through the PRIMARY socket, scoped per request. Activate the
  // primary instead of dialing a doomed duplicate socket at the same
  // descriptor — $activeGatewayProfile still moves to `key`, so request
  // scoping and profile-aware surfaces behave identically.
  if (await sharedPrimaryRoute(key)) {
    applyActive(g.primaryProfile, activationEpoch)

    return
  }

  let entry = g.secondaries.get(key)

  if (!entry) {
    entry = createSecondary(key)
  }

  entry.retained = true
  rearmSecondary(entry)
  // Lease the entry against the live-work pruner for the whole dial — the
  // profile-door twin of the agent path's lease above (#89622).
  entry.activationLeaseUntil = Date.now() + ACTIVATION_LEASE_MS

  try {
    if (!isOpen(entry.gateway)) {
      clearTimer(entry)
      entry.reconnectAttempt = 0

      try {
        await openSecondary(entry)
      } catch (error) {
        // #81094: a failed secondary dial must NOT fall through to setActive()
        // with a closed socket — that silently routes the user's messages to the
        // primary backend (cross-profile session writes). Keep the reconnect
        // schedule (transient failures still self-heal via the backoff below)
        // but RE-THROW so the profile-door caller surfaces the failure and skips
        // the activation. The agent-door twin (ensureGatewayForAgent) keeps its
        // boolean contract and is guarded by the activeGateway() null invariant.
        scheduleReconnect(entry)
        throw error
      }
    }
  } finally {
    // The activation is settling either way — release the prune lease.
    entry.activationLeaseUntil = 0
  }

  // Only publish when the WebSocket actually reached open -- entry.connection
  // is set before the dial completes, so a transient first-dial failure must
  // not count as a successful activation (issue #92265).
  if (
    entry.wantOpen &&
    g.secondaries.get(key) === entry &&
    isOpen(entry.gateway) &&
    applyActive(key, activationEpoch) &&
    entry.connection
  ) {
    publishActiveConnection(entry.connection)
  }
}

// Reconnect the active gateway after a transient request failure. Primary
// reconnects are owned by use-gateway-boot, so we only drive secondaries here.
// A scope parked on a rejected session stays parked for automatic request
// retries; only a user gesture (`explicit`: the Reconnect action) may redial it.
export async function ensureActiveGatewayOpen({
  explicit = false
}: { explicit?: boolean } = {}): Promise<HermesGateway | null> {
  if (g.activeKey === g.primaryProfile) {
    return g.primaryGateway
  }

  const entry = g.secondaries.get(g.activeKey)

  if (!entry || (!explicit && g.reauthFailures.has(entry.scope))) {
    return null
  }

  if (!isOpen(entry.gateway)) {
    // The viewed scope is a recovery target: a stall-parked entry must dial
    // again here, not stay parked. A reauth-parked one only reaches this line
    // via `explicit`; the foreground rearm clears its rejection.
    rearmSecondary(entry)
    await reconnectSecondary(entry)
  }

  if (!isOpen(entry.gateway)) {
    // A remote/registry secondary can still be ACTIVATING (backend waking,
    // socket dialing). Failing instantly turned a routine cold start into
    // "Hermes gateway is not connected" on the Sessions `+` action (#88880).
    // Wait a bounded beat for the in-flight activation instead of erroring;
    // a genuinely dead gateway still returns null when the window closes.
    const deadline = Date.now() + ACTIVE_GATEWAY_OPEN_WAIT_MS

    while (Date.now() < deadline && entry.wantOpen && g.secondaries.get(g.activeKey) === entry) {
      if (isOpen(entry.gateway)) {
        break
      }

      await new Promise(resolve => setTimeout(resolve, 250))
    }
  }

  return isOpen(entry.gateway) ? entry.gateway : null
}

// How long ensureActiveGatewayOpen waits out an in-flight secondary
// activation before reporting the gateway as unavailable.
const ACTIVE_GATEWAY_OPEN_WAIT_MS = 8_000

// Grace period before the live-work pruner may dispose a freshly opened
// secondary socket; see the min-lifetime guard in pruneSecondaryGateways
// (#94769 prune ↔ redial race). Exported for the tests that age a socket past it.
export const SECONDARY_MIN_LIFETIME_MS = 30_000

// A deferred liveness re-probe for one entry: cleared when the probe is
// answered, the socket is redialed (fresh streak), or the entry is disposed.
export function clearSecondaryLivenessReprobe(entry: Secondary): void {
  if (entry.livenessReprobeTimer !== null) {
    clearTimeout(entry.livenessReprobeTimer)
    entry.livenessReprobeTimer = null
  }
}

// Recovery signal: nudge every live secondary back open. Power-resume/network
// signals can force sockets that still report open to retire before redialing.
export function reconnectSecondaryGateways({ forceOpenSockets = false }: { forceOpenSockets?: boolean } = {}): void {
  for (const entry of g.secondaries.values()) {
    // A backend main retired for a foreground open stays parked: redialing it
    // from a focus/wake nudge would queue a background spawn for the slot the
    // retirement just freed. Its tile still shows; the next click re-arms it.
    if (entry.retiredByPool || g.reauthFailures.has(entry.scope)) {
      continue
    }

    // A parked entry (stall budget spent) is still pinned by its surface, or
    // the pruner would have removed it. This nudge is an explicit recovery
    // signal (online / focus / wake), so it re-arms with a fresh budget.
    rearmSecondary(entry)

    if (isOpen(entry.gateway)) {
      if (!forceOpenSockets) {
        continue
      }

      // A forced wake (power resume / network online) used to close EVERY open
      // secondary socket before redialing. Closing one that is mid-use detaches
      // its runtime → the backend orphan-reaps it → `session.reclaimed` → the
      // surface re-resumes on a fresh socket the same signal may close again:
      // the #94769 flicker loop. But a live socket also cannot simply be
      // SKIPPED: a half-open socket never fires a close event, so an in-flight
      // request would hang until its per-call timeout. Probe liveness instead —
      // a healthy-but-busy backend answers and keeps its socket; a dead
      // transport is closed and healed by the ordinary reconnect backoff.
      if (entry.activeRequests > 0 || relayRetained(entry) || foregroundPinned(entry)) {
        probeSecondaryLiveness(entry)

        continue
      }

      entry.gateway.close()
    }

    entry.reconnectAttempt = 0
    clearTimer(entry)
    void reconnectSecondary(entry)
  }
}

// How many non-primary backends currently hold an open socket. Hover-intent
// prewarming consults this before spawning: a speculative spawn that pushes
// the pool past its cap causes the Electron main to LRU-evict a warm backend
// — often one the user is about to click — turning the prewarm into churn
// (the #91545 evict/respawn cascade). The active gateway's backend is
// primary-routed and never counts toward the pool cap.
export function openSecondaryCount(): number {
  let count = 0

  for (const entry of g.secondaries.values()) {
    if (isOpen(entry.gateway)) {
      count += 1
    }
  }

  return count
}

// Keep the idle reaper from killing a backend we still need: ping every live
// secondary. The active one is pinged separately (touchActiveGatewayBackend).
// "Live" means the socket is OPEN: a wantOpen entry stuck in its reconnect
// backoff has no consumer on that backend, and pinging it anyway kept a
// tile-pinned backend keepalive-fresh forever, so LRU eviction and the idle
// reaper never freed its pool slot (#103375). Each ping also carries whether a
// prompt turn leases the scope, so a foreground dial that must retire a
// resident can skip leased ones early (the backend probe stays the proof).
export function touchSecondaryGateways(): void {
  // Older Desktop hosts own pooled children. Canonical hosts expose no touch
  // capability: their gateway lifetime is independent of renderer keepalives.
  const desktop = window.hermesDesktop as typeof window.hermesDesktop & {
    touchBackend?: (scope: string, options?: { activeTurn?: boolean }) => Promise<unknown>
  }

  for (const entry of g.secondaries.values()) {
    if (entry.wantOpen && isOpen(entry.gateway)) {
      void desktop?.touchBackend?.(entry.scope, { activeTurn: scopeHasTurnLease(entry.scope) }).catch(() => undefined)
    }
  }
}

// A local child is pooled under the bare profile (legacy route) or
// `conn:local::<profile>`; both renderer scopes ride the same child, so a
// retirement of either key parks both.
function secondaryRidesPoolKey(entry: Secondary, poolKey: string): boolean {
  if (entry.scope === poolKey) {
    return true
  }

  const local = !entry.connectionId || entry.connectionId === 'local'
  const profile = normKey(entry.profile)

  return local && (profile === poolKey || `conn:local::${profile}` === poolKey)
}

// Main is retiring the pooled backend under `poolKey` for a foreground open
// (electron/pool-retire.ts). Park every scope riding it BEFORE the socket
// drops: the 'closed' state must not scheduleReconnect, and the focus/wake
// nudge must not re-arm it either — both would queue a background redial for
// the slot the retirement freed. The entry stays (bot tiles keep their card);
// the next explicit open of the scope re-arms it. Returns the parked scopes.
export function parkSecondariesForRetiredBackend(poolKey: string): string[] {
  const key = String(poolKey || '').trim()
  const parked: string[] = []

  if (!key) {
    return parked
  }

  for (const entry of g.secondaries.values()) {
    if (!secondaryRidesPoolKey(entry, key)) {
      continue
    }

    entry.wantOpen = false
    entry.retiredByPool = true
    entry.stalledDials = 0
    clearTimer(entry)
    parked.push(entry.scope)
  }

  return parked
}

// Tear a secondary down: stop its reconnect loop, detach listeners, close the
// socket. Caller handles removal from the map.
export function disposeSecondary(entry: Secondary): void {
  entry.wantOpen = false
  clearTimer(entry)
  clearSecondaryLivenessReprobe(entry)
  entry.offEvent()
  entry.offRequest()
  entry.offState()
  entry.gateway.close()
}

// Invariant restore for every eviction path: if the active key names a
// secondary that no longer exists, fall back to the primary EXPLICITLY (atoms
// and composer state follow) instead of leaving a dangling key that
// activeGateway() can no longer resolve. Without this, a soft gateway switch
// (closeSecondaryGateways in use-gateway-boot) left activeKey pointing at an
// evicted registry scope and every call silently hit the primary backend.
export function restoreActiveToPrimaryIfEvicted(): void {
  if (
    g.activeKey !== g.primaryProfile &&
    !g.secondaries.has(g.activeKey) &&
    // A redial evicts the entry and re-activates the same scope moments later; taking the
    // activation in between cancels it through the epoch. The redial's own finally always clears
    // this, so a failed redial still falls back to the primary.
    !reactivatingScopes().has(g.activeKey)
  ) {
    setActive(g.primaryProfile)
  }
}

// Close + evict secondaries whose scope is neither active nor in `keep`
// (scopes with a running / needs-input session). Bounds cost to live work.
// `keep` carries PROFILE names for local/legacy entries and composite
// registryBackendScopeKey(connectionId, profile) scopes for registry-sourced live
// work. A registry-scoped entry matches ONLY on its composite key: every
// source exposes a 'default' profile, so matching a non-local entry on the
// bare profile name kept gateway B's 'default' socket alive off gateway A's
// 'default' activity (and vice versa) — cross-connection attribution.
//
// Live work is not the only thing worth a socket: an idle tile still holds a
// resumed runtime on its owner's socket, and closing that socket makes the
// backend detach and orphan-reap the runtime, whose `session.reclaimed`
// unbinds the tile and re-resumes it on a fresh socket that the next
// recompute closes again — a spinner loop with no terminal state (#93892).
// Foreground-bound scopes come from the registry's `foregroundScopes` hook
// (foregroundPinned), not from `keep`, so every dispose path sees the same
// pin. `entry.retained` is deliberately NOT consulted here (see the field's
// doc).
export function pruneSecondaryGateways(keep: Set<string>): void {
  const now = Date.now()

  for (const [key, entry] of [...g.secondaries]) {
    if (drainPendingConnectionRedial(entry)) {
      continue
    }

    if (
      key === g.activeKey ||
      keep.has(key) ||
      (!entry.connectionId && keep.has(entry.profile)) ||
      // Bot-relay retention (#93594): the relay pins its remote routes for
      // its whole active lifetime; the live-work pruner must not undo that
      // pin between drain ticks or the socket churn returns.
      relayRetained(entry) ||
      // A mounted tile / the primary thread is bound to a runtime on this
      // socket (#93892) — pinned for as long as that surface is mounted.
      foregroundPinned(entry) ||
      // Mid-dial activation target: the profile being switched TO is not yet
      // active and has no live work, so without this lease any recompute
      // during its cold spawn disposed the entry and the click died silently
      // (#89622). Number guard: dev-HMR entries predate the field. Bounded:
      // an orphaned lease expires on its own.
      (Number.isFinite(entry.activationLeaseUntil) && entry.activationLeaseUntil > now)
    ) {
      continue
    }

    // Min-lifetime grace: an idle prune can race an on-demand dial (prune →
    // redial → prune) and dispose a socket that opened moments ago, before
    // its consumer registered in the keep-set — closing it detaches the
    // runtime, the backend orphan-reaps it, and the reclaimed surface
    // re-resumes on a fresh socket the next recompute closes again: the
    // #94769 flicker loop. A young socket rides one prune tick; the keepalive
    // tick recomputes the keep-set so an idle one is still reaped within a minute.
    if (now - entry.lastOpenedAt < SECONDARY_MIN_LIFETIME_MS) {
      continue
    }

    // The route is no longer live work. Release turn leases first so their
    // counted request holds cannot outlive a disposed route or leave a stale
    // release closure attached to a later same-key socket.
    releaseTurnLeasesForScope(key)

    if (g.secondaries.get(key) !== entry) {
      continue
    }

    if (entry.activeRequests > 0) {
      continue
    }

    disposeSecondary(entry)
    g.secondaries.delete(key)
  }

  restoreActiveToPrimaryIfEvicted()
}

function closeSecondariesWhere(shouldClose: (entry: Secondary) => boolean): void {
  for (const [scope, entry] of [...g.secondaries]) {
    if (!shouldClose(entry)) {
      continue
    }

    disposeSecondary(entry)
    g.secondaries.delete(scope)
  }

  restoreActiveToPrimaryIfEvicted()
}

function isLegacySecondary(entry: Secondary): boolean {
  // Every v2 registry route is created with an explicit connection id,
  // including the registry's `local` source. A missing id is reserved for the
  // old profile-only pool; the loose null check also retires HMR entries from
  // builds that predate the field instead of leaving an old legacy socket
  // behind during a mode apply.
  return entry.connectionId == null
}

/**
 * Close only profile sockets that follow the legacy v1 connection config.
 *
 * A global mode apply re-homes the primary backend, but registered connection
 * sockets are independent sources in the v2 registry. Closing every secondary
 * here would detach their sessions and arm `ws_orphan_reap` even though those
 * sources remain valid and reusable. Legacy profile sockets still need to be
 * retired because their endpoint is derived from the v1 config being changed.
 */
export function closeLegacySecondaryGateways(): void {
  for (const [scope, failure] of g.reauthFailures) {
    if (failure.connectionId === null) {
      g.reauthFailures.delete(scope)
    }
  }

  closeSecondariesWhere(isLegacySecondary)
}

export function closeSecondaryGateways(): void {
  // Full teardown releases every routed-turn lease (class-2 #94284) and the
  // renderer-generation ledger; the predicate close leaves live sources'
  // leases alone (their sockets stay open).
  for (const timer of g.turnLeaseReleaseTimers.values()) {
    clearTimeout(timer)
  }

  g.turnLeaseReleaseTimers.clear()

  for (const release of [...g.turnLeases.values()]) {
    release()
  }

  g.turnLeases.clear()

  closeSecondariesWhere(() => true)
  openedSecondaryScopes().clear()
  g.reauthFailures.clear()
}

// A local profile can have two renderer-owned sockets: the legacy bare
// profile scope and the explicit `local` registry scope. Profile deletion
// stops their Electron backend processes, but a retained Secondary otherwise
// sees that shutdown as a transient disconnect and starts its reconnect loop,
// resurrecting the backend that was just deleted. Retire both local scopes
// before the DELETE request while preserving same-named agents on remote,
// cloud, or SSH connections.
export function retireLocalProfileGateways(profile: string): void {
  const name = String(profile || '').trim()

  if (!name) {
    return
  }

  const key = normKey(name)
  const scopes = new Set([key, registryBackendScopeKey('local', key)])
  let activeInvalidated = false

  // A profile-only owner is a claim about the legacy local pool, not durable
  // session identity. Clear it with that pool before a delayed session action
  // can recreate the deleted/old-name backend. Exact remote owners are
  // descriptors and remain routable even when they share this profile name.
  g.config?.onLocalProfileRetired?.(key)

  for (const scope of scopes) {
    const entry = g.secondaries.get(scope)

    if (!entry) {
      continue
    }

    activeInvalidated ||= scope === g.activeKey
    disposeSecondary(entry)
    g.secondaries.delete(scope)
  }

  restoreActiveToPrimaryIfEvicted()

  if (activeInvalidated) {
    g.config?.onActiveConnectionInvalidated?.(g.primaryProfile, gatewayActivationEpoch())
  }
}

// Registry lifecycle: a connection was removed or materially edited. Removal
// disposes every scoped secondary immediately (a removed remote/cloud source
// has no local process to die, so otherwise its WebSocket streams ghost
// events). A material edit redials each profile through the normal open path so
// fresh sockets target the NEW endpoint, but request/relay leases and mounted
// foreground runtimes keep their old socket until they drain; the active scope
// re-activates when its replacement is safe to publish.
export function disposeSecondariesForConnection(connectionId: string, opts: { redial?: boolean } = {}): void {
  const id = String(connectionId || '').trim()
  let activeInvalidated = false

  if (!id) {
    return
  }

  // Invalidate first, even when a retained request keeps the old socket alive
  // to settle. A remove/edit/re-add ABA of the same id cannot revive a lease.
  advanceConnectionRouteGeneration(id)

  for (const [scope, failure] of g.reauthFailures) {
    if (failure.connectionId === id) {
      g.reauthFailures.delete(scope)
    }
  }

  for (const [key, entry] of [...g.secondaries]) {
    if (entry.connectionId !== id) {
      continue
    }

    const wasActive = key === g.activeKey
    activeInvalidated ||= wasActive

    if (opts.redial && (entry.activeRequests > 0 || relayRetained(entry) || foregroundPinned(entry))) {
      entry.pendingConnectionRedial = true

      continue
    }

    disposeSecondary(entry)
    g.secondaries.delete(key)

    if (opts.redial) {
      reopenAfterRedial(entry, wasActive)
    }
  }

  if (activeInvalidated && !opts.redial) {
    setActive(g.primaryProfile)
    g.config?.onActiveConnectionInvalidated?.(g.primaryProfile, gatewayActivationEpoch())
  }
}

// Self-accept so editing this module (or a fan-out that lands here) is an
// in-place hot update instead of a full page reload — the live sockets in `g`
// survive the swap. Dev-only: production strips import.meta.hot.
if (import.meta.hot) {
  import.meta.hot.accept()
}

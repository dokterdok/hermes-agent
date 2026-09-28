import {
  isGatewayReauthRequired,
  isStableOpen,
  reconnectBackoffDelayMs,
  registryBackendScopeKey,
  resolveGatewayWsUrl
} from '@hermes/shared'

import { HermesGateway } from '@/hermes'
import { translateNow } from '@/i18n'
import {
  decideLivenessForceClose,
  LIVENESS_PROBE_TIMEOUT_MS,
  LIVENESS_REPROBE_DELAY_MS
} from '@/lib/gateway-liveness-policy'
import { isMissingRpcMethod } from '@/lib/gateway-rpc'
import { isTimeoutError, RECONNECT_ATTEMPT_TIMEOUT_MS, withTimeout } from '@/lib/with-timeout'
import { notifyError, RECOVERY_ACTIONS } from '@/store/notifications'
import { stampSecondaryProfileOwner } from '@/store/session-event-provenance'

import {
  type AttachedRemoteProbe,
  clearSecondaryLivenessReprobe,
  clearTimer,
  dispatchServerRequest,
  disposeSecondary,
  foregroundPinned,
  g,
  isOpen,
  isPrimaryRegistryRoute,
  normKey,
  openedSecondaryScopes,
  publishActiveConnection,
  relayRetained,
  releaseTerminalTurnLease,
  releaseTurnLeasesForScope,
  reportGatewayState,
  restoreActiveToPrimaryIfEvicted,
  type Secondary,
  SECONDARY_STALLED_DIAL_BUDGET,
  type SpawnPriority
} from './gateway'

/** True when `connectionId` is the window's already-attached source AND main
 *  says `profile` rides the backend that source's primary socket is already
 *  connected to — a one-host-many-profiles remote (`sharedRemote`, #96493) or
 *  the one local host backend that serves every local profile under
 *  multiplex-only (`sharedPrimary`, #118246). Either way a registry secondary
 *  would be a SECOND WebSocket to the SAME process: on a remote it accept/closes
 *  in ~30ms and never runs `session.create`; on the local host backend it joins
 *  the chat's transport fan-out and the renderer receives every event twice
 *  (garbled streaming text + a duplicate interim bubble, #120005). Isolated
 *  SSH/pooled backends (neither flag) still get their own secondary. */
export async function attachedRemoteProbe(
  connectionId: null | string,
  profile: string,
  signal?: AbortSignal,
  spawnPriority: SpawnPriority = 'background'
): Promise<AttachedRemoteProbe | null> {
  signal?.throwIfAborted()
  const id = String(connectionId ?? '').trim()
  const key = normKey(profile)
  const parked = g.secondaries.get(registryBackendScopeKey(connectionId, key))

  if (parked?.retiredByPool) {
    rearmSecondary(parked, spawnPriority)
  }

  if (!id || !g.primaryConnectionId || id !== g.primaryConnectionId || isPrimaryRegistryRoute(id, key)) {
    return null
  }

  const desktop = window.hermesDesktop

  if (!desktop?.getConnectionFor) {
    return null
  }

  const gateway = g.primaryGateway
  const primaryProfile = g.primaryProfile
  const generation = g.primaryOwnerGeneration ?? 0

  const assertCurrent = () => {
    signal?.throwIfAborted()

    if (
      gateway !== g.primaryGateway ||
      id !== g.primaryConnectionId ||
      primaryProfile !== g.primaryProfile ||
      generation !== (g.primaryOwnerGeneration ?? 0)
    ) {
      throw new Error('Hermes gateway connection owner changed')
    }
  }

  let sharedRemote = true

  // Resolved per call, never cached: main answers the route per request
  // (`resolveProfileBackendRoute` case 6 keeps a pooled backend for
  // `HERMES_DESKTOP_ISOLATED_BACKEND=1`), and for a pooled profile this is the
  // same dial `openSecondary` makes next, coalesced by main's claim key.
  try {
    const conn = await withTimeout(
      desktop.getConnectionFor({ connectionId: id, profile: key }),
      RECONNECT_ATTEMPT_TIMEOUT_MS,
      `Timed out resolving the backend route for "${key}"`
    )

    const flags =
      conn && typeof conn === 'object' ? (conn as { sharedPrimary?: boolean; sharedRemote?: boolean }) : null

    sharedRemote = flags?.sharedRemote === true || flags?.sharedPrimary === true
  } catch {
    // Probe failed on a remote (or not-yet-classified) primary: a secondary at
    // this already-attached source is the #96493 ghost WebSocket (accept/close,
    // messages=1), so prefer the primary until a later probe can prove
    // isolation (`sharedRemote: false`). A LOCAL primary must NOT get that
    // fallback: when main routes the profile to a pooled child (isolated
    // backend), the primary would still ACCEPT a `profile`-tagged
    // session.create (profile_home multiplexing) and mint the session under
    // its own pid, but the exact-owner route names the pool backend — after a
    // renderer reload or a pool respawn the resume dials that backend and is
    // refused SESSION_NOT_OWNED by a pid of the same Desktop (#101416).
    sharedRemote = g.primaryConnectionMode !== 'local'
  }

  // A stale probe is NOT a false descriptor (nor a failed-probe fallback).
  assertCurrent()

  return { gateway, sharedRemote, assertCurrent }
}

export async function requestOnPrimaryGateway<T>(
  owner: AttachedRemoteProbe,
  method: string,
  params: Record<string, unknown>,
  timeoutMs?: number,
  signal?: AbortSignal
): Promise<T> {
  owner.assertCurrent()
  const gateway = owner.gateway

  if (!gateway || !isOpen(gateway)) {
    throw new Error('Hermes gateway unavailable')
  }

  return timeoutMs === undefined && signal === undefined
    ? gateway.request<T>(method, params)
    : gateway.request<T>(method, params, timeoutMs, signal)
}

export async function openSecondary(entry: Secondary): Promise<void> {
  const desktop = window.hermesDesktop

  const reauthError = g.reauthFailures.get(entry.scope)?.error

  if (reauthError) {
    throw reauthError
  }

  if (!desktop) {
    return
  }

  if (entry.connectPromise) {
    await entry.connectPromise

    return
  }

  const pending = (async () => {
    entry.ownerGeneration = Number.isFinite(entry.ownerGeneration) ? entry.ownerGeneration + 1 : 1
    // A secondary can be reopened directly by the next routed user action,
    // without passing through reconnectSecondary(). Its previous backend may
    // have been respawned, so every stored→runtime binding for this exact scope
    // is process-local stale state. Invalidate BEFORE connect publishes `open`:
    // otherwise an eager route effect / submit can send the old runtime id in
    // the narrow window between the new socket opening and post-connect cleanup.
    //
    // Dynamic import keeps the existing session-states → gateway module cycle
    // open. Awaiting it is intentional: correctness at the generation boundary
    // outranks the single local-module microtask this adds to a reconnect.
    const openedScopes = openedSecondaryScopes()
    const reopening = entry.lastOpenedAt > 0 || entry.connection !== null || openedScopes.has(entry.scope)
    let reconcileBusyAfterOpen: null | (() => void) = null

    if (reopening) {
      try {
        const { reconcileBusyStatesOnReconnect, resetRouteOwnedTileRuntimeBindings, resetTileRuntimeBindings } =
          await import('@/store/session-states')

        const scope = { connectionId: entry.connectionId || 'local', profile: entry.profile }

        // Only the window's ambient gateway carries un-owned tiles and the main
        // thread. A background route (e.g. a relay request lease that disposed
        // its socket after the last tick) can only have minted runtimes for
        // tiles that name it as their owner route.
        if (g.activeKey === entry.scope) {
          resetTileRuntimeBindings(scope)
        } else {
          resetRouteOwnedTileRuntimeBindings(scope)
        }

        reconcileBusyAfterOpen = () => reconcileBusyStatesOnReconnect(entry.scope)
      } catch {
        // Best effort for partial test/HMR graphs. Production always loads the
        // real store; a failed import must not make the transport unrecoverable.
      }
    }

    // Registry-scoped entries dial through getConnectionFor when the bridge has
    // it. Local/legacy entries retain the existing getConnection path. Both are
    // IPC round-trips into the main process with no timeout of their own
    // (#93454) — a wedged main-process round-trip otherwise hangs this await
    // forever, latching entry.connectPromise so every routed action against
    // this secondary (SSH terminal, messaging DELETE, session send, …) never
    // settles either. Bound the same way use-gateway-boot.ts bounds the
    // primary's equivalent awaits.
    const conn =
      entry.connectionId && desktop.getConnectionFor
        ? await withTimeout(
            desktop.getConnectionFor({ connectionId: entry.connectionId, profile: entry.profile }),
            RECONNECT_ATTEMPT_TIMEOUT_MS,
            `Timed out connecting to profile "${entry.profile}"`
          )
        : await withTimeout(
            desktop.getConnection(entry.profile),
            RECONNECT_ATTEMPT_TIMEOUT_MS,
            `Timed out connecting to profile "${entry.profile}"`
          )

    entry.connection = conn

    const wsDeps =
      entry.connectionId && desktop.getGatewayWsUrlFor
        ? {
            getGatewayWsUrl: () =>
              desktop.getGatewayWsUrlFor!({ connectionId: entry.connectionId, profile: entry.profile })
          }
        : entry.connectionId
          ? {}
          : desktop

    const wsUrl = await withTimeout(
      resolveGatewayWsUrl(wsDeps, conn),
      RECONNECT_ATTEMPT_TIMEOUT_MS,
      `Timed out re-minting the gateway WebSocket URL for profile "${entry.profile}"`
    )

    try {
      await entry.gateway.connect(wsUrl)
    } catch (error) {
      // Log the dial target for support, but RETHROW THE ORIGINAL ERROR —
      // reconnectSecondary classifies failures by message ("No connection
      // with id", "no longer exists") to fail-stop permanent conditions, and
      // wrapping here would break that. Callers decide surfacing (#81094).
      console.error(`[gateway] dial failed for scope="${entry.scope}" profile="${entry.profile}":`, error)
      throw error
    }

    entry.lastOpenedAt = Date.now()
    // A fresh socket owes nothing to a previous socket's missed pings.
    entry.livenessProbeFailures = 0
    clearSecondaryLivenessReprobe(entry)
    openedScopes.add(entry.scope)

    try {
      reconcileBusyAfterOpen?.()
    } catch {
      // The socket is already open. A best-effort UI-state reconcile must not
      // turn that successful transport recovery into a reported dial failure.
    }

    if (!entry.wantOpen) {
      entry.gateway.close()

      return
    }

    if (g.activeKey === entry.scope) {
      publishActiveConnection(conn)
    }
  })()

  entry.connectPromise = pending

  try {
    await pending
  } catch (error) {
    if (isGatewayReauthRequired(error) && g.secondaries.get(entry.scope) === entry) {
      g.reauthFailures.set(entry.scope, { connectionId: entry.connectionId, error })
      entry.wantOpen = false
      clearTimer(entry)
    }

    throw error
  } finally {
    if (entry.connectPromise === pending) {
      entry.connectPromise = null
    }
  }
}

function isStalledDialError(error: unknown): boolean {
  if (isTimeoutError(error)) {
    return true
  }

  const message = error instanceof Error ? error.message : String(error ?? '')

  return message.includes('timed out while waiting for a free slot')
}

export function rearmSecondary(entry: Secondary, priority: SpawnPriority = 'foreground'): void {
  const reauthError = g.reauthFailures.get(entry.scope)?.error

  if (reauthError && priority !== 'foreground') {
    throw reauthError
  }

  g.reauthFailures.delete(entry.scope)

  if (entry.retiredByPool && priority !== 'foreground') {
    throw new Error(`Backend for "${entry.profile}" was retired; open it explicitly to reconnect.`)
  }

  entry.wantOpen = true
  entry.stalledDials = 0
  entry.retiredByPool = false
}

export function scheduleReconnect(entry: Secondary): void {
  if (entry.reconnecting || entry.reconnectTimer !== null || !entry.wantOpen) {
    return
  }

  // Full-jitter exponential backoff — same shape (and same reason: avoid a
  // reconnect storm against a restarting gateway) as the primary's.
  const delay = reconnectBackoffDelayMs(entry.reconnectAttempt)
  entry.reconnectAttempt += 1
  entry.reconnectTimer = setTimeout(() => {
    entry.reconnectTimer = null
    void reconnectSecondary(entry)
  }, delay)
}

export async function reconnectSecondary(entry: Secondary): Promise<void> {
  if (entry.reconnecting || !entry.wantOpen || isOpen(entry.gateway)) {
    return
  }

  entry.reconnecting = true

  try {
    await openSecondary(entry)
  } catch (error) {
    if (isGatewayReauthRequired(error)) {
      notifyError(error, translateNow('boot.errors.gatewaySignInRequired'), { action: RECOVERY_ACTIONS.openGateways() })

      return
    }

    // The registry no longer knows this connection (removed while we were
    // backing off), or Electron's deletion guard reports the profile itself
    // gone/mid-delete. Both are permanent for this scoped socket — retrying
    // forever can never succeed and hammers the spawn guard every backoff
    // tick (#88769). Fail-stop: dispose the entry and evict it instead of an
    // infinite 15s-cap retry loop.
    if ((entry.connectionId && isMissingConnectionError(error)) || isMissingProfileError(error)) {
      entry.reconnecting = false
      disposeSecondary(entry)

      if (g.secondaries.get(entry.scope) === entry) {
        g.secondaries.delete(entry.scope)
      }

      restoreActiveToPrimaryIfEvicted()

      return
    }

    // Only a successful open resets the stall budget (the 'open' state
    // listener): a treadmill that alternates slot-wait timeouts with a
    // spawned-but-unresponsive socket must still run out of budget.
    if (isStalledDialError(error)) {
      entry.stalledDials += 1

      if (entry.stalledDials >= SECONDARY_STALLED_DIAL_BUDGET) {
        console.warn(
          `[gateway] parking scope="${entry.scope}" after ${entry.stalledDials} stalled dials; the next open or wake nudge redials it`
        )
        entry.wantOpen = false
        entry.stalledDials = 0
      }
    }
    // Still wantOpen → fall through to the backoff below.
  } finally {
    entry.reconnecting = false

    if (entry.wantOpen && !isOpen(entry.gateway)) {
      scheduleReconnect(entry)
    }
  }
}

// Electron's getConnectionFor rejects with `No connection with id "…"` when
// the registry entry is gone. That is a permanent condition for the scoped
// socket, unlike transient transport errors.
function isMissingConnectionError(error: unknown): boolean {
  const message = error instanceof Error ? error.message : String(error ?? '')

  return message.includes('No connection with id')
}

// Electron's spawn guard (assertLocalProfileCanStart) rejects with these when
// the profile's directory is gone or its DELETE is still in flight. For a
// renderer socket that condition is permanent: the backend it reconnects to
// can never come back, and every retry hammers the guard (#88769).
function isMissingProfileError(error: unknown): boolean {
  const message = error instanceof Error ? error.message : String(error ?? '')

  return message.includes('no longer exists') || message.includes('is being deleted')
}

export function createSecondary(profile: string, connectionId: null | string = null): Secondary {
  const gateway = new HermesGateway()
  const scope = registryBackendScopeKey(connectionId, profile)

  const entry: Secondary = {
    scope,
    profile,
    connectionId,
    connection: null,
    gateway,
    lastOpenedAt: 0,
    openedAt: null,
    activeRequests: 0,
    connectPromise: null,
    offEvent: () => {},
    offRequest: () => {},
    offState: () => {},
    reconnectTimer: null,
    reconnectAttempt: 0,
    livenessProbeFailures: 0,
    livenessReprobeTimer: null,
    stalledDials: 0,
    reconnecting: false,
    pendingConnectionRedial: false,
    ownerGeneration: 0,
    retained: false,
    relayRetainCount: 0,
    wantOpen: true,
    retiredByPool: false,
    activationLeaseUntil: 0
  }

  // Events keep carrying the bare profile — session routing is profile-keyed
  // everywhere. A pool secondary with no registry connection has no exact
  // connection id, so stamp this closure-owned profile before registry fan-in;
  // the recorder must not promote an arbitrary wire `profile` field instead.
  entry.offEvent = gateway.onEvent(event => {
    const scopedEvent = stampSecondaryProfileOwner({ ...event, ...(connectionId ? { connectionId } : {}) }, profile)

    g.config?.onEvent(scopedEvent)
    releaseTerminalTurnLease(entry.scope, event)
  })
  entry.offRequest = gateway.onRequest?.(request => dispatchServerRequest(request, profile, connectionId)) ?? (() => {})
  entry.offState = gateway.onState(state => {
    reportGatewayState(scope, state)

    if (state === 'open') {
      entry.stalledDials = 0
      entry.openedAt = Date.now()
      clearTimer(entry)
    } else if (state === 'closed' || state === 'error') {
      // Same stable-open rule as the primary (#83134): an accept-then-close socket
      // is a failed attempt, so the ladder resets only after a socket that lived.
      if (isStableOpen(entry.openedAt)) {
        entry.reconnectAttempt = 0
      }

      entry.openedAt = null

      // A dead socket cannot emit the terminal event that normally releases
      // its turn lease. Drop the orphaned lease before deciding whether this
      // route is still retained/active enough to reconnect.
      releaseTurnLeasesForScope(scope)

      if (entry.wantOpen) {
        scheduleReconnect(entry)
      }
    }
  })

  g.secondaries.set(scope, entry)

  return entry
}

// True when `profile`'s backend route resolves to the SHARED primary backend
// (global-remote case 3 in resolveProfileBackendRoute). Both shared-primary and
// pooled descriptors carry `profile` so WebSocket URL minting targets the right
// profile. `sharedPrimary` is the explicit discriminator; treating every tagged
// descriptor as shared strands local/own-remote pooled profiles on the default
// socket. Dialing a second socket at the shared descriptor is wrong — over SSH
// the second dial fails (tunnel/token are per-backend) and the closed socket
// poisons the active gateway with "not connected" even though the primary is
// open right next to it.
export async function sharedPrimaryRoute(profile: string): Promise<boolean> {
  const desktop = window.hermesDesktop

  if (!desktop) {
    return false
  }

  try {
    // Unbounded IPC round-trip into main (#93454) — a wedge here must reject
    // like any other failure, not hang the route decision forever, since
    // every caller (gatewayForProfile → requestGatewayForProfile/Agent) awaits
    // this before it can fall back to dialing a secondary.
    const conn = await withTimeout(
      desktop.getConnection(profile),
      RECONNECT_ATTEMPT_TIMEOUT_MS,
      `Timed out resolving the shared-primary route for profile "${profile}"`
    )

    return Boolean(conn && typeof conn === 'object' && (conn as { sharedPrimary?: boolean }).sharedPrimary === true)
  } catch {
    return false
  }
}

// Resolve and open `profile`'s socket WITHOUT changing the active gateway.
// Shared global-remote profiles intentionally return the primary socket plus a
// request-scope flag; dedicated local/remote profiles use their pooled socket.
export async function gatewayForProfile(
  profile: string,
  leaseRequest = false,
  spawnPriority: SpawnPriority = 'background'
): Promise<{ gateway: HermesGateway | null; key: string; release: () => void; scopeProfile: boolean }> {
  const key = normKey(profile)
  const noRelease = () => undefined
  const parked = g.secondaries.get(key)

  if (parked?.retiredByPool) {
    rearmSecondary(parked, spawnPriority)
  }

  if (key === g.primaryProfile) {
    return { gateway: g.primaryGateway, key, release: noRelease, scopeProfile: false }
  }

  if (await sharedPrimaryRoute(key)) {
    return { gateway: g.primaryGateway, key, release: noRelease, scopeProfile: true }
  }

  const entry = g.secondaries.get(key) ?? createSecondary(key)

  // Existing dev-HMR entries predate the request lease/ownership fields.
  if (!Number.isFinite(entry.activeRequests)) {
    entry.activeRequests = 0
  }

  if (typeof entry.retained !== 'boolean') {
    entry.retained = true
  }

  if (!leaseRequest) {
    entry.retained = true
  }

  rearmSecondary(entry, spawnPriority)

  if (leaseRequest) {
    entry.activeRequests += 1
  }

  let released = false

  const release = () => {
    if (!released && leaseRequest) {
      released = true
      entry.activeRequests = Math.max(0, entry.activeRequests - 1)

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
  }

  try {
    if (!isOpen(entry.gateway)) {
      await openSecondary(entry)
    }
  } catch (error) {
    release()
    throw error
  }

  return { gateway: entry.gateway, key, release, scopeProfile: false }
}

// Probe a live-in-use secondary instead of blind-closing it on a forced wake,
// and close it only when the probe proves it not alive. A half-open TCP
// connection (sleep/wake, silent network drop) reports connectionState
// 'open' forever and fires no close event, so without this close an in-flight
// request rides a dead transport until its per-call timeout — prompt.submit's
// is 30 minutes. Closing arms the entry's ordinary reconnect backoff via its
// onState('closed') handler; a healthy-but-busy backend answers the ping and
// keeps its socket (#94769 review).
export function probeSecondaryLiveness(entry: Secondary): void {
  void entry.gateway.request('ping', {}, LIVENESS_PROBE_TIMEOUT_MS).then(
    () => {
      entry.livenessProbeFailures = 0
      clearSecondaryLivenessReprobe(entry)
    },
    (error: unknown) => {
      // -32601 (method not found) = a version-skewed but HEALTHY backend that
      // predates the ping method — the same compatibility carve-out the
      // primary's probe makes in use-gateway-boot.
      if (isMissingRpcMethod(error)) {
        entry.livenessProbeFailures = 0
        clearSecondaryLivenessReprobe(entry)

        return
      }

      // The entry may have been pruned or redialed while the probe was
      // pending; only the very same socket may be torn down.
      if (g.secondaries.get(entry.scope) !== entry || !isOpen(entry.gateway)) {
        return
      }

      // ONE missed ping is not proof of death: a live backend mid tool call
      // can starve its event loop past the probe budget, and force-closing it
      // feeds the backend's ws_orphan_reap, interrupting the valid turn
      // (#94769 review). Apply the SAME streak policy as the primary's probe
      // (decideLivenessForceClose): defer the first failure while work is
      // in flight behind a bounded re-probe, close only when the streak is
      // exhausted — or immediately when nothing is in flight to protect.
      entry.livenessProbeFailures += 1

      // Counted RPCs alone under-report in-flight work: prompt.submit returns
      // before the turn ends, so a foreground turn mid tool call shows
      // activeRequests 0. The registry's live-scope hook supplies the turn.
      const liveScopes = g.config?.liveScopes?.()

      const turnInFlight =
        liveScopes && (liveScopes.has(entry.scope) || (!entry.connectionId && liveScopes.has(entry.profile))) ? 1 : 0

      const decision = decideLivenessForceClose({
        workingSessionCount: entry.activeRequests + turnInFlight,
        consecutiveFailures: entry.livenessProbeFailures
      })

      if (!decision.close) {
        if (entry.livenessReprobeTimer === null) {
          entry.livenessReprobeTimer = setTimeout(() => {
            entry.livenessReprobeTimer = null

            // The entry may have been pruned or redialed while the re-probe
            // waited; only the same open socket may be probed again.
            if (g.secondaries.get(entry.scope) !== entry || !isOpen(entry.gateway)) {
              return
            }

            probeSecondaryLiveness(entry)
          }, LIVENESS_REPROBE_DELAY_MS)
        }

        return
      }

      entry.livenessProbeFailures = 0
      clearSecondaryLivenessReprobe(entry)
      entry.gateway.close()
    }
  )
}

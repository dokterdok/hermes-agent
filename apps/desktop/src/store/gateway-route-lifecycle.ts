import { setApiRequestConnection } from '@/hermes'
import { setGatewayState } from '@/store/session'

import {
  activeGateway,
  activeGatewayConnectionId,
  disposeSecondary,
  ensureGatewayForAgent,
  foregroundPinned,
  g,
  gatewayActivationEpoch,
  normKey,
  openGatewayForAgent,
  reactivatingScopes,
  relayRetained,
  type Secondary
} from './gateway'

export function setActive(profile: string): void {
  const activationEpoch = beginGatewayActivation()
  applyActive(profile, activationEpoch)
}

export function beginGatewayActivation(): number {
  g.activationEpoch = gatewayActivationEpoch() + 1

  return g.activationEpoch
}

export function applyActive(profile: string, activationEpoch: number): boolean {
  if (gatewayActivationEpoch() !== activationEpoch) {
    return false
  }

  g.activeKey = normKey(profile)
  const gateway = activeGateway()
  g.$gateway.set(gateway)
  setGatewayState(gateway?.connectionState ?? 'closed')
  // Push the active scope's registry connection into the hermes module (null
  // for the local pool) so connection-building WS calls (pluginSocket) resolve
  // through the same source of truth every activation path maintains here —
  // registry-agent activations included, not just profile switches.
  setApiRequestConnection(activeGatewayConnectionId())

  // Publish the BARE profile this route serves, in the same synchronous step
  // as the socket selection. activeKey may be a composite registry scope
  // (connectionId::profile); consumers route RPCs by profile, so resolve it
  // through the secondary's own record. This atom is the single source of
  // truth for "which profile is the active gateway on" — every eviction /
  // fallback path funnels through applyActive, so the published profile can
  // never linger on a backend that is no longer selected (#89206).
  const routeProfile =
    g.activeKey === g.primaryProfile ? g.primaryProfile : (g.secondaries.get(g.activeKey)?.profile ?? g.primaryProfile)

  g.$activeProfile.set(routeProfile)
  g.config?.onActiveRouteChanged?.(routeProfile)

  return true
}

/**
 * Finish a material-edit redial once no request, relay, or foreground surface
 * still owns the old socket. Removal deliberately bypasses this drain: a
 * deleted source can never become valid again and must fail-stop immediately.
 */
export function drainPendingConnectionRedial(entry: Secondary): boolean {
  if (
    entry.pendingConnectionRedial !== true ||
    entry.activeRequests > 0 ||
    relayRetained(entry) ||
    foregroundPinned(entry) ||
    g.secondaries.get(entry.scope) !== entry
  ) {
    return false
  }

  entry.pendingConnectionRedial = false
  const wasActive = g.activeKey === entry.scope
  disposeSecondary(entry)
  g.secondaries.delete(entry.scope)
  reopenAfterRedial(entry, wasActive)

  return true
}

// Re-open a redialed scope after its entry left the map. An active scope's
// re-activation is asynchronous, and until it lands
// restoreActiveToPrimaryIfEvicted sees an active scope with no entry, calls
// setActive(primary) and bumps the activation epoch — which turns this redial's
// own applyActive(epoch) into a no-op. The window would then sit on the primary
// backend with the redial silently discarded, after nothing more than an edit
// to the connection being viewed. Mark the scope for the pruner while the
// re-activation is in flight; the finally clears it on both outcomes so a
// redial that never lands still falls back.
export function reopenAfterRedial(entry: Secondary, wasActive: boolean): void {
  if (!wasActive) {
    void openGatewayForAgent(entry.connectionId, entry.profile).catch(() => undefined)

    return
  }

  reactivatingScopes().add(entry.scope)
  void ensureGatewayForAgent(entry.connectionId, entry.profile)
    .catch(() => undefined)
    .finally(() => {
      reactivatingScopes().delete(entry.scope)
    })
}

export function cancelTurnLeaseRelease(key: string): void {
  const timer = g.turnLeaseReleaseTimers.get(key)

  if (timer !== undefined) {
    clearTimeout(timer)
    g.turnLeaseReleaseTimers.delete(key)
  }
}

export function scopeHasTurnLease(scope: string): boolean {
  const prefix = `${scope}\u0000`

  for (const key of g.turnLeases.keys()) {
    if (key.startsWith(prefix)) {
      return true
    }
  }

  return false
}

// Tell main whether a prompt turn leases this scope's pooled backend. An early
// skip for cooperative retirement (electron/pool-retire.ts), never the proof:
// main asks the backend itself before stopping anything. From #104871.
export function publishTurnLease(scope: string, activeTurn: boolean): void {
  // Older Desktop hosts own pooled children. Canonical hosts expose no touch
  // capability: their gateway lifetime is independent of renderer leases.
  const desktop = window.hermesDesktop as typeof window.hermesDesktop & {
    touchBackend?: (scope: string, options?: { activeTurn?: boolean }) => Promise<unknown>
  }

  void desktop?.touchBackend?.(scope, { activeTurn }).catch(() => undefined)
}

import { $workspaceMode, $workspaceOwnerKey } from '@/components/pane-shell/workspace-scope'
import { $activeGatewayProfile, $hydrationSyncProfile, normalizeProfileKey } from '@/store/profile'
import { $activeSessionId, $messages, $selectedStoredSessionId } from '@/store/session'
import {
  $focusedRuntimeId,
  $focusedSessionState,
  $focusedStoredSessionId,
  $sessionStates,
  $sessionTiles
} from '@/store/session-states'

import { $activeConnectionId, HYDRATION_SYNC_BADGE_TIMEOUT_MS, openSessionGeneration } from './index'

// Raise the "Syncing…" affordance for a paint-first wake (#89843) and tear it
// down as soon as the active-profile gate catches up. The listener clears ONLY
// its own profile's badge: a newer wake may have replaced the badge with a
// different profile, and the stale listener must not wipe the winner's.
//
// The gate is not guaranteed to fire. `.listen()` is change-only, and a wake
// is routed here precisely because $activeGatewayProfile did not match at
// resolve time. ensureGatewayProfile does publish the target on a
// shared-primary connection, but when the activation did NOT land it publishes
// the route the registry actually settled on instead — so on that path the
// atom may never become this profile and the listener never fires. Without a
// cap the badge would outlive the wake it describes and strand a permanent
// "Syncing <profile>…" spinner with no user-reachable dismissal; only a full
// app restart would clear it. Cap the wait so the badge can never outlive the
// work.
function beginHydrationBackgroundSync(profile: string): void {
  $hydrationSyncProfile.set(profile)

  let timer: number | undefined

  const clearOwnBadge = (): void => {
    if ($hydrationSyncProfile.get() === profile) {
      $hydrationSyncProfile.set(null)
    }
  }

  const unlisten = $activeGatewayProfile.listen(next => {
    if (normalizeProfileKey(next) === profile) {
      clearOwnBadge()

      if (timer !== undefined) {
        window.clearTimeout(timer)
      }

      unlisten()
    }
  })

  timer = window.setTimeout(() => {
    clearOwnBadge()
    unlisten()
  }, HYDRATION_SYNC_BADGE_TIMEOUT_MS)
}

export function waitForFocusedSessionHydration({
  expectHistory,
  generation,
  isCurrent,
  profile,
  requireActiveProfile,
  storedSessionId,
  timeoutMs
}: {
  expectHistory: boolean
  generation: number
  isCurrent?: () => boolean
  profile: string
  requireActiveProfile: boolean
  storedSessionId: string
  timeoutMs: number
}): Promise<void> {
  return new Promise((resolve, reject) => {
    let settled = false
    const unbinds: Array<() => void> = []
    let timer: number | undefined

    const finish = (error?: Error) => {
      if (settled) {
        return
      }

      settled = true

      if (timer !== undefined) {
        window.clearTimeout(timer)
      }

      for (const unbind of unbinds) {
        unbind()
      }

      if (error) {
        reject(error)
      } else {
        resolve()
      }
    }

    const check = () => {
      if (generation !== openSessionGeneration || (isCurrent && !isCurrent())) {
        finish(new Error('Session open was superseded by a newer selection.'))

        return
      }

      const profileMatches = !requireActiveProfile || normalizeProfileKey($activeGatewayProfile.get()) === profile
      const mainMatches = $selectedStoredSessionId.get() === storedSessionId
      const storedTile = $sessionTiles.get().find(tile => tile.storedSessionId === storedSessionId)
      const tileMatches = $focusedStoredSessionId.get() === storedSessionId || Boolean(storedTile)
      const focusedTileMatches = $focusedStoredSessionId.get() === storedSessionId
      const tileRuntimeId = focusedTileMatches ? $focusedRuntimeId.get() : (storedTile?.runtimeId ?? null)

      const tileState = focusedTileMatches
        ? $focusedSessionState.get()
        : tileRuntimeId
          ? $sessionStates.get()[tileRuntimeId]
          : undefined

      const runtimeReady = mainMatches ? Boolean($activeSessionId.get()) : tileMatches ? Boolean(tileRuntimeId) : false

      const historyPainted = mainMatches
        ? Boolean($messages.get().length)
        : tileMatches
          ? Boolean(tileState?.messages.length)
          : false

      // Paint-first hydration: for a history-bearing chat, the wake is DONE
      // the moment the persisted transcript is painted on the right session —
      // the REST prefetch delivers it seconds after the profile backend's
      // HTTP comes up, while the full runtime resume (agent build, MCP
      // discovery, skill load) keeps warming in the background and binds the
      // composer when it lands. Gating on runtimeReady serialized the wake
      // behind that whole boot: on a cold multi-profile start the 20s budget
      // regularly lost the race on slower machines and surfaced as "errors
      // waking up bots" even though the transcript had been available almost
      // immediately. Only an expected-EMPTY chat still waits for the runtime
      // — with no transcript to paint, a bound runtime is the only proof the
      // surface is real rather than a stuck loader.
      const hydrated = expectHistory ? historyPainted : runtimeReady

      if ((mainMatches || tileMatches) && hydrated) {
        if (profileMatches) {
          finish()

          return
        }

        // Paint-first completion on an unsatisfiable profile gate (#89843).
        // On a shared-remote connection every profile is legitimately served
        // through the primary socket, so $activeGatewayProfile can NEVER
        // equal the bot's profile — the old gate held a fully painted
        // transcript hostage for the whole 20s budget and then stranded the
        // pane. When the stored history is already painted on exactly this
        // session, that content IS the proof the surface is real: resolve
        // now, raise the subtle "Syncing…" affordance, and let the profile
        // gate catch up in the background.
        //
        // Fail closed everywhere the content is NOT its own proof: a
        // superseded generation already rejected above (conflicting
        // concurrent hydration never resolves paint-first), and an
        // expected-EMPTY chat keeps waiting for the full gate — with no
        // transcript to paint, a bound runtime on an unmatched profile is
        // not evidence of a real surface.
        if (expectHistory && historyPainted) {
          beginHydrationBackgroundSync(profile)
          finish()
        }
      }
    }

    unbinds.push($activeGatewayProfile.listen(check))
    unbinds.push($activeConnectionId.listen(check))
    unbinds.push($selectedStoredSessionId.listen(check))
    unbinds.push($activeSessionId.listen(check))
    unbinds.push($messages.listen(check))
    unbinds.push($focusedStoredSessionId.listen(check))
    unbinds.push($focusedRuntimeId.listen(check))
    unbinds.push($focusedSessionState.listen(check))
    unbinds.push($sessionTiles.listen(check))
    unbinds.push($sessionStates.listen(check))
    unbinds.push($workspaceMode.listen(check))
    unbinds.push($workspaceOwnerKey.listen(check))

    timer = window.setTimeout(() => {
      finish(new Error(`Timed out loading ${profile}'s session history.`))
    }, timeoutMs)

    check()
  })
}

// Wait for a profile switch, but never longer than the wake budget.
//
// ensureGatewayProfile awaits the store's dial, and HermesGateway.connect() has
// no dial timeout of its own: a backend that accepts the socket and then never
// completes the handshake leaves this promise pending for the life of the
// window. That is not merely a slow open. waitForFocusedSessionHydration arms
// the only timer on this path, and it is armed AFTER this await returns - so an
// unbounded activation means the wake never settles at all, and the pane wedges
// with no error, no Retry and no timeout (#89556: `ws accepted` in the gateway
// log with no matching `ws closed`).
//
// The activation gets its OWN budget rather than sharing the hydration one. A
// cold profile backend can legitimately spend most of the hydration budget
// painting a large transcript - that race is already tight enough to lose
// (#89617) - so charging activation to the same clock would turn a wedge into a
// regression. The trade is that a wake that is slow in BOTH phases can now take
// up to twice the budget before it surfaces; that is a maintainer call and is
// called out in the PR rather than buried here.
// The caller supplies the dial itself, because WHICH backend to open is a
// routing decision (a workspace switch moves chrome; a plain bot navigation
// only opens the gateway) while the deadline enforced here is the same either
// way.
export async function awaitProfileActivation(
  dial: () => Promise<void>,
  targetProfile: string,
  timeoutMs: number
): Promise<void> {
  const activation = dial()
  let timer: number | undefined

  try {
    await Promise.race([
      activation,
      new Promise<never>((_resolve, reject) => {
        // Same message shape as the hydration timeout on purpose: openSession's
        // catch keys the core stranded-session surface off this prefix, and a
        // wedged dial wants exactly that surface. The phase is distinguished in
        // the [bot-wake] support log, not in the user-facing string.
        timer = window.setTimeout(
          () => reject(new Error(`Timed out loading ${targetProfile}'s session history.`)),
          timeoutMs
        )
      })
    ])
  } finally {
    if (timer !== undefined) {
      window.clearTimeout(timer)
    }
  }

  // No extra catch on the abandoned dial: an in-flight activation has no
  // cancellation handle and keeps running after the budget expires, but
  // Promise.race subscribes to every input, so a rejection that lands after the
  // race has settled is already handled and cannot escape as an unhandled
  // rejection. An explicit `activation.catch()` here was dead code - verified
  // by mutation: removing it changed no test outcome.
}

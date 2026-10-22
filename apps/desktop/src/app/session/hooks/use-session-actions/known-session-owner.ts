import { resolveSessionRpcOwner } from '@/app/contrib/wiring-routing'
import { normalizeProfileKey } from '@/store/profile'
import { getSessionOwnerHint, knownSessionOwner, ownerLookupSessionRows } from '@/store/session'
import { isSessionOwnerRoute, type SessionOwnerScope } from '@/store/session-request-router'
import { runtimeSessionOwner, sessionTileOwnerRoute } from '@/store/session-states'
import { provideKnownSessionWindowRoute } from '@/store/session-window-route'

/** resolveSessionOwner's sync ladder (tile route → hint → row → runtime event), with no fetch. */
export function resolveKnownSessionOwner(storedSessionId: string): SessionOwnerScope {
  return resolveSessionRpcOwner({
    routingSessionId: storedSessionId,
    tileOwnerRoute: sessionTileOwnerRoute,
    sessionOwnerHint: getSessionOwnerHint,
    sessionRowOwner: id => knownSessionOwner(ownerLookupSessionRows(), id),
    eventOwner: runtimeSessionOwner
  })
}

// A browser pop-out can open inside the click only when its owner is known up
// front; this is the route openSessionInNewWindow would build from that owner.
provideKnownSessionWindowRoute(storedSessionId => {
  const owner = resolveKnownSessionOwner(storedSessionId)

  if (!owner) {
    return undefined
  }

  return isSessionOwnerRoute(owner)
    ? { connectionId: owner.connectionId, profile: normalizeProfileKey(owner.profile) }
    : { connectionId: null, profile: normalizeProfileKey(owner) }
})

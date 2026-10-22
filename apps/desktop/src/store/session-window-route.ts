/**
 * A session owner's pop-out route, when it is already known in memory.
 *
 * The browser host opens pop-outs with `noopener`, so the window cannot be
 * navigated after an await, and Safari drops the user gesture across one.
 * windows.ts loads the owner modules lazily (`./profile` subscribes to the API
 * client on load), so the session actions module, which the app always loads,
 * provides this synchronous lookup instead.
 */
export interface SessionWindowRoute {
  connectionId: null | string
  profile: string
}

type KnownSessionWindowRoute = (storedSessionId: string) => SessionWindowRoute | undefined

let knownRoute: KnownSessionWindowRoute | undefined

export function provideKnownSessionWindowRoute(resolve: KnownSessionWindowRoute): void {
  knownRoute = resolve
}

export function knownSessionWindowRoute(storedSessionId: string): SessionWindowRoute | undefined {
  return knownRoute?.(storedSessionId)
}

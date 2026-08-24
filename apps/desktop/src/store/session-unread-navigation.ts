let openNextUnread: null | (() => void) = null

/** Register the mounted Sessions surface as the owner of unread navigation. */
export function registerOpenNextUnreadSession(handler: () => void): () => void {
  openNextUnread = handler

  return () => {
    if (openNextUnread === handler) {
      openNextUnread = null
    }
  }
}

/** Invoke the current Sessions action without coupling pane chrome to routing. */
export function openNextUnreadSession(): void {
  openNextUnread?.()
}

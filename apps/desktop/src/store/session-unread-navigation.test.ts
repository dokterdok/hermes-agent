import { afterEach, describe, expect, it, vi } from 'vitest'

import { openNextUnreadSession, registerOpenNextUnreadSession } from './session-unread-navigation'

let dispose: null | (() => void) = null

afterEach(() => {
  dispose?.()
  dispose = null
})

describe('unread session navigation delegate', () => {
  it('routes the request to the mounted Sessions owner', () => {
    const open = vi.fn()
    dispose = registerOpenNextUnreadSession(open)

    openNextUnreadSession()

    expect(open).toHaveBeenCalledOnce()
  })

  it('does nothing after the owning surface unmounts', () => {
    const open = vi.fn()
    dispose = registerOpenNextUnreadSession(open)
    dispose()
    dispose = null

    openNextUnreadSession()

    expect(open).not.toHaveBeenCalled()
  })
})

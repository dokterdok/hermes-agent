/**
 * Bot Mode's pane layout contract, asserted by running the real `register()`
 * against a recording plugin context:
 *
 *  - the Bots pane center-stacks into the sessions zone (a SESSIONS | BOTS tab
 *    strip), never splits below it, and carries the ENFORCED dock invariant so
 *    every boot re-homes a stacked install. No heal token, no user-placed
 *    exemption — the retired one-shot heal burned its token even when its
 *    guards skipped the move, so exactly the users who had dragged their panes
 *    stayed stacked forever;
 *  - the Scheduled jobs (internally `routines`) pane only exists while a BOT
 *    CHAT owns the main workspace and the Bots pane is on screen. It is
 *    registered and unregistered through the contribution disposer, driven by
 *    the feature-detected `host.paneVisibility` export, with the
 *    always-registered fallback kept for older desktops. Cron jobs are
 *    bot-scoped, so the tile must not sit beside a group chat.
 */

import type * as HermesSdk from '@hermes/plugin-sdk'
import type { PluginContext } from '@hermes/plugin-sdk'
import { atom } from 'nanostores'
import type { ReactNode } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

// The app provider the plugin's tab label renders under; a plugin test may reach it.
// eslint-disable-next-line no-restricted-imports
import { I18nProvider } from '@/i18n'

import type * as DataModule from './data'
import type * as GroupChatModule from './group-chat'
import { desktopCommandResult, settleDesktopCommand } from './group-command-receipts'
import { classicAuthorityHash } from './group-desktop-authority'
import type * as RoutingModule from './routing'
import type { GroupChat } from './types'
import { canonicalUser, optimisticUser, userRoom } from './user-event-test-fixtures'

const mocks = vi.hoisted(() => ({
  activateClassicGroupAuthorities: vi.fn(async () => false),
  adoptShippedGroupChats: vi.fn(async (): Promise<void> => undefined),
  botChatOwnsWorkspace: vi.fn(() => false),
  mailboxClaim: vi.fn(),
  onEvent: vi.fn((_name: string, _callback: (event?: unknown) => void) => () => undefined),
  paneVisibility: vi.fn(),
  scheduleGroupChatServerSync: vi.fn(),
  startDesktopRoomCommandRuntime: vi.fn(async (_storage: PluginContext['storage']): Promise<void> => undefined),
  sessionOwnsWorkspace: vi.fn(() => false),
  startHostedRoomRuntime: vi.fn(async (): Promise<void> => undefined),
  stopHostedRoomRuntime: vi.fn(),
  stopDesktopRoomCommandRuntime: vi.fn(),
  setWorkspaceScope: vi.fn(),
  undismissPane: vi.fn()
}))

vi.mock('@hermes/plugin-sdk', async importOriginal => {
  const original = await importOriginal<typeof HermesSdk>()

  return {
    ...original,
    host: {
      ...original.host,
      onEvent: mocks.onEvent,
      paneVisibility: mocks.paneVisibility,
      setWorkspaceScope: mocks.setWorkspaceScope,
      undismissPane: mocks.undismissPane
    }
  }
})

// Everything below is a boundary this test does not exercise: clocks, sockets,
// storage sweeps and the panes' own render trees.
vi.mock('./avatar', () => ({ startFaceClock: vi.fn(), stopFaceClock: vi.fn() }))
vi.mock('./relay', () => ({ startBotRelay: vi.fn(), stopBotRelay: vi.fn() }))
vi.mock('./hosted-room-runtime', () => ({
  startHostedRoomRuntime: mocks.startHostedRoomRuntime,
  stopHostedRoomRuntime: mocks.stopHostedRoomRuntime
}))
vi.mock('./desktop-room-command-runtime', () => ({
  startDesktopRoomCommandRuntime: mocks.startDesktopRoomCommandRuntime,
  stopDesktopRoomCommandRuntime: mocks.stopDesktopRoomCommandRuntime
}))
vi.mock('./session-sweep', () => ({ startHideSweepScheduler: vi.fn() }))
vi.mock('./canonical-chat', () => ({ openBotCanonicalChat: vi.fn() }))
vi.mock('./chat-empty', () => ({ BotChatEmpty: () => null }))
vi.mock('./hygiene', () => ({ annotateOrphanedGroupChatMembers: () => ({ changed: false, rooms: {} }) }))
vi.mock('./group-chat-view', () => ({ renameGroupChat: vi.fn(async (_old, next) => next) }))
vi.mock('./cron', () => ({ bindProfileSync: () => () => undefined, RoutinesPane: () => null }))
vi.mock('./roster-pane', () => ({
  botChatOwnsWorkspace: mocks.botChatOwnsWorkspace,
  BotsPane: () => null,
  releaseStaleOpenBotChat: vi.fn(),
  selectedRosterBot: () => null,
  sessionOwnsWorkspace: mocks.sessionOwnsWorkspace
}))
vi.mock('./group-chat', async importOriginal => {
  const { atom: nanoAtom } = await import('nanostores')
  const actual = await importOriginal<typeof GroupChatModule>()

  return {
    $groupChats: nanoAtom({}),
    $groupChatWorkspace: nanoAtom(null),
    activateClassicGroupAuthorities: mocks.activateClassicGroupAuthorities,
    assignLegacyThreads: (log: unknown[]) => log,
    groupChatHostedGateway: (room: GroupChat) => room?.hosted || '',
    handleSessionsGatewayTransition: vi.fn(),
    hydrateGroupChatRooms: actual.hydrateGroupChatRooms,
    pullGroupChatServerState: vi.fn(async () => false),
    scheduleGroupChatServerSync: mocks.scheduleGroupChatServerSync,
    setGroupChatSyncDisposed: vi.fn(),
    stopGroupChatServerSync: vi.fn(),
    sweepGroupChatMembersForRemovedConnection: vi.fn(),
    updateGroupChat: vi.fn()
  }
})
vi.mock('./shipped-group-adoption', () => ({
  adoptShippedGroupChats: mocks.adoptShippedGroupChats,
  stopShippedGroupAdoption: vi.fn()
}))
vi.mock('./data', async importOriginal => {
  const original = await importOriginal<typeof DataModule>()

  return { ...original, migrateBotMeta: async () => undefined }
})
vi.mock('./routing', async importOriginal => {
  const original = await importOriginal<typeof RoutingModule>()

  return { ...original, setBotsWorkspaceOwner: vi.fn() }
})

const plugin = (await import('./plugin')).default

interface Registration {
  area: string
  data?: Record<string, unknown>
  id: string
}

/** A recording `PluginContext`: registrations, their disposers, teardown. */
function recordingContext(
  storageGet: (key: string) => Promise<unknown> = async () => undefined,
  storageSet: (key: string, value: unknown) => Promise<void> = async () => undefined
) {
  const disposers: (() => void)[] = []
  const registrations: Registration[] = []
  const unregisters = new Map<string, () => void>()

  const ctx = {
    i18n: { register: () => () => undefined, t: (key: string) => key },
    onDispose: (fn: () => void) => disposers.push(fn),
    register: (registration: Registration) => {
      registrations.push(registration)

      const unregister = vi.fn(() => {
        registrations.splice(registrations.indexOf(registration), 1)
      })

      unregisters.set(registration.id, unregister)

      return unregister
    },
    storage: { get: storageGet, set: storageSet }
  }

  return {
    ctx: ctx as unknown as PluginContext,
    dispose: () => disposers.forEach(fn => fn()),
    find: (id: string) => registrations.find(registration => registration.id === id),
    unregisters
  }
}

/** Nanostore stand-ins for the SDK's per-pane visibility stores. */
function paneStores() {
  const stores = new Map<string, ReturnType<typeof atom<boolean>>>()

  mocks.paneVisibility.mockImplementation((id: string) => {
    if (!stores.has(id)) {
      stores.set(id, atom(false))
    }

    return stores.get(id)
  })

  return (id: string) => {
    mocks.paneVisibility(id)

    return stores.get(id)!
  }
}

/** The plugin defers one reconcile to a macrotask so it never re-enters the
 *  tree store mid-mutation. */
const settle = () => new Promise(resolve => setTimeout(resolve, 0))

beforeEach(() => {
  vi.clearAllMocks()
  mocks.botChatOwnsWorkspace.mockReturnValue(false)
  mocks.activateClassicGroupAuthorities.mockResolvedValue(false)
  mocks.adoptShippedGroupChats.mockResolvedValue(undefined)
  mocks.onEvent.mockImplementation((_name: string, _callback: (event?: unknown) => void) => () => undefined)
  mocks.sessionOwnsWorkspace.mockReturnValue(false)
  mocks.startDesktopRoomCommandRuntime.mockResolvedValue(undefined)
})

afterEach(() => {
  vi.useRealTimers()
})

describe('the Bots pane dock', () => {
  it('renders its tab label from the live locale, not the register-time string', () => {
    paneStores()

    const harness = recordingContext()

    // Registration runs at module import, before the app has loaded
    // `display.language`: the string `title` is English here no matter what.
    plugin.register(harness.ctx)

    const tabTitle = harness.find('pane')!.data!.tabTitle as () => ReactNode

    const inLocale = (locale: string) =>
      renderToStaticMarkup(
        <I18nProvider configClient={null} initialLocale={locale}>
          {tabTitle()}
        </I18nProvider>
      )

    expect(inLocale('en')).toBeTruthy()
    expect(inLocale('ru')).not.toBe(inLocale('en'))

    harness.dispose()
  })
})

describe('hosted Group Chat startup', () => {
  it('awaits durability-gated classic authority activation before publishing or starting services', async () => {
    paneStores()
    let rejectActivation!: (error: Error) => void
    mocks.activateClassicGroupAuthorities.mockImplementationOnce(
      () =>
        new Promise<boolean>((_resolve, reject) => {
          rejectActivation = reject
        })
    )

    const existing = {
      Planning: {
        log: [{ at: 1, from: { kind: 'user', name: 'You' }, text: 'Existing', thread: 'thread-1' }],
        members: [{ name: 'reviewer' }],
        roomId: 'room-1',
        watermarks: {}
      }
    }

    const failed = recordingContext(async key => (key === 'group-chats' ? existing : undefined))
    plugin.register(failed.ctx)
    await settle()
    expect(mocks.scheduleGroupChatServerSync).not.toHaveBeenCalled()
    expect(mocks.startHostedRoomRuntime).not.toHaveBeenCalled()
    expect(mocks.mailboxClaim).not.toHaveBeenCalled()
    rejectActivation(new Error('disk unavailable'))
    await settle()
    expect(mocks.scheduleGroupChatServerSync).not.toHaveBeenCalled()
    expect(mocks.mailboxClaim).not.toHaveBeenCalled()
    failed.dispose()

    mocks.activateClassicGroupAuthorities.mockImplementationOnce(async () => {
      mocks.scheduleGroupChatServerSync()

      return true
    })
    const recovered = recordingContext(async key => (key === 'group-chats' ? existing : undefined))
    plugin.register(recovered.ctx)
    await settle()
    await settle()
    expect(mocks.scheduleGroupChatServerSync).toHaveBeenCalledTimes(1)
    expect(mocks.adoptShippedGroupChats).toHaveBeenCalledWith(recovered.ctx.storage)
    expect(mocks.activateClassicGroupAuthorities.mock.invocationCallOrder.at(-1))
      .toBeLessThan(mocks.adoptShippedGroupChats.mock.invocationCallOrder.at(-1)!)
    expect(mocks.adoptShippedGroupChats.mock.invocationCallOrder.at(-1))
      .toBeLessThan(mocks.startHostedRoomRuntime.mock.invocationCallOrder.at(-1)!)
    expect(mocks.startDesktopRoomCommandRuntime).toHaveBeenCalledWith(recovered.ctx.storage)
    expect(mocks.mailboxClaim).not.toHaveBeenCalled()
    recovered.dispose()
  })

  it('retries failed mailbox starts after discovery and on push signals without polling', async () => {
    paneStores()
    let pending!: () => void
    let discovered!: () => void
    mocks.startHostedRoomRuntime.mockImplementationOnce(() => new Promise<void>(resolve => { discovered = resolve }))
    mocks.onEvent.mockImplementation((name: string, callback: (event?: unknown) => void) => {
      if (name === 'desktop_rooms.commands.pending') {
        pending = callback as () => void
      }

      return () => undefined
    })
    mocks.startDesktopRoomCommandRuntime
      .mockRejectedValueOnce(new Error('disk unavailable one'))
      .mockRejectedValueOnce(new Error('disk unavailable two'))
      .mockImplementationOnce(async () => {
        mocks.mailboxClaim()
      })
    const harness = recordingContext()
    plugin.register(harness.ctx)
    await settle()
    await settle()
    expect(mocks.startDesktopRoomCommandRuntime).toHaveBeenCalledTimes(1)

    discovered()
    await settle()
    expect(mocks.startDesktopRoomCommandRuntime).toHaveBeenCalledTimes(2)
    pending()
    await settle()
    expect(mocks.startDesktopRoomCommandRuntime).toHaveBeenCalledTimes(3)
    expect(mocks.mailboxClaim).toHaveBeenCalledTimes(1)
    harness.dispose()
  })

  it('heals a cold duplicated user cache before any gateway replay is available', async () => {
    paneStores()
    const cold = JSON.parse(JSON.stringify({ Board: userRoom([optimisticUser(), canonicalUser()]) }))
    const harness = recordingContext(async key => (key === 'group-chats' ? cold : undefined))
    const { $groupChats } = await import('./group-chat')

    plugin.register(harness.ctx)
    await settle()

    expect($groupChats.get().Board.log).toEqual([canonicalUser()])
    expect(mocks.startHostedRoomRuntime).toHaveBeenCalled()
    harness.dispose()
  })

  it('hydrates bounded completion results before mailbox startup without rotating authority', async () => {
    paneStores()

    const room: GroupChat = {
      roomId: 'room-1',
      members: [{ name: 'builder' }],
      log: [],
      watermarks: {},
      desktopAuthorityToken: 'authority:saved',
      desktopAuthorityHash: classicAuthorityHash('authority:saved')
    }

    room.desktopCommandSettled = settleDesktopCommand('Workshop', room, 'send:latest', 'send', {
      room_name: 'Workshop',
      thread_id: 'original'
    })

    for (let index = 0; index < 140; index++) {
      room.desktopCommandSettled[`old:${index}`] = index
    }

    const cold = JSON.parse(JSON.stringify({ Renamed: room }))
    const harness = recordingContext(async key => (key === 'group-chats' ? cold : undefined))
    const chat = await import('./group-chat')
    let checked = false
    mocks.startDesktopRoomCommandRuntime.mockImplementationOnce(async () => {
      const hydrated = chat.$groupChats.get().Renamed
      expect(Object.keys(hydrated.desktopCommandSettled!)).toHaveLength(128)
      expect(desktopCommandResult('Renamed', hydrated, 'send:latest', 'send')).toEqual({
        room_name: 'Workshop',
        thread_id: 'original'
      })
      expect(hydrated.desktopAuthorityToken).toBe('authority:saved')
      checked = true
    })

    try {
      plugin.register(harness.ctx)
      await settle()
      expect(checked).toBe(true)
    } finally {
      harness.dispose()
      chat.$groupChats.set({})
    }
  })

  it.each([true, false, undefined, 'true'])(
    'restores local membership verification before projection pull: %s',
    async value => {
      paneStores()
      const chat = await import('./group-chat')
      chat.$groupChats.set({})
      const beforePull: boolean[] = []
      vi.mocked(chat.pullGroupChatServerState).mockImplementationOnce(async () => {
        beforePull.push(chat.$groupChats.get().Saved?.hostedMembersVerified === true)

        return false
      })

      const harness = recordingContext(async key =>
        key === 'group-chats'
          ? {
              Saved: {
                roomId: 'saved-room',
                hosted: 'saved-authority',
                hostedMembersVerified: value,
                members: [{ name: 'default' }],
                log: [],
                watermarks: {}
              }
            }
          : undefined
      )

      try {
        plugin.register(harness.ctx)
        await settle()
        expect(beforePull).toEqual([value === true])
        expect(chat.$groupChats.get().Saved?.hostedMembersVerified === true).toBe(value === true)
        expect(mocks.startHostedRoomRuntime).toHaveBeenCalledTimes(1)
      } finally {
        harness.dispose()
        chat.$groupChats.set({})
      }
    }
  )

  it('does not probe or replay hosted rooms before local Group Chat hydration settles', async () => {
    paneStores()
    let releaseRooms: (value: unknown) => void = () => undefined

    const rooms = new Promise(resolve => {
      releaseRooms = resolve
    })

    const harness = recordingContext(async key => (key === 'group-chats' ? rooms : undefined))

    plugin.register(harness.ctx)
    await Promise.resolve()
    await Promise.resolve()

    expect(mocks.startHostedRoomRuntime).not.toHaveBeenCalled()

    releaseRooms({})
    await settle()

    expect(mocks.startHostedRoomRuntime).toHaveBeenCalledTimes(1)
    expect(mocks.startHostedRoomRuntime).toHaveBeenCalledWith(
      harness.ctx.storage,
      expect.objectContaining({
        renameGroupChat: expect.any(Function)
      })
    )

    harness.dispose()
    expect(mocks.stopHostedRoomRuntime).toHaveBeenCalled()
  })
})

describe('the Scheduled jobs pane', () => {
  it('stays unregistered until a bot chat owns the workspace', async () => {
    const store = paneStores()
    const harness = recordingContext()

    plugin.register(harness.ctx)
    await settle()

    expect(harness.find('routines')).toBeUndefined()

    mocks.botChatOwnsWorkspace.mockReturnValue(true)
    store(`hermes-bots:pane`).set(true)

    expect(harness.find('routines')).toBeTruthy()

    harness.dispose()
  })

  it('unregisters when Bot Mode leaves the screen', async () => {
    const store = paneStores()
    const harness = recordingContext()

    mocks.botChatOwnsWorkspace.mockReturnValue(true)
    plugin.register(harness.ctx)
    await settle()

    expect(harness.find('routines')).toBeTruthy()

    mocks.botChatOwnsWorkspace.mockReturnValue(false)
    store(`hermes-bots:pane`).set(true)
    store(`hermes-bots:pane`).set(false)

    expect(harness.unregisters.get('routines')).toHaveBeenCalled()
    expect(harness.find('routines')).toBeUndefined()

    harness.dispose()
  })

  it('keeps the tile alive while the tile itself holds focus', async () => {
    const store = paneStores()
    const harness = recordingContext()

    mocks.botChatOwnsWorkspace.mockReturnValue(true)
    plugin.register(harness.ctx)
    await settle()

    // Clicking the tile drops bot-chat workspace ownership for a beat. A pane
    // must never unregister itself out from under its own click.
    store(`hermes-bots:routines`).set(true)
    mocks.botChatOwnsWorkspace.mockReturnValue(false)
    store(`hermes-bots:pane`).set(true)

    expect(harness.unregisters.get('routines')).not.toHaveBeenCalled()
    expect(harness.find('routines')).toBeTruthy()

    harness.dispose()
  })

  it('drops a remembered Close only on entering Bot Mode, not on every ownership regain', async () => {
    const store = paneStores()
    const harness = recordingContext()

    mocks.botChatOwnsWorkspace.mockReturnValue(true)
    store(`hermes-bots:pane`).set(true)
    plugin.register(harness.ctx)
    await settle()

    // Boot straight into a bot chat: the pane arrives and a Close from a past
    // launch is dropped once (#102224).
    expect(mocks.undismissPane).toHaveBeenCalledTimes(1)
    expect(mocks.undismissPane).toHaveBeenCalledWith('hermes-bots:routines')

    // The user ✕-es the pane, opens a group room (the tile must not sit
    // beside a group chat) and comes back to the bot chat — all inside one
    // Bots session. Re-registration must not undo their Close.
    const { $groupChatWorkspace } = await import('./group-chat')
    mocks.botChatOwnsWorkspace.mockReturnValue(false)
    $groupChatWorkspace.set({ id: 'room' } as never)
    expect(harness.find('routines')).toBeUndefined()

    mocks.botChatOwnsWorkspace.mockReturnValue(true)
    $groupChatWorkspace.set(null)
    expect(harness.find('routines')).toBeTruthy()
    expect(mocks.undismissPane).toHaveBeenCalledTimes(1)

    // Leaving Bot Mode and coming back is the ask for the bot's chrome again.
    mocks.botChatOwnsWorkspace.mockReturnValue(false)
    store(`hermes-bots:pane`).set(false)
    mocks.botChatOwnsWorkspace.mockReturnValue(true)
    store(`hermes-bots:pane`).set(true)
    expect(mocks.undismissPane).toHaveBeenCalledTimes(2)

    harness.dispose()
  })

  it('stops every lifecycle listener when the plugin is disabled', async () => {
    const store = paneStores()
    const harness = recordingContext()

    plugin.register(harness.ctx)
    await settle()
    harness.dispose()

    // A disable → re-enable cycle used to stack a duplicate listener per cycle.
    mocks.botChatOwnsWorkspace.mockReturnValue(true)
    store(`hermes-bots:pane`).set(true)

    expect(harness.find('routines')).toBeUndefined()
  })
})

describe('returning to Sessions', () => {
  it('drops a cold bot open still pending (#120277)', async () => {
    const store = paneStores()
    const harness = recordingContext()
    const { $pendingBotOpen } = await import('./shared')

    plugin.register(harness.ctx)
    await settle()
    store(`hermes-bots:pane`).set(true)
    $pendingBotOpen.set({ generation: 1, key: 'local::bravo' })

    store(`hermes-bots:pane`).set(false)

    expect($pendingBotOpen.get()).toBeNull()
    expect(mocks.setWorkspaceScope).toHaveBeenCalledWith('sessions')

    harness.dispose()
  })
})

describe('a desktop without host.paneVisibility', () => {
  it('keeps the always-registered pane', async () => {
    const { host } = await import('@hermes/plugin-sdk')
    const restore = host.paneVisibility

    // @ts-expect-error modelling an older SDK that lacks the export entirely
    host.paneVisibility = undefined

    const harness = recordingContext()

    plugin.register(harness.ctx)

    expect(harness.find('routines')).toBeTruthy()

    harness.dispose()
    host.paneVisibility = restore
  })
})

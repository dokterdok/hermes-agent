/* eslint-disable no-restricted-imports, no-restricted-globals -- Mounted production pane fixture observes DOM focus at the real locale/store boundary. */
import { act, cleanup, render, screen } from '@testing-library/react'
import type { ReactNode } from 'react'
import { afterEach, beforeAll, beforeEach, expect, it, vi } from 'vitest'

import { I18nProvider } from '@/i18n/context'
import { registerPluginLocales } from '@/i18n/plugin-i18n'
import { $connection, $gatewayState } from '@/store/session'

import { BOTS_LOCALES } from './i18n'

beforeAll(async () => {
  Element.prototype.scrollIntoView = vi.fn()
  await import('./group-chat-view')
}, 30_000)
beforeEach(() => {
  registerPluginLocales('hermes-bots', BOTS_LOCALES)
  $gatewayState.set('open')
  $connection.set({ connectionId: 'owner' } as NonNullable<typeof $connection.value>)
})
afterEach(async () => {
  cleanup()
  vi.restoreAllMocks()
  const { stopGroupChatServerSync } = await import('./group-chat')
  stopGroupChatServerSync()
  const { groupChatMainTabs, dropGroupMainTab } = await import('./group-panes')

  for (const group of groupChatMainTabs.keys()) {
    dropGroupMainTab(group)
  }

  $gatewayState.set('idle')
  $connection.set(null)
})

function mount(node: ReactNode) {
  return render(
    <I18nProvider configClient={null} initialLocale="en">
      {node}
    </I18nProvider>
  )
}

it('retains the renamed room body and focus, reuses its pane, and retires only its current closer', async () => {
  const { host } = await import('@hermes/plugin-sdk')
  const chat = await import('./group-chat')
  const panes = await import('./group-panes')
  const view = await import('./group-chat-view')
  const { scriptedStorage } = await import('./group-test-utils')
  const { setPluginCtx } = await import('./shared')
  setPluginCtx(scriptedStorage(new Map()))
  vi.spyOn(host, 'requestProfile').mockResolvedValue({ driver: false } as never)
  const opened: { id: string; options: Parameters<typeof host.openWorkspace>[1]; close: () => void }[] = []
  const livePanes = new Set<string>()
  vi.spyOn(host, 'openWorkspace').mockImplementation((id, options) => {
    livePanes.add(id)

    const close = vi.fn(() => {
      livePanes.delete(id)
      options.onClose?.()
    })

    opened.push({ id, options, close })

    return close
  })
  chat.$groupChats.set({
    Release: {
      continuityMode: 'gateway',
      hosted: 'install:studio',
      hostedConnectionId: 'owner',
      hostedEpoch: 1,
      log: [{ at: 1, id: 'm1', thread: 't1', from: { kind: 'member', name: 'research' }, text: 'Same room history' }],
      members: [],
      roomId: 'room-1',
      watermarks: {}
    }
  })
  act(() => view.openGroupChat('Release'))
  const firstClose = panes.groupChatMainTabs.get('Release')!
  mount(opened[0].options.render())
  const composer = screen.getByRole('textbox', { name: 'Message Release' })
  composer.focus()
  await act(async () => {
    await view.renameGroupChat('Release', 'Renamed', [], { hostedAlreadyRenamed: true })
  })
  expect(opened).toHaveLength(1)
  expect(screen.getByText('Same room history')).toBeTruthy()
  expect(screen.getByRole('textbox', { name: 'Message Renamed' })).toBe(composer)
  expect(document.activeElement).toBe(composer)
  expect(chat.$groupChatWorkspace.get()).toBe('Renamed')
  expect(panes.groupChatMainTabs.has('Release')).toBe(false)
  act(() => view.openGroupChat('Renamed'))
  expect(opened).toHaveLength(2)
  expect(opened[1].id).toBe(opened[0].id)
  expect(opened[1].options.title).toBe('Renamed')
  act(firstClose)
  expect(opened[0].close).not.toHaveBeenCalled()
  expect(livePanes.has(opened[1].id)).toBe(true)
  expect(panes.groupChatMainTabs.has('Renamed')).toBe(true)
  act(() => panes.closeGroupChatMainTab('Renamed'))
  expect(opened[1].close).toHaveBeenCalledTimes(1)
  expect(livePanes.has(opened[1].id)).toBe(false)
  expect(panes.groupChatMainTabs.has('Renamed')).toBe(false)
  expect(chat.$groupChatWorkspace.get()).toBeNull()
})

it('does not alias a retained renamed pane when a new room reuses its former name', async () => {
  const { host } = await import('@hermes/plugin-sdk')
  const chat = await import('./group-chat')
  const panes = await import('./group-panes')
  const view = await import('./group-chat-view')
  const { scriptedStorage } = await import('./group-test-utils')
  const { setPluginCtx } = await import('./shared')
  setPluginCtx(scriptedStorage(new Map()))
  const opened: { id: string; close: () => void }[] = []
  vi.spyOn(host, 'openWorkspace').mockImplementation((id, options) => {
    const close = () => options.onClose?.()
    opened.push({ id, close })

    return close
  })
  chat.$groupChats.set({ Release: { log: [], members: [], roomId: 'retained-room', watermarks: {} } })
  view.openGroupChat('Release')
  await view.renameGroupChat('Release', 'Renamed', [], { hostedAlreadyRenamed: true })
  chat.$groupChats.set({
    ...chat.$groupChats.get(),
    Release: { log: [], members: [], roomId: 'replacement-room', watermarks: {} }
  })
  view.openGroupChat('Release')
  expect(opened).toHaveLength(2)
  expect(opened[1].id).not.toBe(opened[0].id)
  opened[1].close()
  expect(panes.groupChatMainTabs.has('Renamed')).toBe(true)
  expect(panes.groupChatMainTabs.has('Release')).toBe(false)
  opened[0].close()
  expect(panes.groupChatMainTabs.has('Renamed')).toBe(false)
})

it.each([true, false])(
  'background rename=%s preserves navigation intent while foreground rename follows it',
  async background => {
    const { host } = await import('@hermes/plugin-sdk')
    const chat = await import('./group-chat')
    const panes = await import('./group-panes')
    const view = await import('./group-chat-view')
    const { scriptedStorage } = await import('./group-test-utils')
    const shared = await import('./shared')
    shared.setPluginCtx(scriptedStorage(new Map()))
    const open = vi.spyOn(host, 'openWorkspace').mockImplementation((_id, options) => () => options.onClose?.())
    chat.$groupChats.set({ Release: { log: [], members: [], roomId: 'room', watermarks: {} } })
    view.openGroupChat('Release')
    chat.$groupChatWorkspace.set('Other room')
    const generation = shared.bumpBotOpenGeneration()
    const pending = { generation, key: 'local::builder' }
    shared.$pendingBotOpen.set(pending)
    await view.renameGroupChat('Release', 'Renamed', [], { hostedAlreadyRenamed: background })
    expect(panes.groupChatMainTabs.has('Release')).toBe(false)
    expect(panes.groupChatMainTabs.has('Renamed')).toBe(true)

    if (background) {
      expect(open).toHaveBeenCalledTimes(1)
      expect(chat.$groupChatWorkspace.get()).toBe('Other room')
      expect(shared.getBotOpenGeneration()).toBe(generation)
      expect(shared.$pendingBotOpen.get()).toBe(pending)
    } else {
      expect(open).toHaveBeenCalledTimes(2)
      expect(chat.$groupChatWorkspace.get()).toBe('Renamed')
      expect(shared.getBotOpenGeneration()).toBeGreaterThan(generation)
      expect(shared.$pendingBotOpen.get()).toBeNull()
    }

    shared.$pendingBotOpen.set(null)
  }
)

it('does not remove a replacement registered during host close', async () => {
  const chat = await import('./group-chat')
  const panes = await import('./group-panes')
  const replacement = vi.fn()
  panes.recordGroupMainTab('Room', () => panes.recordGroupMainTab('Room', replacement))
  chat.$groupChatWorkspace.set('Room')
  panes.closeGroupChatMainTab('Room')
  expect(panes.groupChatMainTabs.get('Room')).toBe(replacement)
  expect(chat.$groupChatWorkspace.get()).toBe('Room')
  expect(replacement).not.toHaveBeenCalled()
})

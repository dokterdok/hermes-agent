import type * as HermesSdk from '@hermes/plugin-sdk'
import { host } from '@hermes/plugin-sdk'
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { useState } from 'react'
import { afterEach, expect, it, vi } from 'vitest'

import { GroupRow } from './bot-row'
import { $botMeta } from './data'
import * as botData from './data'
import { $groupChats } from './group-chat'
import { GroupChatSettingsDialog } from './group-chat-view'
import { translateBots } from './i18n-test-helper'
import { renderRosterDialogs } from './roster-pane-dialogs'
import type { RosterRow } from './types'

vi.mock('@hermes/plugin-sdk', async importOriginal => {
  const sdk = await importOriginal<typeof HermesSdk>()

  return { ...sdk, usePluginI18n: () => translateBots }
})

afterEach(() => vi.restoreAllMocks())

it('opens the existing Group settings from the roster context menu', async () => {
  const onSettings = vi.fn()

  const props = {
    active: false,
    group: 'Planning',
    members: [],
    needsYou: false,
    onDisband: vi.fn(),
    onOpen: vi.fn(),
    onSettings,
    onNewSection: vi.fn()
  }

  render(<GroupRow {...props} />)
  fireEvent.contextMenu(screen.getByRole('button'))
  fireEvent.click(await screen.findByText('Group settings'))
  expect(onSettings).toHaveBeenCalledWith({ members: [], name: 'Planning' })
})

it('keeps one settings save pending across clicks and Enter', async () => {
  const member = { name: 'alpha' } as RosterRow
  $groupChats.set({ Old: { log: [], members: [], roomId: 'room-settings', watermarks: {} } })
  $botMeta.set({ alpha: { groups: ['Old'] } })
  let finish: () => void = () => undefined

  const pending = new Promise<Awaited<ReturnType<typeof botData.saveBotMeta>>>(resolve => {
    finish = () => resolve({ serverOutcome: 'persisted', serverPersisted: true })
  })

  const saveMetadata = vi.spyOn(botData, 'saveBotMeta').mockReturnValue(pending)
  const notify = vi.spyOn(host, 'notify').mockReturnValue('settings-notice')
  const onClose = vi.fn()
  render(<GroupChatSettingsDialog group="Old" members={[member]} onClose={onClose} open />)
  const name = screen.getByRole('textbox', { name: 'Group name' })
  fireEvent.change(name, { target: { value: 'New' } })
  const save = screen.getByRole('button', { name: 'Save' })
  fireEvent.click(save)

  try {
    expect((save as HTMLButtonElement).disabled).toBe(true)
    fireEvent.submit(name.closest('form')!)
    expect(saveMetadata).toHaveBeenCalledTimes(1)
    expect(notify).not.toHaveBeenCalled()
    expect(onClose).not.toHaveBeenCalled()
  } finally {
    await act(async () => finish())
    saveMetadata.mockRestore()
    notify.mockRestore()
  }

  await waitFor(() => expect(onClose).toHaveBeenCalledTimes(1))
})

it('renames membership current at Save, not the roster row snapshot from opening settings', async () => {
  const alpha = { name: 'alpha' } as RosterRow
  const beta = { name: 'beta' } as RosterRow
  $groupChats.set({ Old: { log: [], members: [], watermarks: {} } })
  $botMeta.set({ alpha: { groups: ['Old'] }, beta: { groups: [] } })

  let changeMembership = () => undefined

  function RosterSettings() {
    const [editingGroup, setEditingGroup] = useState<null | string>(null)
    const [roster, setRoster] = useState([alpha, beta])

    changeMembership = () => {
      $botMeta.set({ alpha: { groups: [] }, beta: { groups: ['Old'] } })
      setRoster([...roster])
    }

    return (
      <>
        <GroupRow
          active={false}
          group="Old"
          members={[alpha]}
          needsYou={false}
          onDisband={vi.fn()}
          onNewSection={vi.fn()}
          onOpen={vi.fn()}
          onSettings={row => setEditingGroup(row.name)}
        />
        {renderRosterDialogs({
          b: {
            group: { deleteAction: 'Delete', deleteTitle: 'Delete group' },
            bot: { deleteTitle: 'Delete bot' }
          } as unknown as Parameters<typeof renderRosterDialogs>[0]['b'],
          t: { common: { delete: 'Delete' } } as unknown as Parameters<typeof renderRosterDialogs>[0]['t'],
          createOpen: false,
          setCreateOpen: vi.fn(),
          groupCreateOpen: false,
          setGroupCreateOpen: vi.fn(),
          editing: null,
          setEditing: vi.fn(),
          deleting: null,
          setDeleting: vi.fn(),
          deletingGroup: null,
          setDeletingGroup: vi.fn(),
          editingGroup,
          setEditingGroup,
          grouping: null,
          setGrouping: vi.fn(),
          sectionDialog: null,
          setSectionDialog: vi.fn(),
          roster,
          activeSourceRoster: roster,
          refetch: vi.fn(async () => ({})) as unknown as Parameters<typeof renderRosterDialogs>[0]['refetch']
        })}
      </>
    )
  }

  render(<RosterSettings />)
  fireEvent.contextMenu(screen.getByRole('button', { name: /Old/ }))
  fireEvent.click(await screen.findByText('Group settings'))
  changeMembership()
  fireEvent.change(screen.getByRole('textbox', { name: 'Group name' }), { target: { value: 'New' } })
  fireEvent.click(screen.getByRole('button', { name: 'Save' }))

  await waitFor(() => expect($groupChats.get().New).toBeTruthy())
  await waitFor(() => expect($botMeta.get().beta.groups).toEqual(['New']))
  expect($botMeta.get().alpha.groups).toEqual([])
})

it('offers one retry for failed sync using the current name and membership', async () => {
  const members = [{ name: 'alpha' }, { name: 'beta' }, { name: 'gamma' }] as RosterRow[]
  $groupChats.set({ Old: { log: [], roomId: 'sync-settings', watermarks: {} } })
  $botMeta.set(Object.fromEntries(members.map(member => [member.name, { groups: ['Old'] }])))

  const request = vi.spyOn(host, 'request').mockImplementation(async (method, params) => {
    if (method === 'profiles.configure') {
      return { applied: { ui_meta: params?.name === 'gamma' } }
    }

    return {}
  })

  const notify = vi.spyOn(host, 'notify').mockReturnValue('sync-notice')
  const onClose = vi.fn()
  render(<GroupChatSettingsDialog group="Old" members={members} onClose={onClose} open />)
  fireEvent.change(screen.getByRole('textbox', { name: 'Group name' }), { target: { value: 'New' } })
  fireEvent.click(screen.getByRole('button', { name: 'Save' }))
  await waitFor(() => expect(onClose).toHaveBeenCalledOnce())
  const warning = notify.mock.calls.map(([message]) => message).find(message => message.kind === 'warning')!
  expect(warning.action?.label).toBe('Retry')
  expect($groupChats.get().New.roomId).toBe('sync-settings')
  expect($botMeta.get().alpha.groups).toEqual(['New'])

  // A later edit changes the name and removes beta before the user retries.
  act(() => {
    $groupChats.set({ Latest: $groupChats.get().New })
    $botMeta.set({ alpha: { groups: ['Latest', 'Other'] }, beta: { groups: [] }, gamma: { groups: ['Latest'] } })
  })
  request.mockClear()
  request.mockResolvedValue({ applied: { ui_meta: true } })
  warning.action!.onClick()
  warning.action!.onClick()
  warning.onDismiss?.()
  await waitFor(() => expect(request.mock.calls.filter(([method]) => method === 'profiles.configure')).toHaveLength(1))
  expect(request).toHaveBeenCalledWith('profiles.configure', {
    name: 'alpha',
    ui_meta: { 'hermes-bots': { groups: ['Latest', 'Other'], group: 'Latest' } }
  })
  expect($botMeta.get().beta.groups).toEqual([])
  expect($groupChats.get().New).toBeUndefined()
})

it.each([false, true])(
  'does not edit a removed or replaced group from its stale settings (replacement=%s)',
  async replaced => {
    $groupChats.set({ Planning: { log: [], roomId: 'deleted-settings', watermarks: {} } })
    $botMeta.set({})
    const notify = vi.spyOn(host, 'notify').mockReturnValue('missing-notice')
    const onClose = vi.fn()
    render(<GroupChatSettingsDialog group="Planning" onClose={onClose} open />)

    const remaining: ReturnType<typeof $groupChats.get> = replaced
      ? { Planning: { log: [], roomId: 'replacement-settings', watermarks: {} } }
      : {}

    act(() => $groupChats.set(remaining))
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))
    await waitFor(() =>
      expect(notify).toHaveBeenCalledWith(expect.objectContaining({ message: 'That group is no longer available.' }))
    )
    expect($groupChats.get()).toEqual(remaining)
    expect(onClose).not.toHaveBeenCalled()
  }
)

it('an earlier save finishing does not close or unlock another group’s pending settings', async () => {
  const member = { name: 'alpha' } as RosterRow
  $groupChats.set({
    First: { log: [], roomId: 'first-settings', watermarks: {} },
    Second: { log: [], roomId: 'second-settings', watermarks: {} }
  })
  $botMeta.set({ alpha: { groups: ['First', 'Second'] } })
  const finish: Array<() => void> = []
  vi.spyOn(botData, 'saveBotMeta').mockImplementation(
    () =>
      new Promise(resolve => {
        finish.push(() => resolve({ serverOutcome: 'persisted', serverPersisted: true }))
      })
  )
  const onClose = vi.fn()
  const view = render(<GroupChatSettingsDialog group="First" members={[member]} onClose={onClose} open />)
  fireEvent.change(screen.getByRole('textbox', { name: 'Group name' }), { target: { value: 'First renamed' } })
  fireEvent.click(screen.getByRole('button', { name: 'Save' }))
  view.rerender(<GroupChatSettingsDialog group="Second" members={[member]} onClose={onClose} open />)
  fireEvent.change(screen.getByRole('textbox', { name: 'Group name' }), { target: { value: 'Second renamed' } })
  const save = screen.getByRole('button', { name: 'Save' }) as HTMLButtonElement
  fireEvent.click(save)
  await act(async () => finish[0]())
  expect(onClose).not.toHaveBeenCalled()
  expect(save.disabled).toBe(true)
  await act(async () => finish[1]())
  await waitFor(() => expect(onClose).toHaveBeenCalledOnce())
})

it.each(['changed', 'removed'] as const)('refuses stale legacy settings after a witnessed %s group', async change => {
  $groupChats.set({ Planning: { log: [], watermarks: {} } })
  $botMeta.set({})
  const notify = vi.spyOn(host, 'notify').mockReturnValue('stale-notice')
  const onClose = vi.fn()
  render(<GroupChatSettingsDialog group="Planning" onClose={onClose} open />)
  const replacement = { log: [], watermarks: {}, image: change === 'changed' ? 'replacement.png' : null }
  act(() => {
    if (change === 'removed') {
      $groupChats.set({})
    }
    $groupChats.set({ Planning: replacement })
  })
  fireEvent.change(screen.getByRole('textbox', { name: 'Group name' }), { target: { value: 'Accidental rename' } })
  fireEvent.click(screen.getByRole('button', { name: 'Save' }))
  await waitFor(() => expect(notify).toHaveBeenCalledWith(expect.objectContaining({ kind: 'error' })))
  expect($groupChats.get()).toEqual({ Planning: replacement })
  expect(onClose).not.toHaveBeenCalled()
})

it('preserves newer image settings and allows ordinary legacy transcript updates during a rename', async () => {
  $groupChats.set({ Modern: { log: [], roomId: 'modern-dirty', watermarks: {} }, Legacy: { log: [], watermarks: {} } })
  $botMeta.set({})
  const onClose = vi.fn()
  const view = render(<GroupChatSettingsDialog group="Modern" onClose={onClose} open />)
  act(() =>
    $groupChats.set({ ...$groupChats.get(), Modern: { ...$groupChats.get().Modern, image: 'new-picture.png' } })
  )
  fireEvent.change(screen.getByRole('textbox', { name: 'Group name' }), { target: { value: 'Modern renamed' } })
  fireEvent.click(screen.getByRole('button', { name: 'Save' }))
  await waitFor(() => expect(onClose).toHaveBeenCalledOnce())
  expect($groupChats.get()['Modern renamed'].image).toBe('new-picture.png')

  view.rerender(<GroupChatSettingsDialog group="Legacy" onClose={onClose} open />)
  const message = { from: { kind: 'user' as const, name: 'You' }, text: 'A new message while settings are open', at: 1 }
  act(() => $groupChats.set({ ...$groupChats.get(), Legacy: { ...$groupChats.get().Legacy, log: [message] } }))
  fireEvent.change(screen.getByRole('textbox', { name: 'Group name' }), { target: { value: 'Legacy renamed' } })
  fireEvent.click(screen.getByRole('button', { name: 'Save' }))
  await waitFor(() => expect(onClose).toHaveBeenCalledTimes(2))
  expect($groupChats.get()['Legacy renamed'].log).toEqual([message])
  expect($groupChats.get()['Legacy renamed'].roomId).toBeUndefined()
})

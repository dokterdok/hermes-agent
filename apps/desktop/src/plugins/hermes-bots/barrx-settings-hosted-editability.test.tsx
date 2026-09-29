/**
 * C-14: hosted room settings editability.
 *
 * A rename restriction must not disable every other permitted setting.
 * Classic hold-detection and history compression stay available only where
 * they affect the room. Saves follow the room that was opened.
 */

import type * as HermesSdk from '@hermes/plugin-sdk'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import type { ComponentProps } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { translateBots } from './i18n-test-helper'
import type { GroupChat, GroupMember } from './types'

const { host } = vi.hoisted(() => ({ host: {} as Record<string, unknown> }))

vi.mock('@hermes/plugin-sdk', async importOriginal => {
  const sdk = await importOriginal<typeof HermesSdk>()
  const { pluginSdkMock } = await import('./group-test-utils')
  const base = await pluginSdkMock(host)

  return {
    ...sdk,
    ...base,
    Button: (props: ComponentProps<'button'>) => <button type={props.type || 'button'} {...props} />,
    cn: (...values: unknown[]) => values.filter(Boolean).join(' '),
    Codicon: ({ name }: { name: string }) => <span aria-hidden data-icon={name} />,
    Dialog: ({ children, open }: { children: React.ReactNode; open: boolean }) =>
      open ? <div role="dialog">{children}</div> : null,
    DialogContent: ({ children }: { children?: React.ReactNode }) => <>{children}</>,
    DialogDescription: ({ children }: { children?: React.ReactNode }) => <>{children}</>,
    DialogFooter: ({ children }: { children?: React.ReactNode }) => <>{children}</>,
    DialogHeader: ({ children }: { children?: React.ReactNode }) => <>{children}</>,
    DialogTitle: ({ children }: { children?: React.ReactNode }) => <>{children}</>,
    Input: (props: ComponentProps<'input'>) => <input {...props} />,
    Tip: ({ children }: { children?: React.ReactNode }) => <>{children}</>,
    ToggleRow: ({
      checked,
      description,
      disabled,
      label,
      onChange
    }: {
      checked: boolean
      description?: string
      disabled?: boolean
      label: string
      onChange: (on: boolean) => void
    }) => (
      <label>
        <span>{label}</span>
        <span>{description}</span>
        <input
          aria-label={label}
          checked={checked}
          disabled={disabled}
          onChange={event => onChange(event.target.checked)}
          type="checkbox"
        />
      </label>
    ),
    useI18n: () => ({ t: { common: { cancel: 'Cancel', remove: 'Remove', save: 'Save' } } }),
    usePluginI18n: () => translateBots
  }
})

vi.mock('./avatar-image', async () => {
  const { atom } = await import('nanostores')

  return {
    $imagenAvailable: atom(false),
    normalizeAvatarImage: async (value: string) => value,
    pickImageFromDevice: async () => 'data:image/png;base64,barrx-picture',
    probeImagen: () => undefined
  }
})

const MEMBER: GroupMember = { name: 'writer' }
const PICTURE = 'data:image/png;base64,barrx-picture'
const BUSY_REASON = 'The name cannot change while this Group Chat is busy.'
const READ_ONLY_REASON = 'The name cannot change while this Group Chat is read-only.'
const GONE_REASON = 'This Group Chat is no longer available. Changes were not saved.'

function hostedRoom(overrides: Partial<GroupChat> = {}): GroupChat {
  return {
    continuityMode: 'gateway',
    hosted: 'install:home',
    hostedConnectionId: 'gateway-a',
    hostedEpoch: 1,
    hostedStatus: { label: 'Ready', state: 'ready' },
    image: null,
    log: [],
    members: [MEMBER],
    roomId: 'room-1',
    watermarks: {},
    ...overrides
  }
}

function saveButton() {
  return screen.getByRole('button', { name: 'Save' }) as HTMLButtonElement
}

function nameInput() {
  return screen.getByRole('textbox', { name: 'Group name' }) as HTMLInputElement
}

function settingsForm() {
  const form = nameInput().closest('form')

  if (!form) {
    throw new Error('Group name field is not in a form')
  }

  return form
}

function describedReason(input: HTMLInputElement) {
  const id = input.getAttribute('aria-describedby') || ''

  return id ? input.ownerDocument.getElementById(id)?.textContent : ''
}

function shownPicture() {
  return screen.getByRole('dialog').querySelector('img')?.getAttribute('src')
}

beforeEach(() => {
  vi.resetModules()
  Object.assign(host, { notify: vi.fn(), notifyError: vi.fn(), request: vi.fn() })
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

async function openDialog(room: GroupChat | null, members: GroupMember[] = [MEMBER]) {
  const chat = await import('./group-chat')
  const settings = await import('./group-chat-settings')
  chat.$groupChats.set(room ? { Core: room } : {})
  const onClose = vi.fn()
  const onRenamed = vi.fn()
  const props = { group: 'Core', members, onClose, onRenamed, open: true }
  const view = render(<settings.GroupChatSettingsDialog {...props} />)

  return { chat, onClose, onRenamed, props, settings, view }
}

async function choosePicture() {
  fireEvent.click(screen.getByRole('button', { name: 'Upload' }))
  await waitFor(() => expect(shownPicture()).toBe(PICTURE))
}

describe('hosted room settings editability', () => {
  it.each([
    ['read-only', false],
    ['working', true],
    ['queued', true],
    ['sending', true],
    ['stopping', true],
    ['ready', true]
  ] as const)('saves a picture while %s blocks rename, and explains the name lock', async (state, running) => {
    const opened = await openDialog(
      hostedRoom({
        hostedStatus: { label: state, state },
        running
      })
    )

    const view = await import('./group-chat-view')
    const rename = vi.spyOn(view, 'renameGroupChat')

    expect(screen.queryByText('Detect stop directives')).toBeNull()
    expect(screen.queryByRole('button', { name: 'Compress history: writer' })).toBeNull()
    expect(saveButton().disabled).toBe(true)

    await choosePicture()

    expect(nameInput().readOnly || nameInput().disabled).toBe(true)
    expect(describedReason(nameInput())).toBe(state === 'read-only' ? READ_ONLY_REASON : BUSY_REASON)
    expect(saveButton().disabled).toBe(false)

    fireEvent.click(saveButton())
    await waitFor(() => expect(opened.chat.$groupChats.get().Core.image).toBe(PICTURE))

    expect(opened.chat.$groupChats.get().Core.roomId).toBe('room-1')
    expect(rename).not.toHaveBeenCalled()
    expect(opened.onRenamed).not.toHaveBeenCalled()
    expect(opened.onClose).toHaveBeenCalledTimes(1)
  })

  it('keeps keyboard submit on the same permission rules as Save', async () => {
    const blocked = await openDialog(hostedRoom({ hostedStatus: { label: 'Read-only', state: 'read-only' } }))

    expect(saveButton().disabled).toBe(true)
    fireEvent.submit(settingsForm())
    expect(blocked.chat.$groupChats.get().Core.image || null).toBeNull()
    expect(blocked.onClose).not.toHaveBeenCalled()

    blocked.view.unmount()
    const editable = await openDialog(hostedRoom({ hostedStatus: { label: 'Read-only', state: 'read-only' } }))
    await choosePicture()
    expect(saveButton().disabled).toBe(false)
    fireEvent.submit(settingsForm())
    await waitFor(() => expect(editable.chat.$groupChats.get().Core.image).toBe(PICTURE))
    expect(editable.onClose).toHaveBeenCalledTimes(1)
  })

  it('preserves a picture draft and a blocked rename across a busy transition', async () => {
    const opened = await openDialog(hostedRoom())
    fireEvent.change(nameInput(), { target: { value: 'Launch' } })
    await choosePicture()

    opened.chat.$groupChats.set({
      Core: hostedRoom({ hostedStatus: { label: 'Working', state: 'working' }, running: true })
    })
    opened.view.rerender(<opened.settings.GroupChatSettingsDialog {...opened.props} />)

    expect(nameInput().value).toBe('Launch')
    expect(shownPicture()).toBe(PICTURE)
    expect(describedReason(nameInput())).toBe(BUSY_REASON)
    expect(saveButton().disabled).toBe(false)

    fireEvent.click(saveButton())
    await waitFor(() => expect(opened.chat.$groupChats.get().Core.image).toBe(PICTURE))

    expect(opened.chat.$groupChats.get().Core).toBeTruthy()
    expect(Object.keys(opened.chat.$groupChats.get())).toEqual(['Core'])
    expect(opened.onClose).not.toHaveBeenCalled()
    expect(nameInput().value).toBe('Launch')

    opened.chat.$groupChats.set({
      Core: hostedRoom({ image: PICTURE, hostedStatus: { label: 'Ready', state: 'ready' }, running: false })
    })
    opened.view.rerender(<opened.settings.GroupChatSettingsDialog {...opened.props} />)

    expect(nameInput().value).toBe('Launch')
    expect(describedReason(nameInput()) || '').toBe('')
    expect(saveButton().disabled).toBe(false)
  })

  it('does not save a picture into a room that replaced the one that was opened', async () => {
    const opened = await openDialog(hostedRoom())
    await choosePicture()
    opened.chat.$groupChats.set({
      Core: hostedRoom({ image: null, roomId: 'room-2', hostedStatus: { label: 'Ready', state: 'ready' } })
    })
    opened.view.rerender(<opened.settings.GroupChatSettingsDialog {...opened.props} />)

    expect(screen.getByText(GONE_REASON)).toBeTruthy()
    expect(saveButton().disabled).toBe(true)
    expect(shownPicture()).toBe(PICTURE)

    fireEvent.submit(settingsForm())
    fireEvent.click(saveButton())

    expect(opened.chat.$groupChats.get().Core.roomId).toBe('room-2')
    expect(opened.chat.$groupChats.get().Core.image || null).toBeNull()
    expect(opened.onClose).not.toHaveBeenCalled()
    expect(host.notify).toHaveBeenCalledWith(expect.objectContaining({ kind: 'error', message: GONE_REASON }))
  })

  it('does not recreate a room that disappeared while settings were open', async () => {
    const opened = await openDialog(hostedRoom())
    await choosePicture()
    opened.chat.$groupChats.set({})
    opened.view.rerender(<opened.settings.GroupChatSettingsDialog {...opened.props} />)

    fireEvent.submit(settingsForm())

    expect(opened.chat.$groupChats.get()).toEqual({})
    expect(opened.onClose).not.toHaveBeenCalled()
    expect(screen.getByText(GONE_REASON)).toBeTruthy()
  })

  it('reports a hosted rename failure without dropping the permitted picture', async () => {
    const opened = await openDialog(hostedRoom({ hostedConnectionId: null }))
    fireEvent.change(nameInput(), { target: { value: 'Launch' } })
    await choosePicture()
    fireEvent.click(saveButton())

    await waitFor(() => expect(opened.chat.$groupChats.get().Core.image).toBe(PICTURE))
    expect(opened.chat.$groupChats.get().Core.roomId).toBe('room-1')
    expect(Object.keys(opened.chat.$groupChats.get())).toEqual(['Core'])
    expect(opened.onClose).not.toHaveBeenCalled()
    expect(opened.onRenamed).not.toHaveBeenCalled()
    expect(nameInput().value).toBe('Launch')
    expect(host.notify).toHaveBeenCalledWith(
      expect.objectContaining({ kind: 'error', message: expect.stringMatching(/Could not rename/) })
    )
  })

  it('still saves classic name, picture, and hold detection, and keeps history compression', async () => {
    const opened = await openDialog({
      holdDetection: true,
      image: null,
      log: [],
      members: [MEMBER],
      roomId: 'room-classic',
      watermarks: {}
    })

    expect(screen.getByText('Detect stop directives')).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Compress history: writer' })).toBeTruthy()
    expect(saveButton().disabled).toBe(false)

    fireEvent.change(nameInput(), { target: { value: 'Launch' } })
    fireEvent.click(screen.getByRole('checkbox', { name: 'Detect stop directives' }))
    await choosePicture()
    fireEvent.click(saveButton())

    await waitFor(() => expect(opened.onClose).toHaveBeenCalledTimes(1))
    expect(opened.chat.$groupChats.get().Core).toBeUndefined()
    expect(opened.chat.$groupChats.get().Launch).toMatchObject({
      holdDetection: false,
      image: PICTURE,
      roomId: 'room-classic'
    })
    expect(opened.onRenamed).toHaveBeenCalledWith('Launch')
  })

  it('does not submit an empty classic name from the keyboard, and still saves the picture', async () => {
    const opened = await openDialog({
      image: null,
      log: [],
      members: [MEMBER],
      roomId: 'room-classic',
      watermarks: {}
    })

    fireEvent.change(nameInput(), { target: { value: '   ' } })
    expect(saveButton().disabled).toBe(true)
    fireEvent.submit(settingsForm())
    expect(opened.onClose).not.toHaveBeenCalled()
    expect(opened.chat.$groupChats.get().Core.image || null).toBeNull()

    await choosePicture()
    expect(saveButton().disabled).toBe(false)
    fireEvent.submit(settingsForm())
    await waitFor(() => expect(opened.chat.$groupChats.get().Core.image).toBe(PICTURE))
    expect(opened.chat.$groupChats.get().Core.roomId).toBe('room-classic')
    expect(Object.keys(opened.chat.$groupChats.get())).toEqual(['Core'])
    expect(opened.onRenamed).not.toHaveBeenCalled()
  })
})

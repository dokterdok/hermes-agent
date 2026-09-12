import { webcrypto } from 'node:crypto'

import type * as HermesSdk from '@hermes/plugin-sdk'
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

import { expectDownloaded, observeDownloads } from './canonical-download-test-utils'
import { $canonicalGroupBindings } from './canonical-group-registry'
import { $botMeta } from './data'
import { $groupChats } from './group-chat'
import { GroupChatWorkspace } from './group-chat-view'
import { translateBots } from './i18n-test-helper'
import type { RetainedRoom } from './retained-group-files'
import type { GroupChat } from './types'

const request = vi.hoisted(() => vi.fn())
vi.mock('@hermes/plugin-sdk', async importOriginal => {
  const sdk = await importOriginal<typeof HermesSdk>()
  const { en } = await import('@/i18n/en')

  return {
    ...sdk,
    host: { ...sdk.host, requestProfile: request, request },
    useI18n: () => ({ locale: 'en', t: en }),
    usePluginI18n: () => translateBots
  }
})

let observed: ReturnType<typeof observeDownloads>

const fixture = (count = 1): RetainedRoom => ({
  roomId: 'retained-a',
  watermarks: {},
  members: [{ name: 'writer', connectionId: 'source-a' }],
  log: Array.from({ length: count }, (_, index) => ({
    id: `event-${index}`,
    at: 1_700_000_000_000 + index * 1000,
    from: { kind: 'member', name: 'Saved Writer', source: index % 2 ? 'Host B' : 'Host A' },
    text: `Retained message ${index}`,
    images: [{ kind: 'file', name: 'report.txt', data: `data:text/plain;base64,${btoa(String(index))}` }]
  }))
})

const install = (room: RetainedRoom) => $groupChats.set({ Workshop: room as GroupChat })
beforeEach(() => {
  request.mockReset().mockRejectedValue(new Error('No remote operation allowed'))
  $groupChats.set({})
  $canonicalGroupBindings.set({})
  $botMeta.set({ 'Saved Writer': { title: 'Wrong foreground writer' } })
  vi.stubGlobal('crypto', webcrypto)
  observed = observeDownloads()
  Element.prototype.scrollIntoView = vi.fn()
  Element.prototype.hasPointerCapture = vi.fn(() => false)
  Element.prototype.releasePointerCapture = vi.fn()
})
afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
  $groupChats.set({})
  localStorage.clear()
})

it('opens retained history read-only without capabilities, conversion, sessions or replay', () => {
  const room = { ...fixture(), hosted: 'install:old', hostedEpoch: 7, hostedConnectionId: 'original-host' }
  install(room)
  render(<GroupChatWorkspace group="Workshop" members={room.members!} />)
  expect(screen.getByText('Retained message 0')).toBeTruthy()
  expect(screen.getAllByText('Saved Writer (Host A)').length).toBeGreaterThan(0)
  expect(screen.queryByText('Wrong foreground writer')).toBeNull()
  expect(screen.getByText('original-host')).toBeTruthy()
  expect(screen.getByText('Read only')).toBeTruthy()
  expect(screen.queryByRole('textbox')).toBeNull()
  expect(screen.queryByRole('button', { name: /Send|Stop|Start gateway group|Retry|Resume/ })).toBeNull()
  expect(request).not.toHaveBeenCalled()
  expect($groupChats.get().Workshop).toBe(room)
})

it('browses newest-first snapshots, searches saved sources and saves the exact retained version', async () => {
  const room = fixture(10)
  install(room)
  render(<GroupChatWorkspace group="Workshop" members={[]} />)
  fireEvent.click(screen.getByRole('button', { name: 'Files' }))
  const dialog = screen.getByRole('dialog')
  expect(within(dialog).getAllByRole('listitem')).toHaveLength(8)
  fireEvent.click(within(dialog).getAllByRole('button', { name: 'Download: report.txt' })[0])
  await waitFor(() => expect(observed.downloads).toHaveLength(1))
  await expectDownloaded(observed, new Uint8Array([57]), 'report.txt', 'text/plain')
  await act(async () => {
    install({
      ...room,
      log: [
        ...room.log,
        {
          ...room.log[9],
          id: 'new',
          text: 'Later',
          images: [{ kind: 'file', name: 'later.txt', data: 'data:text/plain;base64,bmV3' }]
        }
      ]
    })
  })
  fireEvent.click(within(dialog).getByRole('button', { name: 'Older' }))
  expect(within(dialog).getAllByRole('listitem')).toHaveLength(2)
  fireEvent.click(within(dialog).getAllByRole('button', { name: 'Download: report.txt' })[1])
  await waitFor(() => expect(observed.downloads).toHaveLength(2))
  await expectDownloaded(observed, new Uint8Array([48]), 'report.txt', 'text/plain', 1)
  fireEvent.click(within(dialog).getByRole('button', { name: 'Newer' }))
  expect(within(dialog).queryByText('later.txt')).toBeNull()
  fireEvent.change(within(dialog).getByRole('textbox', { name: 'Search files' }), { target: { value: 'host a' } })
  expect(within(dialog).getAllByRole('listitem')).toHaveLength(5)
  fireEvent.change(within(dialog).getByRole('textbox', { name: 'Search files' }), { target: { value: '' } })
  expect(within(dialog).getByText('later.txt')).toBeTruthy()
  expect(request).not.toHaveBeenCalled()
})

it('displays metadata-only classic exports as unavailable without resolving their producer', () => {
  const room = fixture()
  room.log[0].images = [
    {
      name: 'producer.pdf',
      kind: 'pdf',
      classicExport: {
        group: 'retained-a',
        exportId: 'export-one',
        artifactId: 'artifact-one',
        installation: 'other-install',
        source: { name: 'writer', connectionId: 'other-host' },
        session: 'private-session',
        generation: 1
      }
    }
  ]
  install(room)
  render(<GroupChatWorkspace group="Workshop" members={[]} />)
  fireEvent.click(screen.getByRole('button', { name: 'Files' }))
  const dialog = screen.getByRole('dialog')
  expect((within(dialog).getByRole('button', { name: 'Download: producer.pdf' }) as HTMLButtonElement).disabled).toBe(
    true
  )
  expect(within(dialog).getByText('File bytes are not retained on this Desktop.')).toBeTruthy()
  expect(request).not.toHaveBeenCalled()
})

it.each(['room', 'source', 'hidden', 'unmount'] as const)('retires pending Save on %s change', async change => {
  const room = { ...fixture(), hosted: 'install:a', hostedConnectionId: 'source-a' }
  room.log[0].images![0].sha256 = 'a'.repeat(64)
  install(room)
  let resolve!: (buffer: ArrayBuffer) => void

  const digest = vi.spyOn(crypto.subtle, 'digest').mockImplementation(
    () =>
      new Promise<ArrayBuffer>(done => {
        resolve = done
      })
  )

  const view = render(<GroupChatWorkspace group="Workshop" members={[]} />)
  fireEvent.click(screen.getByRole('button', { name: 'Files' }))
  fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Download: report.txt' }))
  await waitFor(() => expect(digest).toHaveBeenCalledOnce())
  await act(async () => {
    if (change === 'unmount') {
      view.unmount()
    } else if (change === 'hidden') {
      view.rerender(<GroupChatWorkspace group="Workshop" members={[]} visible={false} />)
    } else {
      install({ ...room, ...(change === 'room' ? { roomId: 'replacement' } : { hostedConnectionId: 'source-b' }) })
    }
  })
  await act(async () => {
    resolve(new Uint8Array(32).fill(0xaa).buffer)
  })
  expect(observed.downloads).toHaveLength(0)

  if (change === 'room' || change === 'source') {
    expect(screen.getByText('This retained room is no longer available.')).toBeTruthy()
  }

  expect(request).not.toHaveBeenCalled()
})

it('does not rebind an older hosted cache to the same name after source replacement', async () => {
  const room = { ...fixture(), hosted: 'install:old', hostedConnectionId: 'old-source' }
  install(room)
  const view = render(<GroupChatWorkspace group="Workshop" members={[]} />)
  view.rerender(<GroupChatWorkspace group="Workshop" members={[]} visible={false} />)
  await act(async () => {
    install({ ...room, hosted: 'install:new', hostedConnectionId: 'new-source' })
  })
  view.rerender(<GroupChatWorkspace group="Workshop" members={[]} />)
  expect(screen.queryByText('Retained message 0')).toBeNull()
  expect(screen.getByText('This retained room is no longer available.')).toBeTruthy()
  expect(request).not.toHaveBeenCalled()
})

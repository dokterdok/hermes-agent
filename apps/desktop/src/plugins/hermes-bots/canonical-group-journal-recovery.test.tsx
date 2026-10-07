import type * as HermesSdk from '@hermes/plugin-sdk'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { StrictMode } from 'react'
import { afterEach, expect, it, vi } from 'vitest'

const request = vi.hoisted(() => vi.fn())
vi.mock('@hermes/plugin-sdk', async importOriginal => {
  const sdk = await importOriginal<typeof HermesSdk>()
  const { captureGroupRequests } = await import('./group-test-utils')
  const { en } = await import('@/i18n/en')

  return {
    ...sdk,
    useI18n: () => ({ locale: 'en', t: en }),
    host: { requestProfile: captureGroupRequests(request).request }
  }
})
vi.mock('./canonical-group-labels', async () => {
  const { CANONICAL_GROUP_LOCALES } = await import('./canonical-group-locales')

  return {
    useCanonicalGroupLabels: () => ({
      ...CANONICAL_GROUP_LOCALES.en,
      back: 'Back',
      refresh: 'Refresh',
      retry: 'Retry',
      send: 'Send',
      stop: 'Stop',
      download: 'Download',
      discard: 'Discard',
      cancel: 'Cancel'
    })
  }
})

import { CANONICAL_GROUP_LOCALES } from './canonical-group-locales'
import { attemptCanonicalGroupSend, prepareCanonicalGroupSend, readCanonicalGroupSend } from './canonical-group-send'
import { CanonicalGroupWorkspace } from './canonical-group-workspace'

const originalDesktop = window.hermesDesktop
const originalLocks = Object.getOwnPropertyDescriptor(navigator, 'locks')
afterEach(() => {
  cleanup()
  request.mockReset()
  window.hermesDesktop = originalDesktop
  localStorage.clear()

  if (originalLocks) {Object.defineProperty(navigator, 'locks', originalLocks)} else {Reflect.deleteProperty(navigator, 'locks')}
})

function nativeJournal() {
  const records: Record<string, string> = {}

  const read = vi.fn(async () =>
    JSON.stringify(Object.fromEntries(Object.entries(records).map(([key, value]) => [key, JSON.parse(value)])))
  )

  const compareSend = vi.fn(async (key: string, expected: string | null, value: string | null) => {
    if ((records[key] ?? null) !== expected) {
      return false
    }

    if (value === null) {
      delete records[key]
    } else {
      records[key] = value
    }

    return true
  })

  window.hermesDesktop = {
    preparedSubmissions: { owner: async () => 'journal-window', read, compareSend }
  } as unknown as typeof window.hermesDesktop
  request.mockImplementation(async (_route, method) =>
    method === 'groups.state' ? { room: { name: 'Review' }, driver_status: {} } : { events: [] }
  )

  return {
    read,
    compareSend,
    corrupt: (value: string) => read.mockImplementation(async () => value),
    repair: (value: string) => read.mockImplementation(async () => value),
    encoded: () =>
      JSON.stringify(Object.fromEntries(Object.entries(records).map(([key, value]) => [key, JSON.parse(value)])))
  }
}

function browserJournal() {
  Object.defineProperty(window, 'hermesDesktop', {configurable: true, writable: true, value: undefined})
  const compareSend = vi.fn(async (_key: string, run: () => unknown) => run())
  Object.defineProperty(navigator, 'locks', {configurable: true, value: {request: compareSend}})
  const key = 'hermes.desktop.canonicalGroupSends.v1'
  request.mockImplementation(async (_route, method) => method === 'groups.state' ? {room: {name: 'Review'}, driver_status: {}} : {events: []})

  return {compareSend, encoded: () => localStorage.getItem(key)!,
    corrupt: (value: string) => localStorage.setItem(key, value), repair: (value: string) => localStorage.setItem(key, value)}
}

it.each([{kind: 'native', keepDraft: false, corrupt: '{broken'}, {kind: 'native', keepDraft: true, corrupt: '{broken'},
  {kind: 'browser', keepDraft: true, corrupt: ''}])(
  'reloads repaired $kind storage without replay or losing an uncertain message, preserving a later draft=$keepDraft',
  async ({kind, keepDraft, corrupt}) => {
    const journal = kind === 'native' ? nativeJournal() : browserJournal()
    const binding = { connectionId: 'remote', profile: 'work', roomId: `repair-${kind}-${keepDraft}` }
    const saved = await prepareCanonicalGroupSend(binding, { text: 'Earlier uncertain message', attachments: [] })
    await attemptCanonicalGroupSend(binding, saved)
    const before = journal.encoded()
    journal.compareSend.mockClear()
    journal.corrupt(corrupt)
    render(
      <StrictMode>
        <CanonicalGroupWorkspace binding={binding} />
      </StrictMode>
    )
    await screen.findByText(CANONICAL_GROUP_LOCALES.en.journalLoadFailed)
    const editor = screen.getByRole('textbox') as HTMLTextAreaElement
    expect(editor.disabled).toBe(true)

    // Represents a late local draft restoration, which must not be overwritten by a storage read.
    if (keepDraft) {
      fireEvent.change(editor, { target: { value: 'Keep my newer draft' } })
    }

    expect(journal.encoded()).toBe(kind === 'browser' ? corrupt : before)
    journal.repair(before)
    fireEvent.click(screen.getByRole('button', { name: CANONICAL_GROUP_LOCALES.en.journalReload }))
    await waitFor(() => expect(screen.queryByText(CANONICAL_GROUP_LOCALES.en.journalLoadFailed)).toBeNull())
    await waitFor(() => expect(editor.value).toBe(keepDraft ? 'Keep my newer draft' : 'Earlier uncertain message'))
    expect((await readCanonicalGroupSend(binding))?.params.event_id).toBe(saved.params.event_id)
    expect(journal.compareSend).not.toHaveBeenCalled()
    expect(journal.encoded()).toBe(before)
    expect(request.mock.calls.some(call => call[1] === 'groups.send')).toBe(false)
  }
)

it('ignores a late journal retry after a room switch and keeps the new room draft', async () => {
  const journal = nativeJournal()
  const first = { connectionId: 'remote', profile: 'work', roomId: 'late-first' }
  const second = { ...first, roomId: 'late-second' }
  const saved = await prepareCanonicalGroupSend(first, { text: 'Do not move this message', attachments: [] })
  await attemptCanonicalGroupSend(first, saved)
  const encoded = journal.encoded()
  journal.read.mockImplementation(async () => '{broken')

  const view = render(
    <StrictMode>
      <CanonicalGroupWorkspace binding={first} />
    </StrictMode>
  )

  await screen.findByText(CANONICAL_GROUP_LOCALES.en.journalLoadFailed)
  const releases: Array<(value: string) => void> = []
  journal.read.mockImplementation(() => new Promise(resolve => releases.push(resolve)))
  fireEvent.click(screen.getByRole('button', { name: CANONICAL_GROUP_LOCALES.en.journalReload }))
  await waitFor(() => expect(releases.length).toBeGreaterThan(0))
  journal.read.mockImplementation(async () => encoded)
  view.rerender(
    <StrictMode>
      <CanonicalGroupWorkspace binding={second} />
    </StrictMode>
  )
  await waitFor(() => expect((screen.getByRole('textbox') as HTMLTextAreaElement).disabled).toBe(false))
  fireEvent.change(screen.getByRole('textbox'), { target: { value: 'New room draft' } })
  await act(async () => {
    for (const release of releases) {
      release(encoded)
    }
  })
  expect((screen.getByRole('textbox') as HTMLTextAreaElement).value).toBe('New room draft')
  expect(await readCanonicalGroupSend(second)).toBeUndefined()
  expect((await readCanonicalGroupSend(first))?.params.event_id).toBe(saved.params.event_id)
  expect(request.mock.calls.some(call => call[1] === 'groups.send')).toBe(false)
})

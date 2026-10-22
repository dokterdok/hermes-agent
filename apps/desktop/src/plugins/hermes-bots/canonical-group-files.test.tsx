import type * as HermesSdk from '@hermes/plugin-sdk'
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import type { WritableAtom } from 'nanostores'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

const { request, reader, socket } = vi.hoisted(() => ({
  request: vi.fn(), reader: { locale: 'en' as 'en' | 'fr' }, socket: {} as { gateway: WritableAtom<string> }
}))

vi.mock('@hermes/plugin-sdk', async importOriginal => {
  const sdk = await importOriginal<typeof HermesSdk>()
  const { en } = await import('@/i18n/en')
  const { fr } = await import('@/i18n/fr')
  socket.gateway = sdk.atom('open')

  return { ...sdk, host: { ...sdk.host, requestProfile: request, state: { ...sdk.host.state, gateway: socket.gateway } },
    useI18n: () => ({ locale: reader.locale, t: reader.locale === 'fr' ? fr : en }),
    usePluginI18n: () => reader.locale === 'fr' ? translateBotsIn('fr') : translateBots }
})
import { CanonicalGroupFiles, latestFileSeq } from './canonical-group-files'
import { CanonicalGroupWorkspace } from './canonical-group-workspace'
import { translateBots, translateBotsIn } from './i18n-test-helper'

const binding = { connectionId: 'local', profile: 'reviewer', roomId: 'room-1' }
const authority = { gateway_id: 'install:home', epoch: 1 }
afterEach(() => { reader.locale = 'en'; socket.gateway.set('open') })

// `main`'s gateway answer (gateway/session_group_controls.py) once the catalog is served.
const METHODS = ['groups.capabilities', 'groups.list', 'groups.state', 'groups.log', 'groups.send',
  'groups.attachment.upload', 'groups.attachment.download', 'groups.attachment.list', 'groups.stop']

const file = (seq: number, name = `file-${seq}.txt`, index = 0) => ({
  event_id: `user:${seq}`, attachment_id: `att_${(seq * 8 + index).toString(16).padStart(32, '0')}`, seq,
  manifest_index: index, kind: 'file', name, mime: 'text/plain', size: 1,
  producer: { kind: 'user', id: 'desktop', label: 'You' }, shared_at: 1_700_000_000 + seq
})

type Item = ReturnType<typeof file> | Record<string, unknown>

const page = (items: Item[], cursor: null | string = null, snapshot = 20, epoch = 1) =>
  ({ room_id: binding.roomId, authority: { ...authority, epoch }, snapshot_seq: snapshot, items, next_cursor: cursor,
    has_more: cursor !== null })

let gateway: Record<string, (params: Record<string, unknown>) => unknown>
beforeEach(() => {
  gateway = { 'groups.capabilities': () => ({ driver: true, methods: METHODS }) }
  request.mockImplementation(async (_route, method: string, params: Record<string, unknown>) => {
    if (!gateway[method]) {throw new Error(`Unexpected method ${method}`)}

    return gateway[method](params)
  })
})
afterEach(() => { cleanup(); request.mockReset(); vi.useRealTimers() })

const calls = (method: string) => request.mock.calls.filter(call => call[1] === method).map(call => call[2])
const rows = () => screen.queryAllByRole('listitem')

async function openFiles(latest = 0) {
  render(<CanonicalGroupFiles binding={binding} latest={latest} roomName="Review room" visible />)
  fireEvent.click(await screen.findByRole('button', { name: 'Files' }))

  return within(await screen.findByRole('dialog'))
}

it.each([
  ['an answer without a method list', { driver: true }],
  ['a gateway without the catalog', { driver: true, methods: METHODS.filter(method => method !== 'groups.attachment.list') }]
])('does not offer Files for %s', async (_case, answer) => {
  gateway['groups.capabilities'] = () => answer
  render(<CanonicalGroupFiles binding={binding} latest={0} roomName="Review room" visible />)
  await waitFor(() => expect(calls('groups.capabilities')).toHaveLength(1))
  await act(async () => undefined)
  expect(screen.queryByRole('button', { name: 'Files' })).toBeNull()
})

it('reads the method list again after a failed read, and only while the room is visible', async () => {
  vi.useFakeTimers()
  let attempts = 0

  gateway['groups.capabilities'] = () => {
    if (++attempts === 1) {throw new Error('socket closed')}

    return { driver: true, methods: METHODS }
  }

  const view = render(<CanonicalGroupFiles binding={binding} latest={0} roomName="Review room" visible={false} />)
  await act(async () => { await vi.advanceTimersByTimeAsync(5000) })
  expect(attempts).toBe(0)
  view.rerender(<CanonicalGroupFiles binding={binding} latest={0} roomName="Review room" visible />)
  await act(async () => { await vi.advanceTimersByTimeAsync(0) })
  expect(attempts).toBe(1)
  expect(screen.queryByRole('button', { name: 'Files' })).toBeNull()
  await act(async () => { await vi.advanceTimersByTimeAsync(2000) })
  expect(attempts).toBe(2)
  expect(screen.getByRole('button', { name: 'Files' })).toBeTruthy()
  await act(async () => { await vi.advanceTimersByTimeAsync(10_000) })
  expect(attempts).toBe(2)
})

it('browses newest first, tells same-name versions apart and pages through one snapshot', async () => {
  gateway['groups.attachment.list'] = params => params.cursor === 'after-19'
    ? page([file(12, 'older.txt')]) : page([file(20, 'same.txt'), file(19, 'same.txt')], 'after-19')
  const dialog = await openFiles(20)
  await waitFor(() => expect(rows()).toHaveLength(2))
  expect(dialog.getByText('Review room')).toBeTruthy()
  const [newest, earlier] = rows().map(row => row.getAttribute('aria-label') ?? '')
  // Same name on one page: each row shows who shared it and its own time to the second.
  expect(newest).toMatch(/^same\.txt · You · .*\d{1,2}:\d{2}:\d{2}.* · TXT · /)
  expect(newest).not.toBe(earlier)
  expect(dialog.queryByRole('button', { name: 'Show latest' })).toBeNull()
  fireEvent.click(dialog.getByRole('button', { name: 'Older files' }))
  await dialog.findByText('older.txt')
  fireEvent.click(dialog.getByRole('button', { name: 'Newer files' }))
  expect(rows()).toHaveLength(2)
  fireEvent.click(dialog.getByRole('button', { name: 'Older files' }))
  expect(dialog.getByText('older.txt')).toBeTruthy()
  expect(calls('groups.attachment.list')).toEqual([
    { room_id: 'room-1', limit: 8, profile: 'reviewer' },
    { room_id: 'room-1', limit: 8, cursor: 'after-19', profile: 'reviewer' }])
})

it('offers Show latest when the room log has a newer file than the list', async () => {
  let snapshot = 20
  gateway['groups.attachment.list'] = () => page([file(snapshot)], null, snapshot)
  const dialog = await openFiles(21)
  await dialog.findByText('file-20.txt')
  snapshot = 21
  fireEvent.click(dialog.getByRole('button', { name: 'Show latest' }))
  await dialog.findByText('file-21.txt')
  expect(dialog.queryByRole('button', { name: 'Show latest' })).toBeNull()
})

it('searches by name or sharer, ignores a superseded reply and clears the search', async () => {
  let releaseFirst!: (value: unknown) => void
  gateway['groups.attachment.list'] = params => params.query === undefined
    ? new Promise(resolve => { releaseFirst = resolve })
    : params.query === 'Builder' ? page([{ ...file(7, 'plan.md'), producer: { kind: 'member', id: 'builder', label: 'Builder' } }])
      : page([])
  const dialog = await openFiles()
  await waitFor(() => expect(calls('groups.attachment.list')).toHaveLength(1))
  fireEvent.change(dialog.getByRole('textbox', { name: 'Search files' }), { target: { value: 'Builder' } })
  await dialog.findByText('plan.md')
  expect(rows()[0].getAttribute('aria-label')).toMatch(/^plan\.md · Builder · /)
  await act(async () => releaseFirst(page([file(20, 'stale.txt')])))
  expect(dialog.queryByText('stale.txt')).toBeNull()
  fireEvent.change(dialog.getByRole('textbox', { name: 'Search files' }), { target: { value: 'nothing' } })
  await dialog.findByText('No matching files.')
  // The search field has its own clear control; this is the one the empty result offers.
  fireEvent.click(dialog.getAllByRole('button', { name: 'Clear search' }).at(-1)!)
  await waitFor(() => expect(calls('groups.attachment.list').at(-1)).toEqual({ room_id: 'room-1', limit: 8, profile: 'reviewer' }))
  expect(calls('groups.attachment.list').map(params => params.query)).toEqual([undefined, 'Builder', 'nothing', undefined])
})

it('distinguishes same-name versions shared seconds apart across pages', async () => {
  gateway['groups.attachment.list'] = params => params.cursor
    ? page([file(19, 'report.txt')]) : page([file(20, 'report.txt')], 'after-20')
  const dialog = await openFiles()
  await waitFor(() => expect(rows()).toHaveLength(1))
  const newest = rows()[0].getAttribute('aria-label')
  fireEvent.click(dialog.getByRole('button', { name: 'Older files' }))
  await waitFor(() => expect(calls('groups.attachment.list')).toHaveLength(2))
  await waitFor(() => expect(rows()[0].getAttribute('aria-label')).not.toBe(newest))
  expect(dialog.getByText('report.txt')).toBeTruthy()
})

const WITHOUT_CATALOG = METHODS.filter(method => method !== 'groups.attachment.list')

/** The room's gateway restarts (an upgrade, a downgrade): its socket closes and opens again. */
function reconnect() {
  act(() => { socket.gateway.set('closed') })
  act(() => { socket.gateway.set('open') })
}

it('offers Files once a gateway that gained the catalog reconnects', async () => {
  gateway['groups.capabilities'] = () => ({ driver: true, methods: WITHOUT_CATALOG })
  render(<CanonicalGroupFiles binding={binding} latest={0} roomName="Review room" visible />)
  await waitFor(() => expect(calls('groups.capabilities')).toHaveLength(1))
  await act(async () => undefined)
  expect(screen.queryByRole('button', { name: 'Files' })).toBeNull()
  gateway['groups.capabilities'] = () => ({ driver: true, methods: METHODS })
  reconnect()
  expect(await screen.findByRole('button', { name: 'Files' })).toBeTruthy()
})

it('stops offering Files once a gateway that lost the catalog reconnects', async () => {
  render(<CanonicalGroupFiles binding={binding} latest={0} roomName="Review room" visible />)
  await screen.findByRole('button', { name: 'Files' })
  gateway['groups.capabilities'] = () => ({ driver: true, methods: WITHOUT_CATALOG })
  reconnect()
  await waitFor(() => expect(screen.queryByRole('button', { name: 'Files' })).toBeNull())
  expect(calls('groups.capabilities')).toHaveLength(2)
})

it('drops a method list the gateway gave before it reconnected', async () => {
  let earlier!: (value: unknown) => void
  gateway['groups.capabilities'] = () => new Promise(resolve => { earlier = resolve })
  render(<CanonicalGroupFiles binding={binding} latest={0} roomName="Review room" visible />)
  await waitFor(() => expect(calls('groups.capabilities')).toHaveLength(1))
  gateway['groups.capabilities'] = () => ({ driver: true, methods: WITHOUT_CATALOG })
  reconnect()
  await act(async () => earlier({ driver: true, methods: METHODS }))
  await waitFor(() => expect(calls('groups.capabilities')).toHaveLength(2))
  await act(async () => undefined)
  expect(screen.queryByRole('button', { name: 'Files' })).toBeNull()
})

it('offers Files when the room is shown again after the gateway gained the catalog while it was hidden', async () => {
  gateway['groups.capabilities'] = () => ({ driver: true, methods: WITHOUT_CATALOG })
  const view = render(<CanonicalGroupFiles binding={binding} latest={0} roomName="Review room" visible />)
  await waitFor(() => expect(calls('groups.capabilities')).toHaveLength(1))
  view.rerender(<CanonicalGroupFiles binding={binding} latest={0} roomName="Review room" visible={false} />)
  gateway['groups.capabilities'] = () => ({ driver: true, methods: METHODS })
  reconnect()
  // Hidden: nothing is read until the room is shown.
  await act(async () => undefined)
  expect(calls('groups.capabilities')).toHaveLength(1)
  view.rerender(<CanonicalGroupFiles binding={binding} latest={0} roomName="Review room" visible />)
  expect(await screen.findByRole('button', { name: 'Files' })).toBeTruthy()
  expect(calls('groups.capabilities')).toHaveLength(2)
})

it('searches your own shares by the word Files shows for you', async () => {
  reader.locale = 'fr'
  gateway['groups.attachment.list'] = params => params.cursor ? page([file(12, 'older.txt')]) : page([file(20)], 'after-20')
  render(<CanonicalGroupFiles binding={binding} latest={0} roomName="Review room" visible />)
  fireEvent.click(await screen.findByRole('button', { name: 'Fichiers' }))
  const dialog = within(await screen.findByRole('dialog'))
  await waitFor(() => expect(rows()).toHaveLength(1))
  expect(rows()[0].getAttribute('aria-label')).toMatch(/^file-20\.txt · Vous · /)
  fireEvent.change(dialog.getByRole('textbox', { name: 'Rechercher des fichiers' }), { target: { value: 'Vous' } })
  await waitFor(() => expect(calls('groups.attachment.list')).toHaveLength(2))
  await waitFor(() => expect(rows()).toHaveLength(1))
  fireEvent.click(dialog.getByRole('button', { name: 'Fichiers plus anciens' }))
  await dialog.findByText('older.txt')
  // The first page names no search, so it needs no word for you; a search and its continuation do.
  expect(calls('groups.attachment.list')).toEqual([
    { room_id: 'room-1', limit: 8, profile: 'reviewer' },
    { room_id: 'room-1', limit: 8, query: 'Vous', you_label: 'Vous', profile: 'reviewer' },
    { room_id: 'room-1', limit: 8, cursor: 'after-20', query: 'Vous', you_label: 'Vous', profile: 'reviewer' }])
})

it('keeps an input method composing in the search field on ArrowDown', async () => {
  gateway['groups.attachment.list'] = () => page([file(20)])
  const dialog = await openFiles()
  await waitFor(() => expect(rows()).toHaveLength(1))
  const search = dialog.getByRole('textbox', { name: 'Search files' })
  fireEvent.keyDown(search, { key: 'ArrowDown', isComposing: true })
  expect(search.ownerDocument.activeElement).toBe(search)
})

it('recovers from a refused cursor by showing the latest files, and retries an unavailable gateway', async () => {
  let attempts = 0

  gateway['groups.attachment.list'] = params => {
    if (params.cursor) {throw { code: 4001, data: { reason: 'attachment_cursor_invalid' } }}

    if (++attempts === 1) {return page([file(20)], 'after-20')}

    if (attempts === 2) {throw { code: 4001, data: { reason: 'runtime_coordination_required' } }}

    return page([file(21)], null, 21)
  }

  const dialog = await openFiles()
  await dialog.findByText('file-20.txt')
  fireEvent.click(dialog.getByRole('button', { name: 'Older files' }))
  expect((await dialog.findByRole('status')).textContent).toContain('This list is out of date.')
  fireEvent.click(within(dialog.getByRole('status')).getByRole('button', { name: 'Show latest' }))
  expect(await dialog.findByText('Files are temporarily unavailable.')).toBeTruthy()
  fireEvent.click(dialog.getByRole('button', { name: 'Retry' }))
  await dialog.findByText('file-21.txt')
  expect(calls('groups.attachment.list').filter(params => !params.cursor)).toHaveLength(3)
})

it.each([
  ['snapshot', page([file(12)], null, 21)],
  ['authority', page([file(12)], null, 20, 2)],
  ['order', page([file(20)])],
  ['cursor', page([file(12)], 'after-20')]
])('refuses an Older page that changes its %s', async (_case, older) => {
  gateway['groups.attachment.list'] = params => params.cursor ? older : page([file(20)], 'after-20')
  const dialog = await openFiles()
  await dialog.findByText('file-20.txt')
  fireEvent.click(dialog.getByRole('button', { name: 'Older files' }))
  expect((await dialog.findByRole('status')).textContent).toContain('This list is out of date.')
  expect(dialog.queryByText('file-12.txt')).toBeNull()
})

it.each([
  ['order', [file(19), file(20)]],
  ['date', [{ ...file(19), shared_at: 1e30 }]],
  ['sharer', [{ ...file(19), producer: { kind: 'robot', id: 'x', label: 'X' } }]],
  ['version', [{ ...file(19), attachment_id: '' }]]
])('refuses a page with unusable %s data without breaking Files', async (_case, items) => {
  gateway['groups.attachment.list'] = () => page(items)
  const dialog = await openFiles()
  expect(await dialog.findByText('Files could not be loaded.')).toBeTruthy()
  expect(rows()).toHaveLength(0)
  expect(dialog.getByRole('button', { name: 'Retry' })).toBeTruthy()
})

it.each(['permission_denied', 'room_history_expired'])('has nothing to retry when the gateway answers %s', async reason => {
  gateway['groups.attachment.list'] = () => { throw { code: 4001, data: { reason } } }
  const dialog = await openFiles()
  expect(await dialog.findByText('Files are unavailable for this Group Chat.')).toBeTruthy()
  expect(dialog.queryByRole('button', { name: 'Retry' })).toBeNull()
})

it.each([
  ['storage_unavailable', { code: 5001, data: { reason: 'storage_unavailable' } }],
  ['method not found', { code: -32601, message: 'Method not found' }]
])('offers Retry when the catalog is temporarily unavailable (%s)', async (_case, refusal) => {
  gateway['groups.attachment.list'] = () => { throw refusal }
  const dialog = await openFiles()
  expect(await dialog.findByText('Files are temporarily unavailable.')).toBeTruthy()
  expect(dialog.getByRole('button', { name: 'Retry' })).toBeTruthy()
})

it('fails a list the gateway never answers instead of spinning', async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true })
  gateway['groups.attachment.list'] = () => new Promise(() => undefined)
  const dialog = await openFiles()
  await act(async () => { await vi.advanceTimersByTimeAsync(10_000) })
  expect(await dialog.findByText('Files are temporarily unavailable.')).toBeTruthy()
})

it('moves between rows with the arrow keys and focuses search with /', async () => {
  gateway['groups.attachment.list'] = () => page([file(20), file(19)])
  const dialog = await openFiles()
  await waitFor(() => expect(rows()).toHaveLength(2))
  const search = dialog.getByRole('textbox', { name: 'Search files' })
  const focused = () => search.ownerDocument.activeElement
  expect(focused()).toBe(search)
  fireEvent.keyDown(search, { key: 'ArrowDown' })
  expect(focused()).toBe(rows()[0])
  fireEvent.keyDown(rows()[0], { key: 'ArrowDown' })
  expect(focused()).toBe(rows()[1])
  fireEvent.keyDown(rows()[1], { key: '/' })
  expect(focused()).toBe(search)
})

it('closes Files and retires its reads when the room is hidden', async () => {
  let release!: (value: unknown) => void
  gateway['groups.attachment.list'] = () => new Promise(resolve => { release = resolve })
  const view = render(<CanonicalGroupFiles binding={binding} latest={0} roomName="Review room" visible />)
  fireEvent.click(await screen.findByRole('button', { name: 'Files' }))
  await waitFor(() => expect(calls('groups.attachment.list')).toHaveLength(1))
  view.rerender(<CanonicalGroupFiles binding={binding} latest={0} roomName="Review room" visible={false} />)
  expect(screen.queryByRole('dialog')).toBeNull()
  await act(async () => release(page([file(20)])))
  view.rerender(<CanonicalGroupFiles binding={binding} latest={0} roomName="Review room" visible />)
  expect(screen.queryByRole('dialog')).toBeNull()
  expect(screen.getByRole('button', { name: 'Files' })).toBeTruthy()
  expect(calls('groups.capabilities')).toHaveLength(1)
})

it('counts only file-bearing messages as the newest shared file', () => {
  expect(latestFileSeq([
    { seq: 3, kind: 'message.user', payload: { attachments: [{ kind: 'file', name: 'a.txt', mime: 'text/plain' }] } },
    { seq: 4, kind: 'message.user', payload: { text: 'just text' } },
    { seq: 5, kind: 'message.member', payload: { attachments: [] } }
  ])).toBe(3)
})

it('offers Files in the gateway room header and tells it the newest shared file', async () => {
  gateway['groups.state'] = () => ({ room: { room_id: binding.roomId, name: 'Review room', members: [] }, driver_status: {} })
  gateway['groups.log'] = () => ({ events: [
    { seq: 21, event_id: 'user:21', room_id: binding.roomId, kind: 'message.user',
      payload: { text: 'files', attachments: [{ attachment_id: file(21).attachment_id, kind: 'file', name: 'a.txt', mime: 'text/plain', size: 1 }] } }] })
  gateway['groups.attachment.list'] = () => page([file(20)])
  render(<CanonicalGroupWorkspace binding={binding} />)
  fireEvent.click(await screen.findByRole('button', { name: 'Files' }))
  const dialog = within(await screen.findByRole('dialog'))
  await dialog.findByText('file-20.txt')
  expect(dialog.getByText('Review room')).toBeTruthy()
  expect(dialog.getByRole('button', { name: 'Show latest' })).toBeTruthy()
})

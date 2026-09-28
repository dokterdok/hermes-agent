import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import type { ComponentProps, ReactNode } from 'react'
import { afterEach, expect, it, vi } from 'vitest'

import { expectDownloaded, observeDownloads } from './canonical-download-test-utils'

const request = vi.hoisted(() => vi.fn())
vi.mock('@hermes/plugin-sdk', async () => {
  const { en } = await import('@/i18n/en')

  return { host: { requestProfile: request }, useI18n: () => ({ locale: 'en', t: en }),
    Button: (props: ComponentProps<'button'>) => <button {...props} />,
    Codicon: () => <span />, Tip: ({ children }: { children: ReactNode }) => <>{children}</> }
})
vi.mock('./canonical-group-labels', async () => {
  const { CANONICAL_GROUP_LOCALES } = await import('./canonical-group-locales')

  return { useCanonicalGroupLabels: () => ({ ...CANONICAL_GROUP_LOCALES.en, back: 'Back', refresh: 'Refresh', retry: 'Retry',
    send: 'Send', stop: 'Stop', download: 'Download', discard: 'Discard', cancel: 'Cancel' }) }
})

import { CanonicalGroupWorkspace } from './canonical-group-workspace'

const roomId = 'room-one'
const profile = 'reviewer'
const original = { connectionId: 'original-owner', profile, roomId }
const fresh = { connectionId: 'fresh-client', profile, roomId }
const members = [
  { member_id: 'one', profile: 'default', handle: 'one', display_name: 'One' },
  { member_id: 'two', profile: 'two', handle: 'two', display_name: 'Two' }
]
const pixel = { kind: 'file', name: 'pixel.png', mime: 'image/png', size: 1 }
const events = [
  { seq: 1, room_id: roomId, event_id: 'file-v1', kind: 'message.user', actor: { member_id: 'one' },
    payload: { text: 'FILE_V1', attachments: [{ ...pixel, attachment_id: 'att-v1' }] } },
  { seq: 2, room_id: roomId, event_id: 'file-v2', kind: 'message.user', actor: { member_id: 'one' },
    payload: { text: 'FILE_V2', attachments: [{ ...pixel, attachment_id: 'att-v2' }] } }
]
const bytes: Record<string, string> = { 'att-v1': 'QQ==', 'att-v2': 'Qg==' }
const originalDesktop = window.hermesDesktop

afterEach(() => { cleanup(); request.mockReset(); vi.restoreAllMocks(); vi.unstubAllGlobals(); localStorage.clear(); window.hermesDesktop = originalDesktop })

it('a fresh connection recovers member order and same-name file versions by attachment id', async () => {
  const observed = observeDownloads()
  window.hermesDesktop = { saveImageBuffer: vi.fn() } as unknown as typeof window.hermesDesktop
  request.mockImplementation(async (route, method, params) => {
    if (method === 'groups.state') {return { room: { name: 'Room', members }, driver_status: {} }}

    if (method === 'groups.log') {return { events }}

    if (method === 'groups.attachment.download') {
      return { ...pixel, attachment_id: params.attachment_id, event_id: params.event_id, data_base64: bytes[params.attachment_id] }
    }

    throw new Error(`Unexpected method ${method} on ${route.connectionId}`)
  })

  const closed = render(<CanonicalGroupWorkspace binding={original} />)
  await waitFor(() => expect(within(screen.getByRole('list', { name: 'Members' })).getAllByRole('listitem').map(item => item.textContent)).toEqual([
    'One (default)', 'Two (two)'
  ]))
  closed.unmount()

  render(<CanonicalGroupWorkspace binding={fresh} />)
  const roster = await screen.findByRole('list', { name: 'Members' })
  expect(within(roster).getAllByRole('listitem').map(item => item.textContent)).toEqual(['One (default)', 'Two (two)'])
  const history = within(screen.getByRole('log'))
  await waitFor(() => expect(history.getByText('FILE_V2')).toBeTruthy())
  expect(history.getByText('FILE_V1').compareDocumentPosition(history.getByText('FILE_V2')) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  const downloads = history.getAllByRole('button', { name: 'Download' })
  expect(downloads).toHaveLength(2)
  fireEvent.click(downloads[0])
  await waitFor(() => expect(observed.downloads).toHaveLength(1))
  fireEvent.click(downloads[1])
  await waitFor(() => expect(observed.downloads).toHaveLength(2))
  await expectDownloaded(observed, new Uint8Array([65]), 'pixel.png', 'image/png', 0)
  await expectDownloaded(observed, new Uint8Array([66]), 'pixel.png', 'image/png', 1)
  const reads = request.mock.calls.filter(call => call[1] === 'groups.attachment.download' && call[0].connectionId === fresh.connectionId)
  expect(reads.map(call => call[2])).toEqual([
    { profile, room_id: roomId, event_id: 'file-v1', attachment_id: 'att-v1' },
    { profile, room_id: roomId, event_id: 'file-v2', attachment_id: 'att-v2' }
  ])
  expect(request.mock.calls.some(call => call[0].connectionId === fresh.connectionId && call[1] === 'groups.state')).toBe(true)
  expect(request.mock.calls.some(call => ['groups.approve', 'groups.deny'].includes(call[1]))).toBe(false)
})

it('a fresh connection pages the room log and keeps each file version', async () => {
  const observed = observeDownloads()
  window.hermesDesktop = { saveImageBuffer: vi.fn() } as unknown as typeof window.hermesDesktop
  request.mockImplementation(async (route, method, params) => {
    if (method === 'groups.state') {return { room: { name: 'Room', members }, driver_status: {} }}

    if (method === 'groups.log') {
      expect(route.connectionId).toBe(fresh.connectionId)
      expect(params).toMatchObject({ profile, room_id: roomId, limit: 100 })
      if (params.since_seq === 0) {return { events: [events[0]], has_more: true }}
      expect(params.since_seq).toBe(1)

      return { events: [events[1]], has_more: false }
    }

    if (method === 'groups.attachment.download') {
      return { ...pixel, attachment_id: params.attachment_id, event_id: params.event_id, data_base64: bytes[params.attachment_id] }
    }

    throw new Error(`Unexpected method ${method}`)
  })
  render(<CanonicalGroupWorkspace binding={fresh} />)
  const history = within(screen.getByRole('log'))
  await waitFor(() => expect(history.getByText('FILE_V2')).toBeTruthy())
  expect(history.getByText('FILE_V1').compareDocumentPosition(history.getByText('FILE_V2')) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  const cursors = request.mock.calls.filter(call => call[1] === 'groups.log').map(call => call[2].since_seq)
  expect(cursors).toContain(0)
  expect(cursors).toContain(1)
  const downloads = history.getAllByRole('button', { name: 'Download' })
  fireEvent.click(downloads[0])
  await waitFor(() => expect(observed.downloads).toHaveLength(1))
  fireEvent.click(downloads[1])
  await waitFor(() => expect(observed.downloads).toHaveLength(2))
  await expectDownloaded(observed, new Uint8Array([65]), 'pixel.png', 'image/png', 0)
  await expectDownloaded(observed, new Uint8Array([66]), 'pixel.png', 'image/png', 1)
})

it('a fresh connection does not publish a log page whose cursor does not advance', async () => {
  request.mockImplementation(async (_route, method) => {
    if (method === 'groups.state') {return { room: { name: 'Room', members }, driver_status: {} }}

    if (method === 'groups.log') {return { events: [events[0]], has_more: true }}

    throw new Error(`Unexpected method ${method}`)
  })
  render(<CanonicalGroupWorkspace binding={fresh} />)
  expect((await screen.findByRole('alert')).textContent).toContain('Invalid room log cursor')
  expect(screen.queryByText('FILE_V1')).toBeNull()
  expect(screen.queryByRole('list', { name: 'Members' })).toBeNull()
  expect(request.mock.calls.some(call => call[1] === 'groups.attachment.download')).toBe(false)
  expect(request.mock.calls.some(call => ['groups.approve', 'groups.deny'].includes(call[1]))).toBe(false)
})

it('a fresh connection on another room downloads only that room version', async () => {
  const observed = observeDownloads()
  window.hermesDesktop = { saveImageBuffer: vi.fn() } as unknown as typeof window.hermesDesktop
  const other = { connectionId: 'fresh-client', profile, roomId: 'room-two' }
  const otherMembers = [{ member_id: 'three', profile: 'three', handle: 'three', display_name: 'Three' }]
  const otherEvent = { seq: 1, room_id: other.roomId, event_id: 'file-other', kind: 'message.user', actor: { member_id: 'three' },
    payload: { text: 'FILE_OTHER', attachments: [{ ...pixel, attachment_id: 'att-other' }] } }
  request.mockImplementation(async (route, method, params) => {
    const room = params?.room_id

    if (method === 'groups.state') {
      return room === other.roomId
        ? { room: { name: 'Other', members: otherMembers }, driver_status: {} }
        : { room: { name: 'Room', members }, driver_status: {} }
    }

    if (method === 'groups.log') {return { events: room === other.roomId ? [otherEvent] : events, has_more: false }}

    if (method === 'groups.attachment.download') {
      const id = params.attachment_id

      return { ...pixel, attachment_id: id, event_id: params.event_id, data_base64: id === 'att-other' ? 'Qw==' : bytes[id] }
    }

    throw new Error(`Unexpected method ${method} on ${route.connectionId}`)
  })
  const first = render(<CanonicalGroupWorkspace binding={original} />)
  await screen.findByText('FILE_V1')
  first.unmount()
  render(<CanonicalGroupWorkspace binding={other} />)
  const roster = await screen.findByRole('list', { name: 'Members' })
  expect(within(roster).getAllByRole('listitem').map(item => item.textContent)).toEqual(['Three (three)'])
  expect(screen.queryByText('FILE_V1')).toBeNull()
  const history = within(screen.getByRole('log'))
  await waitFor(() => expect(history.getByText('FILE_OTHER')).toBeTruthy())
  fireEvent.click(history.getByRole('button', { name: 'Download' }))
  await waitFor(() => expect(observed.downloads).toHaveLength(1))
  await expectDownloaded(observed, new Uint8Array([67]), 'pixel.png', 'image/png')
  const reads = request.mock.calls.filter(call => call[1] === 'groups.attachment.download')
  expect(reads).toHaveLength(1)
  expect(reads[0][0]).toMatchObject({ connectionId: other.connectionId, targetProfile: profile })
  expect(reads[0][2]).toEqual({ profile, room_id: other.roomId, event_id: 'file-other', attachment_id: 'att-other' })
  expect(request.mock.calls.some(call => ['groups.approve', 'groups.deny'].includes(call[1]))).toBe(false)
})

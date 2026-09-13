import type * as HermesSdk from '@hermes/plugin-sdk'
import { host } from '@hermes/plugin-sdk'
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import type { WritableAtom } from 'nanostores'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({ request: vi.fn(), epoch: 1, locale: 'en' }))
vi.mock('@hermes/plugin-sdk', async importOriginal => {
  const sdk = await importOriginal<typeof HermesSdk>()
  const { en } = await import('@/i18n/en')

  return { ...sdk, gatewayActivationEpoch: () => mocks.epoch, useI18n: () => ({ locale: mocks.locale, t: en }),
    host: { ...sdk.host, requestProfile: mocks.request, state: { ...sdk.host.state,
      connectionId: sdk.atom<string | null>('owner-a'), profile: sdk.atom('default'), gateway: sdk.atom('open') } } }
})
vi.mock('./canonical-group-labels', async () => {
  const { CANONICAL_GROUP_LOCALES } = await import('./canonical-group-locales')

  return { useCanonicalGroupLabels: () => ({ ...CANONICAL_GROUP_LOCALES[mocks.locale as keyof typeof CANONICAL_GROUP_LOCALES], refresh: 'Refresh' }) }
})

import { $canonicalGroupBindings, $canonicalGroupNames } from './canonical-group-registry'
import { parseSavedGroupPage, parseSavedGroupPreview } from './canonical-saved-group-client'
import { SAVED_GROUP_LOCALES } from './canonical-saved-group-locales'
import actualWire from './canonical-saved-group-wire.fixture.json'
import { CanonicalSavedGroups } from './canonical-saved-groups'

const state = {
  connection: host.state.connectionId as WritableAtom<string | null>,
  profile: host.state.profile as WritableAtom<string>, gateway: host.state.gateway as WritableAtom<string>
}

const capabilities = { authority_gateway_id: 'install:holder', methods: ['groups.recovery.list', 'groups.recovery.prepare'] }

const copy = (roomId = 'a-room', name = 'Planning') => ({
  room_id: roomId, name, source_authority: { gateway_id: 'install:original', epoch: 1 },
  saved_through_seq: 12, advertised_latest_seq: 15, copy_updated_at: 1789290000,
  group_ended: false, copy_status: 'saved'
})

const page = (copies = [copy()], nextRoomId: string | null = null) => ({
  object: 'hermes.group_recovery.copies', copies, next_room_id: nextRoomId,
  accepted_tail: 'unverified', execution_authorized: false, target_gateway_id: 'install:holder'
})

const preview = (roomId = 'a-room', name = 'Planning details') => ({
  object: 'hermes.group_recovery.preview', room_id: roomId, name,
  source_authority: { gateway_id: 'install:original', epoch: 1 }, target_gateway_id: 'install:holder',
  saved_through_seq: 12, advertised_latest_seq: 15, copy_updated_at: 1789290000,
  accepted_tail: 'unverified', execution_authorized: false, reconciliation_required: true, blockers: [],
  work_records: { availability: 'available', source_loss_safe: false, incompleteness: [],
    task_count: 2, receipt_count: 1, tasks: [{ task_id: 'PRIVATE-TASK', prompt: 'PRIVATE-PROMPT' }],
    receipts: [{ session_id: 'PRIVATE-SESSION' }] }
})

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (error: unknown) => void
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no })

  return { promise, resolve, reject }
}

beforeEach(() => {
  mocks.request.mockReset()
  mocks.epoch = 1
  mocks.locale = 'en'
  state.connection.set('owner-a'); state.profile.set('default'); state.gateway.set('open')
  $canonicalGroupBindings.set({}); $canonicalGroupNames.set({})
  mocks.request.mockImplementation(async (_route, method, params) => {
    if (method === 'groups.capabilities') {return capabilities}

    if (method === 'groups.recovery.list') {return page()}

    if (method === 'groups.recovery.prepare') {return preview(params.room_id, params.room_id === 'b-room' ? 'Review details' : 'Planning details')}

    throw new Error(`Unexpected RPC ${method}`)
  })
})

afterEach(() => {
  cleanup()
  expect(mocks.request.mock.calls.every(call => ['groups.capabilities', 'groups.recovery.list', 'groups.recovery.prepare'].includes(call[1]))).toBe(true)
  expect($canonicalGroupBindings.get()).toEqual({})
  expect($canonicalGroupNames.get()).toEqual({})
  vi.restoreAllMocks()
})

async function openBrowser() {
  render(<CanonicalSavedGroups />)
  await waitFor(() => expect(mocks.request.mock.calls.some(call => call[1] === 'groups.recovery.list')).toBe(true))
  fireEvent.click(screen.getByRole('button', { name: SAVED_GROUP_LOCALES[mocks.locale as keyof typeof SAVED_GROUP_LOCALES].viewSavedCopies }))

  return screen.findByRole('dialog')
}

it('only previews an explicitly selected saved copy and displays metadata without IDs or private content', async () => {
  const dialog = await openBrowser()
  const row = await within(dialog).findByRole('button', { name: /Planning/ })
  expect(mocks.request.mock.calls.filter(call => call[1] === 'groups.recovery.prepare')).toHaveLength(0)
  expect(screen.queryByRole('textbox')).toBeNull()
  fireEvent.click(row)
  const details = await screen.findByRole('region', { name: 'Saved copy details' })
  await within(details).findByText('Planning details')
  expect(within(dialog).getByText('Work has not been resumed. Recent work may be missing.')).toBeTruthy()
  expect(within(details).getByText('Some recent activity is missing from this copy.')).toBeTruthy()
  expect(within(details).getByText('Tasks in this work record')).toBeTruthy()
  expect(within(details).getByText('2')).toBeTruthy()
  expect(within(details).getByText('1')).toBeTruthy()

  for (const secret of ['install:', 'a-room', 'PRIVATE-', 'accepted_tail', 'execution_authorized']) {
    expect(dialog.textContent).not.toContain(secret)
  }

  expect(within(dialog).queryByRole('button', { name: /Continue|Send|Stop|Retry|Discard|Reconnect|Attach|Promote/i })).toBeNull()
  const read = mocks.request.mock.calls.find(call => call[1] === 'groups.recovery.prepare')!
  expect(read[0]).toMatchObject({ connectionId: 'owner-a', profile: 'default', targetProfile: 'default' })
  expect(read[2]).toEqual({ profile: 'default', room_id: 'a-room' })
})

it('uses exact keyset cursors forward/back without interpreting sequence numbers as messages', async () => {
  const original = mocks.request.getMockImplementation()!
  mocks.request.mockImplementation((route, method, params) => method === 'groups.recovery.list'
    ? Promise.resolve(params.after_room_id === null ? page([copy()], 'a-room') : page([copy('b-room', 'Review')]))
    : original(route, method, params))
  await openBrowser()
  await screen.findByRole('button', { name: /Planning/ })
  fireEvent.click(screen.getByRole('button', { name: 'More saved copies' }))
  await screen.findByRole('button', { name: /Review/ })
  expect(screen.queryByRole('button', { name: /Planning/ })).toBeNull()
  expect((screen.getByRole('button', { name: 'More saved copies' }) as HTMLButtonElement).disabled).toBe(true)
  fireEvent.click(screen.getByRole('button', { name: 'Previous saved copies' }))
  await screen.findByRole('button', { name: /Planning/ })
  expect(mocks.request.mock.calls.filter(call => call[1] === 'groups.recovery.list').map(call => call[2])).toEqual([
    { limit: 20, after_room_id: null, profile: 'default' }, { limit: 20, after_room_id: null, profile: 'default' },
    { limit: 20, after_room_id: 'a-room', profile: 'default' }, { limit: 20, after_room_id: null, profile: 'default' }
  ])
  expect(mocks.request.mock.calls.some(call => call[1] === 'groups.recovery.prepare')).toBe(false)
})

it('does not adopt a replaced selection or a late permission failure from that selection', async () => {
  const first = deferred<unknown>()
  const original = mocks.request.getMockImplementation()!
  mocks.request.mockImplementation((route, method, params) => {
    if (method === 'groups.recovery.list') {return Promise.resolve(page([copy(), copy('b-room', 'Review')]))}

    if (method === 'groups.recovery.prepare' && params.room_id === 'a-room') {return first.promise}

    return original(route, method, params)
  })
  await openBrowser()
  fireEvent.click(await screen.findByRole('button', { name: /Planning/ }))
  fireEvent.click(screen.getByRole('button', { name: /Review/ }))
  await screen.findByText('Review details')
  await act(async () => { first.reject({ message: 'permission_denied' }) })
  expect(screen.queryByRole('alert')).toBeNull()
  expect(screen.getByText('Review details')).toBeTruthy()
})

it('clears disclosed metadata on a current permission denial and rejects an older pending reply', async () => {
  const first = deferred<unknown>()
  const original = mocks.request.getMockImplementation()!
  mocks.request.mockImplementation((route, method, params) => {
    if (method === 'groups.recovery.list') {return Promise.resolve(page([copy(), copy('b-room', 'Review')]))}

    if (method === 'groups.recovery.prepare') {return params.room_id === 'a-room' ? first.promise : Promise.reject({ message: 'permission_denied' })}

    return original(route, method, params)
  })
  await openBrowser()
  fireEvent.click(await screen.findByRole('button', { name: /Planning/ }))
  fireEvent.click(screen.getByRole('button', { name: /Review/ }))
  await screen.findByText(SAVED_GROUP_LOCALES.en.savedCopiesDenied)
  await act(async () => { first.resolve(preview('a-room', 'Old private reply')) })
  expect(screen.queryByText('Old private reply')).toBeNull()
  expect(screen.queryByRole('button', { name: /Planning|Review/ })).toBeNull()
  expect(screen.queryByText(SAVED_GROUP_LOCALES.en.savedCopiesEmpty)).toBeNull()
})

it.each(['connection', 'profile', 'aba', 'activation', 'gateway', 'close'] as const)('refuses a preview after %s changes', async change => {
  const pending = deferred<unknown>()
  const original = mocks.request.getMockImplementation()!
  mocks.request.mockImplementation((route, method, params) => method === 'groups.recovery.prepare'
    ? pending.promise : original(route, method, params))
  await openBrowser()
  fireEvent.click(await screen.findByRole('button', { name: /Planning/ }))
  await act(async () => {
    if (change === 'connection') {state.connection.set('owner-b')}

    if (change === 'profile') {state.profile.set('other')}

    if (change === 'aba') {state.connection.set('owner-b'); mocks.epoch++; state.connection.set('owner-a')}

    if (change === 'activation') {mocks.epoch++}

    if (change === 'gateway') {state.gateway.set('closed')}

    if (change === 'close') {fireEvent.click(screen.getByRole('button', { name: 'Close' }))}
    pending.resolve(preview('a-room', 'Late private detail'))
  })
  expect(screen.queryByText('Late private detail')).toBeNull()
  expect(mocks.request.mock.calls.filter(call => call[1] === 'groups.recovery.prepare')).toHaveLength(1)
})

it('rejects stale paging after refresh and keeps the new page', async () => {
  const pending = deferred<unknown>()
  const original = mocks.request.getMockImplementation()!
  mocks.request.mockImplementation((route, method, params) => method === 'groups.recovery.list'
    ? params.after_room_id ? pending.promise : Promise.resolve(page([copy()], 'a-room'))
    : original(route, method, params))
  await openBrowser()
  await screen.findByRole('button', { name: /Planning/ })
  fireEvent.click(screen.getByRole('button', { name: 'More saved copies' }))
  fireEvent.click(screen.getByRole('button', { name: 'Refresh' }))
  await screen.findByRole('button', { name: /Planning/ })
  await act(async () => { pending.resolve(page([copy('b-room', 'Stale next page')])) })
  expect(screen.queryByText('Stale next page')).toBeNull()
})

it('shows disconnected and empty states without implying that work has resumed', async () => {
  const original = mocks.request.getMockImplementation()!
  mocks.request.mockImplementation((route, method, params) => method === 'groups.recovery.list' ? Promise.resolve(page([])) : original(route, method, params))
  await openBrowser()
  await screen.findByText(SAVED_GROUP_LOCALES.en.savedCopiesEmpty)
  act(() => { state.gateway.set('closed') })
  expect(screen.getByText(SAVED_GROUP_LOCALES.en.savedCopiesOffline)).toBeTruthy()
  expect(screen.queryByText(SAVED_GROUP_LOCALES.en.savedCopiesEmpty)).toBeNull()
  expect(screen.queryByRole('region', { name: 'Saved copy details' })).toBeNull()
})

it.each(['unsupported', 'denied'] as const)('does not show an initial %s affordance', async kind => {
  mocks.request.mockImplementation(async (_route, method) => {
    if (kind === 'unsupported') {return { methods: [] }}

    if (method === 'groups.capabilities') {return capabilities}
    throw { message: 'permission_denied' }
  })
  render(<CanonicalSavedGroups />)
  await waitFor(() => expect(screen.queryByRole('button', { name: 'View saved copies' })).toBeNull())
  expect(screen.queryByText(SAVED_GROUP_LOCALES.en.savedCopiesEmpty)).toBeNull()
})

it('shows a read failure rather than an empty store or a raw server message', async () => {
  await openBrowser()
  await screen.findByRole('button', { name: /Planning/ })
  mocks.request.mockRejectedValue({ message: 'PRIVATE-DATABASE-DETAIL' })
  fireEvent.click(screen.getByRole('button', { name: 'Refresh' }))
  await screen.findByText(SAVED_GROUP_LOCALES.en.savedCopiesFailed)
  expect(screen.queryByText('PRIVATE-DATABASE-DETAIL')).toBeNull()
  expect(screen.queryByText(SAVED_GROUP_LOCALES.en.savedCopiesEmpty)).toBeNull()
})

it.each(Object.keys(SAVED_GROUP_LOCALES) as Array<keyof typeof SAVED_GROUP_LOCALES>)('renders the readonly copy in locale %s', async locale => {
  mocks.locale = locale
  const dialog = await openBrowser()
  const labels = SAVED_GROUP_LOCALES[locale]
  expect(within(dialog).getByText(`${labels.savedCopiesNotResumed} ${labels.savedCopiesRecentWork}`)).toBeTruthy()
  expect(await within(dialog).findByRole('button', { name: /Planning/ })).toBeTruthy()
  expect(within(dialog).getByText(labels.savedCopyPartial)).toBeTruthy()
})

it.each([
  { execution_authorized: true }, { execution_authorized: undefined }, { accepted_tail: 'verified' },
  { target_gateway_id: undefined },
  { next_room_id: 'missing' }, { next_room_id: undefined }, { copies: [copy(), copy()] },
  { copies: [copy('b-room'), copy('a-room')] }, { copies: [{ ...copy(), group_ended: 'false' }] },
  { copies: [{ ...copy(), copy_updated_at: Infinity }] }
])('rejects malformed or executing saved-copy metadata %j', override => {
  expect(() => parseSavedGroupPage({ ...page(), ...override }, null)).toThrow()
})

it('rejects backward keysets and accepts SQLite code-point ordering for non-ASCII IDs', () => {
  expect(() => parseSavedGroupPage(page([copy('a-room')]), 'b-room')).toThrow()
  expect(parseSavedGroupPage(page([copy('\ue000'), copy('\u{10000}')]), null).copies).toHaveLength(2)
})

it.each([
  { execution_authorized: true }, { accepted_tail: 'verified' }, { room_id: 'another-room' },
  { target_gateway_id: 'install:another-holder' }, { source_authority: { gateway_id: 'install:original', epoch: 2 } },
  { work_records: { ...preview().work_records, source_loss_safe: true } }
])('rejects mismatched or executable preview evidence %j', override => {
  const selected = parseSavedGroupPage(page(), null).copies[0]
  expect(() => parseSavedGroupPreview({ ...preview(), ...override }, selected, 'install:holder')).toThrow()
})

it('consumes the actual temporary Python native-handler wire without promoting missing work into safety', async () => {
  mocks.request.mockImplementation(async (_route, method) => {
    if (method === 'groups.capabilities') {return { ...capabilities, authority_gateway_id: actualWire.page.target_gateway_id }}

    if (method === 'groups.recovery.list') {return actualWire.page}

    if (method === 'groups.recovery.prepare') {return actualWire.preview}

    throw new Error(`Unexpected RPC ${method}`)
  })
  const dialog = await openBrowser()
  fireEvent.click(await screen.findByRole('button', { name: /Weekly planning/ }))
  await screen.findByText(SAVED_GROUP_LOCALES.en.savedWorkUnknown)
  expect(screen.getByText(SAVED_GROUP_LOCALES.en.savedWorkReconciliation)).toBeTruthy()
  expect(dialog.textContent).toContain(SAVED_GROUP_LOCALES.en.savedCopiesNotResumed)
  expect(dialog.textContent).toContain(SAVED_GROUP_LOCALES.en.savedCopiesRecentWork)
  expect(dialog.textContent).not.toContain(actualWire.preview.snapshot_id)
  expect(dialog.textContent).not.toContain(actualWire.page.target_gateway_id)
  expect(dialog.textContent).not.toContain('work_evidence_unknown')
})

it.each(['list', 'preview'] as const)('rejects a wrong-holder %s response without adopting its metadata', async kind => {
  if (kind === 'preview') {
    await openBrowser()
    await screen.findByRole('button', { name: /Planning/ })
    const original = mocks.request.getMockImplementation()!
    mocks.request.mockImplementation((route, method, params) => method === 'groups.recovery.prepare'
      ? Promise.resolve({ ...preview('a-room', 'Wrong holder detail'), target_gateway_id: 'install:foreign' })
      : original(route, method, params))
    fireEvent.click(screen.getByRole('button', { name: /Planning/ }))
    await screen.findByText(SAVED_GROUP_LOCALES.en.savedPreviewFailed)
  } else {
    const original = mocks.request.getMockImplementation()!
    mocks.request.mockImplementation((route, method, params) => method === 'groups.recovery.list'
      ? Promise.resolve({ ...page([copy('a-room', 'Wrong holder detail')]), target_gateway_id: 'install:foreign' })
      : original(route, method, params))
    await openBrowser()
    await screen.findByText(SAVED_GROUP_LOCALES.en.savedCopiesFailed)
  }

  expect(screen.queryByText('Wrong holder detail')).toBeNull()
})

it('does not adopt an old source list after a connection switch', async () => {
  const oldPage = deferred<unknown>()
  const original = mocks.request.getMockImplementation()!
  mocks.request.mockImplementation((route, method, params) => method === 'groups.recovery.list'
    ? route.connectionId === 'owner-a' ? oldPage.promise : Promise.resolve(page([copy('b-room', 'New source copy')]))
    : original(route, method, params))
  render(<CanonicalSavedGroups />)
  await waitFor(() => expect(mocks.request.mock.calls.some(call => call[1] === 'groups.recovery.list')).toBe(true))
  act(() => { state.connection.set('owner-b') })
  fireEvent.click(screen.getByRole('button', { name: 'View saved copies' }))
  await screen.findByRole('button', { name: /New source copy/ })
  await act(async () => { oldPage.resolve(page([copy('a-room', 'Old source copy')])) })
  expect(screen.queryByText('Old source copy')).toBeNull()
})

it('distinguishes recorded review, retirement and group-ended status without offering actions', async () => {
  const original = mocks.request.getMockImplementation()!
  mocks.request.mockImplementation((route, method, params) => method === 'groups.recovery.list'
    ? Promise.resolve(page([
      { ...copy('a-room', 'Review needed'), copy_status: 'needs_review' },
      { ...copy('b-room', 'Retained retired copy'), copy_status: 'retired' },
      { ...copy('c-room', 'Ended discussion'), group_ended: true }
    ])) : original(route, method, params))
  const dialog = await openBrowser()
  expect(await within(dialog).findByText(SAVED_GROUP_LOCALES.en.savedCopyNeedsReview)).toBeTruthy()
  expect(within(dialog).getByText(SAVED_GROUP_LOCALES.en.savedCopyRetired)).toBeTruthy()
  expect(within(dialog).getByText(SAVED_GROUP_LOCALES.en.savedGroupEnded)).toBeTruthy()
  expect(mocks.request.mock.calls.some(call => call[1] === 'groups.recovery.prepare')).toBe(false)
})

import { useStore } from '@nanostores/react'
import { cleanup, fireEvent, render, waitFor, within } from '@testing-library/react'
import { atom } from 'nanostores'
import type { ComponentProps } from 'react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

// Seams: `host.requestProfile` is the gateway transport; `window.hermesDesktop.preparedSubmissions`
// is the native journal's IPC (an in-memory compare-and-set owner here), or absent for the
// browser-storage journal. The workspace, Send journal and their outcomes are the real modules.
const request = vi.hoisted(() => vi.fn())
vi.mock('@hermes/plugin-sdk', async () => {
  const { pluginSdkMock, createGroupGateway } = await import('./group-test-utils')
  const gateway = createGroupGateway()
  const { en } = await import('@/i18n/en')
  const { CANONICAL_GROUP_LOCALES } = await import('./canonical-group-locales')
  const { captureGroupRequests } = await import('./canonical-group-contract-test-utils')
  const { ConfirmDialog } = await import('@/components/ui/confirm-dialog')
  const { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } = await import('@/components/ui/dialog')
  const { Input } = await import('@/components/ui/input')

  return { ...await pluginSdkMock(gateway.host), atom, useValue: useStore,
    ConfirmDialog, Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle, Input,
    useI18n: () => ({ t: en }),
    usePluginI18n: () => (key: string) => CANONICAL_GROUP_LOCALES.en[key.replace('canonical.', '') as keyof typeof CANONICAL_GROUP_LOCALES.en] ?? key,
    Button: (p: ComponentProps<'button'>) => <button {...p} />,
    host: { ...gateway.host, requestProfile: captureGroupRequests(request).request } }
})
import { prepareCanonicalGroupSend, readCanonicalGroupSend } from './canonical-group-send'
import { CanonicalGroupWorkspace } from './canonical-group-workspace'

const originalDesktop = window.hermesDesktop
beforeEach(() => { Object.defineProperty(window, 'hermesDesktop', { configurable: true, writable: true, value: undefined }) })
afterEach(() => { cleanup(); request.mockReset(); localStorage.clear(); window.hermesDesktop = originalDesktop })

// What gateway/session_group_controls.py answers for groups.send, and for a refusal: `admitted:
// false` only when the gateway refused before the user event committed (HostedRoomService.send).
const receipt = (params: { event_id?: string }) => ({ event: {}, client_event_id: params.event_id, accepted: true, driver_started: true })

const refusal = (reason: string, admitted?: false) =>
  Object.assign(new Error(reason), { code: 4001, data: { reason, ...(admitted === false ? { admitted } : {}) } })

const MAYBE = 'This message may already have been sent. Retry sends the same message; it will not be posted twice.'
const REFUSED = 'The gateway refused this message. Edit it and send again.'

function room(sendReply: (params: Record<string, unknown>, attempt: number) => unknown, stop?: () => unknown) {
  let attempt = 0
  request.mockImplementation(async (_route, method, params) => {
    if (method === 'groups.state') {return { room: { name: 'Room' }, driver_status: { working: true } }}

    if (method === 'groups.log') {return { events: [] }}

    if (method === 'groups.stop') {return stop?.() ?? { cancelled: 0 }}

    return method === 'groups.send' ? sendReply(params, ++attempt) : {}
  })
}

/** The native journal owner: one atomic compare-and-set per call, as electron/prepared-submissions.ts. */
function nativeJournal(beforeMutation?: (call: number, entry: string | null) => Promise<void> | void) {
  const journal: Record<string, unknown> = {}
  let calls = 0
  window.hermesDesktop = { preparedSubmissions: {
    read: async () => JSON.stringify(journal),
    compareAndSet: async (key: string, expected: string | null, entry: string | null) => {
      await beforeMutation?.(++calls, entry)
      const current = Object.hasOwn(journal, key) ? JSON.stringify(journal[key]) : null

      if (current !== (expected === null ? null : JSON.stringify(JSON.parse(expected)))) {return { applied: false, current }}

      if (entry === null) {delete journal[key]} else {journal[key] = JSON.parse(entry)}

      return { applied: true, current: entry }
    }
  } } as unknown as typeof window.hermesDesktop

  return journal
}

const sentIds = () => request.mock.calls.filter(call => call[1] === 'groups.send').map(call => call[2].event_id as string)
const editor = (scope: HTMLElement) => within(scope).getByRole('textbox') as HTMLTextAreaElement

async function openRoom(binding: { connectionId: string; profile: string; roomId: string }) {
  const view = render(<CanonicalGroupWorkspace binding={binding} />)
  await within(view.container).findByText('Room')

  return view.container
}

async function typeAndSend(scope: HTMLElement, text: string) {
  await waitFor(() => expect(editor(scope).disabled).toBe(false))
  fireEvent.change(editor(scope), { target: { value: text } })
  fireEvent.click(within(scope).getByRole('button', { name: 'Send' }))
}

// AM1: a delivered intent whose journal cleanup failed must never be reused for a new message.
it('sends a new identical message under its own identity after a delivered Send whose cleanup failed', async () => {
  const warn = vi.spyOn(console, 'warn').mockImplementation(() => undefined)
  let failedOnce = false

  const journal = nativeJournal((_call, entry) => {
    if (entry === null && !failedOnce) {
      failedOnce = true

      throw new Error('EIO: journal unavailable')
    }
  })

  room(params => receipt(params))
  const scope = await openRoom({ connectionId: 'local', profile: 'team', roomId: 'cleanup-retry' })
  await typeAndSend(scope, 'Continue')
  await waitFor(() => expect(sentIds()).toHaveLength(1))
  await waitFor(() => expect(editor(scope).value).toBe(''))
  await typeAndSend(scope, 'Continue')
  await waitFor(() => expect(sentIds()).toHaveLength(2))
  expect(new Set(sentIds()).size).toBe(2)
  await waitFor(() => expect(Object.keys(journal)).toHaveLength(0))
  expect(warn).toHaveBeenCalled()
  warn.mockRestore()
})

// AM2: A's refused first request proves nothing once B has dispatched the same intent since.
it('keeps the identity when another window dispatched the intent after this window\'s refused first request', async () => {
  const binding = { connectionId: 'local', profile: 'team', roomId: 'two-dispatchers' }
  let refuseA!: () => void

  room((params, attempt) => attempt === 1
    ? new Promise((_resolve, reject) => { refuseA = () => reject(refusal('permission_denied', false)) })
    : attempt === 2 ? Promise.reject(new Error('request timed out: groups.send')) : receipt(params))
  const a = await openRoom(binding)
  await typeAndSend(a, 'Please do this once')
  await waitFor(() => expect(sentIds()).toHaveLength(1))

  const b = await openRoom(binding)
  const retry = await within(b).findByRole('button', { name: 'Retry' }) as HTMLButtonElement
  await waitFor(() => expect(retry.disabled).toBe(false))
  fireEvent.click(retry)
  await within(b).findByText('request timed out: groups.send')

  refuseA()
  await within(a).findByText('permission_denied')
  expect(within(a).getByText(MAYBE)).toBeTruthy()
  expect(editor(a).disabled).toBe(true)
  const again = within(a).getByRole('button', { name: 'Retry' }) as HTMLButtonElement
  await waitFor(() => expect(again.disabled).toBe(false))
  fireEvent.click(again)
  await waitFor(() => expect(sentIds()).toHaveLength(3))
  expect(new Set(sentIds()).size).toBe(1)
})

it('keeps the identity for the next window when this one closed before its refused first request settled', async () => {
  const binding = { connectionId: 'local', profile: 'team', roomId: 'closed-dispatcher' }
  let refuseA!: () => void

  room((_params, attempt) => attempt === 1
    ? new Promise((_resolve, reject) => { refuseA = () => reject(refusal('permission_denied', false)) })
    : attempt === 2 ? Promise.reject(new Error('request timed out: groups.send')) : Promise.reject(refusal('invalid_params', false)))
  const a = render(<CanonicalGroupWorkspace binding={binding} />)
  await within(a.container).findByText('Room')
  await typeAndSend(a.container, 'Please do this once')
  await waitFor(() => expect(sentIds()).toHaveLength(1))
  const b = await openRoom(binding)
  fireEvent.click(await within(b).findByRole('button', { name: 'Retry' }))
  await within(b).findByText('request timed out: groups.send')
  const before = JSON.stringify(await readCanonicalGroupSend(binding))
  a.unmount()
  refuseA()
  // The closed window still settles its refusal in the journal.
  await waitFor(async () => expect(JSON.stringify(await readCanonicalGroupSend(binding))).not.toBe(before))

  const c = await openRoom(binding)
  const retry = await within(c).findByRole('button', { name: 'Retry' }) as HTMLButtonElement
  await waitFor(() => expect(retry.disabled).toBe(false))
  fireEvent.click(retry)
  await within(c).findByText('invalid_params')
  expect(editor(c).disabled).toBe(true)
  expect((await readCanonicalGroupSend(binding))?.params.event_id).toBe(sentIds()[0])
})

// AM3: a refused intent's words stay durable until the user replaces them with a new Send.
it('hands a refused converged intent back for editing without losing either window\'s words', async () => {
  const binding = { connectionId: 'local', profile: 'team', roomId: 'converged-refusal' }
  room((params, attempt) => attempt === 1 ? Promise.reject(refusal('invalid_params', false)) : receipt(params))
  const scope = await openRoom(binding)
  await waitFor(() => expect(editor(scope).disabled).toBe(false))
  const other = await prepareCanonicalGroupSend(binding, { text: 'Too long for the gateway', attachments: [] })
  await typeAndSend(scope, 'My own words')
  await within(scope).findByText(REFUSED)
  await waitFor(() => expect(editor(scope).disabled).toBe(false))
  expect(editor(scope).value).toBe('Too long for the gateway')
  expect((await readCanonicalGroupSend(binding))?.params.payload.text).toBe('Too long for the gateway')

  fireEvent.change(editor(scope), { target: { value: 'Shorter' } })
  fireEvent.click(within(scope).getByRole('button', { name: 'Send' }))
  await waitFor(() => expect(editor(scope).value).toBe('My own words'))
  const sends = request.mock.calls.filter(call => call[1] === 'groups.send').map(call => call[2])
  expect(sends.map(sent => sent.payload.text)).toEqual(['Too long for the gateway', 'Shorter'])
  expect(sends[0].event_id).toBe(other.params.event_id)
  expect(sends[1].event_id).not.toBe(other.params.event_id)
  expect(await readCanonicalGroupSend(binding)).toBeUndefined()
})

it('keeps a refused message durable when its room closes while the refusal settles', async () => {
  const binding = { connectionId: 'local', profile: 'team', roomId: 'closed-refusal' }
  let entered!: () => void
  let release!: () => void
  const settling = new Promise<void>(resolve => { entered = resolve })
  const gate = new Promise<void>(resolve => { release = resolve })
  // Mutations: 1 prepares the intent, 2 records its request, 3 settles the refusal.
  nativeJournal(async call => { if (call === 3) { entered(); await gate } })
  room(() => Promise.reject(refusal('invalid_params', false)))
  const view = render(<CanonicalGroupWorkspace binding={binding} />)
  await within(view.container).findByText('Room')
  await typeAndSend(view.container, 'Must survive a refused request')
  await settling
  view.unmount()
  release()
  await waitFor(async () => expect((await readCanonicalGroupSend(binding))?.params.payload.text).toBe('Must survive a refused request'))

  const reopened = await openRoom(binding)
  await waitFor(() => expect(editor(reopened).value).toBe('Must survive a refused request'))
})

const frame = (message: string, code: number, data?: Record<string, unknown>) => Object.assign(new Error(message), { code, data })

// Both surfaces Desktop dials: the session gateway (4001 + reason) and `hermes serve`
// (tui_gateway/methods_groups.py: 4111 room errors, 4123 no worker, 5112 anything else).
it.each([
  ['the gateway refuses the payload', frame('invalid_params', 4001, { reason: 'invalid_params', admitted: false })],
  ['hermes serve finds the room ended', frame('hosted room not found', 4111, { admitted: false })],
  ['hermes serve has no Group Chat worker', frame('Group Chat worker is unavailable.', 4123, { admitted: false })],
  ['hermes serve refuses the payload', frame('user payload text must be a bounded string', 5112, { admitted: false })]
])('hands the words back for editing when %s before admitting the Send', async (_case, refused) => {
  const binding = { connectionId: 'local', profile: 'team', roomId: 'refused-before-admission' }
  room((params, attempt) => attempt === 1 ? Promise.reject(refused) : receipt(params))
  const scope = await openRoom(binding)
  await typeAndSend(scope, 'Ended elsewhere')
  await within(scope).findByText(REFUSED)
  await waitFor(() => expect(editor(scope).disabled).toBe(false))
  expect(editor(scope).value).toBe('Ended elsewhere')
  expect((await readCanonicalGroupSend(binding))?.attempts).toBe(0)
})

// AM4: the same reasons and codes also follow a committed user event, without `admitted: false`.
it.each([
  ['an invalid_params from the gateway', frame('invalid_params', 4001, { reason: 'invalid_params' })],
  ['a room error from hermes serve', frame('hosted room not found', 4111)],
  ['a failure from hermes serve', frame('storage is full', 5112)]
])('keeps the identity of a first Send after %s that does not prove nothing was admitted', async (_case, failed) => {
  const binding = { connectionId: 'local', profile: 'team', roomId: 'post-commit' }
  room((params, attempt) => attempt === 1 ? Promise.reject(failed) : receipt(params))
  const scope = await openRoom(binding)
  await typeAndSend(scope, 'Second')
  await within(scope).findByText(MAYBE)
  expect(editor(scope).disabled).toBe(true)
  expect(editor(scope).value).toBe('Second')
  const retry = within(scope).getByRole('button', { name: 'Retry' }) as HTMLButtonElement
  await waitFor(() => expect(retry.disabled).toBe(false))
  fireEvent.click(retry)
  await waitFor(() => expect(sentIds()).toHaveLength(2))
  expect(new Set(sentIds()).size).toBe(1)
})

// AM6: Stop covers a Send already initiated when it was pressed.
it('does not dispatch a Send still preparing when Stop is pressed, and keeps its words', async () => {
  const binding = { connectionId: 'local', profile: 'team', roomId: 'stop-while-preparing' }
  let entered!: () => void
  let release!: () => void
  const preparing = new Promise<void>(resolve => { entered = resolve })
  const gate = new Promise<void>(resolve => { release = resolve })
  nativeJournal(async call => { if (call === 1) { entered(); await gate } })
  room(params => receipt(params))
  const scope = await openRoom(binding)
  await typeAndSend(scope, 'Long task')
  await preparing
  const stop = within(scope).getByRole('button', { name: 'Stop' }) as HTMLButtonElement
  expect(stop.disabled).toBe(false)
  fireEvent.click(stop)
  await waitFor(() => expect(request.mock.calls.some(call => call[1] === 'groups.stop')).toBe(true))
  release()
  await within(scope).findByText('Stopped before sending. Your message is still here.')
  await within(scope).findByText('No new replies were cancelled.')
  // Nothing was sent, so the first fence covers it: no second Stop.
  expect(request.mock.calls.filter(call => call[1] === 'groups.stop')).toHaveLength(1)
  expect(request.mock.calls.some(call => call[1] === 'groups.send')).toBe(false)
  await waitFor(() => expect(editor(scope).disabled).toBe(false))
  expect(editor(scope).value).toBe('Long task')
  expect((await readCanonicalGroupSend(binding))?.params.payload.text).toBe('Long task')

  fireEvent.click(within(scope).getByRole('button', { name: 'Send' }))
  await waitFor(() => expect(sentIds()).toHaveLength(1))
  await waitFor(() => expect(editor(scope).value).toBe(''))
})

it('fences no second time behind a Send the gateway refused before admitting it', async () => {
  let refuse!: () => void
  room(() => new Promise((_resolve, reject) => { refuse = () => reject(refusal('invalid_params', false)) }))
  const scope = await openRoom({ connectionId: 'local', profile: 'team', roomId: 'stop-refused' })
  await typeAndSend(scope, 'Never admitted')
  await waitFor(() => expect(sentIds()).toHaveLength(1))
  fireEvent.click(within(scope).getByRole('button', { name: 'Stop' }))
  await waitFor(() => expect(request.mock.calls.some(call => call[1] === 'groups.stop')).toBe(true))
  refuse()
  await within(scope).findByText('No new replies were cancelled.')
  expect(request.mock.calls.filter(call => call[1] === 'groups.stop')).toHaveLength(1)
})

it('does not resend words handed back for editing once another window finished with their intent', async () => {
  const binding = { connectionId: 'local', profile: 'team', roomId: 'edited-elsewhere' }
  room((params, attempt) => attempt === 1 ? Promise.reject(refusal('invalid_params', false)) : receipt(params))
  const scope = await openRoom(binding)
  await typeAndSend(scope, 'Handed back')
  await within(scope).findByText(REFUSED)
  localStorage.clear() // another window sent the intent again and retired it on its receipt
  await waitFor(() => expect(editor(scope).disabled).toBe(false))
  fireEvent.click(within(scope).getByRole('button', { name: 'Send' }))
  await within(scope).findByText('Another Desktop window already finished with this message. Check the conversation before sending it again.')
  expect(sentIds()).toHaveLength(1)
  expect(editor(scope).value).toBe('Handed back')
  await waitFor(() => expect(editor(scope).disabled).toBe(false))
  fireEvent.click(within(scope).getByRole('button', { name: 'Send' }))
  await waitFor(() => expect(sentIds()).toHaveLength(2))
  expect(new Set(sentIds()).size).toBe(2)
})

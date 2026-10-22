import { useStore } from '@nanostores/react'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { atom } from 'nanostores'
import type { ComponentProps } from 'react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

const request = vi.hoisted(() => vi.fn())
vi.mock('@hermes/plugin-sdk', async () => {
  const { pluginSdkMock, createGroupGateway } = await import('./group-test-utils')
  const gateway = createGroupGateway()
  const { en } = await import('@/i18n/en')
  const { CANONICAL_GROUP_LOCALES } = await import('./canonical-group-locales')
  const { captureGroupRequests } = await import('./canonical-group-contract-test-utils')

  return { ...await pluginSdkMock(gateway.host), atom, useValue: useStore,
    useI18n: () => ({ t: en }),
    usePluginI18n: () => (key: string) => CANONICAL_GROUP_LOCALES.en[key.replace('canonical.', '') as keyof typeof CANONICAL_GROUP_LOCALES.en] ?? key,
    Button: (p: ComponentProps<'button'>) => <button {...p} />,
    host: { ...gateway.host, requestProfile: captureGroupRequests(request).request } }
})
import { CANONICAL_GROUP_CAPABILITIES, STANDALONE_GROUP_CAPABILITIES } from './canonical-group-contract-test-utils'
import { registerCanonicalGroup } from './canonical-group-registry'
import { prepareCanonicalGroupSend, readCanonicalGroupSend } from './canonical-group-send'
import { CanonicalGroupWorkspace } from './canonical-group-workspace'
import { GroupChatWorkspace } from './group-chat-view'
const originalDesktop = window.hermesDesktop
beforeEach(() => { Object.defineProperty(window, 'hermesDesktop', { configurable: true, writable: true, value: undefined }) })
afterEach(() => { cleanup(); request.mockReset(); localStorage.clear(); window.hermesDesktop = originalDesktop })
// What gateway/session_group_controls.py answers for groups.send.
const receipt = (params: { event_id?: string }) => ({ event: {}, client_event_id: params.event_id, accepted: true, driver_started: true })

it('preserves the unsent draft when only the room display name changes', async () => {
  request.mockImplementation(async (_route, method) => method === 'groups.state'
    ? { room: { name: 'Room' }, driver_status: {} } : method === 'groups.log' ? { events: [] } : {})
  const binding = { connectionId: 'local', profile: 'team', roomId: 'rename', roomName: 'Room' }
  const view = render(<CanonicalGroupWorkspace binding={binding} />)
  await waitFor(() => expect((screen.getByRole('textbox') as HTMLTextAreaElement).disabled).toBe(false))
  fireEvent.change(screen.getByRole('textbox'), { target: { value: 'Keep this draft' } })
  view.rerender(<CanonicalGroupWorkspace binding={{ ...binding, roomName: 'Renamed room' }} />)
  await waitFor(() => expect((screen.getByRole('textbox') as HTMLTextAreaElement).disabled).toBe(false))
  expect((screen.getByRole('textbox') as HTMLTextAreaElement).value).toBe('Keep this draft')

  view.rerender(<CanonicalGroupWorkspace binding={{ ...binding, roomId: 'another-room' }} />)
  await waitFor(() => expect((screen.getByRole('textbox') as HTMLTextAreaElement).disabled).toBe(false))
  expect((screen.getByRole('textbox') as HTMLTextAreaElement).value).toBe('')
})

it('restores a frozen send after remount and retires only its acknowledged exact retry', async () => {
  const binding = { connectionId: 'remote', profile: 'team', roomId: 'restore' }
  const entry = await prepareCanonicalGroupSend(binding, { text: 'Original', attachments: [{ path: '/owner/image.png', mime_type: 'image/png' }] })
  request.mockImplementation(async (_route, method) => {
    if (method === 'groups.state') {return { room: { name: 'Room' }, driver_status: {} }}

    if (method === 'groups.log') {return { events: [] }}

    if (method === 'groups.send') {throw new Error('lost ACK')}

    return {}
  })
  const first = render(<CanonicalGroupWorkspace binding={binding} />)
  await waitFor(() => expect((screen.getByRole('textbox') as HTMLTextAreaElement).value).toBe('Original'))
  expect((screen.getByRole('textbox') as HTMLTextAreaElement).disabled).toBe(true)
  expect(request.mock.calls.some(c => c[1] === 'groups.send')).toBe(false)
  fireEvent.click(screen.getByRole('button', { name: 'Retry' }))
  await screen.findByText('lost ACK')
  // The request was counted before it left, so this intent stays uncertain until a receipt.
  expect(await readCanonicalGroupSend(binding)).toEqual({ ...entry, attempts: 1 })
  first.unmount()
  render(<CanonicalGroupWorkspace binding={binding} />)
  await screen.findByRole('button', { name: 'Retry' })
  request.mockImplementation(async (_route, method, params) => method === 'groups.state'
    ? { room: { name: 'Room' }, driver_status: {} } : method === 'groups.log' ? { events: [] } : receipt(params))
  await waitFor(() => expect((screen.getByRole('button', { name: 'Retry' }) as HTMLButtonElement).disabled).toBe(false))
  fireEvent.click(screen.getByRole('button', { name: 'Retry' }))
  await waitFor(async () => expect(await readCanonicalGroupSend(binding)).toBeUndefined())
  const sends = request.mock.calls.filter(c => c[1] === 'groups.send')
  expect(sends).toHaveLength(2)

  for (const call of sends) {
    expect(call[0]).toMatchObject({ connectionId: binding.connectionId, targetProfile: binding.profile })
    expect(call[2]).toEqual({ ...entry.params, profile: binding.profile })
  }
})

it('blocks Send until journal restore and durable preparation complete', async () => {
  let releaseRead!: (value: string) => void
  let releaseWrite!: () => void
  const journal: Record<string, unknown> = {}

  const native = {
    read: vi.fn().mockImplementationOnce(() => new Promise<string>(resolve => { releaseRead = resolve }))
      .mockImplementation(async () => JSON.stringify(journal)),
    compareAndSet: vi.fn(async (key: string, _expected: string | null, value: string | null) => {
      await new Promise<void>(resolve => { releaseWrite = resolve })

      if (value === null) {delete journal[key]} else {journal[key] = JSON.parse(value)}

      return { applied: true, current: value }
    })
  }

  window.hermesDesktop = { preparedSubmissions: native } as unknown as typeof window.hermesDesktop
  request.mockImplementation(async (_route, method, params) => method === 'groups.state'
    ? { room: { name: 'Room' }, driver_status: {} } : method === 'groups.log' ? { events: [] } : receipt(params))
  render(<CanonicalGroupWorkspace binding={{ connectionId: 'remote', profile: 'team', roomId: 'new' }} />)
  await screen.findByText('Room')
  fireEvent.change(screen.getByRole('textbox'), { target: { value: 'New text' } })
  expect((screen.getByRole('button', { name: 'Send' }) as HTMLButtonElement).disabled).toBe(true)
  releaseRead('{}')
  await waitFor(() => expect((screen.getByRole('textbox') as HTMLTextAreaElement).disabled).toBe(false))
  fireEvent.change(screen.getByRole('textbox'), { target: { value: 'New text' } })
  fireEvent.click(screen.getByRole('button', { name: 'Send' }))
  await waitFor(() => expect(native.compareAndSet).toHaveBeenCalled())
  expect(request.mock.calls.some(c => c[1] === 'groups.send')).toBe(false)
  releaseWrite()
  // The dispatch is recorded durably before the request leaves.
  await waitFor(() => expect(native.compareAndSet).toHaveBeenCalledTimes(2))
  expect(request.mock.calls.some(c => c[1] === 'groups.send')).toBe(false)
  releaseWrite()
  await waitFor(() => expect(request.mock.calls.some(c => c[1] === 'groups.send')).toBe(true))
  await waitFor(() => expect(native.compareAndSet).toHaveBeenCalledTimes(3))
  releaseWrite()
  await waitFor(() => expect((screen.getByRole('textbox') as HTMLTextAreaElement).value).toBe(''))
})

it('captures exact pending attempts through confirmation and never retargets or retries unknown work', async () => {
  const action = { kind: 'discard', member_id: 'worker', task_id: 'old-task', execution_generation: 7 }
  request.mockImplementation(async (_route, method) => {
    if (method === 'groups.state') {return { room: { name: 'Room' }, driver_status: { pending_actions: [action] } }}

    if (method === 'groups.log') {return { events: [], has_more: false }}

    if (method === 'groups.discard') {throw new Error('stale_attempt')}

    return {}
  })
  render(<CanonicalGroupWorkspace binding={{ connectionId: 'remote', profile: 'team', roomId: 'ack-room' }} />)
  fireEvent.click(await screen.findByRole('button', { name: 'Discard' }))
  expect(screen.getByText(/Side effects may already have occurred/)).toBeTruthy()
  expect(screen.queryByRole('button', { name: 'Retry' })).toBeNull()
  action.execution_generation = 8
  fireEvent.click(screen.getByRole('button', { name: 'Confirm discard' }))
  await screen.findByText('stale_attempt')
  const call = request.mock.calls.find(c => c[1] === 'groups.discard')!
  expect(call[0]).toMatchObject({ connectionId: 'remote', targetProfile: 'team' })
  expect(call[2]).toEqual({ room_id: 'ack-room', member_id: 'worker', task_id: 'old-task', execution_generation: 7, profile: 'team' })
  expect(request.mock.calls.every(c => c[1].startsWith('groups.'))).toBe(true)
})

it('reads back retry on the same authority and sends only through the group driver', async () => {
  request.mockImplementation(async (_route, method) => {
    if (method === 'groups.state') {return { room: { name: 'Room' }, driver_status: { pending_actions: [{ kind: 'retry', member_id: 'w', task_id: 't', execution_generation: 2 }] } }}

    if (method === 'groups.log') {return { events: [{ seq: 1, kind: 'message', payload: { text: 'Owner reply' } }], has_more: false }}

    return {}
  })
  const group = registerCanonicalGroup({ connectionId: 'local', profile: 'default' }, { room_id: 'r', name: 'Room', members: [] })
  render(<GroupChatWorkspace group={group} members={[]} origin="key" />)
  fireEvent.click(await screen.findByRole('button', { name: 'Retry' }))
  await waitFor(() => expect(request.mock.calls.filter(c => c[1] === 'groups.state').length).toBeGreaterThan(1))
  fireEvent.change(screen.getByRole('textbox'), { target: { value: 'Hello' } })
  fireEvent.click(screen.getByRole('button', { name: 'Send' }))
  await waitFor(() => expect(request.mock.calls.some(c => c[1] === 'groups.send')).toBe(true))
  expect(screen.getByText('Owner reply')).toBeTruthy()
  expect(request.mock.calls.every(c => c[1].startsWith('groups.'))).toBe(true)
})

// Review 5475590057 P3: another window won the room's one intent slot after this one restored.
// Send converges on that durable winner (4149282e49) but this window's typed text is kept, not lost.
it('a Send that converges on another window\'s pending intent keeps this window\'s draft', async () => {
  const binding = { connectionId: 'remote', profile: 'team', roomId: 'two-windows' }
  request.mockImplementation(async (_route, method, params) => method === 'groups.state'
    ? { room: { name: 'Room' }, driver_status: {} } : method === 'groups.log' ? { events: [] } : receipt(params))
  render(<CanonicalGroupWorkspace binding={binding} />)
  await screen.findByText('Room')
  await waitFor(() => expect((screen.getByRole('textbox') as HTMLTextAreaElement).disabled).toBe(false))
  const other = await prepareCanonicalGroupSend(binding, { text: 'from the other window', attachments: [] })
  fireEvent.change(screen.getByRole('textbox'), { target: { value: 'typed here' } })
  fireEvent.click(screen.getByRole('button', { name: 'Send' }))
  await waitFor(async () => expect(await readCanonicalGroupSend(binding)).toBeUndefined())
  expect(request.mock.calls.filter(c => c[1] === 'groups.send').map(c => c[2])).toEqual([{ ...other.params, profile: 'team' }])
  await waitFor(() => expect((screen.getByRole('textbox') as HTMLTextAreaElement).value).toBe('typed here'))
  expect((screen.getByRole('textbox') as HTMLTextAreaElement).disabled).toBe(false)
})

it('shows which bots are working, and still says the group is working on a gateway without member activity', async () => {
  const members = [{ member_id: 'ops', profile: 'ops', handle: 'ops', display_name: 'Ops' },
    { member_id: 'qa', profile: 'qa', handle: 'qa', display_name: 'QA' }]

  let driver: Record<string, unknown> = { working: true, member_activity: [
    { member_id: 'qa', status: 'queued' }, { member_id: 'ops', status: 'running' }, { member_id: 'ops', status: 'queued' }] }

  request.mockImplementation(async (_route, method) => method === 'groups.state'
    ? { room: { name: 'Room', members }, driver_status: driver } : method === 'groups.log' ? { events: [] } : {})
  const view = render(<CanonicalGroupWorkspace binding={{ connectionId: 'local', profile: 'team', roomId: 'busy' }} />)
  await waitFor(() => expect(screen.getByRole('status').textContent).toBe('Working: Ops'))
  const phase = (name: string) => screen.getByText(name).closest('li')?.getAttribute('data-phase')
  expect([phase('Ops'), phase('QA')]).toEqual(['working', 'queued'])

  // The same mounted room clears both indicators on its next poll once the turn settles.
  driver = { working: false, member_activity: [] }
  await waitFor(() => expect(screen.queryByRole('status')).toBeNull(), { timeout: 5_000 })
  expect([phase('Ops'), phase('QA')]).toEqual(['idle', 'idle'])

  view.unmount()
  driver = { working: true }
  render(<CanonicalGroupWorkspace binding={{ connectionId: 'local', profile: 'team', roomId: 'older' }} />)
  await waitFor(() => expect(screen.getByRole('status').textContent).toBe('The group is working…'))
})

it.each([
  ['the gateway', CANONICAL_GROUP_CAPABILITIES, true],
  ['the standalone server', STANDALONE_GROUP_CAPABILITIES, false]
])('offers Discard and attachments only where %s lists them', async (_surface, capabilities, offered) => {
  const actions = [{ kind: 'discard', member_id: 'ops', task_id: 'unknown', execution_generation: 2 },
    { kind: 'retry', member_id: 'qa', task_id: 'late', execution_generation: 1 }]

  const events = [{ seq: 1, room_id: 'surfaces', event_id: 'file-event', kind: 'message.member', actor: { kind: 'member', id: 'ops' },
    payload: { attachments: [{ attachment_id: 'att_1', kind: 'file', name: 'notes.txt', mime: 'text/plain' }] } }]

  let answer!: (value: unknown) => void
  request.mockImplementation(async (_route, method) => method === 'groups.capabilities'
    ? new Promise(resolve => { answer = resolve })
    : method === 'groups.state' ? { room: { name: 'Room' }, driver_status: { pending_actions: actions } }
      : method === 'groups.log' ? { events } : {})
  render(<CanonicalGroupWorkspace binding={{ connectionId: 'remote', profile: 'team', roomId: 'surfaces' }} />)
  await screen.findByText('notes.txt')
  // Until the gateway says what it offers, no optional control is shown.
  expect(screen.queryByRole('button', { name: 'Discard' })).toBeNull()
  expect(screen.queryByRole('button', { name: 'Attach files' })).toBeNull()

  answer(capabilities)
  await screen.findByRole('button', { name: 'Retry' })
  expect(Boolean(screen.queryByRole('button', { name: 'Discard' }))).toBe(offered)
  expect(Boolean(screen.queryByRole('button', { name: 'Attach files' }))).toBe(offered)
  expect((screen.getByRole('button', { name: 'Download' }) as HTMLButtonElement).disabled).toBe(!offered)
})

it('reads only new log events, and the whole log again when the room authority changes', async () => {
  const message = (seq: number, text: string) => ({ seq, kind: 'message.member', actor: { kind: 'member', id: 'ops' }, payload: { text } })
  let epoch = 1
  let log = [message(1, 'First reply')]
  request.mockImplementation(async (_route, method, params) => method === 'groups.state'
    ? { room: { name: 'Room', authority_epoch: epoch, latest_seq: log.at(-1)!.seq }, driver_status: {} }
    : method === 'groups.log' ? { events: log.filter(event => event.seq > params.since_seq), has_more: false } : {})
  render(<CanonicalGroupWorkspace binding={{ connectionId: 'local', profile: 'team', roomId: 'long' }} />)
  await screen.findByText('First reply')
  log = [...log, message(2, 'Second reply')]
  await screen.findByText('Second reply', undefined, { timeout: 5_000 })
  const since = () => request.mock.calls.filter(call => call[1] === 'groups.log').map(call => call[2].since_seq)
  expect(since()).toEqual([0, 1])
  expect(screen.getAllByText(/reply$/).map(node => node.textContent)).toEqual(['First reply', 'Second reply'])

  epoch = 2
  log = [message(1, 'Reply after a takeover')]
  await screen.findByText('Reply after a takeover', undefined, { timeout: 5_000 })
  expect(since().at(-1)).toBe(0)
  expect(screen.queryByText('First reply')).toBeNull()
}, 15_000)

it('says what the room waits on beside the live work, never instead of it', async () => {
  const members = [{ member_id: 'ops', profile: 'ops', handle: 'ops', display_name: 'Ops' },
    { member_id: 'qa', profile: 'qa', handle: 'qa', display_name: 'QA' }]

  let driver: Record<string, unknown> | undefined = {
    running: true, working: true, blocked: true, member_activity: [{ member_id: 'ops', status: 'running' }],
    pending_actions: [
      { kind: 'retry', member_id: 'qa', task_id: 'failed', execution_generation: 1 },
      { kind: 'approval', member_id: 'ops', task_id: 'live', execution_generation: 1, request_id: 'r1', approval: { choices: ['deny'] } }
    ]
  }

  request.mockImplementation(async (_route, method) => method === 'groups.state'
    ? { room: { name: 'Room', members }, driver_status: driver } : method === 'groups.log' ? { events: [] } : {})
  const view = render(<CanonicalGroupWorkspace binding={{ connectionId: 'local', profile: 'team', roomId: 'waiting' }} />)
  await screen.findByText('Blocked · 1 waiting for approval · Needs attention: 1')
  expect(screen.getByRole('status').textContent).toBe('Working: Ops')

  view.unmount()
  // A gateway whose driver is down sends no driver status.
  driver = undefined
  render(<CanonicalGroupWorkspace binding={{ connectionId: 'local', profile: 'team', roomId: 'stopped' }} />)
  await screen.findByText('Group driver unavailable. Update or reconnect the owning gateway.')
})

it('keeps one cancellation intent until Stop is confirmed, and fences again behind a message still sending', async () => {
  let deliver!: (value: unknown) => void
  let stops = 0
  request.mockImplementation(async (_route, method, params) => {
    if (method === 'groups.state') {return { room: { name: 'Room' }, driver_status: { working: true } }}

    if (method === 'groups.log') {return { events: [] }}

    if (method === 'groups.send') {return new Promise(resolve => { deliver = resolve })}

    if (method === 'groups.stop') {
      stops += 1

      if (stops === 1) {throw new Error('request timed out: groups.stop')}

      return { cancelled: stops === 2 ? 2 : 0 }
    }

    return params
  })
  render(<CanonicalGroupWorkspace binding={{ connectionId: 'local', profile: 'team', roomId: 'busy-send' }} />)
  await waitFor(() => expect((screen.getByRole('textbox') as HTMLTextAreaElement).disabled).toBe(false))
  const stop = screen.getByRole('button', { name: 'Stop' }) as HTMLButtonElement
  fireEvent.click(stop)
  await screen.findByText('request timed out: groups.stop')
  fireEvent.click(stop)
  await screen.findByText('Stop requested')

  fireEvent.change(screen.getByRole('textbox'), { target: { value: 'Long task' } })
  fireEvent.click(screen.getByRole('button', { name: 'Send' }))
  await waitFor(() => expect(request.mock.calls.some(call => call[1] === 'groups.send')).toBe(true))
  expect(screen.queryByText('Stop requested')).toBeNull()
  expect(stop.disabled).toBe(false)
  fireEvent.click(stop)
  // The first fence leaves at once; the message on the wire may post after it, so Stop stays open.
  await waitFor(() => expect(stops).toBe(3))
  expect(stop.disabled).toBe(true)
  deliver(receipt(request.mock.calls.find(call => call[1] === 'groups.send')![2]))
  await screen.findByText('No new replies were cancelled.')

  const cancelIds = request.mock.calls.filter(call => call[1] === 'groups.stop').map(call => call[2].cancel_id)
  expect(cancelIds).toHaveLength(4)
  expect(cancelIds[1]).toBe(cancelIds[0])
  expect(new Set(cancelIds.slice(1)).size).toBe(3)
})

// `admitted: false`: the gateway refused before the user event committed (HostedRoomService.send).
const refusal = (reason: string, admitted?: false) =>
  Object.assign(new Error(reason), { code: 4001, data: { reason, ...(admitted === false ? { admitted } : {}) } })

function sendingRoom(sendReply: (params: Record<string, unknown>, attempt: number) => unknown) {
  let attempt = 0
  request.mockImplementation(async (_route, method, params) => {
    if (method === 'groups.state') {return { room: { name: 'Room' }, driver_status: {} }}

    if (method === 'groups.log') {return { events: [] }}

    return method === 'groups.send' ? sendReply(params, ++attempt) : {}
  })
}

async function typeAndSend(binding: { connectionId: string; profile: string; roomId: string }, text: string) {
  render(<CanonicalGroupWorkspace binding={binding} />)
  await waitFor(() => expect((screen.getByRole('textbox') as HTMLTextAreaElement).disabled).toBe(false))
  fireEvent.change(screen.getByRole('textbox'), { target: { value: text } })
  fireEvent.click(screen.getByRole('button', { name: 'Send' }))
}

it('hands a first Send refused before admission back for editing, its words still journaled', async () => {
  const binding = { connectionId: 'local', profile: 'team', roomId: 'refused' }
  sendingRoom(() => { throw refusal('invalid_params', false) })
  await typeAndSend(binding, 'Too long for the gateway')
  await screen.findByText('The gateway refused this message. Edit it and send again.')
  const editor = screen.getByRole('textbox') as HTMLTextAreaElement
  await waitFor(() => expect(editor.disabled).toBe(false))
  expect(editor.value).toBe('Too long for the gateway')
  expect(screen.getByRole('button', { name: 'Send' })).toBeTruthy()
  expect(await readCanonicalGroupSend(binding)).toMatchObject({ attempts: 0, params: { payload: { text: 'Too long for the gateway' } } })
})

it('keeps an uncertain Send for Retry under its identity even when a later attempt is refused', async () => {
  const binding = { connectionId: 'local', profile: 'team', roomId: 'uncertain' }
  sendingRoom((_params, attempt) => { throw attempt === 1 ? new Error('request timed out: groups.send') : refusal('invalid_params') })
  await typeAndSend(binding, 'Maybe posted')
  await screen.findByText('This message may already have been sent. Retry sends the same message; it will not be posted twice.')
  const retry = screen.getByRole('button', { name: 'Retry' }) as HTMLButtonElement
  await waitFor(() => expect(retry.disabled).toBe(false))
  fireEvent.click(retry)
  await screen.findByText('invalid_params')
  expect(screen.getByText(/may already have been sent/)).toBeTruthy()
  expect((screen.getByRole('textbox') as HTMLTextAreaElement).disabled).toBe(true)
  const sends = request.mock.calls.filter(call => call[1] === 'groups.send').map(call => call[2])
  expect(sends).toHaveLength(2)
  expect(sends[1]).toEqual(sends[0])
  expect((await readCanonicalGroupSend(binding))?.params.event_id).toBe(sends[0].event_id)
})

it.each([
  ['names another intent', (params: Record<string, unknown>) => ({ accepted: true, client_event_id: `${params.event_id}-other` })],
  ['is not accepted', (params: Record<string, unknown>) => ({ accepted: false, client_event_id: params.event_id })],
  ['is empty', () => undefined]
])('does not treat a reply that %s as delivered', async (_case, reply) => {
  const binding = { connectionId: 'local', profile: 'team', roomId: 'malformed' }
  sendingRoom(reply)
  await typeAndSend(binding, 'Needs a real receipt')
  await screen.findByText(/may already have been sent/)
  expect((screen.getByRole('textbox') as HTMLTextAreaElement).value).toBe('Needs a real receipt')
  expect((await readCanonicalGroupSend(binding))?.params.payload.text).toBe('Needs a real receipt')
})

it('treats a valid receipt as delivered even when the journal cleanup fails', async () => {
  const journal: Record<string, unknown> = {}
  const warn = vi.spyOn(console, 'warn').mockImplementation(() => undefined)
  window.hermesDesktop = { preparedSubmissions: {
    read: async () => JSON.stringify(journal),
    compareAndSet: async (key: string, _expected: string | null, value: string | null) => {
      if (value === null) {throw new Error('EIO: journal unavailable')}
      journal[key] = JSON.parse(value)

      return { applied: true, current: value }
    }
  } } as unknown as typeof window.hermesDesktop
  sendingRoom(params => receipt(params))
  await typeAndSend({ connectionId: 'local', profile: 'team', roomId: 'cleanup' }, 'Delivered once')
  await waitFor(() => expect((screen.getByRole('textbox') as HTMLTextAreaElement).value).toBe(''))
  expect(screen.queryByRole('alert')).toBeNull()
  expect(warn).toHaveBeenCalled()
  warn.mockRestore()
})

it('never resends an intent another window already finished with', async () => {
  const binding = { connectionId: 'local', profile: 'team', roomId: 'finished-elsewhere' }
  const entry = await prepareCanonicalGroupSend(binding, { text: 'From before a reload', attachments: [] })
  sendingRoom(params => receipt(params))
  render(<CanonicalGroupWorkspace binding={binding} />)
  const retry = await screen.findByRole('button', { name: 'Retry' }) as HTMLButtonElement
  await waitFor(() => expect(retry.disabled).toBe(false))
  localStorage.clear() // the other window received its receipt and retired the slot
  fireEvent.click(retry)
  await screen.findByText('Another Desktop window already finished with this message. Check the conversation before sending it again.')
  expect(request.mock.calls.some(call => call[1] === 'groups.send')).toBe(false)
  // Its words are not offered again as a new message: that could post them twice.
  expect((screen.getByRole('textbox') as HTMLTextAreaElement).value).toBe('')
  expect(entry.params.payload.text).toBe('From before a reload')
})

it('keeps a first Send refused after its room closed for the next open, where Retry hands it back', async () => {
  const binding = { connectionId: 'local', profile: 'team', roomId: 'closed-mid-send' }
  let refuse!: () => void
  sendingRoom((_params, attempt) => attempt === 1
    ? new Promise((_resolve, reject) => { refuse = () => reject(refusal('invalid_params', false)) })
    : Promise.reject(refusal('invalid_params', false)))
  render(<CanonicalGroupWorkspace binding={binding} />)
  await waitFor(() => expect((screen.getByRole('textbox') as HTMLTextAreaElement).disabled).toBe(false))
  fireEvent.change(screen.getByRole('textbox'), { target: { value: 'Typed before closing' } })
  fireEvent.click(screen.getByRole('button', { name: 'Send' }))
  await waitFor(() => expect(request.mock.calls.some(call => call[1] === 'groups.send')).toBe(true))
  cleanup()
  refuse()
  await waitFor(async () => expect((await readCanonicalGroupSend(binding))?.attempts).toBe(0))

  render(<CanonicalGroupWorkspace binding={binding} />)
  const retry = await screen.findByRole('button', { name: 'Retry' }) as HTMLButtonElement
  await waitFor(() => expect(retry.disabled).toBe(false))
  fireEvent.click(retry)
  await screen.findByText('The gateway refused this message. Edit it and send again.')
  expect((screen.getByRole('textbox') as HTMLTextAreaElement).value).toBe('Typed before closing')
  expect((await readCanonicalGroupSend(binding))?.attempts).toBe(0)
})

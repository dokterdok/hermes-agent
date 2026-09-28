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

  return { ...await pluginSdkMock(gateway.host), atom, useValue: useStore,
    useI18n: () => ({ t: en }),
    usePluginI18n: () => (key: string) => CANONICAL_GROUP_LOCALES.en[key.replace('canonical.', '') as keyof typeof CANONICAL_GROUP_LOCALES.en] ?? key,
    Button: (p: ComponentProps<'button'>) => <button {...p} />,
    host: { ...gateway.host, requestProfile: request } }
})
import { registerCanonicalGroup } from './canonical-group-registry'
import { prepareCanonicalGroupSend, readCanonicalGroupSend } from './canonical-group-send'
import { CanonicalGroupWorkspace } from './canonical-group-workspace'
import { GroupChatWorkspace } from './group-chat-view'
const originalDesktop = window.hermesDesktop
beforeEach(() => { Object.defineProperty(window, 'hermesDesktop', { configurable: true, writable: true, value: undefined }) })
afterEach(() => { cleanup(); request.mockReset(); localStorage.clear(); window.hermesDesktop = originalDesktop })

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
  expect(await readCanonicalGroupSend(binding)).toEqual(entry)
  first.unmount()
  render(<CanonicalGroupWorkspace binding={binding} />)
  await screen.findByRole('button', { name: 'Retry' })
  request.mockImplementation(async (_route, method) => method === 'groups.state'
    ? { room: { name: 'Room' }, driver_status: {} } : method === 'groups.log' ? { events: [] } : {})
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
    update: vi.fn(async (key: string, value: string | null) => {
      await new Promise<void>(resolve => { releaseWrite = resolve })

      if (value === null) {delete journal[key]} else {journal[key] = JSON.parse(value)}
    })
  }

  window.hermesDesktop = { preparedSubmissions: native } as unknown as typeof window.hermesDesktop
  request.mockImplementation(async (_route, method) => method === 'groups.state'
    ? { room: { name: 'Room' }, driver_status: {} } : method === 'groups.log' ? { events: [] } : {})
  render(<CanonicalGroupWorkspace binding={{ connectionId: 'remote', profile: 'team', roomId: 'new' }} />)
  await screen.findByText('Room')
  fireEvent.change(screen.getByRole('textbox'), { target: { value: 'New text' } })
  expect((screen.getByRole('button', { name: 'Send' }) as HTMLButtonElement).disabled).toBe(true)
  releaseRead('{}')
  await waitFor(() => expect((screen.getByRole('textbox') as HTMLTextAreaElement).disabled).toBe(false))
  fireEvent.change(screen.getByRole('textbox'), { target: { value: 'New text' } })
  fireEvent.click(screen.getByRole('button', { name: 'Send' }))
  await waitFor(() => expect(native.update).toHaveBeenCalled())
  expect(request.mock.calls.some(c => c[1] === 'groups.send')).toBe(false)
  releaseWrite()
  await waitFor(() => expect(request.mock.calls.some(c => c[1] === 'groups.send')).toBe(true))
  await waitFor(() => expect(native.update).toHaveBeenCalledTimes(2))
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

it('explicit Retry keeps the pending member and generation on groups.retry', async () => {
  const action = { kind: 'retry', member_id: 'two', task_id: 'task-uncertain', execution_generation: 4 }
  request.mockImplementation(async (_route, method) => {
    if (method === 'groups.state') {return { room: { name: 'Room' }, driver_status: { pending_actions: [action] } }}

    if (method === 'groups.log') {return { events: [], has_more: false }}

    if (method === 'groups.retry') {throw new Error('invalid_params')}

    return {}
  })
  render(<CanonicalGroupWorkspace binding={{ connectionId: 'fresh-client', profile: 'reviewer', roomId: 'room-one' }} />)
  fireEvent.click(await screen.findByRole('button', { name: 'Retry' }))
  await screen.findByText('invalid_params')
  const call = request.mock.calls.find(c => c[1] === 'groups.retry')!
  expect(call[0]).toMatchObject({ connectionId: 'fresh-client', targetProfile: 'reviewer' })
  expect(call[2]).toEqual({
    profile: 'reviewer', room_id: 'room-one', member_id: 'two', task_id: 'task-uncertain', execution_generation: 4
  })
  expect(request.mock.calls.some(c => c[1] === 'groups.approve' || c[1] === 'groups.deny')).toBe(false)
})

const selectorA = `pa-${'aa'.repeat(32)}`
const selectorB = `pa-${'bb'.repeat(32)}`

function roomWith(actions: Record<string, unknown>[]) {
  request.mockImplementation(async (_route, method) => {
    if (method === 'groups.state') {return { room: { name: 'Room' }, driver_status: { pending_actions: actions } }}

    if (method === 'groups.log') {return { events: [], has_more: false }}

    return {}
  })
}

it('approves the displayed selector and never a list position', async () => {
  roomWith([
    { kind: 'approval', member_id: 'first', task_id: 't1', request_id: 'req-first', execution_generation: 2, selector: selectorA },
    { kind: 'approval', member_id: 'second', task_id: 't2', request_id: 'req-second', execution_generation: 3, selector: selectorB },
    { kind: 'approval', member_id: 'bare', task_id: 't3', request_id: 'req-bare', execution_generation: 4 }
  ])
  render(<CanonicalGroupWorkspace binding={{ connectionId: 'fresh-client', profile: 'reviewer', roomId: 'room-one' }} />)
  expect(await screen.findByRole('button', { name: `Allow once ${selectorB}` })).toBeTruthy()
  expect(screen.queryByRole('button', { name: 'Allow once', exact: true })).toBeNull()
  expect(screen.queryByText('bare')).toBeNull()
  fireEvent.click(screen.getByRole('button', { name: `Allow once ${selectorB}` }))
  await waitFor(() => expect(request.mock.calls.some(call => call[1] === 'groups.approve')).toBe(true))
  const call = request.mock.calls.find(item => item[1] === 'groups.approve')!
  expect(call[2]).toEqual({
    profile: 'reviewer', room_id: 'room-one', member_id: 'second', task_id: 't2',
    execution_generation: 3, request_id: 'req-second', choice: 'once'
  })
  expect(call[2]).not.toHaveProperty('selector')
  expect(request.mock.calls.some(item => String(item[1]).includes('groups.messaging') || item[1] === 'groups.deny')).toBe(false)
})

it('keeps native Stop off the consent methods', async () => {
  roomWith([])
  render(<CanonicalGroupWorkspace binding={{ connectionId: 'fresh-client', profile: 'reviewer', roomId: 'room-one' }} />)
  fireEvent.click(await screen.findByRole('button', { name: 'Stop' }))
  await waitFor(() => expect(request.mock.calls.some(call => call[1] === 'groups.stop')).toBe(true))
  const call = request.mock.calls.find(item => item[1] === 'groups.stop')!
  expect(call[2].room_id).toBe('room-one')
  expect(call[2].cancel_id).toMatch(/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i)
  expect(call[2].cancel_id).not.toMatch(/^pa-/)
  expect(request.mock.calls.some(item => String(item[1]).startsWith('groups.messaging'))).toBe(false)
  expect(screen.queryByRole('button', { name: 'Grant stop consent' })).toBeNull()
})

it('grants stop consent and revokes approval consent without calling Stop or approve', async () => {
  roomWith([])
  const recipient = {
    platform: 'telegram', user_id: 'user-1', chat_id: 'chat-1', thread_id: null, scope_id: null,
    transport_profile: 'telegram', runtime_profile: 'default'
  }
  render(<CanonicalGroupWorkspace
    binding={{ connectionId: 'fresh-client', profile: 'reviewer', roomId: 'room-one' }}
    operatorControl={{
      recipient,
      roomReadBindingId: `mrr-${'cd'.repeat(16)}`,
      roomReadGeneration: 2,
      stop: null,
      approval: { bindingId: `mrc-${'ef'.repeat(16)}`, generation: 5, active: true }
    }}
  />)
  fireEvent.click(await screen.findByRole('button', { name: 'Grant stop consent' }))
  await waitFor(() => expect(request.mock.calls.some(call => call[1] === 'groups.messaging.room.stop.grant')).toBe(true))
  const grant = request.mock.calls.find(item => item[1] === 'groups.messaging.room.stop.grant')!
  expect(grant[2]).toMatchObject({
    profile: 'reviewer',
    recipient,
    room_id: 'room-one',
    room_read_binding_id: `mrr-${'cd'.repeat(16)}`,
    room_read_generation: 2,
    expected_generation: 0
  })
  expect(grant[2].request_id).toMatch(/^[0-9a-f-]{36}$/i)
  expect(grant[2]).not.toHaveProperty('binding_id')
  await waitFor(() => expect((screen.getByRole('button', { name: 'Revoke approval consent' }) as HTMLButtonElement).disabled).toBe(false))
  fireEvent.click(screen.getByRole('button', { name: 'Revoke approval consent' }))
  await waitFor(() => expect(request.mock.calls.some(call => call[1] === 'groups.messaging.room.approval.revoke')).toBe(true))
  const revoke = request.mock.calls.find(item => item[1] === 'groups.messaging.room.approval.revoke')!
  expect(revoke[2]).toMatchObject({
    binding_id: `mrc-${'ef'.repeat(16)}`,
    expected_generation: 5
  })
  expect(request.mock.calls.some(item => item[1] === 'groups.stop' || item[1] === 'groups.approve')).toBe(false)
})

it('reads back retry on the same authority and sends only through the group driver', async () => {
  request.mockImplementation(async (_route, method) => {
    if (method === 'groups.state') {return { room: { name: 'Room' }, driver_status: { pending_actions: [{ kind: 'retry', member_id: 'w', task_id: 't', execution_generation: 2 }] } }}

    if (method === 'groups.log') {return { events: [{ seq: 1, kind: 'message', payload: { text: 'Owner reply' } }], has_more: false }}

    return {}
  })
  const group = registerCanonicalGroup({ connectionId: 'local', profile: 'default' }, { room_id: 'r', name: 'Room', members: [] })
  render(<GroupChatWorkspace group={group} members={[]} />)
  fireEvent.click(await screen.findByRole('button', { name: 'Retry' }))
  await waitFor(() => expect(request.mock.calls.filter(c => c[1] === 'groups.state').length).toBeGreaterThan(1))
  fireEvent.change(screen.getByRole('textbox'), { target: { value: 'Hello' } })
  fireEvent.click(screen.getByRole('button', { name: 'Send' }))
  await waitFor(() => expect(request.mock.calls.some(c => c[1] === 'groups.send')).toBe(true))
  expect(screen.getByText('Owner reply')).toBeTruthy()
  expect(request.mock.calls.every(c => c[1].startsWith('groups.'))).toBe(true)
})

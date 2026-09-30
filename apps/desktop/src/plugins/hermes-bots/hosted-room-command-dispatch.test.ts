import { beforeEach, expect, it, vi } from 'vitest'

import { pluginSdkMock } from './group-test-utils'
import type { HostedRoomCommand } from './hosted-room-client'
import { requestHostedCommand } from './hosted-room-command-dispatch'

const host = vi.hoisted(() => ({ acquireProfileRoute: vi.fn(), requestProfile: vi.fn() }))
vi.mock('@hermes/plugin-sdk', async () => pluginSdkMock(host))

const route = { connectionId: 'owner', profile: 'default', targetProfile: 'default', mode: 'remote' as const }

const command: HostedRoomCommand = {
  commandId: 'intent',
  kind: 'retry',
  roomId: 'room',
  connectionId: 'owner',
  authorityId: 'install:home',
  status: 'in-flight',
  attempts: 1,
  failureCode: '',
  payload: { task_id: 'task' }
}

const capability = { driver: true, persistent_process: true, authority_gateway_id: 'install:home' }
const params = { room_id: 'room', task_id: 'task' }

const receipt = {
  retried: true,
  task: {
    room_id: 'room',
    task_id: 'task',
    thread_id: 'thread',
    turn_id: 'turn',
    status: 'queued',
    execution_generation: 2,
    cancel_generation: 0
  }
}

beforeEach(() => vi.resetAllMocks())

it('checks and dispatches on the same leased authority and releases it after an exact Retry receipt', async () => {
  const lease = {
    request: vi.fn().mockResolvedValueOnce(capability).mockResolvedValueOnce(receipt),
    assertCurrent: vi.fn(),
    release: vi.fn()
  }

  host.acquireProfileRoute.mockResolvedValue(lease)
  expect(await requestHostedCommand(route, command, 'groups.retry', params)).toEqual(receipt)
  expect(host.acquireProfileRoute).toHaveBeenCalledWith(route)
  expect(lease.request.mock.calls).toEqual([
    ['groups.capabilities', {}],
    ['groups.retry', params]
  ])
  expect(lease.assertCurrent).toHaveBeenCalledTimes(2)
  expect(lease.release).toHaveBeenCalledTimes(1)
  expect(host.requestProfile).not.toHaveBeenCalled()
})

it.each([
  { ...capability, features: ['canonical_session_owner'] },
  { ...capability, authority_gateway_id: 'install:other' }
])('never sends an older mutation after the leased protocol/authority changes', async capabilities => {
  const lease = { request: vi.fn().mockResolvedValue(capabilities), assertCurrent: vi.fn(), release: vi.fn() }
  host.acquireProfileRoute.mockResolvedValue(lease)
  await expect(requestHostedCommand(route, command, 'groups.retry', params)).rejects.toMatchObject({ code: 4000 })
  expect(lease.request.mock.calls).toEqual([['groups.capabilities', {}]])
  expect(lease.release).toHaveBeenCalledTimes(1)
  expect(host.requestProfile).not.toHaveBeenCalled()
})

it.each([{ room_id: 'different' }, { task_id: 'different' }, { execution_generation: 0 }])(
  'does not acknowledge a mismatched retry receipt (%s)',
  async mismatch => {
    const lease = {
      request: vi
        .fn()
        .mockResolvedValueOnce(capability)
        .mockResolvedValueOnce({ ...receipt, task: { ...receipt.task, ...mismatch } }),
      assertCurrent: vi.fn(),
      release: vi.fn()
    }

    host.acquireProfileRoute.mockResolvedValue(lease)
    await expect(requestHostedCommand(route, command, 'groups.retry', params)).rejects.toThrow('Unconfirmed')
    expect(lease.request).toHaveBeenCalledTimes(2)
    expect(lease.release).toHaveBeenCalledTimes(1)
  }
)

it('does not disclose a legacy action lacking durable installation identity', async () => {
  await expect(requestHostedCommand(route, { ...command, authorityId: null, kind: 'send' }, 'groups.send', {
    payload: { text: 'private' }
  })).rejects.toMatchObject({ code: 4000 })
  expect(host.requestProfile).not.toHaveBeenCalled()
})

it('refuses private work on an older SDK without physical route leases instead of descriptor fallback', async () => {
  const acquire = host.acquireProfileRoute
  host.acquireProfileRoute = undefined as never
  host.requestProfile.mockResolvedValue(capability)

  try {
    await expect(requestHostedCommand(route, { ...command, kind: 'send' }, 'groups.send', { payload: { text: 'private' } }))
      .rejects.toMatchObject({ code: 4000 })
    expect(host.requestProfile).not.toHaveBeenCalled()
  } finally {
    host.acquireProfileRoute = acquire
  }
})

it('does not turn a canonical/strict-schema refusal into a different execution request', async () => {
  const lease = {
    request: vi.fn().mockResolvedValueOnce(capability).mockRejectedValueOnce({ code: -32602 }),
    assertCurrent: vi.fn(),
    release: vi.fn()
  }

  host.acquireProfileRoute.mockResolvedValue(lease)
  await expect(requestHostedCommand(route, command, 'groups.retry', params)).rejects.toMatchObject({ code: -32602 })
  expect(lease.request.mock.calls).toEqual([
    ['groups.capabilities', {}],
    ['groups.retry', params]
  ])
  expect(lease.release).toHaveBeenCalledTimes(1)
})

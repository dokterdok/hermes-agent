import { afterEach, expect, it, vi } from 'vitest'

import type * as groupChat from './group-chat'
import type * as groupRounds from './group-rounds'
import { pluginSdkMock, scriptedStorage } from './group-test-utils'
import type * as hostedRuntime from './hosted-room-runtime'
import type { GroupChat, GroupMember } from './types'

const { host } = vi.hoisted(() => ({ host: {} as Record<string, unknown> }))
vi.mock('@hermes/plugin-sdk', async () => pluginSdkMock(host))

const route = { connectionId: 'gateway-a', mode: 'remote' as const, profile: 'default', targetProfile: 'default' }

const members: GroupMember[] = [
  { connectionId: 'gateway-a', name: 'research', sourceScoped: true, targetProfile: 'research' },
  { connectionId: 'gateway-a', name: 'builder', sourceScoped: true, targetProfile: 'builder' }
]

const selectedBytes = new Uint8Array([0, 255, 17, 42])
const base64 = 'AP8RKg=='
const attachmentId = 'att_0123456789abcdef0123456789abcdef'

function room(): GroupChat {
  return {
    continuityMode: 'gateway', hosted: 'install:home', hostedConnectionId: 'gateway-a',
    hostedEpoch: 1, hostedSeq: 0, log: [], members, roomId: 'room-1', watermarks: {}
  }
}

afterEach(() => {
  vi.clearAllTimers()
  vi.useRealTimers()
})

it('retains one selected file and command after a lost post-commit response until explicit Retry, never handing off twice', async () => {
  vi.useFakeTimers()
  vi.resetModules()
  const values = new Map<string, unknown>()
  const calls: Array<{ method: string; params: Record<string, unknown> }> = []
  const committed = new Map<string, Record<string, unknown>>()
  let replyLost = true

  for (const key of Object.keys(host)) { delete host[key] }
  Object.assign(host, {
    activeConnectionId: () => route.connectionId,
    notify: vi.fn(),
    profileRoutes: async () => [route],
    requestProfile: async (target: unknown, method: string, params: Record<string, unknown>) => {
      expect(target).toMatchObject(route)
      calls.push({ method, params })

      if (method === 'groups.capabilities') {
        return { authority_gateway_id: 'install:home', driver: true, persistent_process: true,
          methods: ['groups.attachment.put', 'groups.attachment.read'] }
      }

      if (method === 'groups.list') { return { rooms: [] } }

      if (method === 'groups.attachment.put') {
        expect(params).toMatchObject({ room_id: 'room-1', content_base64: base64, name: 'selected.bin',
          mime: 'application/octet-stream', kind: 'file' })

        return { attachment: { attachment_id: attachmentId, kind: 'file', mime: 'application/octet-stream',
          name: 'selected.bin', size: selectedBytes.length } }
      }

      if (method === 'groups.send') {
        const id = String(params.event_id)

        if (!committed.has(id)) { committed.set(id, params) }
        else { expect(params).toEqual(committed.get(id)) }

        if (replyLost) { throw new Error('post-commit response lost') }

        return { accepted: true }
      }

      if (method === 'groups.stop') { return { accepted: true } }

      if (method === 'groups.attachment.list') {
        return { authority: { gateway_id: 'install:home', epoch: 1 }, snapshot_seq: 1,
          has_more: false, next_cursor: null, items: [{ attachment_id: attachmentId,
            event_id: [...committed.keys()][0], seq: 1, kind: 'file', name: 'selected.bin',
            mime: 'application/octet-stream', size: selectedBytes.length,
            producer: { kind: 'member', id: 'builder', label: 'Builder' }, shared_at: 1_700_000_000 }] }
      }

      if (method === 'groups.attachment.read') {
        expect(params).toMatchObject({ room_id: 'room-1', event_id: [...committed.keys()][0],
          attachment_id: attachmentId, purpose: 'viewer' })

        return { attachment: { attachment_id: attachmentId, mime: 'application/octet-stream',
          name: 'selected.bin', size: selectedBytes.length }, content_base64: base64 }
      }

      throw new Error(`unexpected ${method}`)
    },
    state: {
      connectionId: { get: () => route.connectionId, listen: () => () => undefined },
      gateway: { get: () => 'open', listen: () => () => undefined },
      profile: { get: () => 'default', listen: () => () => undefined }
    }
  })

  const [chat, rounds, runtime, shared, files] = await Promise.all([
    import('./group-chat'), import('./group-rounds'), import('./hosted-room-runtime'),
    import('./shared'), import('./group-files-client')
  ])

  const consumer = { chat: chat as typeof groupChat, rounds: rounds as typeof groupRounds,
    runtime: runtime as typeof hostedRuntime }

  const storage = scriptedStorage(values)
  shared.setPluginCtx(storage)
  consumer.chat.$groupChats.set({ Release: room() })
  await consumer.runtime.startHostedRoomRuntime(storage.storage)
  await expect(consumer.runtime.probeHostedRoomMembers(members)).resolves.toMatchObject({ attachmentParity: true })

  const submit = consumer.rounds.sendToGroupChatDurably('Release', members, 'Inspect selected bytes', null,
    [{ data: `data:application/octet-stream;base64,${base64}`, kind: 'file', name: 'selected.bin' }])

  await expect(submit).resolves.toBeTruthy()

  for (let attempt = 0; attempt < 4; attempt++) { await consumer.runtime.dispatchHostedRoomOutbox() }
  const failed = consumer.chat.$groupChats.get().Release.hostedStatus
  const id = String(failed?.retryCommandId || '')
  expect(failed?.state).toBe('failed')
  expect(id).toBeTruthy()
  expect(committed.size).toBe(1)
  expect(calls.filter(call => call.method === 'groups.attachment.put')).toHaveLength(1)
  const attemptsBeforeRetry = calls.filter(call => call.method === 'groups.send').length
  await consumer.runtime.dispatchHostedRoomOutbox()
  expect(calls.filter(call => call.method === 'groups.send')).toHaveLength(attemptsBeforeRetry)
  expect(values.get('hosted-room-outbox-v1')).toMatchObject({ commands: [expect.objectContaining({
    commandId: id, kind: 'send', status: 'failed', payload: expect.objectContaining({
      text: 'Inspect selected bytes', attachments: [expect.objectContaining({
        attachment_id: attachmentId, name: 'selected.bin', size: selectedBytes.length
      })]
    })
  })] })

  // The Files consumer resolves the committed identity and its selected bytes,
  // rather than staging or fabricating a second file after the uncertain reply.
  const page = await files.listHostedGroupFiles('Release')
  expect(page.items).toHaveLength(1)
  const selected = page.items[0]
  expect(selected.eventId).toBe(id)
  expect(selected.attachment.attachmentId).toBe(attachmentId)

  const read = await consumer.runtime.readHostedGroupChatAttachment('Release',
    { at: 1, eventId: selected.eventId, from: { kind: 'user', name: 'You' }, text: '' }, selected.attachment)

  expect(read.data).toBe(`data:application/octet-stream;base64,${base64}`)
  expect(Uint8Array.from(atob(read.data!.split(',')[1]), char => char.charCodeAt(0))).toEqual(selectedBytes)
  expect(committed.get(id)).toMatchObject({ room_id: 'room-1', event_id: id,
    payload: { attachments: [{ attachment_id: attachmentId, name: 'selected.bin' }] } })

  replyLost = false
  expect(await consumer.runtime.retryFailedHostedRoomCommand('Release', id)).toBe(true)
  expect(calls.filter(call => call.method === 'groups.send')).toHaveLength(attemptsBeforeRetry + 1)
  expect(calls.filter(call => call.method === 'groups.attachment.put')).toHaveLength(1)
  expect(committed.size).toBe(1)
  expect(await consumer.runtime.retryFailedHostedRoomCommand('Release', id)).toBe(false)

  // A second, explicitly distinct command can fail, but Stop retires its
  // retry authority. A late click on that stale Retry must not send again.
  replyLost = true
  await expect(consumer.rounds.sendToGroupChatDurably('Release', members, 'Cancel this one')).resolves.toBeTruthy()

  for (let attempt = 0; attempt < 4; attempt++) { await consumer.runtime.dispatchHostedRoomOutbox() }
  const stoppedId = String(consumer.chat.$groupChats.get().Release.hostedStatus?.retryCommandId || '')
  expect(stoppedId).toBeTruthy()
  expect(stoppedId).not.toBe(id)
  const beforeStop = calls.filter(call => call.method === 'groups.send').length
  expect(await consumer.runtime.stopHostedGroupChat('Release')).toBe(true)
  expect(calls.filter(call => call.method === 'groups.stop')).toHaveLength(1)
  expect(values.get('hosted-room-outbox-v1')).toMatchObject({ commands: [] })
  expect(await consumer.runtime.retryFailedHostedRoomCommand('Release', stoppedId)).toBe(false)
  expect(await consumer.runtime.retryFailedHostedRoomCommand('Release', id)).toBe(false)
  expect(calls.filter(call => call.method === 'groups.send')).toHaveLength(beforeStop)
  expect(committed.size).toBe(2)
  consumer.runtime.stopHostedRoomRuntime()
})

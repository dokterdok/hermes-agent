import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type * as groupChat from './group-chat'
import type * as groupRounds from './group-rounds'
import { pluginSdkMock, scriptedStorage } from './group-test-utils'
import type * as hostedRuntime from './hosted-room-runtime'
import type { GroupChat, GroupMember } from './types'

const { host } = vi.hoisted(() => ({
  host: {} as Record<string, unknown>
}))

vi.mock('@hermes/plugin-sdk', async () => pluginSdkMock(host))

interface RpcCall {
  method: string
  params: Record<string, unknown>
}

interface RuntimeRoom {
  chat: typeof groupChat
  calls: RpcCall[]
  rounds: typeof groupRounds
  runtime: typeof hostedRuntime
  storage: Map<string, unknown>
}

const MEMBERS: GroupMember[] = [
  {
    name: 'research',
    connectionId: 'gateway-a',
    sourceScoped: true,
    targetProfile: 'research'
  },
  {
    name: 'builder',
    connectionId: 'gateway-a',
    sourceScoped: true,
    targetProfile: 'builder'
  }
]

function room(overrides: Partial<GroupChat> = {}): GroupChat {
  return {
    log: [],
    watermarks: {},
    members: MEMBERS,
    roomId: 'room-1',
    hosted: 'install:home',
    hostedEpoch: 1,
    hostedConnectionId: 'gateway-a',
    hostedSeq: 0,
    continuityMode: 'gateway',
    ...overrides
  }
}

function hostedEvent(
  seq: number,
  eventId: string,
  kind: string,
  payload: Record<string, unknown> = {},
  actor: Record<string, unknown> = {
    kind: 'gateway',
    id: 'install:home'
  }
) {
  return {
    room_id: 'room-1',
    seq,
    event_id: eventId,
    kind,
    actor,
    payload,
    created_at: seq
  }
}

async function loadRuntime(
  handler: (method: string, params: Record<string, unknown>) => Promise<unknown> | unknown
): Promise<RuntimeRoom> {
  vi.resetModules()
  const calls: RpcCall[] = []
  const storage = new Map<string, unknown>()

  const route = {
    connectionId: 'gateway-a',
    mode: 'remote' as const,
    profile: 'default',
    targetProfile: 'default'
  }

  for (const key of Object.keys(host)) {
    delete host[key]
  }

  Object.assign(host, {
    activeConnectionId: () => 'gateway-a',
    notify: vi.fn(),
    profileRoutes: async () => [route],
    request: async (method: string, params: Record<string, unknown>) => {
      calls.push({
        method,
        params
      })

      return handler(method, params)
    },
    requestProfile: async (_route: unknown, method: string, params: Record<string, unknown>) => {
      calls.push({
        method,
        params
      })

      return handler(method, params)
    },
    state: {
      connectionId: {
        get: () => 'gateway-a',
        listen: () => () => undefined
      },
      gateway: {
        get: () => 'open',
        listen: () => () => undefined
      },
      profile: {
        get: () => 'default',
        listen: () => () => undefined
      }
    }
  })

  const [chat, rounds, runtime, shared] = await Promise.all([
    import('./group-chat'),
    import('./group-rounds'),
    import('./hosted-room-runtime'),
    import('./shared')
  ])

  shared.setPluginCtx(scriptedStorage(storage))

  return {
    chat,
    calls,
    rounds,
    runtime,
    storage
  }
}

beforeEach(() => {
  vi.useFakeTimers()
})

afterEach(() => {
  vi.clearAllTimers()
  vi.useRealTimers()
})

describe('hosted Group Chat runtime', () => {
  it('hydrates after local state, reconciles optimistic ids, and replays one contiguous gateway log', async () => {
    const events = [
      hostedEvent(1, 'created-1', 'room.created', {
        name: 'Release',
        members: [
          {
            member_id: 'research',
            profile: 'research'
          },
          {
            member_id: 'builder',
            profile: 'builder'
          }
        ]
      }),
      hostedEvent(
        2,
        'user-1',
        'message.user',
        {
          text: 'Start',
          thread_id: 'thread-1'
        },
        {
          kind: 'user',
          id: 'desktop'
        }
      ),
      hostedEvent(
        3,
        'member-1',
        'message.member',
        {
          text: 'Done',
          thread_id: 'thread-1'
        },
        {
          kind: 'member',
          id: 'builder',
          display_name: 'Builder'
        }
      ),
      hostedEvent(4, 'settled-1', 'turn.settled')
    ]

    const loaded = await loadRuntime(method => {
      if (method === 'groups.capabilities') {
        return {
          driver: true,
          persistent_process: true,
          authority_gateway_id: 'install:home',
          max_log_limit: 100
        }
      }

      if (method === 'groups.list') {
        return {
          rooms: [
            {
              room_id: 'room-1',
              name: 'Release',
              members: MEMBERS,
              authority_gateway_id: 'install:home',
              authority_epoch: 1,
              disbanded_at: null
            }
          ]
        }
      }

      if (method === 'groups.state') {
        return {
          room: {
            room_id: 'room-1',
            name: 'Release',
            members: [
              {
                member_id: 'research',
                profile: 'research'
              },
              {
                member_id: 'builder',
                profile: 'builder'
              }
            ],
            authority_gateway_id: 'install:home',
            authority_epoch: 1,
            disbanded_at: null
          },
          driver_status: {
            working: false
          }
        }
      }

      if (method === 'groups.log') {
        return {
          events,
          latest_seq: 4,
          has_more: false
        }
      }

      throw new Error(`unexpected method: ${method}`)
    })

    loaded.chat.$groupChats.set({
      Release: room({
        log: [
          {
            at: 2,
            from: {
              kind: 'user',
              name: 'You'
            },
            id: 'user-1',
            text: 'Start',
            thread: 'thread-1'
          }
        ]
      })
    })

    await loaded.runtime.startHostedRoomRuntime(scriptedStorage(loaded.storage).storage)

    const hydrated = loaded.chat.$groupChats.get().Release

    expect(hydrated.continuityMode).toBe('gateway')
    expect(hydrated.hostedSeq).toBe(4)
    expect(hydrated.log.map(entry => [entry.seq, entry.id, entry.text])).toEqual([
      [2, 'user-1', 'Start'],
      [3, 'member-1', 'Done']
    ])
    expect(hydrated.log.filter(entry => entry.id === 'user-1')).toHaveLength(1)
    expect(hydrated.running).toBe(false)
    expect(hydrated.hostedStatus).toMatchObject({
      state: 'ready',
      label: 'Ready'
    })
    expect(loaded.storage.get('group-chats')).toBeTruthy()

    loaded.runtime.stopHostedRoomRuntime()
  })

  it('degrades an old gateway without starting the classic Desktop round driver', async () => {
    const missing = Object.assign(new Error('method not found'), {
      code: -32601
    })

    const loaded = await loadRuntime(method => {
      if (method === 'groups.capabilities' || method === 'groups.send') {
        throw missing
      }

      throw new Error(`unexpected method: ${method}`)
    })

    loaded.chat.$groupChats.set({
      Legacy: room({
        log: [
          {
            at: 1,
            from: {
              kind: 'user',
              name: 'You'
            },
            id: 'legacy-1',
            text: 'Keep going',
            thread: 'thread-1'
          }
        ]
      })
    })

    const localProjection = loaded.chat.groupChatSyncSnapshot(loaded.chat.$groupChats.get())

    const mergedProjection = loaded.chat.mergeGroupChatSyncSnapshots(
      {
        version: 3,
        rooms: {
          'id:room-1': {
            name: 'Legacy',
            roomId: 'room-1',
            log: [],
            revision: 9,
            hosted: 'install:untrusted-projection',
            hostedEpoch: 9,
            continuityMode: 'gateway'
          }
        }
      },
      localProjection
    )

    expect(mergedProjection.rooms['id:room-1']).toMatchObject({
      hosted: 'install:home',
      hostedEpoch: 1,
      continuityMode: 'gateway'
    })

    await loaded.runtime.startHostedRoomRuntime(scriptedStorage(loaded.storage).storage)

    expect(loaded.chat.$groupChats.get().Legacy).toMatchObject({
      hosted: 'install:home',
      continuityMode: 'gateway',
      running: false,
      hostedStatus: {
        state: 'unsupported',
        label: 'Update this host to keep this Group Chat running.'
      }
    })

    const thread = loaded.rounds.sendToGroupChat('Legacy', MEMBERS, 'Continue', null, [])

    await expect(Promise.resolve(thread)).resolves.toBeTruthy()
    expect(loaded.calls.some(call => call.method === 'session.create' || call.method === 'prompt.submit')).toBe(false)
    expect(loaded.chat.$groupChats.get().Legacy.hosted).toBe('install:home')

    loaded.runtime.stopHostedRoomRuntime()
  })

  it('persists send, stop, and disband commands before dispatch and acknowledges them idempotently', async () => {
    const loaded = await loadRuntime(method => {
      if (method === 'groups.capabilities') {
        return {
          driver: true,
          persistent_process: true,
          authority_gateway_id: 'install:home'
        }
      }

      if (method === 'groups.list') {
        return {
          rooms: []
        }
      }

      if (method === 'groups.create') {
        return {
          room: {
            room_id: 'room-new',
            authority_gateway_id: 'install:home',
            authority_epoch: 1
          }
        }
      }

      if (method === 'groups.send' || method === 'groups.stop' || method === 'groups.disband') {
        return {
          ok: true
        }
      }

      throw new Error(`unexpected method: ${method}`)
    })

    loaded.chat.$groupChats.set({
      Release: room()
    })
    await loaded.runtime.startHostedRoomRuntime(scriptedStorage(loaded.storage).storage)

    const probe = await loaded.runtime.probeHostedRoomMembers(MEMBERS)

    await expect(
      loaded.runtime.createHostedGroupChat({
        route: probe.route,
        roomId: 'room-new',
        name: 'New Group',
        members: MEMBERS.map(member => ({
          member_id: member.name,
          profile: member.name,
          handle: member.name
        }))
      })
    ).resolves.toEqual({
      authorityId: 'install:home',
      authorityEpoch: 1,
      connectionId: 'gateway-a'
    })

    await expect(
      loaded.runtime.sendHostedGroupChat(
        'Release',
        {
          at: 1,
          from: {
            kind: 'user',
            name: 'You'
          },
          id: 'send-1',
          text: 'Ship it',
          thread: 'thread-1'
        },
        'thread-1'
      )
    ).resolves.toBe(true)
    await expect(loaded.runtime.stopHostedGroupChat('Release')).resolves.toBe(true)
    await expect(loaded.runtime.disbandHostedGroupChat('Release')).resolves.toBe(true)

    expect(loaded.calls.map(call => call.method)).toEqual(
      expect.arrayContaining(['groups.create', 'groups.send', 'groups.stop', 'groups.disband'])
    )
    expect((loaded.storage.get('hosted-room-outbox-v1') as { commands: unknown[] }).commands).toEqual([])

    loaded.runtime.stopHostedRoomRuntime()
  })

  it('uploads attachment bytes before enqueue and reopens only verified gateway data', async () => {
    const attachment = {
      attachment_id: 'att_11111111111111111111111111111111',
      kind: 'pdf' as const,
      mime: 'application/pdf',
      name: 'brief.pdf',
      size: 3
    }
    const loaded = await loadRuntime((method, params) => {
      if (method === 'groups.capabilities') {
        return {
          authority_gateway_id: 'install:home',
          driver: true,
          features: ['attachment_ids', 'attachment_same_gateway_delivery'],
          methods: ['groups.attachment.put', 'groups.attachment.read'],
          persistent_process: true
        }
      }

      if (method === 'groups.list') {
        return {
          rooms: []
        }
      }

      if (method === 'groups.attachment.put') {
        expect(params).toMatchObject({
          room_id: 'room-1',
          upload_id: 'send-file:upload:0',
          content_base64: 'JVBERg=='
        })

        return {
          attachment
        }
      }

      if (method === 'groups.send') {
        return {
          accepted: true
        }
      }

      if (method === 'groups.attachment.read') {
        return {
          attachment,
          content_base64: 'JVBERg=='
        }
      }

      throw new Error(`unexpected method: ${method}`)
    })

    loaded.chat.$groupChats.set({
      Release: room()
    })
    await loaded.runtime.startHostedRoomRuntime(scriptedStorage(loaded.storage).storage)

    expect(loaded.runtime.hostedRoomAcceptsAttachments(room())).toBe(true)
    await expect(
      loaded.runtime.sendHostedGroupChat(
        'Release',
        {
          at: 1,
          from: {
            kind: 'user',
            name: 'You'
          },
          id: 'send-file',
          text: 'Review this',
          thread: 'thread-1'
        },
        'thread-1',
        [
          {
            data: 'data:application/pdf;base64,JVBERg==',
            kind: 'pdf',
            mime: 'application/pdf',
            name: 'brief.pdf',
            size: 3
          }
        ]
      )
    ).resolves.toBe(true)

    const send = loaded.calls.find(call => call.method === 'groups.send')

    expect(send?.params).toMatchObject({
      event_id: 'send-file',
      payload: {
        attachments: [attachment],
        text: 'Review this',
        thread_id: 'thread-1'
      },
      room_id: 'room-1'
    })
    expect(JSON.stringify(send)).not.toContain('JVBERg')
    await expect(
      loaded.runtime.loadHostedGroupAttachmentData(
        room(),
        {
          at: 1,
          eventId: 'send-file',
          from: {
            kind: 'user',
            name: 'You'
          },
          text: 'Review this',
          thread: 'thread-1'
        },
        {
          ...attachment,
          connectionId: 'gateway-a'
        }
      )
    ).resolves.toBe('data:application/pdf;base64,JVBERg==')

    loaded.runtime.stopHostedRoomRuntime()
  })

  it('confirms an unknown create outcome before allowing Desktop fallback', async () => {
    const loaded = await loadRuntime(method => {
      if (method === 'groups.capabilities') {
        return {
          driver: true,
          persistent_process: true,
          authority_gateway_id: 'install:home'
        }
      }

      if (method === 'groups.list') {
        return {
          rooms: []
        }
      }

      if (method === 'groups.create') {
        throw new Error('response lost')
      }

      if (method === 'groups.state') {
        return {
          room: {
            room_id: 'room-new',
            name: 'New Group',
            authority_gateway_id: 'install:home',
            authority_epoch: 1
          }
        }
      }

      throw new Error(`unexpected method: ${method}`)
    })

    await loaded.runtime.startHostedRoomRuntime(scriptedStorage(loaded.storage).storage)
    const probe = await loaded.runtime.probeHostedRoomMembers(MEMBERS)

    await expect(
      loaded.runtime.createHostedGroupChat({
        route: probe.route,
        roomId: 'room-new',
        name: 'New Group',
        members: MEMBERS.map(member => ({
          member_id: member.name,
          profile: member.name,
          handle: member.name
        }))
      })
    ).resolves.toEqual({
      authorityId: 'install:home',
      authorityEpoch: 1,
      connectionId: 'gateway-a'
    })
    expect(loaded.calls.map(call => call.method)).toEqual(expect.arrayContaining(['groups.create', 'groups.state']))

    loaded.runtime.stopHostedRoomRuntime()
  })

  it('retries a hosted rename with the same idempotency key', async () => {
    let renameAttempts = 0

    const loaded = await loadRuntime(method => {
      if (method === 'groups.capabilities') {
        return {
          driver: true,
          persistent_process: true,
          authority_gateway_id: 'install:home'
        }
      }

      if (method === 'groups.list') {
        return {
          rooms: []
        }
      }

      if (method === 'groups.rename') {
        renameAttempts += 1

        if (renameAttempts === 1) {
          throw new Error('connection closed')
        }

        return {
          room: {
            room_id: 'room-1',
            name: 'Renamed'
          }
        }
      }

      throw new Error(`unexpected method: ${method}`)
    })

    loaded.chat.$groupChats.set({
      Release: room()
    })
    await loaded.runtime.startHostedRoomRuntime(scriptedStorage(loaded.storage).storage)

    await expect(loaded.runtime.renameHostedGroupChat('Release', 'Renamed')).resolves.toBe(false)

    const pending = loaded.storage.get('hosted-room-outbox-v1') as {
      commands: Array<{ commandId: string; kind: string; status: string }>
    }

    expect(pending.commands).toEqual([
      expect.objectContaining({
        kind: 'rename',
        status: 'pending'
      })
    ])

    await loaded.runtime.dispatchHostedRoomOutbox()

    const calls = loaded.calls.filter(call => call.method === 'groups.rename')

    expect(calls).toHaveLength(2)
    expect(calls[0].params.event_id).toBe(calls[1].params.event_id)
    expect(calls[1].params).toMatchObject({
      room_id: 'room-1',
      name: 'Renamed'
    })
    expect(loaded.storage.get('hosted-room-outbox-v1')).toMatchObject({
      commands: []
    })

    loaded.runtime.stopHostedRoomRuntime()
  })

  it('replays an unknown in-flight send after Desktop closes with the same command id', async () => {
    let releaseFirstSend: () => void = () => undefined
    let firstSend = true

    const loaded = await loadRuntime(method => {
      if (method === 'groups.capabilities') {
        return {
          driver: true,
          persistent_process: true,
          authority_gateway_id: 'install:home'
        }
      }

      if (method === 'groups.list') {
        return {
          rooms: []
        }
      }

      if (method === 'groups.send' && firstSend) {
        firstSend = false

        return new Promise(resolve => {
          releaseFirstSend = () =>
            resolve({
              ok: true
            })
        })
      }

      if (method === 'groups.send') {
        return {
          ok: true
        }
      }

      throw new Error(`unexpected method: ${method}`)
    })

    loaded.chat.$groupChats.set({
      Release: room()
    })
    const storage = scriptedStorage(loaded.storage).storage

    await loaded.runtime.startHostedRoomRuntime(storage)

    const delivery = loaded.runtime.sendHostedGroupChat(
      'Release',
      {
        at: 1,
        from: {
          kind: 'user',
          name: 'You'
        },
        id: 'send-after-close',
        text: 'Keep working',
        thread: 'thread-1'
      },
      'thread-1'
    )

    for (let attempt = 0; attempt < 10; attempt++) {
      await Promise.resolve()
    }

    expect(loaded.storage.get('hosted-room-outbox-v1')).toMatchObject({
      commands: [
        {
          commandId: 'send-after-close',
          status: 'in-flight'
        }
      ]
    })

    loaded.runtime.stopHostedRoomRuntime()
    releaseFirstSend()
    await expect(delivery).resolves.toBe(false)

    await loaded.runtime.startHostedRoomRuntime(storage)

    expect(loaded.calls.filter(call => call.method === 'groups.send')).toHaveLength(2)
    expect(loaded.storage.get('hosted-room-outbox-v1')).toMatchObject({
      commands: []
    })

    loaded.runtime.stopHostedRoomRuntime()
  })
})

import { afterEach, describe, expect, it, vi } from 'vitest'

import type * as groupChat from './group-chat'
import type * as groupRounds from './group-rounds'
import { pluginSdkMock, scriptedStorage } from './group-test-utils'
import type * as groupTurns from './group-turns'
import type * as hostedRuntime from './hosted-room-runtime'
import type { GroupChat, GroupMember } from './types'

const { host } = vi.hoisted(() => ({
  host: {} as Record<string, unknown>
}))

vi.mock('@hermes/plugin-sdk', async () => pluginSdkMock(host))

const MEMBERS: GroupMember[] = [
  { connectionId: 'gateway-a', name: 'research', sourceScoped: true, targetProfile: 'research' },
  { connectionId: 'gateway-a', name: 'builder', sourceScoped: true, targetProfile: 'builder' }
]

function room(overrides: Partial<GroupChat> = {}): GroupChat {
  return {
    continuityMode: 'gateway',
    hosted: 'install:home',
    hostedConnectionId: 'gateway-a',
    hostedEpoch: 1,
    hostedSeq: 0,
    log: [],
    members: MEMBERS,
    roomId: 'room-1',
    watermarks: {},
    ...overrides
  }
}

// Minimum identity-bearing groups.send projection from methods_groups.py.
// Unlike a bare accepted flag, this can confirm a same-key Send replay.
function sendReceipt(params: Record<string, unknown>) {
  return {
    accepted: true,
    client_event_id: params.event_id,
    event: { kind: 'message.user', room_id: params.room_id }
  }
}

async function loadRuntime(
  handler: (method: string, params: Record<string, unknown>, route: Record<string, unknown>) => unknown,
  routes: Record<string, unknown>[] = [
    { connectionId: 'gateway-a', mode: 'remote', profile: 'default', targetProfile: 'default' }
  ]
) {
  vi.resetModules()
  const calls: Array<{ connectionId: string; method: string; params: Record<string, unknown> }> = []
  const values = new Map<string, unknown>()

  for (const key of Object.keys(host)) {
    delete host[key]
  }

  const request = async (route: Record<string, unknown>, method: string, params: Record<string, unknown>) => {
    calls.push({ connectionId: String(route.connectionId || ''), method, params })

    return handler(method, params, route)
  }

  Object.assign(host, {
    activeConnectionId: () => String(routes[0]?.connectionId || ''),
    notify: vi.fn(),
    profileRoutes: async () => routes,
    requestProfile: request,
    // These fixtures have fixed endpoints. Capture the route, not a fresh
    // descriptor lookup; the actual SDK/registry replacement and ABA contract
    // is exercised separately in sdk/hosted-outbox-registry.test.ts.
    acquireProfileRoute: async (route: Record<string, unknown>) => {
      const captured = Object.freeze({ ...route })
      let released = false

      const assertCurrent = () => {
        if (released) {
          throw new Error('Route lease has been released')
        }
      }

      return {
        route: captured,
        assertCurrent,
        release: () => {
          released = true
        },
        request: (method: string, params: Record<string, unknown> = {}) => {
          assertCurrent()

          return request(captured, method, params)
        }
      }
    },
    state: {
      connectionId: { get: () => String(routes[0]?.connectionId || ''), listen: () => () => undefined },
      gateway: { get: () => 'open', listen: () => () => undefined },
      profile: { get: () => 'default', listen: () => () => undefined }
    }
  })

  const [chat, rounds, runtime, turns, shared] = await Promise.all([
    import('./group-chat'),
    import('./group-rounds'),
    import('./hosted-room-runtime'),
    import('./group-turns'),
    import('./shared')
  ])

  shared.setPluginCtx(scriptedStorage(values))

  return {
    calls,
    chat: chat as typeof groupChat,
    rounds: rounds as typeof groupRounds,
    runtime: runtime as typeof hostedRuntime,
    storage: scriptedStorage(values).storage,
    turns: turns as typeof groupTurns,
    values
  }
}

afterEach(() => {
  vi.clearAllTimers()
  vi.useRealTimers()
})

describe('hosted Group Chat client safety', () => {
  it('requires file parity before automatic hosting', async () => {
    const loaded = await loadRuntime(method => {
      if (method === 'groups.capabilities') {
        return { attachments: false, authority_gateway_id: 'install:home', driver: true, persistent_process: true }
      }

      throw new Error(`unexpected method: ${method}`)
    })

    await expect(loaded.runtime.probeHostedRoomMembers(MEMBERS)).resolves.toMatchObject({
      attachmentParity: false,
      eligible: true
    })
  })

  it('requires every remote RoomLink to advertise file delivery', async () => {
    let remoteAttachments = false

    const routes = [
      { connectionId: 'host-a', mode: 'remote', profile: 'default', targetProfile: 'default' },
      { connectionId: 'host-b', mode: 'remote', profile: 'default', targetProfile: 'default' }
    ]

    const loaded = await loadRuntime((method, _params, route) => {
      if (method !== 'groups.capabilities') {
        throw new Error(`unexpected method: ${method}`)
      }

      const remote = route.connectionId === 'host-b'

      return {
        authority_gateway_id: remote ? 'install:remote' : 'install:home',
        driver: true,
        methods: ['groups.attachment.put', 'groups.attachment.read'],
        persistent_process: true,
        room_link: {
          catalog: {
            attachments: remote ? remoteAttachments : true,
            catalog_digest: remote ? 'b'.repeat(64) : 'a'.repeat(64),
            installation_id: remote ? 'install:remote' : 'install:home',
            link_modes: ['direct'],
            persistent_process: true,
            protocol_versions: [2],
            text: true
          },
          enabled: true,
          endpoint: { available: true, url: remote ? 'https://remote.example.test' : 'https://home.example.test' },
          profile: 'default'
        }
      }
    }, routes)

    const members: GroupMember[] = [
      { connectionId: 'host-a', name: 'research', sourceScoped: true, targetProfile: 'research' },
      { connectionId: 'host-b', name: 'builder', sourceScoped: true, targetProfile: 'builder' }
    ]

    await expect(loaded.runtime.probeHostedRoomMembers(members)).resolves.toMatchObject({
      attachmentParity: false,
      route: { kind: 'multi-gateway' }
    })
    remoteAttachments = true
    await expect(loaded.runtime.probeHostedRoomMembers(members)).resolves.toMatchObject({ attachmentParity: true })
    remoteAttachments = false
    loaded.chat.$groupChats.set({
      Distributed: room({ continuityMode: 'distributed', members })
    })
    await expect(
      loaded.rounds.sendToGroupChatDurably('Distributed', members, 'Review this', null, [
        { data: 'data:image/png;base64,YQ==', kind: 'image', name: 'proof.png' }
      ])
    ).rejects.toThrow('cannot reach builder')
    expect(loaded.calls.some(call => call.method === 'groups.attachment.put')).toBe(false)
    expect(loaded.chat.$groupChats.get().Distributed.log).toEqual([])
  })

  it('probes member gateways concurrently before a file send', async () => {
    const routes = [
      { connectionId: 'host-a', mode: 'remote', profile: 'default', targetProfile: 'default' },
      { connectionId: 'host-b', mode: 'remote', profile: 'default', targetProfile: 'default' }
    ]

    const releases = new Map<string, (value: Record<string, unknown>) => void>()

    const loaded = await loadRuntime((_method, _params, route) => {
      const connectionId = String(route.connectionId || '')

      return new Promise(resolve => {
        releases.set(connectionId, resolve)
      })
    }, routes)

    const members: GroupMember[] = [
      { connectionId: 'host-a', name: 'research', sourceScoped: true, targetProfile: 'research' },
      { connectionId: 'host-b', name: 'builder', sourceScoped: true, targetProfile: 'builder' }
    ]

    const probe = loaded.runtime.probeHostedRoomMembers(members)

    for (let tick = 0; tick < 10 && releases.size < 2; tick++) {
      await Promise.resolve()
    }

    expect([...releases.keys()].sort()).toEqual(['host-a', 'host-b'])

    for (const [connectionId, release] of releases) {
      release({
        attachments: true,
        authority_gateway_id: connectionId === 'host-a' ? 'install:home' : 'install:remote',
        driver: true,
        methods: ['groups.attachment.put', 'groups.attachment.read'],
        persistent_process: true,
        room_link: {
          catalog: {
            attachments: true,
            catalog_digest: (connectionId === 'host-a' ? 'a' : 'b').repeat(64),
            installation_id: connectionId === 'host-a' ? 'install:home' : 'install:remote',
            link_modes: ['direct'],
            persistent_process: true,
            protocol_versions: [2],
            text: true
          },
          enabled: true,
          endpoint: { available: true, url: `https://${connectionId}.example.test` },
          profile: 'default'
        }
      })
    }

    await expect(probe).resolves.toMatchObject({ attachmentParity: true })
  })

  it('stages and replays files when the Desktop and gateway advertise the complete client contract', async () => {
    vi.useFakeTimers()

    const loaded = await loadRuntime((method, params) => {
      if (method === 'groups.capabilities') {
        return {
          authority_gateway_id: 'install:home',
          driver: true,
          methods: ['groups.attachment.put', 'groups.attachment.read'],
          persistent_process: true
        }
      }

      if (method === 'groups.list') {
        return { rooms: [] }
      }

      if (method === 'groups.attachment.put') {
        expect(params).toMatchObject({
          content_base64: 'YQ==',
          kind: 'image',
          mime: 'image/png',
          name: 'proof.png',
          room_id: 'room-1'
        })

        return {
          attachment: {
            attachment_id: 'att_0123456789abcdef0123456789abcdef',
            kind: 'image',
            mime: 'image/png',
            name: 'proof.png',
            size: 1
          }
        }
      }

      if (method === 'groups.send') {
        expect(params).toMatchObject({
          payload: {
            attachments: [
              {
                attachment_id: 'att_0123456789abcdef0123456789abcdef',
                kind: 'image',
                mime: 'image/png',
                name: 'proof.png',
                size: 1
              }
            ],
            text: 'Review this',
            thread_id: 'thread-1'
          }
        })

        return sendReceipt(params)
      }

      if (method === 'groups.attachment.read') {
        return {
          attachment: {
            attachment_id: 'att_0123456789abcdef0123456789abcdef',
            mime: 'image/png',
            name: 'proof.png',
            size: 1
          },
          content_base64: 'YQ=='
        }
      }

      throw new Error(`unexpected method: ${method}`)
    })

    loaded.chat.$groupChats.set({ Release: room() })
    await loaded.runtime.startHostedRoomRuntime(loaded.storage)
    await expect(loaded.runtime.probeHostedRoomMembers(MEMBERS)).resolves.toMatchObject({ attachmentParity: true })
    await expect(
      loaded.runtime.sendHostedGroupChat(
        'Release',
        {
          at: 1,
          from: { kind: 'user', name: 'You' },
          id: 'send-file',
          images: [{ data: 'data:image/png;base64,YQ==', kind: 'image', name: 'proof.png' }],
          text: 'Review this',
          thread: 'thread-1'
        },
        'thread-1'
      )
    ).resolves.toBe(true)
    await expect(
      loaded.runtime.readHostedGroupChatAttachment(
        'Release',
        { at: 1, eventId: 'event-1', from: { kind: 'user', name: 'You' }, text: '', thread: 'thread-1' },
        {
          attachmentId: 'att_0123456789abcdef0123456789abcdef',
          kind: 'image',
          mime: 'image/png',
          name: 'proof.png',
          size: 1
        }
      )
    ).resolves.toMatchObject({ data: 'data:image/png;base64,YQ==' })
    loaded.runtime.stopHostedRoomRuntime()
  })

  it('holds later Stop behind attachment staging and paints only after durable enqueue', async () => {
    vi.useFakeTimers()
    let releaseUpload: () => void = () => undefined
    let uploadStarted = false

    const loaded = await loadRuntime((method, params) => {
      if (method === 'groups.capabilities') {
        return {
          authority_gateway_id: 'install:home',
          driver: true,
          methods: ['groups.attachment.put', 'groups.attachment.read'],
          persistent_process: true
        }
      }

      if (method === 'groups.list') {
        return { rooms: [] }
      }

      if (method === 'groups.attachment.put') {
        uploadStarted = true

        return new Promise(resolve => {
          releaseUpload = () =>
            resolve({
              attachment: {
                attachment_id: 'att_0123456789abcdef0123456789abcdef',
                kind: 'image',
                mime: 'image/png',
                name: 'proof.png',
                size: 1
              }
            })
        })
      }

      if (method === 'groups.send') {
        return sendReceipt(params)
      }

      if (method === 'groups.stop') {
        return { cancelled: 1 }
      }

      throw new Error(`unexpected method: ${method}`)
    })

    loaded.chat.$groupChats.set({ Release: room() })
    await loaded.runtime.startHostedRoomRuntime(loaded.storage)

    const delivery = loaded.rounds.sendToGroupChatDurably('Release', MEMBERS, 'Review this', null, [
      { data: 'data:image/png;base64,YQ==', kind: 'image', name: 'proof.png' }
    ])

    for (let attempt = 0; attempt < 100 && !uploadStarted; attempt++) {
      await Promise.resolve()
    }

    const stop = loaded.runtime.stopHostedGroupChat('Release')

    for (let attempt = 0; attempt < 20; attempt++) {
      await Promise.resolve()
    }

    expect(uploadStarted).toBe(true)
    expect(loaded.chat.$groupChats.get().Release.log).toEqual([])
    expect((loaded.values.get('hosted-room-outbox-v1') ?? { commands: [] }) as { commands: unknown[] }).toMatchObject({
      commands: []
    })

    releaseUpload()
    await expect(delivery).resolves.toBeTruthy()
    await stop
    await loaded.runtime.dispatchHostedRoomOutbox()

    let ordered: string[] = []

    for (let attempt = 0; attempt < 500; attempt++) {
      ordered = loaded.calls
        .filter(call => call.method === 'groups.send' || call.method === 'groups.stop')
        .map(call => call.method)

      if (ordered.length === 2) {
        break
      }

      await Promise.resolve()
    }

    expect(ordered).toEqual(['groups.send', 'groups.stop'])
    expect(loaded.values.get('hosted-room-outbox-v1')).toMatchObject({ commands: [] })
    expect(loaded.chat.$groupChats.get().Release.log).toHaveLength(1)
    loaded.runtime.stopHostedRoomRuntime()
  })

  it('does not paint or discard a Send refused before handler admission', async () => {
    vi.useFakeTimers()
    let releaseBlockingSend: () => void = () => undefined

    const loaded = await loadRuntime((method, params) => {
      if (method === 'groups.capabilities') {
        return { authority_gateway_id: 'install:home', driver: true, persistent_process: true }
      }

      if (method === 'groups.list') {
        return { rooms: [] }
      }

      if (method === 'groups.send') {
        if (params.event_id === 'blocking-send') {
          return new Promise(resolve => {
            releaseBlockingSend = () => resolve(sendReceipt(params))
          })
        }

        // Dispatcher validation refusal, not a HostedRoomError: service.send
        // can raise 4111 after append_user_event has already committed.
        throw Object.assign(new Error('invalid Send parameters'), { code: -32602 })
      }

      throw new Error(`unexpected method: ${method}`)
    })

    loaded.chat.$groupChats.set({
      Busy: room({ roomId: 'room-busy' }),
      Release: room()
    })
    await loaded.runtime.startHostedRoomRuntime(loaded.storage)

    const blocking = loaded.runtime.sendHostedGroupChat(
      'Busy',
      {
        at: 1,
        from: { kind: 'user', name: 'You' },
        id: 'blocking-send',
        text: 'Hold the dispatcher',
        thread: 'thread-busy'
      },
      'thread-busy'
    )

    for (let attempt = 0; attempt < 100; attempt++) {
      if (loaded.calls.some(call => call.method === 'groups.send' && call.params.event_id === 'blocking-send')) {
        break
      }

      await Promise.resolve()
    }

    const rejected = loaded.rounds.sendToGroupChatDurably('Release', MEMBERS, 'Do not paint')

    releaseBlockingSend()
    await expect(blocking).resolves.toBe(true)
    await expect(rejected).rejects.toThrow('rejected this action')
    expect(loaded.chat.$groupChats.get().Release.log).toEqual([])
    expect(loaded.values.get('hosted-room-outbox-v1')).toMatchObject({
      commands: [{
        attempts: 1,
        failureCode: '-32602',
        kind: 'send',
        payload: { text: 'Do not paint' },
        possibleAdmission: false,
        roomId: 'room-1',
        status: 'failed'
      }]
    })
    expect(loaded.chat.$groupChats.get().Release.hostedStatus).toMatchObject({
      canRetry: true,
      state: 'failed'
    })
    expect(loaded.chat.$groupChats.get().Release.hostedStatus).toHaveProperty('dismissCommandId', expect.any(String))
    expect(loaded.chat.$groupChats.get().Release.continuityIssue).toContain('rejected this action')
    loaded.runtime.stopHostedRoomRuntime()
  })

  it.each([
    { code: 4123, failureCode: '4123', possibleAdmission: false, state: 'failed', status: 'failed' },
    { code: 4111, failureCode: 'outcome-unknown', possibleAdmission: true, state: 'indeterminate', status: 'unknown' }
  ])('keeps an exhausted Send $status with saved intent and explicit recovery', async outcome => {
    vi.useFakeTimers()
    let available = false
    let confirmed = true
    let attempts = 0

    const loaded = await loadRuntime((method, params) => {
      if (method === 'groups.capabilities') {
        return { authority_gateway_id: 'install:home', driver: true, persistent_process: true }
      }

      if (method === 'groups.list') {
        return { rooms: [] }
      }

      if (method === 'groups.send') {
        attempts += 1

        if (!available) {
          throw Object.assign(new Error('Send unavailable'), { code: outcome.code })
        }

        return confirmed ? sendReceipt(params) : { accepted: true }
      }

      throw new Error(`unexpected method: ${method}`)
    })

    loaded.chat.$groupChats.set({ Release: room() })
    loaded.values.set('hosted-room-outbox-v1', {
      commands: [
        {
          authorityId: 'install:home',
          commandId: 'stuck-send',
          connectionId: 'gateway-a',
          kind: 'send',
          payload: { text: 'stuck', thread_id: 'thread-1' },
          roomId: 'room-1',
          status: 'pending'
        }
      ],
      version: 1
    })

    await loaded.runtime.startHostedRoomRuntime(loaded.storage)

    for (let attempt = 1; attempt < 5; attempt++) {
      await loaded.runtime.dispatchHostedRoomOutbox()
    }

    expect(attempts).toBe(5)
    expect(loaded.values.get('hosted-room-outbox-v1')).toMatchObject({
      commands: [
        expect.objectContaining({
          commandId: 'stuck-send',
          attempts: 5,
          failureCode: outcome.failureCode,
          payload: { text: 'stuck', thread_id: 'thread-1' },
          possibleAdmission: outcome.possibleAdmission,
          status: outcome.status
        })
      ]
    })
    expect(loaded.chat.$groupChats.get().Release.hostedStatus).toMatchObject({
      canRetry: true,
      retryCommandId: 'stuck-send',
      state: outcome.state
    })
    expect(loaded.runtime.groupChatContinuityReady(room({
      hostedStatus: { label: 'Cached ready', state: 'ready' }
    }))).toBe(false)
    // Remote task uncertainty alone is not an unresolved local command. Keep
    // unrelated rooms usable; do not blanket-disable the indeterminate state.
    expect(loaded.runtime.groupChatContinuityReady(room({
      roomId: 'room-independent',
      hostedStatus: { label: 'Remote task uncertain', state: 'indeterminate' }
    }))).toBe(true)
    await expect(loaded.rounds.sendToGroupChatDurably('Release', MEMBERS, 'Blocked work')).resolves.toBeNull()
    expect(loaded.chat.$groupChats.get().Release.log).toEqual([])

    loaded.runtime.stopHostedRoomRuntime()
    await loaded.runtime.startHostedRoomRuntime(loaded.storage)
    expect(loaded.chat.$groupChats.get().Release.hostedStatus).toMatchObject({
      canRetry: true,
      retryCommandId: 'stuck-send',
      state: outcome.state
    })

    available = true

    if (outcome.possibleAdmission) {
      // A bare ACK does not settle a possibly committed Send. Only the same
      // client event and room receipt may retire this saved intent.
      confirmed = false
      // This API reports successful requeue, not receipt confirmation. Send
      // remains replayable under its same event key within the bounded budget;
      // the saved row, not this boolean, proves whether it was acknowledged.
      await expect(loaded.runtime.retryFailedHostedRoomCommand('Release', 'stuck-send')).resolves.toBe(true)
      expect(loaded.values.get('hosted-room-outbox-v1')).toMatchObject({
        commands: [{ commandId: 'stuck-send', status: 'pending', attempts: 1, possibleAdmission: true }]
      })

      for (let attempt = 1; attempt < 5; attempt++) {
        await loaded.runtime.dispatchHostedRoomOutbox()
      }

      expect(loaded.values.get('hosted-room-outbox-v1')).toMatchObject({
        commands: [{ commandId: 'stuck-send', status: 'unknown', attempts: 5, possibleAdmission: true }]
      })
      expect(loaded.calls.filter(call => call.method === 'groups.send')).toHaveLength(10)
      expect(loaded.calls.filter(call => call.method === 'groups.send').every(call =>
        call.params.event_id === 'stuck-send' && call.params.room_id === 'room-1'
      )).toBe(true)
      expect(loaded.chat.$groupChats.get().Release.hostedStatus).toMatchObject({ state: 'indeterminate' })
      confirmed = true
    }

    await expect(loaded.runtime.retryFailedHostedRoomCommand('Release', 'stuck-send')).resolves.toBe(true)
    expect(loaded.values.get('hosted-room-outbox-v1')).toMatchObject({ commands: [] })
    await expect(loaded.rounds.sendToGroupChatDurably('Release', MEMBERS, 'Fresh work')).resolves.toBeTruthy()
    expect(loaded.chat.$groupChats.get().Release.log).toHaveLength(1)
    loaded.runtime.stopHostedRoomRuntime()
  })

  it.each([
    { code: 4115, attempts: 5, failureCode: '4115', possibleAdmission: false, state: 'failed', status: 'failed' },
    { code: undefined, attempts: 1, failureCode: 'outcome-unknown', possibleAdmission: true, state: 'indeterminate', status: 'unknown' }
  ])('keeps Stop $status durable beyond the retry budget without indefinite replay', async outcome => {
    vi.useFakeTimers()
    let attempts = 0

    const loaded = await loadRuntime(method => {
      if (method === 'groups.capabilities') {
        return { authority_gateway_id: 'install:home', driver: true, persistent_process: true }
      }

      if (method === 'groups.list') {
        return { rooms: [] }
      }

      if (method === 'groups.stop') {
        attempts += 1
        throw Object.assign(new Error('Stop response unavailable'), { code: outcome.code })
      }

      throw new Error(`unexpected method: ${method}`)
    })

    loaded.chat.$groupChats.set({ Release: room({ running: true }) })
    loaded.values.set('hosted-room-outbox-v1', {
      commands: [
        {
          authorityId: 'install:home',
          commandId: 'durable-stop',
          connectionId: 'gateway-a',
          kind: 'stop',
          payload: {},
          roomId: 'room-1',
          status: 'pending'
        }
      ],
      version: 1
    })

    await loaded.runtime.startHostedRoomRuntime(loaded.storage)

    for (let attempt = 0; attempt < 7; attempt++) {
      await loaded.runtime.dispatchHostedRoomOutbox()
    }

    expect(attempts).toBe(outcome.attempts)
    expect(loaded.values.get('hosted-room-outbox-v1')).toMatchObject({
      commands: [
        expect.objectContaining({
          attempts: outcome.attempts,
          commandId: 'durable-stop',
          failureCode: outcome.failureCode,
          payload: {},
          possibleAdmission: outcome.possibleAdmission,
          status: outcome.status
        })
      ]
    })
    expect(loaded.chat.$groupChats.get().Release.hostedStatus).toMatchObject({
      canRetry: true,
      retryCommandId: 'durable-stop',
      state: outcome.state
    })
    expect(loaded.chat.$groupChats.get().Release.running).toBe(true)

    if (outcome.status === 'unknown') {
      await expect(loaded.runtime.stopHostedGroupChat('Release')).resolves.toBe(false)
      expect(loaded.chat.$groupChats.get().Release.hostedStatus).not.toHaveProperty('dismissCommandId')
    }

    loaded.runtime.stopHostedRoomRuntime()
    await loaded.runtime.startHostedRoomRuntime(loaded.storage)
    expect(attempts).toBe(outcome.attempts)
    expect(loaded.values.get('hosted-room-outbox-v1')).toMatchObject({
      commands: [{ commandId: 'durable-stop', attempts: outcome.attempts, status: outcome.status }]
    })
    loaded.runtime.stopHostedRoomRuntime()
  })

  it.each([
    { status: 'failed', state: 'failed', failureCode: '4123', possibleAdmission: false },
    { status: 'unknown', state: 'indeterminate', failureCode: 'retry-exhausted', possibleAdmission: undefined }
  ])('lets Stop bypass a $status Send without erasing its intent', async outcome => {
    const loaded = await loadRuntime(method => {
      if (method === 'groups.capabilities') {
        return { authority_gateway_id: 'install:home', driver: true, persistent_process: true }
      }

      if (method === 'groups.list') {
        return { rooms: [] }
      }

      if (method === 'groups.stop') {
        return { cancelled: 1 }
      }

      throw new Error(`unexpected method: ${method}`)
    })

    loaded.chat.$groupChats.set({ Release: room({ running: true }) })
    loaded.values.set('hosted-room-outbox-v1', {
      commands: [
        {
          attempts: 5,
          authorityId: 'install:home',
          commandId: 'failed-send',
          connectionId: 'gateway-a',
          failureCode: outcome.failureCode,
          possibleAdmission: outcome.possibleAdmission,
          kind: 'send',
          payload: { text: 'stuck', thread_id: 'thread-1' },
          roomId: 'room-1',
          status: 'failed'
        }
      ],
      version: 1
    })

    await loaded.runtime.startHostedRoomRuntime(loaded.storage)
    await expect(loaded.runtime.stopHostedGroupChat('Release')).resolves.toBe(true)

    expect(loaded.calls.filter(call => call.method === 'groups.stop')).toHaveLength(1)
    expect(loaded.calls.filter(call => call.method === 'groups.send')).toEqual([])
    expect(loaded.values.get('hosted-room-outbox-v1')).toMatchObject({
      commands: [{
        commandId: 'failed-send',
        kind: 'send',
        payload: { text: 'stuck', thread_id: 'thread-1' },
        status: outcome.status
      }]
    })
    expect(loaded.chat.$groupChats.get().Release.hostedStatus).toMatchObject({
      retryCommandId: 'failed-send',
      state: outcome.state
    })
    loaded.runtime.stopHostedRoomRuntime()
  })

  it.each([
    { code: 5116, state: 'indeterminate', status: 'unknown' },
    { code: -32602, state: 'failed', status: 'failed' }
  ])('retains Stop intent on room-error $code rather than inventing acknowledgement', async outcome => {
    vi.useFakeTimers()

    const loaded = await loadRuntime(method => {
      if (method === 'groups.capabilities') {
        return { authority_gateway_id: 'install:home', driver: true, persistent_process: true }
      }

      if (method === 'groups.list') {
        return { rooms: [] }
      }

      if (method === 'groups.stop') {
        // 5116 wraps every service.stop_room exception, including errors
        // after request_room_stop commits; its message cannot prove refusal.
        throw Object.assign(new Error('hosted room not found'), { code: outcome.code })
      }

      throw new Error(`unexpected method: ${method}`)
    })

    loaded.chat.$groupChats.set({ Release: room({ running: true }) })
    await loaded.runtime.startHostedRoomRuntime(loaded.storage)

    const stop = loaded.runtime.stopHostedGroupChat('Release')

    if (outcome.status === 'failed') {
      await expect(stop).rejects.toThrow('rejected this action')
    } else {
      await expect(stop).resolves.toBe(false)
    }

    const stopCall = loaded.calls.find(call => call.method === 'groups.stop')!

    expect(loaded.values.get('hosted-room-outbox-v1')).toMatchObject({
      commands: [{
        attempts: 1,
        commandId: stopCall.params.cancel_id,
        kind: 'stop',
        payload: {},
        possibleAdmission: outcome.status === 'unknown',
        roomId: 'room-1',
        status: outcome.status
      }]
    })
    expect(loaded.chat.$groupChats.get().Release.hostedStatus).toMatchObject({ state: outcome.state })
    expect(loaded.chat.$groupChats.get().Release.running).toBe(true)

    for (let attempt = 0; attempt < 8; attempt++) {
      await loaded.runtime.dispatchHostedRoomOutbox()
    }

    loaded.runtime.stopHostedRoomRuntime()
    await loaded.runtime.startHostedRoomRuntime(loaded.storage)
    expect(loaded.calls.filter(call => call.method === 'groups.stop')).toHaveLength(1)
    expect(loaded.values.get('hosted-room-outbox-v1')).toMatchObject({
      commands: [{ commandId: stopCall.params.cancel_id, status: outcome.status }]
    })
    loaded.runtime.stopHostedRoomRuntime()
  })

  it('keeps FIFO inside one room while another room can continue', async () => {
    vi.useFakeTimers()
    let available = false

    const loaded = await loadRuntime((method, params) => {
      if (method === 'groups.capabilities') {
        return { attachments: true, authority_gateway_id: 'install:home', driver: true, persistent_process: true }
      }

      if (method === 'groups.list') {
        return { rooms: [] }
      }

      if (method === 'groups.send' && params.event_id === 'send-a' && !available) {
        throw new Error('temporary outage')
      }

      if (method === 'groups.send') {
        return sendReceipt(params)
      }

      if (method === 'groups.stop') {
        return { cancelled: 1 }
      }

      throw new Error(`unexpected method: ${method}`)
    })

    loaded.values.set('hosted-room-outbox-v1', {
      commands: [
        {
          authorityId: 'install:home',
          commandId: 'send-a',
          connectionId: 'gateway-a',
          kind: 'send',
          payload: {},
          roomId: 'room-1',
          status: 'in-flight'
        },
        {
          authorityId: 'install:home',
          commandId: 'stop-b',
          connectionId: 'gateway-a',
          kind: 'stop',
          payload: {},
          roomId: 'room-1',
          status: 'pending'
        },
        {
          authorityId: 'install:home',
          commandId: 'send-c',
          connectionId: 'gateway-a',
          kind: 'send',
          payload: {},
          roomId: 'room-2',
          status: 'pending'
        }
      ],
      version: 1
    })

    await loaded.runtime.startHostedRoomRuntime(loaded.storage)

    expect(loaded.calls.filter(call => call.method === 'groups.stop')).toHaveLength(0)
    expect(loaded.calls.filter(call => call.method === 'groups.send').map(call => call.params.event_id)).toEqual([
      'send-a',
      'send-c'
    ])
    expect(loaded.values.get('hosted-room-outbox-v1')).toMatchObject({
      commands: [{ commandId: 'send-a', status: 'pending', possibleAdmission: true }, { commandId: 'stop-b' }]
    })
    available = true
    await loaded.runtime.dispatchHostedRoomOutbox()
    expect(loaded.calls.filter(call => ['groups.send', 'groups.stop'].includes(call.method)).map(call =>
      call.params.event_id || call.params.cancel_id
    )).toEqual(['send-a', 'send-c', 'send-a', 'stop-b'])
    expect(loaded.values.get('hosted-room-outbox-v1')).toMatchObject({ commands: [] })
    loaded.runtime.stopHostedRoomRuntime()
  })

  it('recovers a pending command through the authority current connection', async () => {
    vi.useFakeTimers()

    const routes = [
      { connectionId: 'gateway-old', mode: 'remote', profile: 'default', targetProfile: 'default' },
      { connectionId: 'gateway-new', mode: 'remote', profile: 'default', targetProfile: 'default' }
    ]

    const loaded = await loadRuntime((method, params, route) => {
      if (method === 'groups.capabilities') {
        if (route.connectionId === 'gateway-old') {
          throw new Error('stale endpoint')
        }

        return { attachments: true, authority_gateway_id: 'install:home', driver: true, persistent_process: true }
      }

      if (method === 'groups.list') {
        return { rooms: [] }
      }

      if (method === 'groups.send') {
        return sendReceipt(params)
      }

      throw new Error(`unexpected method: ${method}`)
    }, routes)

    loaded.values.set('hosted-room-outbox-v1', {
      commands: [
        {
          authorityId: 'install:home',
          commandId: 'send-a',
          connectionId: 'gateway-old',
          kind: 'send',
          payload: {},
          roomId: 'room-1',
          status: 'pending'
        }
      ],
      version: 1
    })

    await loaded.runtime.startHostedRoomRuntime(loaded.storage)

    expect(loaded.calls.find(call => call.method === 'groups.send')?.connectionId).toBe('gateway-new')
    expect(loaded.values.get('hosted-room-outbox-v1')).toMatchObject({ commands: [] })
    loaded.runtime.stopHostedRoomRuntime()
  })

  it('keeps an unsupported hosted room read-only with its update guidance', async () => {
    const loaded = await loadRuntime(() => {
      throw new Error('unsupported room must not dispatch')
    })

    const unsupported = room({
      continuityIssue: null,
      hostedStatus: { label: 'Update Studio to keep this Group Chat running.', state: 'unsupported' }
    })

    loaded.chat.$groupChats.set({ Legacy: unsupported })

    expect(loaded.runtime.groupChatContinuityReady(unsupported)).toBe(false)
    expect(loaded.rounds.sendToGroupChat('Legacy', MEMBERS, 'Do not queue')).toBeNull()
    expect(loaded.chat.$groupChats.get().Legacy.continuityIssue).toBe('Update Studio to keep this Group Chat running.')
    expect(loaded.values.has('hosted-room-outbox-v1')).toBe(false)
  })

  it('mirrors and resolves an exact hosted approval from the room', async () => {
    vi.useFakeTimers()
    let executionGeneration = 2
    let approvalPending = true

    const loaded = await loadRuntime((method, params) => {
      if (method === 'groups.capabilities') {
        return {
          attachments: true,
          authority_gateway_id: 'install:home',
          driver: true,
          max_log_limit: 100,
          persistent_process: true
        }
      }

      if (method === 'groups.list') {
        if (!approvalPending) {
          throw new Error('refresh unavailable')
        }

        return {
          rooms: [
            {
              authority_epoch: 1,
              authority_gateway_id: 'install:home',
              latest_seq: 0,
              members: [
                { handle: 'research', member_id: 'research', profile: 'research' },
                { handle: 'builder', member_id: 'builder', profile: 'builder' }
              ],
              name: 'Release',
              room_id: 'room-1'
            }
          ]
        }
      }

      if (method === 'groups.state') {
        return {
          driver_status: {
            pending_actions: approvalPending
              ? [
                  {
                    approval: { choices: ['once', 'deny'], command: 'npm test', description: 'Run tests' },
                    execution_generation: executionGeneration,
                    kind: 'approval',
                    member_id: 'builder',
                    request_id: 'approval-1',
                    task_id: 'task-1'
                  }
                ]
              : [],
            working: true
          },
          room: {
            authority_epoch: 1,
            authority_gateway_id: 'install:home',
            members: [
              { handle: 'research', member_id: 'research', profile: 'research' },
              { handle: 'builder', member_id: 'builder', profile: 'builder' }
            ],
            name: 'Release',
            room_id: 'room-1'
          }
        }
      }

      if (method === 'groups.log') {
        return { events: [], has_more: false, latest_seq: 0 }
      }

      if (method === 'groups.approve') {
        expect(params).toMatchObject({
          choice: 'once',
          execution_generation: 3,
          member_id: 'builder',
          request_id: 'approval-1',
          room_id: 'room-1',
          task_id: 'task-1'
        })

        approvalPending = false

        return { approved: true }
      }

      throw new Error(`unexpected method: ${method}`)
    })

    await loaded.runtime.startHostedRoomRuntime(loaded.storage)
    const prompt = Object.values(loaded.chat.$groupClarify.get())[0]
    const member = loaded.chat.$groupChats.get().Release.members?.find(candidate => candidate.name === 'builder')

    expect(prompt).toMatchObject({
      command: 'npm test',
      hostedApproval: { executionGeneration: 2, memberId: 'builder', taskId: 'task-1' },
      kind: 'approval'
    })
    expect(loaded.chat.$groupHostedNeedsYou.get().Release).toBe(true)
    expect(member).toBeTruthy()
    executionGeneration = 3
    await loaded.runtime.refreshHostedRooms()
    const refreshed = Object.values(loaded.chat.$groupClarify.get())[0]

    expect(refreshed).toMatchObject({
      hostedApproval: { executionGeneration: 3, memberId: 'builder', taskId: 'task-1' },
      requestId: 'approval-1'
    })
    await loaded.turns.answerGroupClarify(refreshed, member!, 'once')
    expect(loaded.calls.filter(call => call.method === 'groups.approve')).toHaveLength(1)
    expect(Object.values(loaded.chat.$groupClarify.get())).toEqual([])
    expect(loaded.chat.$groupHostedNeedsYou.get().Release).toBe(false)
    loaded.runtime.stopHostedRoomRuntime()
  })
})

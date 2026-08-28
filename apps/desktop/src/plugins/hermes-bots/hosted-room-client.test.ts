import { describe, expect, it } from 'vitest'

import {
  classifyHostedRoomCapability,
  createHostedRoomOutbox,
  createHostedRoomReplayState,
  deriveFriendlyHostedRoomStatus,
  isHostedRoomContinuityEligible,
  reduceHostedRoomEvents,
  reduceHostedRoomOutbox,
  replayHostedRoomPages,
  resolveSingleGatewayRoute
} from './hosted-room-client'

function event(
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

describe('hosted Group Chat capability negotiation', () => {
  it('distinguishes an old gateway from a transient outage and a persistent driver', () => {
    const missing = Object.assign(new Error('JSON-RPC -32601: method not found'), {
      code: -32601
    })

    const old = classifyHostedRoomCapability(
      {
        ok: false,
        error: missing
      },
      {
        connectionId: 'gateway-a'
      }
    )

    const offline = classifyHostedRoomCapability(new Error('socket closed during reconnect'))

    const capable = classifyHostedRoomCapability(
      {
        driver: true,
        persistent_process: true,
        authority_gateway_id: 'install:home',
        max_log_limit: 250
      },
      {
        connectionId: 'gateway-a'
      }
    )

    expect(old).toMatchObject({
      kind: 'unsupported',
      reason: 'old-gateway',
      connectionId: 'gateway-a'
    })
    expect(offline.kind).toBe('transient-failure')
    expect(capable).toMatchObject({
      kind: 'driver-capable',
      authorityId: 'install:home',
      persistentProcess: true,
      maxLogLimit: 250
    })
    expect(isHostedRoomContinuityEligible(capable)).toBe(true)
    expect(
      isHostedRoomContinuityEligible({
        driver: true,
        persistent_process: false
      })
    ).toBe(false)
  })

  it('offers hosted continuity only when every member resolves to one gateway', () => {
    const same = resolveSingleGatewayRoute(
      [
        {
          name: 'research',
          connectionId: 'gateway-a',
          sourceScoped: true
        },
        {
          name: 'builder',
          route: {
            connectionId: 'gateway-a',
            mode: 'remote',
            profile: 'builder',
            targetProfile: 'builder'
          },
          remoteSource: true
        }
      ],
      {
        activeConnectionId: 'local'
      }
    )

    const mixed = resolveSingleGatewayRoute([
      {
        name: 'research',
        connectionId: 'gateway-a',
        sourceScoped: true
      },
      {
        name: 'builder',
        connectionId: 'gateway-b',
        sourceScoped: true
      }
    ])

    const unresolved = resolveSingleGatewayRoute(
      [
        {
          name: 'research'
        },
        {
          name: 'missing',
          sourceScoped: true
        }
      ],
      {
        activeConnectionId: 'local'
      }
    )

    expect(same).toMatchObject({
      kind: 'single-gateway',
      connectionId: 'gateway-a'
    })
    expect(mixed).toMatchObject({
      kind: 'unsupported',
      reason: 'cross-gateway'
    })
    expect(unresolved.reason).toBe('unresolved-member-route')
  })
})

describe('hosted Group Chat replay', () => {
  it('orders, deduplicates, and advances across unknown event kinds without applying them', () => {
    const initial = createHostedRoomReplayState({
      roomId: 'room-1',
      name: 'Release',
      authorityId: 'install:home',
      connectionId: 'gateway-a'
    })

    const user = event(
      1,
      'message-1',
      'message.user',
      {
        text: 'Start the review',
        thread_id: 'thread-1'
      },
      {
        kind: 'user',
        id: 'desktop'
      }
    )

    const unknown = event(2, 'future-1', 'future.room.signal', {
      destructive: true
    })

    const member = event(
      3,
      'message-2',
      'message.member',
      {
        text: 'Review complete',
        thread_id: 'thread-1'
      },
      {
        kind: 'member',
        id: 'research',
        display_name: 'Research'
      }
    )

    const replayed = reduceHostedRoomEvents(initial, [member, unknown, user, user])

    expect(replayed.cursor).toBe(3)
    expect(replayed.messages.map(message => [message.seq, message.eventId, message.text])).toEqual([
      [1, 'message-1', 'Start the review'],
      [3, 'message-2', 'Review complete']
    ])
    expect(replayed.timeline.map(entry => entry.eventId)).toEqual(['message-1', 'message-2'])
    expect(reduceHostedRoomEvents(replayed, [member, user]).messages).toHaveLength(2)
  })

  it('buffers gaps, preserves metadata-only attachments, and never advances past missing history', () => {
    const initial = createHostedRoomReplayState({
      roomId: 'room-1'
    })

    const later = event(2, 'message-2', 'message.member', {
      text: 'Done',
      attachments: [
        {
          kind: 'pdf',
          name: 'brief.pdf',
          size: 42,
          mime: 'application/pdf',
          refs: {
            research: 'staged:brief-1'
          },
          data: 'data:application/pdf;base64,never-copy-file-bytes'
        }
      ]
    })

    const gapped = reduceHostedRoomEvents(initial, [later])

    expect(gapped.cursor).toBe(0)
    expect(gapped.messages).toEqual([])
    expect(gapped.pendingEvents).toHaveLength(1)

    const complete = reduceHostedRoomEvents(gapped, [event(1, 'message-1', 'message.user', { text: 'Go' })])

    expect(complete.cursor).toBe(2)
    expect(complete.messages[1].attachments).toEqual([
      {
        kind: 'pdf',
        name: 'brief.pdf',
        size: 42,
        mime: 'application/pdf',
        refs: {
          research: 'staged:brief-1'
        }
      }
    ])
    expect(JSON.stringify(complete)).not.toContain('never-copy-file-bytes')
  })

  it('pages from the persisted cursor and stops safely on a stalled gap', async () => {
    const pages = [
      {
        events: [event(1, 'message-1', 'message.user', { text: 'Go' })],
        latest_seq: 3,
        has_more: true
      },
      {
        events: [event(3, 'message-3', 'message.member', { text: 'Done' })],
        latest_seq: 3,
        has_more: false
      }
    ]

    const replayed = await replayHostedRoomPages({
      state: createHostedRoomReplayState({
        roomId: 'room-1'
      }),
      fetchPage: async () => pages.shift()
    })

    expect(replayed).toMatchObject({
      complete: false,
      reason: 'stalled',
      pages: 2
    })
    expect(replayed.state.cursor).toBe(1)
    expect(replayed.state.pendingEvents).toHaveLength(1)
  })

  it('derives short status copy without reflecting raw provider details', () => {
    const state = reduceHostedRoomEvents(
      createHostedRoomReplayState({
        roomId: 'room-1',
        connectionId: 'gateway-a'
      }),
      [
        event(1, 'failed-1', 'turn.failed', {
          member_display_name: 'Builder',
          reason_code: 'provider_auth_or_access',
          raw_error: 'secret upstream payload'
        })
      ]
    )

    const friendly = deriveFriendlyHostedRoomStatus(state)

    expect(friendly).toMatchObject({
      kind: 'needs-attention',
      member: 'Builder',
      reasonCode: 'provider_auth_or_access'
    })
    expect(JSON.stringify(friendly)).not.toContain('secret upstream payload')
  })
})

describe('hosted Group Chat command outbox', () => {
  const command = {
    commandId: 'command-1',
    kind: 'send' as const,
    roomId: 'room-1',
    authorityId: 'install:home',
    connectionId: 'gateway-a',
    payload: {
      text: 'Hello',
      thread_id: 'thread-1'
    }
  }

  it('returns an interrupted in-flight command to pending with the same idempotency key', () => {
    const enqueued = reduceHostedRoomOutbox(createHostedRoomOutbox(), {
      type: 'enqueue',
      command
    })

    const inFlight = reduceHostedRoomOutbox(enqueued, {
      type: 'dispatch',
      commandId: command.commandId
    })

    const restored = createHostedRoomOutbox(inFlight)

    expect(restored.commands).toEqual([
      expect.objectContaining({
        commandId: command.commandId,
        status: 'pending',
        attempts: 1
      })
    ])
  })

  it('deduplicates identical commands, rejects conflicting reuse, and drops acknowledged work', () => {
    const once = reduceHostedRoomOutbox(createHostedRoomOutbox(), {
      type: 'enqueue',
      command
    })

    const twice = reduceHostedRoomOutbox(once, {
      type: 'enqueue',
      command: {
        ...command,
        payload: {
          thread_id: 'thread-1',
          text: 'Hello'
        }
      }
    })

    expect(twice.commands).toHaveLength(1)
    expect(() =>
      reduceHostedRoomOutbox(twice, {
        type: 'enqueue',
        command: {
          ...command,
          payload: {
            text: 'Different'
          }
        }
      })
    ).toThrow(/different content/)
    expect(() =>
      reduceHostedRoomOutbox(twice, {
        type: 'enqueue',
        command: {
          ...command,
          commandId: 'raw-file-command',
          payload: {
            content_base64: 'not-allowed-in-stage-1'
          }
        }
      })
    ).toThrow(/cannot carry raw attachment/)
    expect(
      reduceHostedRoomOutbox(twice, {
        type: 'acknowledge',
        commandId: command.commandId
      }).commands
    ).toEqual([])
  })
})

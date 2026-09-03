import { sha256 } from '@noble/hashes/sha2.js'
import { bytesToHex, utf8ToBytes } from '@noble/hashes/utils.js'

import { groupChatSyncSequence } from './group-message-author'
import { createHostedRoomReplayState, reduceHostedRoomEvents } from './hosted-room-client'
import type { HostedRoomCommand } from './hosted-room-client'
import type { GroupMessage } from './types'

function clientKey(value: unknown): string | null {
  if (typeof value !== 'string') {
    return null
  }

  const key = value.trim()

  return key.length <= 128 && /^[A-Za-z0-9][A-Za-z0-9._:-]*$/.test(key) ? key : null
}

/** The gateway's normalized retry-key namespace, not a transform of event IDs. */
function userEventId(key: string) {
  return `user:${bytesToHex(sha256(utf8ToBytes(key)))}`
}

function stableId(entry: GroupMessage): string | null {
  if (entry.id && entry.eventId && entry.id !== entry.eventId) {
    return null
  }

  return clientKey(entry.eventId || entry.id)
}

/** Reconcile only within an identified hosted room. Mirrors never supply payload
 * fields to accepted user events, and every sequenced record stays intact. */
export function reconcileHostedUserEvents(roomId: string, ...logs: GroupMessage[][]): GroupMessage[] {
  // Older durable replays have sequence/ID but no per-entry room metadata.
  // Bind those records to their known container before any shared matching.
  const entries = logs
    .flat()
    .map(entry =>
      entry.from?.kind === 'user' && !entry.roomId && groupChatSyncSequence(entry) !== null
        ? { ...entry, roomId }
        : entry
    )

  const canonical = new Map<string, Set<string>>()
  const receiptedKeys = new Set<string>()
  const inRoom = (entry: GroupMessage) => !entry.roomId || entry.roomId === roomId

  for (const entry of entries) {
    const seq = groupChatSyncSequence(entry)
    const id = stableId(entry)

    if (entry.from?.kind === 'user' && inRoom(entry) && seq !== null && id) {
      const events = canonical.get(id) || new Set<string>()
      events.add(`${id}:${seq}`)
      canonical.set(id, events)
      const key = clientKey(entry.clientEventId)

      if (key && id === userEventId(key)) {
        receiptedKeys.add(key)
      }
    }
  }

  return entries.filter(entry => {
    if (entry.from?.kind !== 'user' || !inRoom(entry) || groupChatSyncSequence(entry) !== null) {
      return true
    }

    const id = stableId(entry)

    if (!id) {
      return true
    }

    // A raw UUID may itself be an old backend's genuine canonical event. Its
    // own sequence/marker takes precedence over any possible hashed neighbor.
    if (canonical.has(id)) {
      return canonical.get(id)!.size !== 1
    }

    if (entry.eventId) {
      return true
    }

    const explicitClient = receiptedKeys.has(id) || (entry.roomId === roomId && clientKey(entry.clientEventId) === id)

    if (/^user:[0-9a-f]{64}$/.test(id) && !explicitClient) {
      return true
    }

    return canonical.get(userEventId(id))?.size !== 1
  })
}

/** Mirrors carry canonical markers for old raw-ID backends, never replay order. */
export function projectedUserEvent(entry: GroupMessage): GroupMessage {
  return entry.from?.kind === 'user' && entry.seq !== undefined ? { ...entry, seq: undefined } : entry
}

/** Validate the response against the exact command before using the normal replay
 * decoder. A receipt does not advance the room cursor past unobserved events. */
export function hostedUserEventReceipt(command: HostedRoomCommand, value: unknown): GroupMessage | null {
  if (command.kind !== 'send' || !value || typeof value !== 'object' || Array.isArray(value)) {
    return null
  }

  const reply = value as Record<string, unknown>
  const event = reply.event as Record<string, unknown> | undefined
  const key = clientKey(command.commandId)

  if (
    !key ||
    !event ||
    typeof event !== 'object' ||
    Array.isArray(event) ||
    (reply.client_event_id !== undefined && clientKey(reply.client_event_id) !== key) ||
    event.room_id !== command.roomId ||
    event.kind !== 'message.user' ||
    (event.event_id !== key && event.event_id !== userEventId(key)) ||
    typeof event.seq !== 'number' ||
    !Number.isSafeInteger(event.seq) ||
    event.seq < 1
  ) {
    return null
  }

  const state = reduceHostedRoomEvents(createHostedRoomReplayState({ roomId: command.roomId, cursor: event.seq - 1 }), [
    event
  ])

  const message = state.messages[0]

  return message ? { ...message, id: message.eventId, roomId: command.roomId, clientEventId: key } : null
}

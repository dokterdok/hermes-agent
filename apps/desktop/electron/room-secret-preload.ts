import { ROOM_SECRET_CHANNEL } from './room-secret-types'
import type { RoomSecretApi, RoomSecretReply } from './room-secret-types'

/** Synchronous because the existing plugin persistence contract is synchronous.
 * Bounded batches only; no gateway/network work is performed by this channel. */
export function createRoomSecretBridge(sendSync: (channel: string, request: unknown) => unknown): RoomSecretApi {
  const exchange = (request: unknown) => {
    const reply = sendSync(ROOM_SECRET_CHANNEL, request) as RoomSecretReply

    if (!reply || !reply.ok) {
      throw new Error('Group Chat secure credential storage is unavailable or busy.')
    }

    return reply.values
  }

  return {
    exchange,
    lock: key => exchange({ action: 'lock', key })[0],
    unlock: (key, token) => {
      exchange({ action: 'unlock', key, token })
    }
  }
}

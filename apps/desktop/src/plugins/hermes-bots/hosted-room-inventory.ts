import { isHostedRoomContinuityEligible, isHostedRoomReadEligible } from './hosted-room-client'
import type { HostedRoomCapability } from './hosted-room-client'
import { botsText } from './i18n'
import type { GroupChat } from './types'

const LIST_LIMIT = 500
const MAX_LIST_PAGES = 4

type RoomRecord = Record<string, unknown> & { room_id: string; name: string }

function record(value: unknown): Record<string, unknown> | null {
  return value && typeof value === 'object' && !Array.isArray(value) ? (value as Record<string, unknown>) : null
}

function namedRoom(value: unknown): value is RoomRecord {
  const room = record(value)

  return (
    typeof room?.room_id === 'string' &&
    Boolean(room.room_id.trim()) &&
    room.room_id === room.room_id.trim() &&
    typeof room.name === 'string' &&
    Boolean(room.name.trim())
  )
}

/** A complete bounded scan is evidence of absence; a failed/partial one is not. */
export async function readHostedRoomInventory(
  request: (params: Record<string, unknown>) => Promise<unknown>,
  observe?: (ids: ReadonlySet<string>) => void
) {
  const rooms: RoomRecord[] = []
  const ids = new Set<string>()
  let offset = 0

  for (let page = 0; page < MAX_LIST_PAGES; page += 1) {
    const response = record(await request({ include_disbanded: true, limit: LIST_LIMIT, offset }))
    const rows = response?.rooms

    if (
      response?.ok === false ||
      response?.error ||
      !Array.isArray(rows) ||
      rows.length > LIST_LIMIT ||
      !rows.every(namedRoom)
    ) {
      throw new Error('Invalid Group Chat inventory page')
    }

    for (const room of rows) {
      if (ids.has(room.room_id)) {
        throw new Error('Repeated Group Chat inventory room')
      }

      ids.add(room.room_id)
      rooms.push(room)
    }

    const next = response?.next_offset

    if (next === null || next === undefined) {
      observe?.(ids)

      return { rooms, ids, complete: next === null || rows.length < LIST_LIMIT }
    }

    if (!Number.isSafeInteger(next) || !rows.length || next !== offset + rows.length) {
      throw new Error('Invalid Group Chat inventory cursor')
    }

    observe?.(ids)

    offset = next as number
  }

  return { rooms, ids, complete: false }
}

export function hostedRoomCapabilityFingerprint(capability: HostedRoomCapability | undefined) {
  return capability
    ? JSON.stringify([
        capability.kind,
        capability.authorityId,
        capability.persistentProcess,
        capability.exactPeerGrantRevoke,
        capability.routeGrantFingerprint
      ])
    : ''
}

export function hostedRoomContinuityMode(room: { members?: unknown }) {
  return (Array.isArray(room?.members) ? room.members : []).some(raw => record(record(raw)?.target)?.kind === 'peer')
    ? ('distributed' as const)
    : ('gateway' as const)
}

/** Do not replace a failed/malformed state read with the list projection. */
export function readHostedInventoryState(response: unknown, roomId: string): RoomRecord {
  const state = record(response)
  const room = record(state?.room)

  if (
    state?.ok === false ||
    state?.error ||
    !namedRoom(room) ||
    room.room_id !== roomId ||
    !Array.isArray(room.members) ||
    typeof room.authority_gateway_id !== 'string' ||
    !room.authority_gateway_id.trim() ||
    !Number.isSafeInteger(room.authority_epoch) ||
    Number(room.authority_epoch) < 1 ||
    (room.latest_seq !== undefined && (!Number.isSafeInteger(room.latest_seq) || Number(room.latest_seq) < 0))
  ) {
    throw new Error('Invalid Group Chat inventory state')
  }

  return room
}

export function classicRoomInventoryReady(room: GroupChat, inventories: ReadonlyMap<string, ReadonlySet<string>>) {
  if (!room.roomId) {
    return true
  }

  const members = Array.isArray(room.members) ? room.members : []

  if (!members.length || members.some(member => member.remoteSource !== true)) {
    return true
  }

  const connections = [...new Set(members.map(member => String(member.connectionId || '')).filter(Boolean))]

  return connections.every(connectionId => {
    const inventory = inventories.get(connectionId)

    return inventory !== undefined && !inventory.has(room.roomId!)
  })
}

export function hostedReadOnlyState(): Pick<GroupChat, 'hostedStatus' | 'continuityIssue' | 'running'> {
  return {
    hostedStatus: {
      state: 'read-only',
      label: botsText().group.continuityReadOnlyTitle,
      canRetry: false,
      canStop: false
    },
    continuityIssue: botsText().group.continuityReadOnlyDesc,
    running: false
  }
}

/** A failed read cannot turn a known read-only copy back into a writable room. */
export function unavailableHostedReadOnlyState(room: GroupChat, capability: HostedRoomCapability | undefined) {
  return room.hostedStatus?.state === 'read-only' ||
    (capability &&
      isHostedRoomReadEligible(capability) &&
      (!isHostedRoomContinuityEligible(capability) || capability.authorityId !== room.hosted))
    ? hostedReadOnlyState()
    : {}
}

import { $groupChats, durableGroupChatRooms, hydrateGroupChatRooms } from './group-chat'
import type { GroupChatRoom } from './group-chat'
import {
  equalGroupRoomSnapshot, inheritGroupRoomSnapshots, rememberGroupRoomSnapshot, retainedRoomOwner, withGroupRoomWrite
} from './group-room-ownership'
import { getPluginCtx } from './shared'
import type { GroupChat } from './types'

/** Storage owns adoption across windows; an atom is only this renderer's view.
 * Read without writing or normalizing the retained history/checkpoint. */
export function observeGroupChatExecutionOwner(group: string, saved: Record<string, GroupChat>) {
  const all = $groupChats.get()
  const room = all[group]

  const owner = room?.roomId
    ? Object.values(saved).find(candidate => candidate?.roomId === room.roomId)
    : saved[group]

  if (owner && retainedRoomOwner(owner)) {
    const refreshed: GroupChatRoom = hydrateGroupChatRooms({ [group]: owner })[group]
    refreshed.epoch = room?.epoch
    refreshed.running = room?.running
    refreshed.stoppedEpoch = room?.stoppedEpoch
    $groupChats.set({ ...all, [group]: refreshed })

    return false
  }

  return !retainedRoomOwner(room)
}

export function refreshGroupChatExecutionOwner(group: string) {
  const storage = getPluginCtx()?.storage

  if (!storage) { return false }
  const saved = storage.get<Record<string, GroupChat>>('group-chats', {})

  // PluginStorage is synchronous in the public SDK. Async test transports
  // are checked again after await at the member's native admission boundary.
  if (saved && typeof (saved as unknown as Promise<unknown>).then === 'function') { return true }

  return observeGroupChatExecutionOwner(group, saved || {})
}

export function persistGroupChatRooms(all: Record<string, GroupChat> = $groupChats.get(), expected?: Record<string, GroupChat>) {
  try {
    if (expected) {
      inheritGroupRoomSnapshots(all, expected)
    }

    const durable = durableGroupChatRooms(all)
    const receipt = withGroupRoomWrite(durable, {}, () => getPluginCtx()?.storage?.set?.('group-chats', durable))
    recordCommittedGroupRooms(all, durable, receipt.committed)

    return Promise.resolve(receipt.value).catch(() => undefined)
  } catch {
    return Promise.resolve()
  }
}

export function recordCommittedGroupRooms(all: Record<string, GroupChat>, durable: Record<string, GroupChat>, saved?: Record<string, GroupChat>) {
  if (!saved) { return }

  for (const [name, room] of Object.entries(all)) {
    // A retained stale row is not an acknowledgement of the old atom's data.
    if (saved[name] && durable[name] && equalGroupRoomSnapshot(saved[name], durable[name])) {
      rememberGroupRoomSnapshot(name, room, saved[name], durable[name])
      rememberGroupRoomSnapshot(name, durable[name], saved[name], durable[name])
    }
  }
}

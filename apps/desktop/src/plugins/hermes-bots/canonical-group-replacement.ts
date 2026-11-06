import { randomUuid } from '@hermes/plugin-sdk'

import type { CanonicalGroupMessages } from './canonical-group-locales'
import { canonicalGroupRequest } from './canonical-groups'
import type { CanonicalGroupBinding, CanonicalRoom, CanonicalRoomMember } from './canonical-groups'
import { uniqueGroupChatName } from './group-chat'
import { liveGroupChatNames } from './group-membership'
import { getPluginCtx } from './shared'

interface Replacement {
  payload: { room_id: string; name: string; members: CanonicalRoomMember[] }
  confirmed: boolean
}

/** Read the shared Desktop store on every attempt, including another window's saved link.
 *  Persist before creating; after acknowledgement the gateway's current room is authoritative. */
export async function replacementRoom(binding: CanonicalGroupBinding, room: CanonicalRoom, memberId: string,
  members: CanonicalRoomMember[], labels: CanonicalGroupMessages) {
  const storage = getPluginCtx()?.storage

  if (!storage) {throw new Error(labels.createRefused)}
  const key = `group-replacement:${JSON.stringify([binding.connectionId, binding.profile, binding.roomId, memberId])}`

  const save = async (value: Replacement | null) => {
    await storage.set(key, value)

    // Plugin storage is best-effort; never create if its recovery link was not saved.
    if (JSON.stringify(await storage.get(key, null)) !== JSON.stringify(value)) {throw new Error(labels.createRefused)}
  }

  const saved = await storage.get<Replacement | null>(key, null)
  const link = saved ?? { payload: { room_id: randomUuid(), name: uniqueGroupChatName(room.name, new Set(liveGroupChatNames())), members }, confirmed: false }

  if (!saved) {await save(link)}

  const { room: next } = link.confirmed
    ? await canonicalGroupRequest<{ room: CanonicalRoom }>(binding, 'groups.state', { room_id: link.payload.room_id, include_disbanded: true })
    : await canonicalGroupRequest<{ room: CanonicalRoom }>(binding, 'groups.create', link.payload)

  if (next.room_id !== link.payload.room_id) {throw new Error(labels.createRefused)}

  if (next.disbanded_at != null) {
    await save(null)
    throw new Error(labels.peerReplacementEnded)
  }

  if (!link.confirmed) {await save({ ...link, confirmed: true })}

  return { room: next, clear: () => save(null) }
}

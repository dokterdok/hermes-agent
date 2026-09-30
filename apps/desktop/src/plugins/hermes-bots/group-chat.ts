import { atom } from '@hermes/plugin-sdk'

import { $botMeta, $lastRoster, botRosterKey } from './data'
import { recordCommittedGroupRooms } from './group-chat-ownership'
import { storedHostedPeerProbeHint, storedShippedGroupAdoption } from './group-chat-stored-records'
import {
  adoptGroupChatSyncRooms,
  compareGroupChatSyncEntries,
  flushGroupChatServerSync,
  groupChatRemoteSnapshot,
  groupChatSyncConnectionId,
  groupChatSyncEnvelope,
  groupChatSyncFallbackKey,
  groupChatSyncTargetConnections,
  mergeGroupChatSyncJobs,
  normalizeGroupChatSyncSnapshot
} from './group-chat-sync-transport'
import { boundedDesktopCommandSettled } from './group-command-receipts'
import {
  classicDesktopAuthority,
  classicProjectionAuthority,
  ensureClassicDesktopAuthority,
  storedClassicDesktopAuthority
} from './group-desktop-authority'
import { mergeMemberProjectionIntoRoom, mergeProjectedMemberLists } from './group-member-projection'
import {
  compactGroupMessageAuthor,
  compatibleGroupMessageCopies,
  GROUP_CHAT_SYNC_TEXT_CHARS,
  groupChatSyncSequence,
  groupMessageAnchors,
  mergeGroupMessageCopies
} from './group-message-author'
import {
  equalGroupRoomSnapshot, inheritGroupRoomSnapshot, rememberGroupRoomSnapshot, withGroupRoomWrite
} from './group-room-ownership'
import type { GroupRoomWriteOptions } from './group-room-ownership'
import {
  outgoingHostedUserEvent,
  projectedGroupMessage,
  reconcileHostedUserEvents,
  storedHostedUserEvent
} from './hosted-user-events'
import { groupMemberReferencesConnection, markOrphanedGroupMemberDescriptor } from './hygiene'
import { displayName } from './labels'
import { botRosterMeta } from './routing'
import { getPluginCtx } from './shared'
import type {
  Attachment,
  GroupChat,
  GroupHold,
  GroupMember,
  GroupMessage,
  GroupMessageAuthor,
  GroupPrompt,
  RosterRow
} from './types'

/** Optional secondary navigation inside the Bots pane (group-chat rooms). */

/** Group-chat rooms: { [group]: { log: [{from:{kind,name},text,at}], watermarks:{[member]:idx}, epoch, running } }.
 *  Log + watermarks persist via plugin storage; epoch/running are runtime-only. */
export const $groupChats = atom<Record<string, GroupChatRoom>>({})

export { observeGroupChatExecutionOwner, persistGroupChatRooms, refreshGroupChatExecutionOwner } from './group-chat-ownership'

/** Group whose room view is open in the Bots pane (secondary navigation
 *  inside the pane; a normal row click returns to the roster). */
export const $groupChatWorkspace = atom<null | string>(null)
/** Groups whose latest room activity mentions @user — the needs-you badge. */
export const $groupNeedsYou = atom<Record<string, boolean>>({})
/** Hosted approval attention stays separate from message mentions so resolving
 * one source cannot erase the other source's unread state. */
export const $groupHostedNeedsYou = atom<Record<string, boolean>>({})
// Pending prompts (clarify questions AND command approvals) raised inside
// hidden group-member sessions, keyed `${group}::${memberKey}` (#90694).
// Members run in invisible plumbing sessions, so a member's blocking prompt
// used to park server-side with no surface to answer it — the user saw
// "is thinking…" until the prompt timeout. The turn poll mirrors each
// member's `open_requests` / `pending_approval` resume fields in here;
// the room renders answer cards from it.
export const $groupClarify = atom<Record<string, GroupPrompt>>({})

export const GROUP_CHAT_SYNC_META_KEY = 'hermes-bots-groups'
// Gateway ui_meta is capped after Python JSON serialization. Keep a healthy
// margin below that limit because Python escapes Unicode while JS does not.
export const GROUP_CHAT_SYNC_MAX_BYTES = 48000
const GROUP_CHAT_SYNC_MESSAGES = 16
const GROUP_CHAT_SYNC_TRUNCATION_MARK = '… [truncated]'
const GROUP_CHAT_SYNC_IMAGE_CHARS = 24000
let groupChatSyncTimer: ReturnType<typeof setTimeout> | null = null

/** One room inside the bounded ui_meta projection: a compacted log plus the
 *  identity fields, without any of `GroupChat`'s runtime/orchestration state. */
export interface GroupChatSyncRoom {
  desktopAuthorityHash?: string
  desktopAuthorityConflict?: true
  continuityMode?: 'desktop' | 'distributed' | 'gateway'
  hosted?: null | string
  hostedEpoch?: null | number
  holdDetection?: boolean
  image?: null | string
  log: GroupMessage[]
  members?: GroupMember[]
  name?: string
  /** At least this many earlier room entries exist that the projection does
   *  not carry (head-trimmed to the message/byte budget). */
  omitted?: number
  revision?: number
  roomId?: string
}

/** The v3 envelope stored under the default profile's `hermes-bots-groups`
 *  ui_meta key. `deleted` maps a room key to its tombstone revision. */
export interface GroupChatSyncSnapshot {
  deleted?: Record<string, number>
  rooms: Record<string, GroupChatSyncRoom>
  updatedAt?: number
  version: number
}

/** A queued publish for one gateway, coalesced while the debounce runs. */
export interface GroupChatSyncJob {
  allowEmpty?: boolean
  changedRooms?: string[]
  connectionId: string
  deletedRooms?: string[]
}
// Fan-out scheduler state, keyed by gateway connectionId ('' = active/local).
// Every connected gateway carries the full projection so a room survives any
// single gateway being removed and surfaces on every remote backend.
export const groupChatSyncPendingByConnection = new Map<string, GroupChatSyncJob>()
export const groupChatSyncInFlightConnections = new Set<string>()
export const groupChatSyncRetryTimers = new Map<string, ReturnType<typeof setTimeout>>()
export const groupChatSyncRetryCounts = new Map<string, number>()
export let groupChatSyncDisposed = false

// Durable disband memory in the mirror's own tombstone shape (room key ->
// tombstone revision). A pending sync job forgets its deletedRooms once the
// retry ladder gives up or the window closes, and "missing remote rooms are
// not deletions" — so a gateway mirror that missed the tombstone push would
// re-merge the room on every later pull. This map rides every publish and
// every pull merge until the room is gone from every mirror (#105275).
export const groupChatTombstones: Record<string, number> = {}
const GROUP_CHAT_TOMBSTONES_KEY = 'group-chat-tombstones'

export function groupChatTombstoneMemory(): Record<string, number> {
  return { ...groupChatTombstones }
}

/** Remember a disband durably — ONLY by roomId. A name key would outlive the
 *  room: a same-name recreate starts at syncRevision 0 and the memory is
 *  applied on every pull with `deletedRevision >= syncRevision`, so the
 *  fresh room would be deleted forever. Legacy name-only rooms keep the
 *  job-scoped tombstone of the pending sync (the pre-#105275 behaviour).
 *  Revision = the room's last known sync revision + 1, the ordering the
 *  live tombstone merge applies. */
export function rememberGroupChatTombstone(name: string, roomId?: null | string, syncRevision?: number) {
  if (typeof roomId !== 'string' || !roomId) {
    return Promise.resolve()
  }

  const key = `id:${roomId}`
  groupChatTombstones[key] = Math.max(Number(groupChatTombstones[key] || 0), Math.max(0, Number(syncRevision || 0)) + 1)

  for (const stale of Object.keys(groupChatTombstones)
    .sort((left, right) => groupChatTombstones[right] - groupChatTombstones[left])
    .slice(64)) {
    delete groupChatTombstones[stale]
  }

  try {
    return Promise.resolve(getPluginCtx()?.storage?.set?.(GROUP_CHAT_TOMBSTONES_KEY, { ...groupChatTombstones })).catch(
      () => undefined
    )
  } catch {
    return Promise.resolve()
  }
}

export function hydrateGroupChatTombstones(value: unknown) {
  for (const key of Object.keys(groupChatTombstones)) {
    delete groupChatTombstones[key]
  }

  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    return
  }

  for (const [key, revision] of Object.entries(value as Record<string, unknown>)) {
    // Older builds persisted `name:` keys too; they are exactly the memory
    // that blocks a same-name recreate, so they are dropped on hydrate.
    if (key.startsWith('id:')) {
      groupChatTombstones[key] = Math.max(0, Number(revision || 0))
    }
  }
}

/** Cut one room body to a per-message budget and mark the cut (the sync
 *  projection and the per-turn delta window share the convention).
 *  Receivers used to see a silent mid-sentence slice with no signal that the
 *  body continued. Keep the mark inside the same char budget so CJK/envelope
 *  accounting does not grow. */
export function compactGroupChatSyncText(text: string, limit = GROUP_CHAT_SYNC_TEXT_CHARS) {
  const raw = String(text || '')

  if (raw.length <= limit) {
    return { text: raw }
  }

  const budget = Math.max(0, limit - GROUP_CHAT_SYNC_TRUNCATION_MARK.length)

  return {
    text: `${raw.slice(0, budget)}${GROUP_CHAT_SYNC_TRUNCATION_MARK}`,
    truncated: true as const
  }
}

/** #114341: the ui_meta mirror is the only on-disk copy of a room, so a
 *  head-trimmed log must say how many earlier entries it does not carry —
 *  a bare slice reads as "the user never said it". */
function noteGroupChatSyncOmitted(room: GroupChatSyncRoom, total: number) {
  const omitted = total - room.log.length

  if (omitted > 0) {
    room.omitted = omitted
  }
}

/** Conservative byte count for the gateway's ensure_ascii JSON encoding.
 *  Python also inserts separator spaces, so reserve one extra byte per JS
 *  structural separator on top of escaped Unicode code-point widths. */
export function groupChatGatewayJsonSize(value: unknown) {
  const json = JSON.stringify(value)
  let bytes = 0

  for (const character of json) {
    // Non-null: string iteration yields whole code points, never an empty string.
    const codePoint = character.codePointAt(0)!

    if (codePoint <= 0x7f) {
      bytes += 1

      if (character === ',' || character === ':') {
        bytes += 1
      }
    } else {
      bytes += codePoint <= 0xffff ? 6 : 12
    }
  }

  return bytes
}

/** Durable room identity for the sync projection. Rooms minted on current
 *  builds carry an immutable roomId; the projection keys rooms by
 *  `id:<roomId>` so rename is a display-name edit, not a distributed
 *  delete+create, and disband tombstones follow the room itself. Legacy
 *  rooms (no roomId) fall back to `name:<name>` keys with the older
 *  revision-gated tombstone semantics. */
export function groupChatRoomKey(name: string, room: GroupChat) {
  return typeof room?.roomId === 'string' && room.roomId ? `id:${room.roomId}` : `name:${String(name)}`
}

/** Stable authority id for a gateway-hosted room. Presence is an execution
 * fence: a Desktop that cannot reach that gateway must not start a second
 * local round driver for the same room. */
export function groupChatHostedGateway(room: null | Partial<Pick<GroupChat, 'hosted' | 'hostedEpoch'>> | undefined) {
  return typeof room?.hosted === 'string' ? room.hosted.trim().slice(0, 128) : ''
}

/** Monotonic authority epoch. Legacy hosted records predate the explicit
 * field and safely mean epoch 1. */
export function groupChatHostedEpoch(room: null | Partial<Pick<GroupChat, 'hosted' | 'hostedEpoch'>> | undefined) {
  const epoch = Number(room?.hostedEpoch || 0)

  if (Number.isSafeInteger(epoch) && epoch >= 1) {
    return epoch
  }

  return groupChatHostedGateway(room) ? 1 : 0
}

export function groupChatContinuityMode(
  room: null | Partial<Pick<GroupChat, 'continuityMode' | 'hosted' | 'hostedEpoch'>> | undefined
) {
  if (!groupChatHostedGateway(room)) {
    return 'desktop' as const
  }

  return room?.continuityMode === 'distributed' ? ('distributed' as const) : ('gateway' as const)
}

/** Apply authority only from `groups.state`, never from the client-writable
 * ui_meta display projection. A conflicting owner cannot replace an existing
 * fence without a server-issued authority transfer receipt. */
export function applyHostedRoomAuthority(room: GroupChat, serverRoom: Record<string, unknown>): GroupChat {
  const authorityGateway = groupChatHostedGateway({
    hosted: typeof serverRoom.authority_gateway_id === 'string' ? serverRoom.authority_gateway_id : null
  })

  const authorityEpoch = Number(serverRoom.authority_epoch || 0)
  const roomId = typeof room?.roomId === 'string' ? room.roomId : ''
  const serverRoomId = typeof serverRoom.room_id === 'string' ? serverRoom.room_id : ''

  if (
    !authorityGateway ||
    !Number.isSafeInteger(authorityEpoch) ||
    authorityEpoch < 1 ||
    (roomId && serverRoomId && roomId !== serverRoomId)
  ) {
    return room
  }

  const currentGateway = groupChatHostedGateway(room)
  const currentEpoch = groupChatHostedEpoch(room)

  const claim =
    serverRoom.authority_claim && typeof serverRoom.authority_claim === 'object'
      ? (serverRoom.authority_claim as Record<string, unknown>)
      : null

  const actor = claim?.actor && typeof claim.actor === 'object' ? (claim.actor as Record<string, unknown>) : null

  const payload =
    claim?.payload && typeof claim.payload === 'object' ? (claim.payload as Record<string, unknown>) : null

  const transferProven = Boolean(
    claim?.kind === 'authority.claimed' &&
    actor?.kind === 'system' &&
    actor?.id === 'authority-control' &&
    Number(claim?.authority_epoch || 0) === authorityEpoch &&
    payload?.previous_gateway_id === currentGateway &&
    payload?.authority_gateway_id === authorityGateway &&
    Number(payload?.authority_epoch || 0) === authorityEpoch
  )

  if (
    currentEpoch > authorityEpoch ||
    (currentGateway && currentGateway !== authorityGateway && !transferProven) ||
    (currentEpoch === authorityEpoch && currentGateway && currentGateway !== authorityGateway)
  ) {
    return room
  }

  return {
    ...room,
    hosted: authorityGateway,
    hostedEpoch: authorityEpoch,
    continuityMode: room.continuityMode === 'distributed' ? 'distributed' : 'gateway'
  }
}

/** Compact, display-oriented copy of Desktop's room log for gateway clients.
 *  The live orchestration state stays in plugin storage; this bounded mirror
 *  rides the default profile's ui_meta so mobile can show the same messages.
 *  Newest rooms/messages win when the profile metadata size cap is reached. */
export function groupChatSyncSnapshot(
  // `revision` is the pre-`syncRevision` field name, still read below as a
  // fallback for rooms hydrated from an older plugin-storage record.
  all: Record<string, GroupChat & { revision?: number }> = $groupChats.get(),
  deleted: Record<string, number> = {}
): GroupChatSyncSnapshot {
  const ranked = Object.entries(all || {})
    // Empty runtime tombstones are used to stop an in-flight room after
    // disband. They are not real rooms and must never reappear on mobile.
    .filter(([, room]) => room && Array.isArray(room.log) && room.log.length > 0)
    .sort(([, left], [, right]) => {
      const leftAt = Number(left.log[left.log.length - 1]?.at || 0)
      const rightAt = Number(right.log[right.log.length - 1]?.at || 0)

      return rightAt - leftAt
    })

  const rooms: Record<string, GroupChatSyncRoom> = {}

  const boundedDeleted = Object.fromEntries(
    Object.entries(deleted)
      .sort(([, left], [, right]) => Number(right || 0) - Number(left || 0))
      .slice(0, 64)
  )

  const envelope: GroupChatSyncSnapshot = {
    version: 3,
    updatedAt: Date.now(),
    rooms,
    ...(Object.keys(boundedDeleted).length
      ? {
          deleted: boundedDeleted
        }
      : {})
  }

  for (const [name, room] of ranked) {
    const entries =
      room.roomId && groupChatHostedGateway(room) ? reconcileHostedUserEvents(room.roomId, room.log) : room.log

    const log: GroupMessage[] = entries.slice(-GROUP_CHAT_SYNC_MESSAGES).map(entry => ({
      ...(entry?.id
        ? {
            id: String(entry.id).slice(0, 160)
          }
        : {}),
      ...(entry.from?.kind === 'user' && (entry.eventId || (groupChatSyncSequence(entry) !== null && entry.id))
        ? { eventId: String(entry.eventId || entry.id).slice(0, 160) }
        : {}),
      ...(entry.roomId ? { roomId: String(entry.roomId).slice(0, 128) } : {}),
      from: compactGroupMessageAuthor(entry?.from),
      text: groupChatHostedGateway(room)
        ? String(entry?.text || '').slice(0, GROUP_CHAT_SYNC_TEXT_CHARS)
        : compactGroupChatSyncText(String(entry?.text || '')).text,
      ...(!groupChatHostedGateway(room) && compactGroupChatSyncText(String(entry?.text || '')).truncated
        ? { truncated: true }
        : {}),
      at: Number(entry?.at || 0),
      ...(entry?.thread
        ? {
            thread: String(entry.thread).slice(0, 128)
          }
        : {})
    }))

    const compact: GroupChatSyncRoom = {
      ...classicDesktopAuthority(room),
      name: String(name).slice(0, 64),
      ...(typeof room?.roomId === 'string' && room.roomId
        ? {
            roomId: String(room.roomId).slice(0, 128)
          }
        : {}),
      // Presence is the mixed-version contract. Older clients omit these
      // fields; hosted-aware clients must preserve an existing non-empty
      // authority fence instead of interpreting omission as a local takeover.
      hosted: groupChatHostedGateway(room) || null,
      hostedEpoch: groupChatHostedEpoch(room) || null,
      continuityMode: groupChatContinuityMode(room),
      log,
      holdDetection: room.holdDetection !== false,
      revision: Math.max(0, Number(room?.syncRevision ?? room?.revision ?? 0)),
      members: (Array.isArray(room.members) ? room.members : []).slice(0, GROUP_CHAT_MAX_MEMBERS).map(member => ({
        name: String(member?.name || '').slice(0, 128),
        ...(member?.hostedIdentity
          ? {
              hostedIdentity: { ...member.hostedIdentity },
              title: String(member.title || '').slice(0, 256),
              display_name: String(member.display_name || '').slice(0, 256),
              targetProfile: member.hostedIdentity.profile,
              remoteSource: true,
              sourceMissing: member.sourceMissing,
              sourceReachable: member.sourceReachable
            }
          : {}),
        ...(member?.handle
          ? {
              handle: String(member.handle).slice(0, 128)
            }
          : {}),
        ...(member?.connectionId
          ? {
              connectionId: String(member.connectionId).slice(0, 128)
            }
          : {}),
        ...(member?.connectionKind
          ? {
              connectionKind: String(member.connectionKind).slice(0, 64)
            }
          : {}),
        ...(member?.connectionLabel
          ? {
              connectionLabel: String(member.connectionLabel).slice(0, 128)
            }
          : {}),
        ...(member?.sourceScoped
          ? {
              sourceScoped: true
            }
          : {})
      })),
      ...(typeof room?.image === 'string' && room.image.length <= GROUP_CHAT_SYNC_IMAGE_CHARS
        ? {
            image: room.image
          }
        : {})
    }

    const key = groupChatRoomKey(name, room)
    rooms[key] = compact
    noteGroupChatSyncOmitted(compact, room.log.length)

    while (compact.log.length > 1 && groupChatGatewayJsonSize(envelope) > GROUP_CHAT_SYNC_MAX_BYTES) {
      compact.log.shift()
      noteGroupChatSyncOmitted(compact, room.log.length)
    }

    if (compact.image && groupChatGatewayJsonSize(envelope) > GROUP_CHAT_SYNC_MAX_BYTES) {
      delete compact.image
    }

    if (groupChatGatewayJsonSize(envelope) > GROUP_CHAT_SYNC_MAX_BYTES) {
      delete rooms[key]
    }
  }

  return envelope
}

export { boundedDesktopCommandSettled } from './group-command-receipts'

/** Union a hosted replay with local/compact mirrors without briefly showing
 * both the optimistic event id and its authoritative sequence twin. */
export function mergeGroupChatSyncEntries(...logs: GroupMessage[][]) {
  const entries: GroupMessage[] = []
  const byAnchor = new Map<string, Set<number>>()

  for (const entry of logs.flat()) {
    const keys = groupMessageAnchors(entry)

    if (!entry.from?.hostedIdentity && !entry.from?.hostedIdentityEvidence) {
      keys.push(`fallback:${groupChatSyncFallbackKey(entry)}`)
    }

    // Keep all candidates for conflicting anchors. Neither insertion order
    // nor a matching sequence may silently alias two different stable IDs.
    const candidates = new Set(keys.flatMap(key => [...(byAnchor.get(key) || [])]))
    const compatible = [...candidates].filter(index => compatibleGroupMessageCopies(entries[index], entry))
    const scores = compatible.map(index => groupMessageAnchors(entries[index]).filter(key => keys.includes(key)).length)
    const strongest = Math.max(0, ...scores)
    const matches = compatible.filter((_, index) => scores[index] === strongest)
    const index = matches.length === 1 ? matches[0] : entries.length

    if (index === entries.length) {
      entries.push(entry)
    } else {
      entries[index] = mergeGroupMessageCopies(entries[index], entry)
    }

    for (const key of keys) {
      const indices = byAnchor.get(key) || new Set<number>()
      indices.add(index)
      byAnchor.set(key, indices)
    }
  }

  return entries.sort(compareGroupChatSyncEntries)
}

/** Legacy display rows may lack room/actor metadata. Keep their kinds separate
 * through import and later room merges, even before room identity is available. */
export function mergeGroupChatRoomEntries(room: Pick<GroupChat, 'roomId' | 'hosted'>, ...logs: GroupMessage[][]) {
  const byKind = new Map<GroupMessageAuthor['kind'] | undefined, GroupMessage[]>()

  for (const entry of logs.flat()) {
    const kind = entry.from?.kind
    const entries = byKind.get(kind) || []
    entries.push(entry)
    byKind.set(kind, entries)
  }

  return [...byKind.values()]
    .flatMap(entries =>
      mergeGroupChatSyncEntries(
        room.roomId && groupChatHostedGateway(room) ? reconcileHostedUserEvents(room.roomId, entries) : entries
      )
    )
    .sort(compareGroupChatSyncEntries)
}

/** Merge two bounded projections without treating an absent room/message as
 *  deletion. Rooms are identified by durable room keys (id:<roomId> when the
 *  room carries one), so a rename is a same-key field update — never a
 *  distributed delete+create — and a disband tombstone follows the room
 *  itself. Gateway revisions order identity/membership/picture and
 *  tombstones; stable message ids make concurrent log union idempotent.
 *  `changedRooms`/`deletedRooms` accept display names or room keys. */
export function mergeGroupChatSyncSnapshots(
  remote: GroupChatSyncSnapshot | null | undefined,
  local: GroupChatSyncSnapshot | null | undefined,
  {
    changedRooms = [],
    deletedRooms = [],
    writeRevision = 0
  }: { changedRooms?: string[]; deletedRooms?: string[]; writeRevision?: number } = {}
) {
  const remoteNorm = normalizeGroupChatSyncSnapshot(remote)
  const localNorm = normalizeGroupChatSyncSnapshot(local)

  const keysFor = (label: string, norm: GroupChatSyncSnapshot) => {
    const keys = new Set<string>()

    for (const [key, room] of Object.entries(norm.rooms || {})) {
      if (key === label || String(room?.name || '') === label || key === `name:${label}`) {
        keys.add(key)
      }
    }

    if (String(label).startsWith('id:') || String(label).startsWith('name:')) {
      keys.add(label)
    } else if (!keys.size) {
      keys.add(`name:${label}`)
    }

    return keys
  }

  const changed = new Set<string>()

  for (const label of changedRooms) {
    for (const key of keysFor(label, localNorm)) {
      changed.add(key)
    }
  }

  const deleted: Record<string, number> = {}

  for (const source of [remoteNorm, localNorm]) {
    for (const [key, at] of Object.entries(source.deleted || {})) {
      deleted[key] = Math.max(Number(deleted[key] || 0), Math.max(0, Number(at || 0)))
    }
  }

  for (const label of deletedRooms) {
    for (const key of new Set([...keysFor(label, remoteNorm), ...keysFor(label, localNorm)])) {
      // Rename passes changedRooms:[newName] + deletedRooms:[oldName]. For an
      // id-keyed room both labels resolve to the SAME durable key (the remote
      // copy still carries the old display name), and id tombstones are
      // final — so tombstoning here would kill the room being renamed. A key
      // that is being written this cycle is a rename target, not a disband.
      if (changed.has(key)) {
        continue
      }

      deleted[key] = Math.max(Number(deleted[key] || 0), Number(writeRevision || 0))
    }
  }

  const rooms: Record<string, GroupChatSyncRoom> = {}
  const roomKeys = new Set([...Object.keys(remoteNorm.rooms || {}), ...Object.keys(localNorm.rooms || {})])

  for (const key of roomKeys) {
    const remoteRoom = remoteNorm.rooms?.[key]
    const localRoom = localNorm.rooms?.[key]

    if ((!remoteRoom || !Array.isArray(remoteRoom.log)) && (!localRoom || !Array.isArray(localRoom.log))) {
      continue
    }

    const remoteRevision = Math.max(0, Number(remoteRoom?.revision || 0))
    // Either writer's head trim is a lower bound on what the union still lacks.
    const omitted = Math.max(Number(remoteRoom?.omitted || 0), Number(localRoom?.omitted || 0))

    const localRevision = changed.has(key)
      ? Math.max(0, Number(writeRevision || 0))
      : Math.max(0, Number(localRoom?.revision || 0))

    const keyRoomId = key.startsWith('id:') ? key.slice(3) : undefined

    const entries = mergeGroupChatRoomEntries(
      {},
      (remoteRoom?.log || []).map(entry =>
        projectedGroupMessage(entry, groupChatHostedGateway(remoteRoom) ? remoteRoom?.roomId || keyRoomId : undefined)
      ),
      (localRoom?.log || []).map(entry =>
        projectedGroupMessage(entry, groupChatHostedGateway(localRoom) ? localRoom?.roomId || keyRoomId : undefined)
      )
    )

    // Identity fields (display name, membership, picture) follow the higher
    // revision; a tie unions members and prefers the local writer's fields.
    let identity: GroupChatSyncRoom | undefined
    let members: GroupMember[]
    let image: null | string | undefined
    let hosted: null | string | undefined
    let hostedEpoch = 0
    let hostedPresent = false
    const remoteHostedPresent = Object.prototype.hasOwnProperty.call(remoteRoom || {}, 'hosted')
    const localHostedPresent = Object.prototype.hasOwnProperty.call(localRoom || {}, 'hosted')
    let holdDetection = true

    if (localRevision > remoteRevision) {
      identity = localRoom
      members = [...(localRoom?.members || [])]
      image = localRoom?.image
      hostedPresent = localHostedPresent || remoteHostedPresent
      hosted = localHostedPresent ? groupChatHostedGateway(localRoom) : groupChatHostedGateway(remoteRoom)
      hostedEpoch = localHostedPresent ? groupChatHostedEpoch(localRoom) : groupChatHostedEpoch(remoteRoom)
      holdDetection = localRoom?.holdDetection !== false
    } else if (remoteRevision > localRevision) {
      identity = remoteRoom
      members = [...(remoteRoom?.members || [])]
      image = remoteRoom?.image
      hostedPresent = remoteHostedPresent || localHostedPresent
      hosted = remoteHostedPresent ? groupChatHostedGateway(remoteRoom) : groupChatHostedGateway(localRoom)
      hostedEpoch = remoteHostedPresent ? groupChatHostedEpoch(remoteRoom) : groupChatHostedEpoch(localRoom)
      holdDetection = remoteRoom?.holdDetection !== false
    } else {
      identity = localRoom || remoteRoom
      members = mergeProjectedMemberLists(remoteRoom, localRoom)
      image = Object.prototype.hasOwnProperty.call(localRoom || {}, 'image') ? localRoom.image : remoteRoom?.image
      hostedPresent = localHostedPresent || remoteHostedPresent
      hosted = localHostedPresent ? groupChatHostedGateway(localRoom) : groupChatHostedGateway(remoteRoom)
      hostedEpoch = localHostedPresent ? groupChatHostedEpoch(localRoom) : groupChatHostedEpoch(remoteRoom)
      holdDetection = Object.prototype.hasOwnProperty.call(localRoom || {}, 'holdDetection')
        ? localRoom?.holdDetection !== false
        : remoteRoom?.holdDetection !== false
    }

    // ui_meta is a display cache, not an authority receipt. Preserve any
    // existing non-empty fence even if a newer legacy writer omitted it.
    const remoteHosted = groupChatHostedGateway(remoteRoom)
    const localHosted = groupChatHostedGateway(localRoom)

    if (localHosted) {
      hostedPresent = true
      hosted = localHosted
      hostedEpoch = groupChatHostedEpoch(localRoom)
    } else if (remoteHosted) {
      hostedPresent = true
      hosted = remoteHosted
      hostedEpoch = groupChatHostedEpoch(remoteRoom)
    }

    rooms[key] = {
      ...classicDesktopAuthority(
        { ...classicProjectionAuthority(remoteRoom, key), hosted: remoteRoom?.hosted },
        { ...classicProjectionAuthority(localRoom, key), hosted: localRoom?.hosted }
      ),
      ...(identity?.name
        ? {
            name: identity.name
          }
        : {}),
      ...(identity?.roomId || (key.startsWith('id:') ? key.slice(3) : '')
        ? {
            roomId: key.startsWith('id:') ? key.slice(3) : identity?.roomId
          }
        : {}),
      log: entries,
      holdDetection,
      ...(omitted > 0 ? { omitted } : {}),
      members,
      revision: Math.max(remoteRevision, localRevision),
      ...(hostedPresent
        ? {
            hosted: hosted || null,
            hostedEpoch: hostedEpoch || null,
            continuityMode: hosted
              ? identity?.continuityMode === 'distributed'
                ? ('distributed' as const)
                : ('gateway' as const)
              : ('desktop' as const)
          }
        : {}),
      ...(typeof image === 'string' && image
        ? {
            image
          }
        : {})
    }
  }

  for (const [key, deletedRevision] of Object.entries(deleted)) {
    if (key.startsWith('id:')) {
      // Tombstones for id-keyed rooms are FINAL: the roomId is minted once
      // and never reused (same-name recreation mints a fresh id), so a
      // resurrect-by-revision race is structurally impossible. Keep the
      // tombstone even when a lagging gateway's copy carries a higher
      // revision — that copy is the resurrection this exists to prevent.
      delete rooms[key]
    } else if (Number(deletedRevision || 0) >= Number(rooms[key]?.revision || 0)) {
      delete rooms[key]
    } else {
      delete deleted[key]
    }
  }

  return groupChatSyncEnvelope(rooms, deleted)
}

/** Merge the gateway's bounded display projection into Desktop's richer room
 *  state without discarding local session/watermark/runtime fields. Missing
 *  remote rooms/messages are not deletions; only explicit tombstones remove a
 *  room, and a genuinely newer local message wins over a stale tombstone. */
export function mergeRemoteGroupChatSnapshotIntoRooms(
  remote: GroupChatSyncSnapshot | null | undefined,
  current: Record<string, GroupChat> = $groupChats.get(),
  {
    preserveRooms = [],
    deletedRooms = [],
    tombstones = {}
  }: { deletedRooms?: string[]; preserveRooms?: string[]; tombstones?: Record<string, number> } = {}
) {
  const remoteNorm = normalizeGroupChatSyncSnapshot(remote)

  // Remote tombstones plus this Desktop's durable disband memory: a mirror
  // that missed the tombstone push still projects the room, and without the
  // local memory it would resurrect here on every pull (#105275).
  const deleted: Record<string, number> = { ...tombstones }

  for (const [key, at] of Object.entries(remoteNorm.deleted || {})) {
    deleted[key] = Math.max(Number(deleted[key] || 0), Math.max(0, Number(at || 0)))
  }

  const rooms: Record<string, GroupChat> = {
    ...(current || {})
  }

  const preserved = new Set(preserveRooms)
  const locallyDeleted = new Set(deletedRooms)

  // deletedRooms names a pending local disband. It must hide the REMOTE copy
  // of that room, but a live local record under the same name is newer than
  // the disband (the disband itself leaves nothing, or only the flagged
  // runtime tombstone, under that name): a same-name recreate that landed
  // inside the sync window. Only the disbanded record itself may be dropped.
  const dropLocallyDeleted = (name: null | string | undefined) => {
    if (!name || (rooms[name] && !rooms[name].tombstone)) {
      return
    }

    delete rooms[name]
  }

  // Local rooms indexed by durable identity so an id-keyed projection room
  // finds its local twin even when the display name changed remotely.
  const localByRoomId = new Map<string, string>()

  for (const [name, room] of Object.entries(rooms)) {
    if (typeof room?.roomId === 'string' && room.roomId) {
      localByRoomId.set(room.roomId, name)
    }
  }

  for (const [key, projected] of Object.entries(remoteNorm.rooms || {})) {
    if (!projected || !Array.isArray(projected.log)) {
      continue
    }

    const projectedRoomId = projected.roomId || (key.startsWith('id:') ? key.slice(3) : null)

    const localName =
      projectedRoomId && localByRoomId.has(projectedRoomId)
        ? localByRoomId.get(projectedRoomId)
        : projected.name && rooms[projected.name]
          ? projected.name
          : null

    const displayName = String(projected.name || localName || (key.startsWith('name:') ? key.slice(5) : key))

    if (locallyDeleted.has(displayName) || (localName && locallyDeleted.has(localName))) {
      // Mid-rename guard: the remote copy may still be under the OLD display
      // name while the local record was already re-keyed (same roomId, new
      // name). That old name sits in deletedRooms, but the local record is
      // the rename in flight — deleting it here would kill the renamed room.
      if (localName && localName !== displayName && !locallyDeleted.has(localName)) {
        continue
      }

      dropLocallyDeleted(displayName)
      dropLocallyDeleted(localName)

      continue
    }

    const existing = (localName ? rooms[localName] : rooms[displayName]) || {}
    const remoteRevision = Math.max(0, Number(projected.revision || 0))
    const localRevision = Math.max(0, Number(existing.syncRevision || 0))

    const isPreserved = preserved.has(displayName) || (localName && preserved.has(localName))

    const membership = mergeMemberProjectionIntoRoom(
      existing,
      projected,
      Boolean(isPreserved) || remoteRevision < localRevision,
      remoteRevision > localRevision
    )

    // Projection copies are compact and therefore go first: the local rich
    // twin overlays them while retaining the authoritative hosted sequence.
    const logRoom = projectedRoomId && existing.roomId === projectedRoomId ? existing : {}
    const differentRooms = projectedRoomId && existing.roomId && projectedRoomId !== existing.roomId
    const hostedScope = groupChatHostedGateway(existing) || groupChatHostedGateway(projected)

    // A name fallback cannot make (room, event) IDs global. Scope imported
    // actors to their container, and fill missing local scope without replacing
    // explicit canonical evidence already retained in the local cache.
    const projectedLog = projected.log.map(entry => {
      const display = projectedGroupMessage(entry, hostedScope ? projectedRoomId : undefined)

      return differentRooms ? { ...display, roomId: projectedRoomId! } : display
    })

    const localLog = (existing.log || []).map(entry =>
      existing.roomId && (differentRooms || hostedScope) && !entry.roomId
        ? { ...entry, roomId: existing.roomId }
        : entry
    )

    const log = assignLegacyThreads(mergeGroupChatRoomEntries(logRoom, projectedLog, localLog))

    const bounded = trimGroupChatLog(log, existing.watermarks || {})
    const projectedHosted = groupChatHostedGateway(projected)
    const existingHosted = groupChatHostedGateway(existing)
    const cachedHosted = existingHosted || projectedHosted
    const cachedHostedEpoch = existingHosted ? groupChatHostedEpoch(existing) : groupChatHostedEpoch(projected)

    // A remote rename with a higher revision moves the local record to the
    // new display name; local views keyed by the old name follow on the
    // next repaint (roster derives from $groupChats keys).
    const targetName = !isPreserved && remoteRevision > localRevision ? displayName : localName || displayName

    if (localName && targetName !== localName) {
      delete rooms[localName]
    }

    rooms[targetName] = {
      ...existing,
      desktopAuthorityHash: undefined,
      desktopAuthorityConflict: undefined,
      ...classicDesktopAuthority(
        existing,
        differentRooms
          ? undefined
          : {
              ...classicProjectionAuthority(projected, key),
              hosted: projected.hosted
            }
      ),
      log: bounded.log,
      holdDetection:
        !isPreserved && remoteRevision >= localRevision
          ? projected.holdDetection !== false
          : existing.holdDetection !== false,
      watermarks: bounded.watermarks,
      sessions: existing.sessions && typeof existing.sessions === 'object' ? existing.sessions : {},
      stranded: existing.stranded && typeof existing.stranded === 'object' ? existing.stranded : {},
      members: membership.members,
      ...(membership.needsRefresh ? { hostedMembersNeedRefresh: true } : {}),
      externalCursors:
        existing.externalCursors && typeof existing.externalCursors === 'object' ? existing.externalCursors : {},
      ...(projectedRoomId || existing.roomId
        ? {
            roomId: existing.roomId || projectedRoomId
          }
        : {}),
      image: isPreserved
        ? existing.image || null
        : remoteRevision >= localRevision && Object.prototype.hasOwnProperty.call(projected, 'image')
          ? projected.image || null
          : existing.image || null,
      hosted: cachedHosted || null,
      hostedEpoch: cachedHostedEpoch || null,
      continuityMode: cachedHosted
        ? existing.continuityMode === 'distributed' || projected.continuityMode === 'distributed'
          ? 'distributed'
          : 'gateway'
        : 'desktop',
      syncRevision: isPreserved ? localRevision : Math.max(remoteRevision, localRevision),
      epoch: Number(existing.epoch || 0),
      running: Boolean(existing.running)
    }
  }

  // Re-index by roomId: a resurrected projection room may have just landed
  // under a roomId no local twin carried when the index was first built.
  localByRoomId.clear()

  for (const [name, room] of Object.entries(rooms)) {
    if (typeof room?.roomId === 'string' && room.roomId) {
      localByRoomId.set(room.roomId, name)
    }
  }

  for (const [key, deletedAt] of Object.entries(deleted)) {
    const deletedRoomId = key.startsWith('id:') ? key.slice(3) : null

    const targetName =
      deletedRoomId && localByRoomId.has(deletedRoomId)
        ? localByRoomId.get(deletedRoomId)
        : key.startsWith('name:')
          ? key.slice(5)
          : null

    if (!targetName || preserved.has(targetName)) {
      continue
    }

    if (deletedRoomId) {
      // Id tombstones are final — the id is never reused, so there is no
      // legitimate higher-revision recreation to protect.
      delete rooms[targetName]
    } else {
      const deletedRevision = Math.max(0, Number(deletedAt || 0))

      if (deletedRevision >= Number(rooms[targetName]?.syncRevision || 0)) {
        delete rooms[targetName]
      }
    }
  }

  for (const name of locallyDeleted) {
    dropLocallyDeleted(name)
  }

  return rooms
}

/** Parse the exact local-storage room shape used by plugin registration. */
export function hydrateGroupChatRooms(value: unknown): Record<string, GroupChat> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    return {}
  }

  const rooms: Record<string, GroupChat> = {}

  for (const [name, raw] of Object.entries(value)) {
    const room = raw as GroupChat

    if (!room || !Array.isArray(room.log)) {
      continue
    }

    const log = room.log.map(storedHostedUserEvent)
    rooms[name] = {
      ...storedClassicDesktopAuthority(room),
      log: assignLegacyThreads(room.roomId && room.hosted ? reconcileHostedUserEvents(room.roomId, log) : log),
      watermarks: room.watermarks && typeof room.watermarks === 'object' ? room.watermarks : {},
      sessions: room.sessions && typeof room.sessions === 'object' ? room.sessions : {},
      sessionOwners: room.sessionOwners && typeof room.sessionOwners === 'object' ? room.sessionOwners : {},
      stranded: room.stranded && typeof room.stranded === 'object' ? room.stranded : {},
      holds: room.holds && typeof room.holds === 'object' ? room.holds : {},
      desktopCommandSettled: boundedDesktopCommandSettled(room.desktopCommandSettled),
      members: Array.isArray(room.members) ? room.members : [],
      shippedAdoption: storedShippedGroupAdoption(room),
      shippedPreflight: room.shippedPreflight?.state === 'waiting' && !room.shippedPreflight.route
        ? storedShippedGroupAdoption({ shippedAdoption: room.shippedPreflight }) : undefined,
      roomId: typeof room.roomId === 'string' && room.roomId ? room.roomId : null,
      hosted: typeof room.hosted === 'string' && room.hosted ? room.hosted : null,
      hostedEpoch: Math.max(0, Number(room.hostedEpoch || 0)) || null,
      hostedConnectionId:
        typeof room.hostedConnectionId === 'string' && room.hostedConnectionId ? room.hostedConnectionId : null,
      hostedSeq: Math.max(0, Number(room.hostedSeq || 0)),
      hostedMembersVerified: room.hostedMembersVerified === true,
      peerProbeHint: storedHostedPeerProbeHint(room),
      continuityMode: room.hosted
        ? room.continuityMode === 'distributed'
          ? 'distributed'
          : 'gateway'
        : room.continuityMode === 'gateway'
          ? 'gateway'
          : 'desktop',
      image: typeof room.image === 'string' && room.image ? room.image : null,
      rosterOrder: Number.isFinite(room.rosterOrder) ? room.rosterOrder : undefined,
      pinned: Boolean(room.pinned),
      syncRevision: Math.max(0, Number(room.syncRevision || 0)),
      epoch: 0,
      running: false
    }
  }

  const projections = durableGroupChatRooms(rooms)

  for (const [name, room] of Object.entries(rooms)) {
    rememberGroupRoomSnapshot(name, room, (value as Record<string, GroupChat>)[name], projections[name])
  }

  return rooms
}

export function durableGroupChatRooms(all: Record<string, GroupChat> = $groupChats.get()) {
  const durable: Record<string, GroupChat> = {}

  for (const [name, room] of Object.entries(all || {})) {
    if (!room || !Array.isArray(room.log)) {
      continue
    }

    // Disband tombstones are runtime-only coordination state (they hold the
    // epoch bump for an in-flight drive). Persisting one would resurrect the
    // room as an empty record on the next load AND keep its name "taken" for
    // same-name recreates. Mirrors updateGroupChat's inline durable map.
    if (room.tombstone) {
      continue
    }

    durable[name] = {
      ...storedClassicDesktopAuthority(room),
      log: room.log.map(storedHostedUserEvent),
      holdDetection: room.holdDetection !== false,
      heldMessages: room.heldMessages || {},
      watermarks: room.watermarks || {},
      sessions: room.sessions || {},
      sessionOwners: room.sessionOwners || {},
      holds: room.holds || {},
      desktopCommandSettled: boundedDesktopCommandSettled(room.desktopCommandSettled),
      stranded: room.stranded || {},
      externalCursors: room.externalCursors || {},
      members: Array.isArray(room.members) ? room.members : [],
      shippedAdoption: storedShippedGroupAdoption(room),
      shippedPreflight: room.shippedPreflight?.state === 'waiting' && !room.shippedPreflight.route
        ? storedShippedGroupAdoption({ shippedAdoption: room.shippedPreflight }) : undefined,
      // Immutable room identity: without this, a room merged in via the
      // remote-sync path (the only caller of this function) loses its
      // roomId on the next cold hydrate and falls back to legacy
      // name-keyed identity — same field updateGroupChat's inline map
      // already carries.
      roomId: typeof room.roomId === 'string' && room.roomId ? room.roomId : null,
      hosted: groupChatHostedGateway(room) || null,
      hostedEpoch: groupChatHostedEpoch(room) || null,
      hostedConnectionId:
        typeof room.hostedConnectionId === 'string' && room.hostedConnectionId ? room.hostedConnectionId : null,
      hostedSeq: Math.max(0, Number(room.hostedSeq || 0)),
      hostedMembersVerified: room.hostedMembersVerified === true,
      peerProbeHint: storedHostedPeerProbeHint(room),
      continuityMode: groupChatContinuityMode(room),
      image: room.image || null,
      rosterOrder: room.rosterOrder,
      pinned: room.pinned,
      // Sidebar filing (user-sections) is room-local; keep it across sync.
      sectionId: room.sectionId ?? null,
      syncRevision: Math.max(0, Number(room.syncRevision || 0))
    }
    inheritGroupRoomSnapshot(room, durable[name])
  }

  return durable
}

/** Mailbox startup cannot advertise private authority before it is durable. */
export async function persistGroupChatRoomsRequired(
  all: Record<string, GroupChat> = $groupChats.get(),
  storage = getPluginCtx()?.storage,
  requiredGroup?: string,
  options: Omit<GroupRoomWriteOptions, 'target'> = {}
) {
  if (!storage?.set || !storage?.get) {
    throw new Error('Group Chat storage unavailable')
  }

  const durable = durableGroupChatRooms(all)
  const wasCurrent = all === $groupChats.get()
  const receipt = withGroupRoomWrite(durable, { ...options, target: requiredGroup }, () => storage.set('group-chats', durable))
  await receipt.value
  const saved = await storage.get<Record<string, GroupChat>>('group-chats', {}) || {}

  // Adoption commits one exact room. A different window may legitimately edit
  // or rename an independent room while the importer is awaiting its ACK.
  if (requiredGroup !== undefined) {
    if (!equalGroupRoomSnapshot(saved[requiredGroup], durable[requiredGroup]) ||
        (options.renameFrom !== undefined && saved[options.renameFrom] !== undefined) ||
        (wasCurrent && !equalGroupRoomSnapshot(durableGroupChatRooms()[requiredGroup], durable[requiredGroup]))) {
      throw new Error('Group Chat changes could not be saved. Check available storage and try again.')
    }

    recordCommittedGroupRooms(all, durable, saved)

    return
  }

  const reconciled = receipt.committed || durable

  // PluginStorage.set deliberately swallows write errors. Verify the exact
  // snapshot, including the private authority and any settlement receipts.
  if (
    !equalGroupRoomSnapshot(saved, reconciled) ||
    (wasCurrent && all !== $groupChats.get() && !equalGroupRoomSnapshot(durableGroupChatRooms(), durable))
  ) {
    throw new Error('Group Chat changes could not be saved. Check available storage and try again.')
  }

  recordCommittedGroupRooms(all, durable, saved)

  if (wasCurrent && all === $groupChats.get()) {
    const refreshed: Record<string, GroupChatRoom> = { ...all }
    let changed = false

    for (const [name, room] of Object.entries(saved)) {
      if (!equalGroupRoomSnapshot(room, durable[name])) {
        const hydrated = hydrateGroupChatRooms({ [name]: room })[name]

        const retained: GroupChatRoom = { ...all[name], ...hydrated,
          epoch: all[name]?.epoch, running: all[name]?.running }

        inheritGroupRoomSnapshot(hydrated, retained)
        refreshed[name] = retained
        changed = true
      }
    }

    if (changed) { $groupChats.set(refreshed) }
  }
}

/** Register-removed sweep: annotate (not delete) every persisted group-chat
 *  member owned by the deleted connection, in the atom AND plugin storage.
 *  Writes ride updateGroupChat so the durable record keeps its full shape
 *  (sessionOwners, holds — durableGroupChatRooms would drop them).
 *  Returns whether anything changed. */
export function sweepGroupChatMembersForRemovedConnection(connectionId: string) {
  const id = String(connectionId || '').trim()

  if (!id) {
    return false
  }

  let changed = false

  for (const [name, room] of Object.entries($groupChats.get())) {
    const members = Array.isArray(room?.members) ? room.members : []

    if (!members.some(member => groupMemberReferencesConnection(member, id) && !member?.sourceMissing)) {
      continue
    }

    changed = true
    updateGroupChat(name, (current: GroupChat) => ({
      ...current,
      members: (Array.isArray(current.members) ? current.members : []).map(member =>
        groupMemberReferencesConnection(member, id) ? markOrphanedGroupMemberDescriptor(member) : member
      )
    }))
  }

  return changed
}

/** Pull the shared room projection into this Desktop before it publishes any
 *  local state. This is the receive half of the client-only sync contract. */
export async function pullGroupChatServerState(connectionId: string = groupChatSyncConnectionId()) {
  const { snapshot: remote } = await groupChatRemoteSnapshot({
    connectionId
  })

  if (!remote) {
    return false
  }

  const pending = groupChatSyncPendingByConnection.get(String(connectionId || ''))

  const merged = mergeRemoteGroupChatSnapshotIntoRooms(remote, $groupChats.get(), {
    preserveRooms: pending?.changedRooms || [],
    deletedRooms: pending?.deletedRooms || [],
    tombstones: groupChatTombstones
  })

  await adoptGroupChatSyncRooms(merged)

  return true
}

export function stopGroupChatServerSync() {
  groupChatSyncDisposed = true
  groupChatSyncPendingByConnection.clear()

  if (groupChatSyncTimer !== null) {
    clearTimeout(groupChatSyncTimer)
    groupChatSyncTimer = null
  }

  for (const timer of groupChatSyncRetryTimers.values()) {
    clearTimeout(timer)
  }

  groupChatSyncRetryTimers.clear()
  groupChatSyncRetryCounts.clear()
}

/** Debounced, pull-merge-write server mirror, fanned out to every reachable
 *  default-profile gateway. Local storage keeps the complete orchestration
 *  log; ui_meta is a bounded cross-client projection per gateway, each with
 *  its own CAS revision stream. */
export function scheduleGroupChatServerSync(
  all: Record<string, GroupChat> = $groupChats.get(),
  {
    allowEmpty = false,
    changedRooms = [],
    deletedRooms = []
  }: { allowEmpty?: boolean; changedRooms?: string[]; deletedRooms?: string[] } = {}
) {
  // Browser shells provide timers; source-level VM tests and older embedded
  // hosts may not. Room persistence must never break the surrounding gateway
  // lifecycle when the optional mirror cannot be scheduled.
  if (typeof setTimeout !== 'function') {
    return
  }

  const snapshot = groupChatSyncSnapshot(all)

  // A newly installed Desktop has no local room cache. Publishing that empty
  // state on hydrate/reconnect would erase a valid mirror produced elsewhere.
  // Only an explicit final-room disband is allowed to clear the projection.
  if (Object.keys(snapshot.rooms).length === 0 && !allowEmpty) {
    return
  }

  if (groupChatSyncTimer !== null) {
    clearTimeout(groupChatSyncTimer)
  }

  // Queue on the ACTIVE gateway synchronously (tests and older hosts have no
  // async route inventory), then widen to every reachable gateway before the
  // debounce fires.
  const activeId = String(groupChatSyncConnectionId() || '')

  const queueFor = (connectionId: string, coalesced?: GroupChatSyncJob) => {
    const id = String(connectionId || '')
    const retryTimer = groupChatSyncRetryTimers.get(id)

    if (retryTimer !== undefined) {
      clearTimeout(retryTimer)
      groupChatSyncRetryTimers.delete(id)
    }

    groupChatSyncPendingByConnection.set(
      id,
      mergeGroupChatSyncJobs(groupChatSyncPendingByConnection.get(id), {
        connectionId: id,
        allowEmpty: Boolean(allowEmpty || coalesced?.allowEmpty),
        changedRooms: [...new Set([...(coalesced?.changedRooms || []), ...changedRooms])],
        deletedRooms: [...new Set([...(coalesced?.deletedRooms || []), ...deletedRooms])]
      })
    )
  }

  queueFor(activeId)
  groupChatSyncTimer = setTimeout(() => {
    groupChatSyncTimer = null
    void groupChatSyncTargetConnections()
      .then(targets => {
        // An ordinary update inside the debounce window replaces the timer,
        // so secondaries must inherit the active job's coalesced intent —
        // a disband's deletedRooms included — or the tombstone lands on the
        // active gateway only (#105275).
        const coalesced = groupChatSyncPendingByConnection.get(activeId)

        for (const target of targets) {
          if (String(target || '') !== activeId) {
            queueFor(target, coalesced)
          }
        }
      })
      .catch(() => undefined)
      .then(() => flushGroupChatServerSync())
  }, 350)
}

export function handleSessionsGatewayTransition() {
  // A gateway swap invalidates any in-flight room drive: bump every room's
  // epoch so running loops bail at their next member boundary.
  const rooms = {
    ...$groupChats.get()
  }

  for (const name of Object.keys(rooms)) {
    rooms[name] = {
      ...rooms[name],
      epoch: (rooms[name].epoch || 0) + 1,
      running: false
    }
  }

  $groupChats.set(rooms)

  // Pull before re-publishing so a reconnect or source swap never lets this
  // client's stale cache hide a room written by another Desktop/mobile client.
  return pullGroupChatServerState()
    .catch(() => false)
    .then(() => scheduleGroupChatServerSync($groupChats.get()))
}

/** Re-arm the mirror after a dispose. `register()` owns this door: an
 *  imported binding cannot be assigned, so the flag's reset crosses the
 *  module edge as an accessor (same pattern as shared.ts's plugin context). */
export function setGroupChatSyncDisposed(disposed: boolean) {
  groupChatSyncDisposed = disposed
}

// ── one room's budget ────────────────────────────────────────────────────────
// Every ceiling a single user send can spend, in one block on purpose: making
// them configurable (per room, or model-aware from config.yaml) is live
// contributor work — #92213 (per-room limits) and #96842 (config + token
// budget) — and both need exactly one seam to hook. Carried over at the same
// values the old plugin.js shipped so neither rebase inherits a behavior
// change on top of a rewrite; deciding the shape of the override belongs to
// those PRs, not to a design-system pass.
export const GROUP_CHAT_MAX_ROUNDS = 3

// #94478 review: continuation rounds are bounded independently of the message cap so a pathological mention chain can't consume the room's whole budget on handoffs.
export const GROUP_CHAT_MAX_MESSAGES = 10
export const GROUP_CHAT_MAX_CONTINUATIONS = 2
// Per-turn room window (#114341 follow-up): a member sees every message since
// its last turn, up to BOTH ceilings — oldest dropped first, the cut named
// exactly. Room lines are short by construction (the rules ask for 1-3
// sentences; user lines average ~100-300 chars), so ~200 entries and ~32k
// characters (~8k tokens; budgets are String.length code units, not bytes)
// bite at about the same place for ordinary traffic; the char budget is
// what keeps a prompt bounded when the lines are long. One body is cut to
// LINE_CHARS (mark: '… [truncated]') rather than evicting whole messages, so
// a single giant paste costs a quarter of the window, not all of it, while a
// multi-paragraph member result still lands intact.
export const GROUP_CHAT_HISTORY_LIMIT = 200
export const GROUP_CHAT_HISTORY_CHARS = 32_000
export const GROUP_CHAT_HISTORY_LINE_CHARS = 8_000
// Room log retained locally: twice the turn window so a member that skipped
// a whole window still receives an exact omitted count, not a clamped
// watermark and a silently shortened room.
export const GROUP_CHAT_LOG_RETAIN = GROUP_CHAT_HISTORY_LIMIT * 2
// Storage footprint of the retained log. updateGroupChat persists the WHOLE
// room map to localStorage (~5M-char origin quota, a failed setItem is
// swallowed and every later room write is lost with it). Measured on the
// real persist path: 400 uncapped 8k bodies serialise to 3.25M chars, 400
// 64k pastes to 25.6M. Stored bodies are therefore cut to the same
// LINE_CHARS the turn prompt renders (nothing past it ever reaches a member)
// and the log is head-trimmed to a character budget: 8x the turn window, so
// ordinary traffic never hits it and a room of back-to-back pastes stays
// well under a tenth of the quota.
export const GROUP_CHAT_LOG_RETAIN_CHARS = GROUP_CHAT_HISTORY_CHARS * 8
export const GROUP_CHAT_MAX_MEMBERS = 6

/** Transcript form of a room speaker's identity. Friendly identity wins:
 *  a Bot Mode title or a core profile display_name (e.g. default renamed to
 *  "Lucy") labels the speaker everywhere this helper feeds — the "X is
 *  thinking…" working line, the activity feed, and transcript lines — so a
 *  renamed bot never shows up as its raw profile id or a stale "Hermes"
 *  (community report, Aug 21 2026: renamed default still read "Hermes is
 *  thinking…" in group rooms). The untitled primary profile is literally
 *  named "default" — render it as Hermes (matching displayName and the
 *  @hermes handle) so the main agent never loses its name in rooms.
 *
 *  Accepts either a member key (`connectionId::profile`, what the activity
 *  feed records) or a raw profile name (legacy rooms, the round prompt).
 *  Bot meta is persisted under the route-qualified key (botMetaKey), so a
 *  keyed caller resolves through the exact roster row + botRosterMeta — the
 *  same pipeline the Bots tab renders — and a raw name resolves the same
 *  way when exactly one roster row carries it. Same-named members that
 *  resolve to the same label get their connection label appended, so two
 *  failing `default`s are never one anonymous "Hermes" — judged against the
 *  ROOM's seats when the caller names the room (#94869: a room whose only
 *  `reviewer` is local reads plain "Reviewer" however many other connections
 *  expose one), against the whole roster otherwise. A key with no roster row
 *  ($lastRoster is empty until the Bots pane mounts; the owning connection
 *  may be gone) still resolves through the route-keyed meta and the profile
 *  segment — a keyed caller never renders the raw key. */
export function groupSpeakerLabel(name?: null | string, group?: null | string) {
  const trimmed = (name || '').trim()

  if (!trimmed) {
    return trimmed
  }

  const roster = $lastRoster.get()
  const rows: RosterRow[] = Array.isArray(roster) ? roster.filter(Boolean) : []
  const meta = $botMeta.get()
  const friendly = (bot: RosterRow) => displayName(bot, botRosterMeta(bot, meta))

  const exact = rows.find(bot => botRosterKey(bot) === trimmed)

  if (exact) {
    const label = friendly(exact)
    const seats = group ? new Set(($groupChats.get()[group]?.members || []).map(botRosterKey)) : null
    const peers = seats?.size ? rows.filter(bot => seats.has(botRosterKey(bot))) : rows
    const twin = peers.some(bot => bot !== exact && bot.name === exact.name && friendly(bot) === label)

    return twin ? `${label} · ${exact.connectionLabel || exact.connectionId}` : label
  }

  const boundary = trimmed.indexOf('::')

  if (boundary !== -1) {
    const connection = trimmed.slice(0, boundary)
    const profile = trimmed.slice(boundary + 2)
    const title = String(meta?.[trimmed]?.title || meta?.[profile]?.title || '').trim()
    const label = title || (profile.toLowerCase() === 'default' ? 'Hermes' : profile)

    // Another connection still exposes this name: keep them tellable apart.
    return rows.some(bot => bot.name === profile) ? `${label} · ${connection}` : label
  }

  // A raw `default` names the ACTIVE gateway's primary profile — it must
  // never borrow a remote default's identity, so only a local row counts.
  const isDefault = trimmed.toLowerCase() === 'default'
  const named = rows.filter(bot => bot.name === trimmed && !(isDefault && (bot.remoteSource || bot.sourceScoped)))

  if (named.length === 1) {
    return friendly(named[0])
  }

  // Legacy rungs for names the roster cannot place: a bare-keyed Bot Mode
  // title, then the local row's display_name, then default → Hermes.
  const title = String(meta?.[trimmed]?.title || '').trim()

  if (title) {
    return title
  }

  const row = rows.find(bot => bot.name === trimmed && !bot.remoteSource && !bot.sourceScoped)
  const renamed = typeof row?.display_name === 'string' ? row.display_name.trim() : ''

  if (renamed) {
    return renamed
  }

  return isDefault ? 'Hermes' : trimmed
}

/** Trim a room log + its watermarks to the retained window, keeping
 *  watermark indices consistent with the trimmed array. Runs on every
 *  updateGroupChat, i.e. right before the room map is persisted: entries are
 *  bounded by count AND by stored characters (each body cut to the prompt's
 *  LINE_CHARS, then the oldest dropped until the log fits the char budget). */
export function trimGroupChatLog(
  log: GroupMessage[],
  watermarks: Record<string, number>,
  limit = GROUP_CHAT_LOG_RETAIN,
  chars = GROUP_CHAT_LOG_RETAIN_CHARS
) {
  const capped = log.map(entry =>
    entry.text.length > GROUP_CHAT_HISTORY_LINE_CHARS
      ? { ...entry, text: compactGroupChatSyncText(entry.text, GROUP_CHAT_HISTORY_LINE_CHARS).text }
      : entry
  )

  let total = 0
  let keep = 0

  for (let i = capped.length - 1; i >= 0 && keep < limit; i--) {
    total += capped[i].text.length

    if (keep && total > chars) {
      break
    }

    keep++
  }

  if (keep >= log.length) {
    return {
      log: capped,
      watermarks
    }
  }

  const drop = log.length - keep
  const trimmed: Record<string, number> = {}

  for (const [name, index] of Object.entries(watermarks || {})) {
    trimmed[name] = Math.max(0, index - drop)
  }

  return {
    log: capped.slice(drop),
    watermarks: trimmed
  }
}

interface UpdateGroupChatOptions {
  sync?: boolean
  renameFrom?: string
}

/** Mutate one group's room state through the atom + persist the durable part. */
export function updateGroupChat(
  group: string,
  mutate: (room: GroupChat) => GroupChat,
  { sync = true, renameFrom }: UpdateGroupChatOptions = {}
) {
  const all = {
    ...$groupChats.get()
  }

  const current = all[group] || {
    log: [],
    watermarks: {},
    epoch: 0,
    running: false
  }

  const mutated = mutate({
    ...current,
    log: [...current.log],
    watermarks: {
      ...current.watermarks
    }
  })

  const next = ensureClassicDesktopAuthority(mutated, current)
  inheritGroupRoomSnapshot(current, next)

  const bounded = trimGroupChatLog(next.log, next.watermarks)
  next.log = bounded.log
  next.watermarks = bounded.watermarks
  all[group] = next
  $groupChats.set(all)

  try {
    const durable: Record<string, GroupChat> = {}

    for (const [name, room] of Object.entries(all)) {
      // Disband tombstones are runtime-only coordination state (they hold the
      // epoch bump for an in-flight drive). Persisting one would resurrect
      // the room as an empty record on the next load AND keep its name
      // "taken" for same-name recreates.
      if (room.tombstone) {
        continue
      }

      durable[name] = {
        ...storedClassicDesktopAuthority(room),
        log: room.log.map(storedHostedUserEvent),
        holdDetection: room.holdDetection !== false,
        heldMessages: room.heldMessages || {},
        watermarks: room.watermarks,
        sessions: room.sessions || {},
        sessionOwners: room.sessionOwners || {},
        // Timed-out turns awaiting a late reply — keyed by member, valued
        // with the pre-turn message baseline. Survives reloads so finished
        // work is still harvested after a window restart.
        stranded: room.stranded || {},
        // #93129: sticky per-member stop holds. Watermarks persist, so holds
        // must too — otherwise a window restart silently releases a bot the
        // user explicitly stopped.
        holds: room.holds || {},
        desktopCommandSettled: boundedDesktopCommandSettled(room.desktopCommandSettled),
        // #93813: per-member external-write reconcile cursors. Persisted so
        // external posts aren't re-mirrored after a window restart.
        externalCursors: room.externalCursors || {},
        // Source-qualified member descriptors keep the room whole when the
        // active connection changes and today's local members become remote.
        members: Array.isArray(room.members) ? room.members : [],
        shippedAdoption: storedShippedGroupAdoption(room),
      shippedPreflight: room.shippedPreflight?.state === 'waiting' && !room.shippedPreflight.route
        ? storedShippedGroupAdoption({ shippedAdoption: room.shippedPreflight }) : undefined,
        // Immutable room identity: the member-session title for new rooms.
        roomId: typeof room.roomId === 'string' && room.roomId ? room.roomId : null,
        hosted: groupChatHostedGateway(room) || null,
        hostedEpoch: groupChatHostedEpoch(room) || null,
        hostedConnectionId:
          typeof room.hostedConnectionId === 'string' && room.hostedConnectionId ? room.hostedConnectionId : null,
        hostedSeq: Math.max(0, Number(room.hostedSeq || 0)),
        hostedMembersVerified: room.hostedMembersVerified === true,
        peerProbeHint: storedHostedPeerProbeHint(room),
        continuityMode: groupChatContinuityMode(room),
        // Room picture (small data URL, same normalization as bot avatars).
        image: room.image || null,
        rosterOrder: room.rosterOrder,
        pinned: room.pinned,
        // Sidebar filing (user-sections) is room-local; keep it durable.
        sectionId: room.sectionId ?? null,
        syncRevision: Math.max(0, Number(room.syncRevision || 0))
      }
      inheritGroupRoomSnapshot(room, durable[name])
    }

    const storage = getPluginCtx()?.storage

    const receipt = withGroupRoomWrite(durable, { target: group, renameFrom },
      () => storage?.set?.('group-chats', durable))

    recordCommittedGroupRooms(all, durable, receipt.committed)
    Promise.resolve(receipt.value).catch(() => undefined)
  } catch {
    /* storage unavailable — room survives for this window only */
  }

  if (sync) {
    scheduleGroupChatServerSync(all, {
      changedRooms: [group]
    })
  }

  return next
}

export { groupChatSyncSequence } from './group-message-author'

export function backfillClassicGroupAuthorities(names = Object.keys($groupChats.get())) {
  let changed = false

  for (const name of names) {
    const room = $groupChats.get()[name]

    if (!room || room.tombstone) {
      continue
    }

    const next = ensureClassicDesktopAuthority(room)

    if (next === room) {
      continue
    }

    updateGroupChat(name, () => next, { sync: false })
    changed = true
  }

  return changed
}

let classicAuthorityActivationPending = false

export async function activateClassicGroupAuthorities(names = Object.keys($groupChats.get())) {
  const changed = backfillClassicGroupAuthorities(names)
  classicAuthorityActivationPending ||= changed

  if (!classicAuthorityActivationPending) {
    return false
  }

  await persistGroupChatRoomsRequired()
  classicAuthorityActivationPending = false
  scheduleGroupChatServerSync($groupChats.get())

  return true
}

/** A #93129 member hold as this file mints it. `GroupHold` models only the
 *  two fields that survive a reload; the live stamp also records WHICH user
 *  message, in which thread, put the member on hold. */
export interface GroupHoldStamp extends GroupHold {
  byMessageId?: null | string
  thread?: null | string
}

/** The room record as the coordination engine handles it: `GroupChat` plus
 *  runtime-only turn/cancellation state. Like `running`/`epoch` these fields
 *  never persist, so they have no place in the durable shape. Holds carry the
 *  fuller live stamp. */
export interface GroupChatRoom extends GroupChat {
  holds?: Record<string, GroupHoldStamp>
  /** Epoch minted by the latest explicit Stop action. A turn dispatched under
   *  an older epoch is cancelled even when sticky hold detection is disabled. */
  stoppedEpoch?: number
  turn?: GroupMember | null
}

/** Toggle automatic text-to-hold detection for one room. Turning it off also
 *  releases existing sticky holds; already-consumed messages remain queued
 *  and are delivered on each member's next visible turn. */
export function setGroupChatHoldDetection(group: string, enabled: boolean) {
  return updateGroupChat(group, (room: GroupChatRoom) => ({
    ...room,
    holdDetection: enabled,
    ...(enabled ? {} : { holds: {} })
  }))
}

/** Set or clear a group chat's room picture (small data URL, normalized by
 *  the same pipeline as bot avatars). Persists with the room record. */
export function setGroupChatImage(group: string, image: null | string | undefined) {
  updateGroupChat(group, (room: GroupChatRoom) => {
    room.image = image || null

    return room
  })
}

function groupChatEntryId(): string {
  if (globalThis.crypto && typeof globalThis.crypto.randomUUID === 'function') {
    return globalThis.crypto.randomUUID()
  }

  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`
}

/** The agent loop's "(empty)" terminal sentinel (empty_response_exhausted) is
 *  a FAILURE marker, never bot text. Mirror gateway/run.py's user-friendly
 *  substitution so the room log never shows the raw sentinel. */
const GROUP_EMPTY_SENTINEL = '(empty)'

const GROUP_EMPTY_FRIENDLY =
  '⚠️ The model returned no response after processing tool results. ' +
  'This can happen with some models — try again or rephrase your question.'

function normalizeGroupChatText(text: string): string {
  const trimmed = String(text || '').trim()

  return trimmed === GROUP_EMPTY_SENTINEL ? GROUP_EMPTY_FRIENDLY : trimmed
}

export function appendGroupChatEntry(
  group: string,
  from: GroupMessageAuthor,
  text: string,
  thread?: null | string,
  images?: Attachment[],
  options: string | { entryId?: string; external?: boolean } = {}
): GroupMessage {
  const entryId = typeof options === 'string' ? options : options.entryId || ''
  const external = typeof options === 'object' && options.external === true

  let entry: GroupMessage = {
    id: entryId || groupChatEntryId(),
    at: Date.now(),
    from,
    // Stored bodies share the prompt's per-line cap (see trimGroupChatLog);
    // cutting here too keeps the duplicate-echo guard comparing like with like.
    text: compactGroupChatSyncText(normalizeGroupChatText(text), GROUP_CHAT_HISTORY_LINE_CHARS).text,
    thread: thread || 'legacy',
    ...(external ? { external: true } : {})
  }

  if (Array.isArray(images) && images.length) {
    // [{ name, data }] — data URLs. Persisted with the room log so reloads
    // keep showing what the members were shown.
    entry.images = images
  }

  // #93127 insurance: a residual double-append path (stale loop + fresh
  // loop both committing the same member reply) lands back-to-back and
  // byte-identical. Drop the echo instead of flooding the room. User
  // entries and non-adjacent repeats are never touched.
  const priorLog = ($groupChats.get()[group] || {}).log || []
  const lastEntry = priorLog[priorLog.length - 1]

  if (isDuplicateGroupAppend(lastEntry, from, entry.text, entry.thread)) {
    return lastEntry
  }

  updateGroupChat(group, (room: GroupChatRoom) => {
    // Classic room watermarks advance through the local log by insertion
    // order. Gateway projection merges sort equal-time rows by stable id, so
    // make each locally appended classic row monotonic even when the clock
    // has millisecond collisions. Hosted rooms use the authoritative event
    // sequence instead.
    if (!room.roomId || !groupChatHostedGateway(room)) {
      const latestAt = room.log.reduce((latest, candidate) => Math.max(latest, Number(candidate.at || 0)), 0)
      entry.at = Math.max(entry.at, latestAt + 1)
    }

    if (from.kind === 'user' && room.roomId && groupChatHostedGateway(room)) {
      entry = outgoingHostedUserEvent(entry, room.roomId, entry.id || '')
      room.log = reconcileHostedUserEvents(room.roomId, room.log, [entry])

      return room
    }

    room.log.push(entry)

    return room
  })

  // Needs-you: a member addressing @user badges the group header.
  if (from.kind === 'member' && /@user\b/i.test(entry.text)) {
    $groupNeedsYou.set({
      ...$groupNeedsYou.get(),
      [group]: true
    })
  }

  return entry
}

/** Fresh room identity for a group. Independent of the editable display
 *  name: a disbanded-and-recreated group mints a new roomId even when the
 *  display name is identical, so member sessions never resume by title. */
export function mintGroupRoomId(): string {
  return `r${crypto.randomUUID()}`
}

/** Unique display name for a NEW group. Collisions get a " 2", " 3", …
 *  suffix; the BASE is truncated (never the joined string), so a 64-char
 *  base keeps its suffix instead of colliding with the original. */
export function uniqueGroupChatName(base: string, taken: Set<string>): string {
  if (!taken.has(base)) {
    return base
  }

  for (let n = 2; n < 100; n++) {
    const suffix = ` ${n}`
    const candidate = base.slice(0, 64 - suffix.length) + suffix

    if (!taken.has(candidate)) {
      return candidate
    }
  }

  throw new Error('No free name for the group.')
}

// --- room-turn decision helpers (#93127) — pure, unit-tested ---

/** #93127: whether a finished member turn may still commit (append its reply
 *  and advance its watermark). A turn dispatched under an older epoch was
 *  superseded mid-flight by a newer user send — its late result must be
 *  dropped, because the new send's own loop re-drives this member with the
 *  full delta and committing both is exactly the double-delivery bug.
 *
 *  The re-drive premise is only true for a send in the SAME thread (delta
 *  filters are thread-scoped): a cross-thread epoch bump must NOT discard
 *  finished work no fresh loop will regenerate. Callers pass whether a newer
 *  USER entry landed in this thread since dispatch; the default (true)
 *  preserves the conservative drop when the caller can't tell. */
export function shouldCommitMemberTurn(epochAtDispatch: number, currentEpoch: number, newerUserEntryInThread = true) {
  if (epochAtDispatch === currentEpoch) {
    return true
  }

  return !newerUserEntryInThread
}

/** #93127 insurance: byte-identical member echo detection. TRUE only when
 *  the immediately-preceding log entry has the same author (kind + name +
 *  source), same thread, and identical text, within a short recency window —
 *  a residual double-append fires back-to-back; two legitimately identical
 *  replies hours apart (or with anything in between) are never dropped. */
const GROUP_DUPLICATE_APPEND_WINDOW_MS = 10 * 60 * 1000

function isDuplicateGroupAppend(
  lastEntry: GroupMessage | undefined,
  from: GroupMessageAuthor,
  text: string,
  thread: null | string | undefined,
  now = Date.now()
): boolean {
  if (!lastEntry || !from || from.kind !== 'member' || lastEntry.from?.kind !== 'member') {
    return false
  }

  if (String(lastEntry.from?.name || '') !== String(from.name || '')) {
    return false
  }

  if (String(lastEntry.from?.source || '') !== String(from.source || '')) {
    return false
  }

  if (String(lastEntry.thread || 'legacy') !== String(thread || 'legacy')) {
    return false
  }

  if (now - (lastEntry.at || 0) > GROUP_DUPLICATE_APPEND_WINDOW_MS) {
    return false
  }

  return String(lastEntry.text || '') === String(text || '').trim()
}

// --- end room-turn decision helpers ---

export function groupThreadOf(entry: GroupMessage): string {
  return entry?.thread || 'legacy'
}

/** Count only transcript rows a person can actually see, excluding the
 * thread head itself. Status-only replay events must not inflate replies. */
export function groupThreadReplyCount(log: GroupMessage[], thread: string): number {
  const visible = (log || []).filter(
    entry =>
      groupThreadOf(entry) === thread && (Boolean(String(entry?.text || '').trim()) || Boolean(entry?.images?.length))
  )

  return Math.max(0, visible.length - 1)
}

export function mintGroupThreadId(): string {
  return `t${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 7)}`
}

// Pre-thread logs (hydrated from storage) get synthetic thread ids: a user
// entry after a real lull starts one, so multi-turn tasks stay whole instead
// of splitting on every follow-up.
const GROUP_THREAD_GAP_MS = 15 * 60000

export function assignLegacyThreads(log: GroupMessage[]): GroupMessage[] {
  let current: null | string = null
  let n = 0

  const normalized = (log || []).map(entry => {
    const at = Number(entry?.at || 0)

    return at >= 1_000_000_000 && at < 1_000_000_000_000
      ? {
          ...entry,
          at: at * 1000
        }
      : entry
  })

  return normalized.map((entry, i) => {
    if (entry?.thread) {
      current = null

      return entry
    }

    const prev = normalized[i - 1]
    const lull = !prev || (entry.at || 0) - (prev.at || 0) > GROUP_THREAD_GAP_MS

    if (!current || (entry.from?.kind === 'user' && lull)) {
      current = `legacy-${n++}`
    }

    return {
      ...entry,
      thread: current
    }
  })
}

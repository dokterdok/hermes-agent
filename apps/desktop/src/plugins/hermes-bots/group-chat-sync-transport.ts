import { host } from '@hermes/plugin-sdk'

import {
  $groupChats,
  GROUP_CHAT_SYNC_MAX_BYTES,
  GROUP_CHAT_SYNC_META_KEY,
  groupChatGatewayJsonSize,
  groupChatSyncDisposed,
  groupChatSyncInFlightConnections,
  type GroupChatSyncJob,
  groupChatSyncPendingByConnection,
  groupChatSyncRetryCounts,
  groupChatSyncRetryTimers,
  type GroupChatSyncRoom,
  type GroupChatSyncSnapshot,
  groupChatSyncSnapshot,
  groupChatTombstones,
  mergeGroupChatSyncSnapshots,
  mergeRemoteGroupChatSnapshotIntoRooms,
  persistGroupChatRooms,
  persistGroupChatRoomsRequired,
  scheduleGroupChatServerSync
} from './group-chat'
import { classicAuthorityState } from './group-desktop-authority'
import { groupChatSyncSequence } from './group-message-author'
import type { GroupChat, GroupMessage, RosterRow } from './types'

/** Lift any historical projection shape (v1 wall-clock, v2 name-keyed) to
 *  the v3 room-key shape so one merge path serves mixed-version fleets. */
export function normalizeGroupChatSyncSnapshot(
  snapshot: GroupChatSyncSnapshot | null | undefined
): GroupChatSyncSnapshot {
  if (!snapshot || typeof snapshot !== 'object') {
    return {
      version: 3,
      rooms: {},
      deleted: {}
    }
  }

  if (Number(snapshot.version || 0) >= 3) {
    return {
      version: 3,
      updatedAt: Number(snapshot.updatedAt || 0),
      rooms: snapshot.rooms && typeof snapshot.rooms === 'object' ? snapshot.rooms : {},
      deleted: snapshot.deleted && typeof snapshot.deleted === 'object' ? snapshot.deleted : {}
    }
  }

  const rooms: Record<string, GroupChatSyncRoom> = {}

  for (const [name, room] of Object.entries(snapshot.rooms || {})) {
    if (!room || !Array.isArray(room.log)) {
      continue
    }

    rooms[`name:${name}`] = {
      ...room,
      name
    }
  }

  const deleted: Record<string, number> = {}

  for (const [name, at] of Object.entries(snapshot.deleted || {})) {
    // v1 tombstones carried wall-clock ms, not gateway revisions — they must
    // not outrank real revisions.
    deleted[`name:${name}`] = Number(snapshot.version || 0) >= 2 ? Math.max(0, Number(at || 0)) : 0
  }

  return {
    version: 3,
    updatedAt: Number(snapshot.updatedAt || 0),
    rooms,
    deleted
  }
}

function groupChatSyncEntryKey(entry: GroupMessage) {
  const seq = groupChatSyncSequence(entry)

  if (seq !== null) {
    return `seq:${seq}`
  }

  if (entry?.eventId) {
    return `event:${String(entry.eventId)}`
  }

  if (entry?.id) {
    return `id:${String(entry.id)}`
  }

  return `fallback:${groupChatSyncFallbackKey(entry)}`
}

export function groupChatSyncFallbackKey(entry: GroupMessage) {
  return JSON.stringify([
    Number(entry?.at || 0),
    String(entry?.from?.kind || ''),
    String(entry?.from?.name || ''),
    String(entry?.from?.source || ''),
    // Threadless entries (pre-thread rooms, older Desktop builds) get
    // SYNTHETIC `legacy-N` ids from assignLegacyThreads. Those ids are
    // position-derived — not stable across a gateway round-trip (the
    // projection copy may be threadless or numbered differently). Collapse
    // the whole synthetic family to one bucket, or the merge duplicates
    // every id-less entry — shifting watermarks and manufacturing phantom
    // member turns that re-submit into busy sessions.
    String(entry?.thread || 'legacy').replace(/^legacy-\d+$/, 'legacy'),
    String(entry?.text || '')
  ])
}

export function compareGroupChatSyncEntries(left: GroupMessage, right: GroupMessage) {
  const leftSeq = groupChatSyncSequence(left)
  const rightSeq = groupChatSyncSequence(right)

  if (leftSeq !== null && rightSeq !== null) {
    return leftSeq - rightSeq || groupChatSyncEntryKey(left).localeCompare(groupChatSyncEntryKey(right))
  }

  const byTime = Number(left?.at || 0) - Number(right?.at || 0)

  return byTime || groupChatSyncEntryKey(left).localeCompare(groupChatSyncEntryKey(right))
}

/** Assemble + size-bound a v3 envelope from already-compacted rooms. */
export function groupChatSyncEnvelope(
  rooms: Record<string, GroupChatSyncRoom>,
  deleted: Record<string, number> = {}
): GroupChatSyncSnapshot {
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

  const ranked = Object.entries(rooms).sort(([, left], [, right]) => {
    const leftAt = Number(left?.log?.[left.log.length - 1]?.at || 0)
    const rightAt = Number(right?.log?.[right.log.length - 1]?.at || 0)

    return leftAt - rightAt
  })

  for (const [key, room] of ranked) {
    while ((room.log?.length || 0) > 1 && groupChatGatewayJsonSize(envelope) > GROUP_CHAT_SYNC_MAX_BYTES) {
      room.log.shift()
      room.omitted = (room.omitted || 0) + 1
    }

    if (room.image && groupChatGatewayJsonSize(envelope) > GROUP_CHAT_SYNC_MAX_BYTES) {
      delete room.image
    }

    if (groupChatGatewayJsonSize(envelope) > GROUP_CHAT_SYNC_MAX_BYTES) {
      delete rooms[key]
    }
  }

  return envelope
}

export function groupChatSyncConnectionId() {
  return String(host.state.connectionId?.get?.() || host.activeConnectionId?.() || '')
}

/** Route a sync job back to the gateway that was active when it was queued.
 *  A foreground switch during debounce must not write the old snapshot into
 *  the newly active gateway. */
async function groupChatSyncRequest<T>(
  job: GroupChatSyncJob,
  method: string,
  params: Record<string, unknown>
): Promise<T> {
  if (job.connectionId && typeof host.profileRoutes === 'function' && typeof host.requestProfile === 'function') {
    const routes = await host.profileRoutes()

    const route = (Array.isArray(routes) ? routes : []).find(candidate => {
      const profile = String(candidate?.targetProfile || candidate?.profile || '')

      return String(candidate?.connectionId || '') === job.connectionId && profile === 'default'
    })

    if (route) {
      return host.requestProfile(route, method, params)
    }
  }

  const currentConnectionId = groupChatSyncConnectionId()

  if (job.connectionId && currentConnectionId && job.connectionId !== currentConnectionId) {
    throw new Error('Group chat gateway changed before sync')
  }

  return host.request(method, params)
}

export async function groupChatRemoteSnapshot(job: GroupChatSyncJob) {
  const result = await groupChatSyncRequest<{ profiles?: RosterRow[] }>(job, 'profiles.list', {
    include_sessions: false
  })

  const profile = (Array.isArray(result?.profiles) ? result.profiles : []).find(row => row?.name === 'default')
  const snapshot = profile?.ui_meta?.[GROUP_CHAT_SYNC_META_KEY] as GroupChatSyncSnapshot | undefined
  const supportsCas = Boolean(profile && Object.prototype.hasOwnProperty.call(profile, 'ui_meta_revisions'))

  return {
    snapshot: snapshot && typeof snapshot === 'object' && !Array.isArray(snapshot) ? snapshot : null,
    revision: Math.max(0, Number(profile?.ui_meta_revisions?.[GROUP_CHAT_SYNC_META_KEY] || 0)),
    supportsCas
  }
}

export async function adoptGroupChatSyncRooms(rooms: Record<string, GroupChat>) {
  const before = $groupChats.get()
  const authorityChanged = classicAuthorityState(rooms) !== classicAuthorityState(before)
  $groupChats.set(rooms)
  await persistGroupChatRooms(rooms, before)

  // A newly learned commitment or conflict must reach the other gateways too.
  // No changedRooms: equal projections settle without revision ping-pong.
  if (authorityChanged) {
    scheduleGroupChatServerSync($groupChats.get())
  }
}

function groupChatSyncBackoff(connectionId: string) {
  const count = Number(groupChatSyncRetryCounts.get(connectionId) || 0)

  return Math.min(30000, 1000 * 2 ** Math.min(count, 5))
}

export function mergeGroupChatSyncJobs(
  existing: GroupChatSyncJob | undefined,
  incoming: GroupChatSyncJob
): GroupChatSyncJob {
  if (!existing || existing.connectionId !== incoming.connectionId) {
    return incoming
  }

  return {
    connectionId: incoming.connectionId,
    allowEmpty: Boolean(existing.allowEmpty || incoming.allowEmpty),
    changedRooms: [...new Set([...(existing.changedRooms || []), ...(incoming.changedRooms || [])])],
    deletedRooms: [...new Set([...(existing.deletedRooms || []), ...(incoming.deletedRooms || [])])]
  }
}

function groupChatSyncPayloadEqual(
  left: GroupChatSyncSnapshot | null | undefined,
  right: GroupChatSyncSnapshot | null | undefined
) {
  return (
    JSON.stringify(left?.rooms || {}) === JSON.stringify(right?.rooms || {}) &&
    JSON.stringify(left?.deleted || {}) === JSON.stringify(right?.deleted || {})
  )
}

/** Every default-profile gateway route this Desktop can currently reach.
 *  The projection fans out to ALL of them, so any single gateway can die or
 *  be removed without losing the shared room state, and gateway-only
 *  clients (Hermes Go, headless backends) see rooms regardless of which
 *  gateway a Desktop was foregrounding when the room was used. */
export async function groupChatSyncTargetConnections() {
  const targets = new Set<string>()
  const active = groupChatSyncConnectionId()
  targets.add(String(active || ''))

  if (typeof host.profileRoutes === 'function' && typeof host.requestProfile === 'function') {
    try {
      const routes = await host.profileRoutes()

      for (const route of Array.isArray(routes) ? routes : []) {
        const profile = String(route?.targetProfile || route?.profile || '')
        const connectionId = String(route?.connectionId || '')

        if (profile === 'default' && connectionId) {
          targets.add(connectionId)
        }
      }
    } catch {
      // Route inventory unavailable — the active gateway alone still syncs.
    }
  }

  return [...targets]
}

export async function flushGroupChatServerSync(connectionId?: string) {
  if (connectionId === undefined) {
    // Drain every connection with pending work.
    for (const pendingId of [...groupChatSyncPendingByConnection.keys()]) {
      void flushGroupChatServerSync(pendingId)
    }

    return
  }

  const id = String(connectionId || '')

  if (groupChatSyncDisposed || groupChatSyncInFlightConnections.has(id) || !groupChatSyncPendingByConnection.has(id)) {
    return
  }

  // Non-null: the `has(id)` guard directly above is the entry condition.
  const job = groupChatSyncPendingByConnection.get(id)!
  groupChatSyncPendingByConnection.delete(id)
  groupChatSyncInFlightConnections.add(id)

  try {
    const remoteState = await groupChatRemoteSnapshot(job)
    // The local snapshot carries the durable disband memory, so every publish
    // re-tombstones a mirror whose original tombstone push was lost.
    const local = groupChatSyncSnapshot($groupChats.get(), groupChatTombstones)
    const writeRevision = remoteState.revision + 1

    const snapshot = mergeGroupChatSyncSnapshots(remoteState.snapshot, local, {
      changedRooms: job.changedRooms,
      deletedRooms: job.deletedRooms,
      writeRevision
    })

    // Never advertise a freshly minted commitment that cannot survive reload.
    // Unlike the optional display cache, this write must not swallow failure.
    if (Object.values(snapshot.rooms).some(room => room.desktopAuthorityHash || room.desktopAuthorityConflict)) {
      await persistGroupChatRoomsRequired()
    }

    // Reconnect/startup reconciliation often discovers that the gateway
    // already holds the exact merged projection. Avoid advancing a revision
    // merely because a view reopened.
    if (
      !(job.changedRooms || []).length &&
      !(job.deletedRooms || []).length &&
      groupChatSyncPayloadEqual(snapshot, remoteState.snapshot)
    ) {
      if (remoteState.snapshot) {
        const pending = groupChatSyncPendingByConnection.get(id)

        const mergedRooms = mergeRemoteGroupChatSnapshotIntoRooms(remoteState.snapshot, $groupChats.get(), {
          preserveRooms: pending?.changedRooms || [],
          deletedRooms: pending?.deletedRooms || [],
          tombstones: groupChatTombstones
        })

        await adoptGroupChatSyncRooms(mergedRooms)
      }

      groupChatSyncRetryCounts.delete(id)

      return
    }

    const configureParams: {
      name: string
      ui_meta: Record<string, GroupChatSyncSnapshot>
      ui_meta_expected_revisions?: Record<string, number>
    } = {
      name: 'default',
      ui_meta: {
        [GROUP_CHAT_SYNC_META_KEY]: snapshot
      }
    }

    if (remoteState.supportsCas) {
      configureParams.ui_meta_expected_revisions = {
        [GROUP_CHAT_SYNC_META_KEY]: remoteState.revision
      }
    }

    const result = await groupChatSyncRequest<{
      applied?: { ui_meta?: boolean; ui_meta_revisions?: Record<string, number> }
    }>(job, 'profiles.configure', configureParams)

    if (result?.applied?.ui_meta !== true) {
      throw new Error('Gateway rejected group chat ui_meta')
    }

    if (
      remoteState.supportsCas &&
      Number(result?.applied?.ui_meta_revisions?.[GROUP_CHAT_SYNC_META_KEY] || 0) !== writeRevision
    ) {
      throw new Error('Gateway did not advance group chat ui_meta revision')
    }

    const confirmedState = await groupChatRemoteSnapshot(job)

    if (remoteState.supportsCas && confirmedState.revision < writeRevision) {
      throw new Error('Group chat ui_meta revision missing after read-back')
    }

    if (confirmedState.snapshot) {
      const pending = groupChatSyncPendingByConnection.get(id)

      const mergedRooms = mergeRemoteGroupChatSnapshotIntoRooms(confirmedState.snapshot, $groupChats.get(), {
        preserveRooms: pending?.changedRooms || [],
        deletedRooms: pending?.deletedRooms || [],
        tombstones: groupChatTombstones
      })

      await adoptGroupChatSyncRooms(mergedRooms)
    }

    groupChatSyncRetryCounts.delete(id)
  } catch {
    if (!groupChatSyncDisposed) {
      const retries = Number(groupChatSyncRetryCounts.get(id) || 0) + 1

      // A gateway that was REMOVED (not just flaky) has no route anymore and
      // would otherwise retry forever. Give up after the backoff ladder tops
      // out; local storage remains authoritative and a future reconnect of
      // that gateway re-seeds it via the gateway-transition pull/publish.
      if (retries > 8) {
        groupChatSyncRetryCounts.delete(id)

        return
      }

      groupChatSyncPendingByConnection.set(id, mergeGroupChatSyncJobs(groupChatSyncPendingByConnection.get(id), job))
      groupChatSyncRetryCounts.set(id, retries)

      if (typeof setTimeout === 'function' && !groupChatSyncRetryTimers.has(id)) {
        groupChatSyncRetryTimers.set(
          id,
          setTimeout(() => {
            groupChatSyncRetryTimers.delete(id)
            void flushGroupChatServerSync(id)
          }, groupChatSyncBackoff(id))
        )
      }
    }
  } finally {
    groupChatSyncInFlightConnections.delete(id)

    if (groupChatSyncPendingByConnection.has(id) && !groupChatSyncRetryTimers.has(id) && !groupChatSyncDisposed) {
      void flushGroupChatServerSync(id)
    }
  }
}

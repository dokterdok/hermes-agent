import type { PluginContext } from '@hermes/plugin-sdk'
import { atom, gatewayActivationEpoch, host } from '@hermes/plugin-sdk'

import { $lastRoster } from './data'
import { $groupChats, groupChatHostedGateway, mergeGroupChatRoomEntries, updateGroupChat } from './group-chat'
import {
  clearHostedRoomApprovalState,
  resetHostedRoomApprovalState,
  resolveHostedRoomApprovalAttention
} from './hosted-room-approval-state'
import {
  acquireHostedAttachmentRoute,
  readHostedMessageAttachment,
  stageHostedMessageAttachments
} from './hosted-room-attachments-client'
import { $hostedRoomCapabilities } from './hosted-room-capability-state'
import {
  addHostedRoomCleanup,
  armHostedRoomCleanup,
  dispatchHostedRoomCleanup,
  hostedRoomCleanupPending,
  releaseHostedRoomCleanup,
  resetHostedRoomCleanupForTests,
  startHostedRoomCleanup,
  stopHostedRoomCleanup
} from './hosted-room-cleanup'
import type {
  AutonomousRoomPlan,
  HostedRoomCapability,
  HostedRoomCommand,
  HostedRoomOutbox,
  HostedRoomRouteResolution,
  reduceHostedRoomOutbox
} from './hosted-room-client'
import {
  classifyHostedRoomCapability,
  createHostedRoomOutbox,
  hasRequestedRoomGrantLifetime,
  isHostedRoomContinuityEligible,
  isHostedRoomReadEligible,
  profileScopedRoomLinkEndpoint,
  resolveAutonomousRoomPlan,
  ROOM_GRANT_STATUS_TTL_SECONDS,
  ROOM_GRANT_TTL_SECONDS
} from './hosted-room-client'
import { requestHostedCommand } from './hosted-room-command-dispatch'
import {
  failedHostedRoomCommand,
  hostedCommandCanRetry,
  hostedRoomCommandFailure,
  surfaceHostedRoomCommandFailure
} from './hosted-room-command-failures'
import {
  hostedReadOnlyState,
  hostedRoomCapabilityFingerprint,
  hostedRoomPollFingerprint,
  hostedUnavailableState
} from './hosted-room-inventory'
import { HostedRoomObservations } from './hosted-room-observations'
import {
  mutateHostedRoomOutbox,
  readHostedRoomOutbox,
  recoverHostedRoomOutbox,
  resetHostedRoomOutboxLocksForTests,
  withHostedRoomCommandOrder,
  withHostedRoomOutboxDispatch
} from './hosted-room-outbox'
import type { AutonomousHostedRoomCreateInput, PreparedHostedPeer } from './hosted-room-peer-setup'
import { registerHostedPeers } from './hosted-room-peer-setup'
import { performHostedRoomRefresh } from './hosted-room-runtime-refresh'
import { requestHostedConnection, withHostedRoomProbeTimeout } from './hosted-room-transport'
import { hostedUserEventReceipt, outgoingHostedUserEvent } from './hosted-user-events'
import { botsText } from './i18n'
import { requestForBot } from './routing'
import type { Attachment, GroupChat, GroupMember, GroupMessage, GroupPrompt, ProfileRoute } from './types'

export { $hostedRoomCapabilities } from './hosted-room-capability-state'
export { $hostedRoomCleanup } from './hosted-room-cleanup'
export { describeAutonomousRoomPlan, describeHostedRoomCreationError } from './hosted-room-client'
export { hostedRoomDriverDisplayStatus, hostedRoomPollFingerprint } from './hosted-room-inventory'
export { requestHostedConnection } from './hosted-room-transport'

const HOSTED_ROOM_SYNC_INTERVAL_MS = 5000
export const HOSTED_ROOM_UNSUPPORTED_REPROBE_MS = 30_000

export const $hostedRoomOutbox = atom<HostedRoomOutbox>(createHostedRoomOutbox())

export const hostedRoomPollCache = new Map<string, string>()
export const hostedRoomPollGenerations = new Map<string, number>()
const hostedRoomMutationGenerations = new Map<string, number>()
export const hostedRoomLocallyDeleted = new Set<string>()
export const hostedRoomObservations = new HostedRoomObservations()
let hostedRoomSyncTimer: ReturnType<typeof setTimeout> | null = null
let hostedRoomSyncRunning = false

export function finishHostedRoomRefresh() {
  hostedRoomSyncRunning = false
}

let hostedRoomRefreshPromise: Promise<void> | null = null
let hostedRoomManualCheckTail: Promise<unknown> = Promise.resolve()
export let hostedRoomSyncDisposed = true
export let hostedRoomLifecycleGeneration = 0
let hostedOutboxDispatchPromise: Promise<void> | null = null
let hostedRoomStorage: null | PluginContext['storage'] = null
export let hostedRoomHooks: HostedRoomRuntimeHooks = {}
export const hostedUnsupportedUntil = new Map<string, number>()
const hostedRoomManualChecks = new Map<string, Promise<boolean>>()

export function hostedRoomLifecycleToken() {
  return hostedRoomLifecycleGeneration
}

export function hostedRoomLifecycleIsCurrent(token: number) {
  return !hostedRoomSyncDisposed && token === hostedRoomLifecycleGeneration
}

export function hostedRoomMutationGeneration(roomId: string) {
  return Math.max(0, Number(hostedRoomMutationGenerations.get(String(roomId || '')) || 0))
}

/** Fence an asynchronous local send/Stop/delete against an older replay. */
export function beginHostedRoomMutation(roomId: string) {
  const id = String(roomId || '')
  const generation = hostedRoomMutationGeneration(id) + 1

  if (id) {
    hostedRoomMutationGenerations.set(id, generation)
  }

  return generation
}

export function hostedRoomMutationIsCurrent(roomId: string, generation: number) {
  const id = String(roomId || '')

  return Boolean(id) && !hostedRoomLocallyDeleted.has(id) && hostedRoomMutationGeneration(id) === generation
}

/** Keep an acknowledged local deletion invisible to stale in-flight polls. */
export function markHostedRoomLocallyDeleted(roomId: string) {
  const id = String(roomId || '')

  if (!id) {
    return
  }

  beginHostedRoomMutation(id)
  hostedRoomLocallyDeleted.add(id)
  hostedRoomPollCache.delete(id)
}

/** A projection-only room must not start a classic Desktop driver until each
 * member gateway has been inventoried. Existing local classic rooms carry
 * either a Desktop authority or non-projected member descriptors and remain
 * immediately usable unless this ID is already known to be hosted. */
export function groupChatContinuityReady(room: GroupChat | null | undefined) {
  if (!room) {
    return true
  }

  if (groupChatHostedGateway(room)) {
    // A presentation refresh must not hide a saved, unresolved command or admit
    // later input that would only queue behind it and overwrite its guidance.
    return (
      !failedHostedRoomCommand($hostedRoomOutbox.get(), String(room.roomId || '')) &&
      !['deleted', 'failed', 'read-only', 'unsupported'].includes(String(room.hostedStatus?.state || ''))
    )
  }

  return hostedRoomObservations.classicReady(room)
}

export interface HostedRoomRuntimeHooks {
  renameGroupChat?: (oldName: string, newName: string, members: GroupMember[]) => Promise<null | string>
}

export interface HostedRoomProbe {
  attachmentParity: boolean
  attachmentUnavailableMembers: string[]
  capability: HostedRoomCapability | null
  capabilities: Record<string, HostedRoomCapability>
  eligible: boolean
  route: AutonomousRoomPlan
  routes: Record<string, ProfileRoute>
}

interface HostedRoomCreateInput {
  members: Array<{
    display_name?: string
    handle: string
    member_id: string
    profile: string
  }>
  name: string
  roomId: string
  route: HostedRoomRouteResolution
}

export interface HostedRoomServerState {
  authority_epoch?: unknown
  authority_gateway_id?: unknown
  disbanded_at?: unknown
  latest_seq?: unknown
  members?: unknown
  name?: unknown
  room_id?: unknown
}

export function record(value: unknown): Record<string, unknown> | null {
  return value && typeof value === 'object' && !Array.isArray(value) ? (value as Record<string, unknown>) : null
}

export function activeConnectionId() {
  return String(host.state.connectionId?.get?.() || host.activeConnectionId?.() || '')
}

export async function hostedDefaultRoutes(): Promise<ProfileRoute[]> {
  if (typeof host.profileRoutes !== 'function') {
    return []
  }

  const routes = await host.profileRoutes()
  const byConnection = new Map<string, ProfileRoute>()

  for (const route of Array.isArray(routes) ? routes : []) {
    const profile = String(route?.targetProfile || route?.profile || '')
    const connectionId = String(route?.connectionId || '')

    if (!connectionId || profile !== 'default' || byConnection.has(connectionId)) {
      continue
    }

    byConnection.set(connectionId, route as ProfileRoute)
  }

  return [...byConnection.values()]
}

async function verifiedHostedAuthorityRoute(routes: ProfileRoute[], authorityId: string, preferredConnectionId = '') {
  const ordered = [...routes].sort(
    (left, right) =>
      Number(right.connectionId === preferredConnectionId) - Number(left.connectionId === preferredConnectionId)
  )

  for (const route of ordered) {
    const connectionId = String(route.connectionId || '')
    const observation = hostedRoomObservations.captureCapability(connectionId)

    try {
      const capability = classifyHostedRoomCapability(
        await hostedRoomObservations.read(observation, () =>
          withHostedRoomProbeTimeout(requestHostedConnection(route, 'groups.capabilities'))
        ),
        { connectionId }
      )

      if (!hostedRoomObservations.current(observation)) {
        continue
      }

      storeHostedCapabilities({ [connectionId]: capability })

      if (capability.authorityId === authorityId && isHostedRoomContinuityEligible(capability)) {
        return route
      }
    } catch (error) {
      if (hostedRoomObservations.current(observation)) {
        storeHostedCapabilities({
          [connectionId]: classifyHostedRoomCapability({ ok: false, error }, { connectionId })
        })
      }

      // Capability probes reveal no room data; try the next current route.
    }
  }

  return null
}

export function sourceLabel(connectionId: string) {
  const source = ($lastRoster.get() || []).find(row => String(row?.connectionId || '') === connectionId)

  return String(source?.connectionLabel || botsText().group.thisHost)
}

export function markHostedConnectionUnavailable(connectionId: string) {
  const connectionName = sourceLabel(connectionId)

  for (const [name, room] of Object.entries($groupChats.get())) {
    if (String(room?.hostedConnectionId || '') !== connectionId || room.hostedStatus?.state === 'deleted') {
      continue
    }

    updateGroupChat(
      name,
      current =>
        hostedUnavailableState(current, $hostedRoomCapabilities.get()[connectionId], connectionName, connectionId),
      {
        sync: false
      }
    )
    clearHostedRoomApprovalState(name)
  }
}

export function isDisbanded(room: HostedRoomServerState) {
  return room.disbanded_at !== null && room.disbanded_at !== undefined
}

export function storeHostedCapabilities(next: Record<string, HostedRoomCapability>, replace = false) {
  const current = $hostedRoomCapabilities.get()

  for (const [connectionId, capability] of Object.entries(next)) {
    if (hostedRoomCapabilityFingerprint(current[connectionId]) !== hostedRoomCapabilityFingerprint(capability)) {
      invalidateHostedRoomsForConnection(connectionId, capability.authorityId || '')
    }
  }

  $hostedRoomCapabilities.set(replace ? next : { ...current, ...next })

  for (const [connectionId, capability] of Object.entries(next)) {
    if (!isHostedRoomReadEligible(capability)) {
      markHostedConnectionUnavailable(connectionId)
    }
  }

  for (const [name, room] of Object.entries($groupChats.get())) {
    const capability = next[String(room.hostedConnectionId || '')]

    if (
      capability &&
      isHostedRoomReadEligible(capability) &&
      room.hostedStatus?.state !== 'deleted' &&
      (!isHostedRoomContinuityEligible(capability) || capability.authorityId !== room.hosted)
    ) {
      updateGroupChat(name, current => ({ ...current, ...hostedReadOnlyState() }), { sync: false })
      clearHostedRoomApprovalState(name)
    }
  }
}

export function invalidateHostedRoomsForConnection(connectionId: string, installationId = '') {
  hostedRoomObservations.invalidate(connectionId)

  for (const room of Object.values($groupChats.get())) {
    if (
      room.hostedConnectionId === connectionId ||
      (room.members || []).some(
        member =>
          String(member.route?.connectionId || member.connectionId || '') === connectionId ||
          (installationId && member.hostedIdentity?.installationId === installationId)
      )
    ) {
      hostedRoomPollCache.delete(String(room.roomId || ''))
    }
  }
}

export function invalidateHostedRoomPoll(roomId: string) {
  const id = String(roomId || '')

  hostedRoomPollCache.delete(id)
  hostedRoomPollGenerations.set(id, Number(hostedRoomPollGenerations.get(id) || 0) + 1)
}

/** Recheck only the connection named by an update notice. This refreshes
 * capability and room projections; it never retries or dispatches Bot work. */
export function checkHostedRoomGateway(group: string): Promise<boolean> {
  const room = $groupChats.get()[group]
  const connectionId = String(room?.hostedStatus?.checkConnectionId || '')

  if (!room || !groupChatHostedGateway(room) || !connectionId) {
    return Promise.resolve(false)
  }

  const owner = {
    activation: gatewayActivationEpoch(),
    authorityId: groupChatHostedGateway(room),
    checkConnectionId: connectionId,
    group,
    lifecycle: hostedRoomLifecycleGeneration,
    ownerConnectionId: String(room.hostedConnectionId || ''),
    roomId: String(room.roomId || ''),
    sourceConnectionId: activeConnectionId(),
    sourceGateway: String(host.state.gateway?.get?.() || ''),
    sourceProfile: String(host.state.profile?.get?.() || ''),
    hint: room.peerProbeHint ? { ...room.peerProbeHint } : undefined
  }

  const key = JSON.stringify(owner)
  const pending = hostedRoomManualChecks.get(key)

  if (pending) {
    return pending
  }

  const current = (requireCheckHint = true) => {
    const live = $groupChats.get()[group]
    const hint = live?.peerProbeHint

    return Boolean(
      live &&
      !hostedRoomSyncDisposed &&
      hostedRoomLifecycleGeneration === owner.lifecycle &&
      gatewayActivationEpoch() === owner.activation &&
      activeConnectionId() === owner.sourceConnectionId &&
      String(host.state.profile?.get?.() || '') === owner.sourceProfile &&
      String(host.state.gateway?.get?.() || '') === owner.sourceGateway &&
      String(live.roomId || '') === owner.roomId &&
      groupChatHostedGateway(live) === owner.authorityId &&
      String(live.hostedConnectionId || '') === owner.ownerConnectionId &&
      (!requireCheckHint ||
        (String(live.hostedStatus?.checkConnectionId || '') === owner.checkConnectionId &&
          (!owner.hint ||
            (hint?.connectionId === owner.hint.connectionId &&
              hint.installationId === owner.hint.installationId &&
              hint.memberId === owner.hint.memberId))))
    )
  }

  // Serialize explicit checks before invalidation: a later room must never
  // retire the observation that an earlier room is still awaiting.
  const check = hostedRoomManualCheckTail
    .catch(() => undefined)
    .then(async () => {
      const routes = await hostedDefaultRoutes()

      if (!current() || !routes.some(candidate => candidate.connectionId === connectionId)) {
        return false
      }

      hostedUnsupportedUntil.delete(connectionId)
      invalidateHostedRoomsForConnection(connectionId)
      invalidateHostedRoomPoll(owner.roomId)
      await refreshHostedRoomsAfterCurrent(current)

      if (!current(false)) {
        return false
      }

      return String($groupChats.get()[group]?.hostedStatus?.checkConnectionId || '') !== connectionId
    })
    .finally(() => {
      if (hostedRoomManualChecks.get(key) === check) {
        hostedRoomManualChecks.delete(key)
      }
    })

  hostedRoomManualChecks.set(key, check)
  hostedRoomManualCheckTail = check

  return check
}

export function shouldRefreshHostedRoom(room: GroupChat | undefined, listed: unknown) {
  if (!room) {
    return true
  }

  const activeStates = new Set(['queued', 'sending', 'stopping', 'working'])

  const active =
    room.running === true ||
    activeStates.has(String(room.hostedStatus?.state || '')) ||
    $hostedRoomOutbox.get().commands.some(command => command.roomId === room.roomId && command.status !== 'failed')

  const fingerprint = hostedRoomPollFingerprint(listed)

  return (
    active ||
    room.hostedMembersNeedRefresh ||
    (Boolean(groupChatHostedGateway(room)) && !room.hostedMembersVerified) ||
    hostedRoomPollCache.get(String(room.roomId || '')) !== fingerprint
  )
}

/** Replay every hosted room only after plugin storage/ui_meta hydration has
 * settled. The contiguous cursor is persisted with the room, so reconnects
 * fetch only missing events and a gap never skips unseen history. */
export function refreshHostedRooms(stillCurrent?: () => boolean): Promise<void> {
  if (hostedRoomSyncDisposed || stillCurrent?.() === false) {
    return Promise.resolve()
  }

  if (hostedRoomRefreshPromise) {
    return hostedRoomRefreshPromise
  }

  hostedRoomSyncRunning = true

  const refresh = performHostedRoomRefresh(stillCurrent).finally(() => {
    if (hostedRoomRefreshPromise === refresh) {
      hostedRoomRefreshPromise = null
    }
  })

  hostedRoomRefreshPromise = refresh

  return refresh
}

/** Explicit checks are serialized, while identical owner requests share their
 * promise. An ordinary in-flight poll may need one refresh-only successor. */
function refreshHostedRoomsAfterCurrent(stillCurrent: () => boolean): Promise<void> {
  const active = hostedRoomRefreshPromise

  if (!active) {
    return refreshHostedRooms(stillCurrent)
  }

  return active.catch(() => undefined).then(() => refreshHostedRooms(stillCurrent))
}

function scheduleHostedRoomSync(delay = HOSTED_ROOM_SYNC_INTERVAL_MS) {
  if (hostedRoomSyncDisposed || typeof setTimeout !== 'function') {
    return
  }

  if (hostedRoomSyncTimer) {
    clearTimeout(hostedRoomSyncTimer)
  }

  hostedRoomSyncTimer = setTimeout(() => {
    hostedRoomSyncTimer = null
    void dispatchHostedRoomCleanup()
      .catch(() => undefined)
      .then(() => refreshHostedRooms())
      .catch(() => undefined)
      .then(() => dispatchHostedRoomOutbox())
      .catch(() => undefined)
      .then(() => scheduleHostedRoomSync())
  }, delay)

  const timer = hostedRoomSyncTimer as ReturnType<typeof setTimeout> & { unref?: () => void }
  timer?.unref?.()
}

async function transitionHostedRoomOutbox(
  action: Parameters<typeof reduceHostedRoomOutbox>[1],
  storage = hostedRoomStorage,
  stillCurrent: () => boolean = () => true
) {
  const next = await mutateHostedRoomOutbox(storage, action)

  // Persistence is not optimistic. A failed write must not restore an older
  // snapshot over later input, and a retired runtime must not publish its row.
  if (stillCurrent()) {
    $hostedRoomOutbox.set(next)
  }

  return next
}

async function reportImmediateHostedRoomCommandFailure(commandId: unknown) {
  const id = String(commandId || '')

  const failed = $hostedRoomOutbox
    .get()
    .commands.find(command => command.commandId === id && command.status === 'failed')

  if (!failed) {
    return false
  }

  // A definite rejection is not an acknowledgement and must not erase the
  // user's saved intent or invent a ready remote state. Explicit Skip releases
  // the queue; authoritative refresh alone decides whether the room is idle.
  surfaceHostedRoomCommandFailure(failed)

  hostedRoomPollCache.delete(failed.roomId)
  await refreshHostedRooms().catch(() => undefined)

  return true
}

export function dispatchHostedRoomOutbox(): Promise<void> {
  const lifecycle = hostedRoomLifecycleToken()
  const storage = hostedRoomStorage
  const current = () => hostedRoomLifecycleIsCurrent(lifecycle) && hostedRoomStorage === storage

  const transition = (action: Parameters<typeof reduceHostedRoomOutbox>[1]) =>
    transitionHostedRoomOutbox(action, storage, current)

  if (!current()) {
    return Promise.resolve()
  }

  if (hostedOutboxDispatchPromise) {
    return hostedOutboxDispatchPromise.then(() => (current() ? dispatchHostedRoomOutbox() : undefined))
  }

  const run = withHostedRoomOutboxDispatch(async () => {
    if (!current()) {
      return
    }

    let state = await recoverHostedRoomOutbox(storage)

    if (!current()) {
      return
    }

    const routes = await hostedDefaultRoutes()

    if (!current()) {
      return
    }

    const blockedRooms = new Set(
      state.commands.filter(command => ['failed', 'unknown'].includes(command.status)).map(command => command.roomId)
    )

    $hostedRoomOutbox.set(state)

    for (const failed of state.commands.filter(command => ['failed', 'unknown'].includes(command.status))) {
      surfaceHostedRoomCommandFailure(failed)
    }

    for (const command of state.commands.filter(entry => entry.status === 'pending')) {
      if (!current()) {
        return
      }

      const bypass =
        (command.kind === 'stop' || command.kind === 'disband') && failedHostedRoomCommand(state, command.roomId)

      if (blockedRooms.has(command.roomId) && !bypass) {
        continue
      }

      const exact = routes.find(candidate => candidate.connectionId === command.connectionId)

      const route = command.authorityId
        ? await verifiedHostedAuthorityRoute(routes, command.authorityId, command.connectionId)
        : exact

      if (!current()) {
        return
      }

      if (!route) {
        blockedRooms.add(command.roomId)

        continue
      }

      state = await transition({
        type: 'dispatch',
        commandId: command.commandId
      })

      if (!current()) {
        return
      }

      const claimed = state.commands.find(entry => entry.commandId === command.commandId)

      if (!claimed || claimed.status !== 'in-flight') {
        continue
      }

      const method: Record<HostedRoomCommand['kind'], string> = {
        create: 'groups.create',
        retry: 'groups.retry',
        rename: 'groups.rename',
        send: 'groups.send',
        stop: 'groups.stop',
        disband: 'groups.disband'
      }

      const params =
        command.kind === 'send'
          ? {
              room_id: command.roomId,
              event_id: command.commandId,
              payload: command.payload
            }
          : command.kind === 'rename'
            ? {
                room_id: command.roomId,
                event_id: command.commandId,
                name: command.payload.name
              }
            : command.kind === 'retry'
              ? {
                  room_id: command.roomId,
                  task_id: command.payload.task_id
                }
              : command.kind === 'stop' || command.kind === 'disband'
                ? {
                    room_id: command.roomId,
                    cancel_id: command.commandId
                  }
                : command.payload

      try {
        const reply = await requestHostedCommand(route, claimed, method[command.kind], params, current)

        // Keep the persisted in-flight command untouched when the window is
        // disposed mid-request. Recovery only replays keyed input mutations;
        // controls stay unknown rather than manufacturing Retry idempotency.
        if (!current()) {
          return
        }

        const receipt = hostedUserEventReceipt(command, reply)

        const local =
          receipt &&
          Object.entries($groupChats.get()).find(
            ([, room]) =>
              room.roomId === command.roomId &&
              (!command.authorityId || groupChatHostedGateway(room) === command.authorityId)
          )

        if (receipt && local) {
          updateGroupChat(local[0], current => ({
            ...current,
            log: mergeGroupChatRoomEntries(current, current.log || [], [receipt])
          }))
        }

        state = await transition({
          type: 'acknowledge',
          commandId: command.commandId
        })
      } catch (error) {
        if (!current()) {
          return
        }

        state = await transition(hostedRoomCommandFailure(error, claimed))

        if (!current()) {
          return
        }

        const unresolved = state.commands.find(entry => entry.commandId === command.commandId)

        if (unresolved) {
          surfaceHostedRoomCommandFailure(unresolved)
        }

        blockedRooms.add(command.roomId)
      }
    }
  })

  let owned: Promise<void>

  owned = run.finally(() => {
    if (hostedOutboxDispatchPromise === owned) {
      hostedOutboxDispatchPromise = null
    }
  })
  hostedOutboxDispatchPromise = owned

  return owned
}

async function enqueueHostedRoomCommand(command: Partial<HostedRoomCommand>) {
  const inserted = await withHostedRoomCommandOrder(String(command.roomId || ''), async () => {
    const current = await readHostedRoomOutbox(hostedRoomStorage)

    const existing = current.commands.find(
      entry =>
        entry.roomId === command.roomId &&
        entry.kind === command.kind &&
        ['pending', 'in-flight', 'unknown'].includes(entry.status) &&
        (entry.kind !== 'retry' || entry.payload.task_id === command.payload?.task_id)
    )

    if (existing && ['retry', 'stop', 'disband'].includes(String(command.kind))) {
      surfaceHostedRoomCommandFailure(existing)

      return false
    }

    await transitionHostedRoomOutbox({
      type: command.kind === 'disband' || command.kind === 'stop' ? 'enqueue-safety' : 'enqueue',
      command
    })

    return true
  })

  if (!inserted) {
    return false
  }

  await dispatchHostedRoomOutbox()

  if (await reportImmediateHostedRoomCommandFailure(command.commandId)) {
    throw new Error(botsText().group.hostRejectedCommand)
  }

  const pending = $hostedRoomOutbox.get().commands.find(entry => entry.commandId === command.commandId)

  if (pending) {
    surfaceHostedRoomCommandFailure(pending)
  }

  scheduleHostedRoomSync(0)

  return !pending
}

async function hostedRouteForRoom(room: GroupChat) {
  const connectionId = String(room?.hostedConnectionId || '')
  const routes = await hostedDefaultRoutes()
  const authorityId = groupChatHostedGateway(room)

  if (authorityId) {
    return verifiedHostedAuthorityRoute(routes, authorityId, connectionId)
  }

  if (connectionId) {
    const exact = routes.find(candidate => candidate.connectionId === connectionId)

    if (exact) {
      return exact
    }
  }

  return null
}

export async function approveHostedGroupChat(entry: GroupPrompt, choice: string) {
  const approval = entry.hostedApproval
  const room = $groupChats.get()[entry.group]
  const route = room ? await hostedRouteForRoom(room) : null

  if (!approval || !route || !['once', 'deny'].includes(choice)) {
    throw new Error(botsText().group.hostRouteMissing)
  }

  await requestHostedConnection(route, 'groups.approve', {
    room_id: approval.roomId,
    member_id: approval.memberId,
    task_id: approval.taskId,
    execution_generation: approval.executionGeneration,
    choice,
    request_id: entry.requestId
  })
  await refreshHostedRooms().catch(() => undefined)
  resolveHostedRoomApprovalAttention(entry)
  scheduleHostedRoomSync(0)
}

export async function probeHostedRoomMembers(members: GroupMember[]): Promise<HostedRoomProbe> {
  const routes = Object.fromEntries(
    (await hostedDefaultRoutes()).map(route => [String(route.connectionId || ''), route])
  )

  const connectionIds = [
    ...new Set(
      (Array.isArray(members) ? members : [])
        .map(member => String(member?.route?.connectionId || member?.connectionId || activeConnectionId() || ''))
        .filter(Boolean)
    )
  ]

  const capabilities: Record<string, HostedRoomCapability> = {}
  const now = Date.now()

  await Promise.all(
    connectionIds.map(async connectionId => {
      const cached = $hostedRoomCapabilities.get()[connectionId]

      if (cached?.kind === 'unsupported' && Number(hostedUnsupportedUntil.get(connectionId) || 0) > now) {
        capabilities[connectionId] = cached

        return
      }

      const observation = hostedRoomObservations.captureCapability(connectionId)
      const route = routes[connectionId]
      let capability: HostedRoomCapability

      try {
        capability = classifyHostedRoomCapability(
          route
            ? await withHostedRoomProbeTimeout(requestHostedConnection(route, 'groups.capabilities'))
            : { ok: false, error: new Error('Gateway route unavailable') },
          { connectionId }
        )
      } catch (error) {
        capability = classifyHostedRoomCapability({ ok: false, error }, { connectionId })
      }

      if (!hostedRoomObservations.current(observation)) {
        capabilities[connectionId] = $hostedRoomCapabilities.get()[connectionId]

        return
      }

      storeHostedCapabilities({ [connectionId]: capability })
      capabilities[connectionId] = capability

      if (capability.kind === 'unsupported') {
        hostedUnsupportedUntil.set(connectionId, now + HOSTED_ROOM_UNSUPPORTED_REPROBE_MS)
      } else {
        hostedUnsupportedUntil.delete(connectionId)
      }
    })
  )

  for (const connectionId of connectionIds) {
    capabilities[connectionId] = $hostedRoomCapabilities.get()[connectionId] || capabilities[connectionId]
  }

  const route = resolveAutonomousRoomPlan(members, {
    activeConnectionId: activeConnectionId(),
    capabilities
  })

  const capability = route.connectionId ? capabilities[route.connectionId] || null : null
  const homeConnectionId = String(route.homeConnectionId || route.connectionId || '')

  const attachmentUnavailableConnections = new Set(
    connectionIds.filter(connectionId => {
      const candidate = capabilities[connectionId]

      return (
        candidate?.limits.attachments !== true ||
        (connectionId !== homeConnectionId && candidate?.roomLink?.catalog?.attachments !== true)
      )
    })
  )

  return {
    attachmentParity:
      Boolean(homeConnectionId) &&
      capabilities[homeConnectionId]?.limits.attachments === true &&
      route.remoteConnectionIds.every(
        connectionId =>
          capabilities[connectionId]?.limits.attachments === true &&
          capabilities[connectionId]?.roomLink?.catalog?.attachments === true
      ),
    attachmentUnavailableMembers: members
      .filter(member =>
        attachmentUnavailableConnections.has(
          String(member?.route?.connectionId || member?.connectionId || activeConnectionId() || '')
        )
      )
      .map(member => String(member.display_name || member.handle || member.name || botsText().group.aBot)),
    route,
    routes,
    capabilities,
    capability,
    eligible: route.kind !== 'unsupported' && isHostedRoomContinuityEligible(capability)
  }
}

export async function createHostedGroupChat({ route, roomId, name, members }: HostedRoomCreateInput): Promise<{
  authorityEpoch: number
  authorityId: string
  connectionId: string
}> {
  if ((route.kind !== 'single-gateway' && route.kind !== 'multi-gateway') || !route.connectionId) {
    throw new Error(botsText().group.botsNeedOneHost)
  }

  const profileRoute = (await hostedDefaultRoutes()).find(candidate => candidate.connectionId === route.connectionId)

  if (!profileRoute) {
    throw new Error(botsText().group.hostRouteMissing)
  }

  let room: Record<string, unknown> | null = null

  try {
    const result = await requestHostedConnection<Record<string, unknown>>(profileRoute, 'groups.create', {
      room_id: roomId,
      name,
      members
    })

    room = record(result.room)
  } catch (createError) {
    // A dropped response has an unknown outcome. Verify the idempotent room id
    // before falling back to Desktop, or both drivers could start the first
    // user turn. A true create failure has no state and safely falls through.
    try {
      const state = await requestHostedConnection<Record<string, unknown>>(profileRoute, 'groups.state', {
        room_id: roomId
      })

      room = record(state.room)
    } catch {
      throw createError
    }
  }

  const authorityId = String(room?.authority_gateway_id || '')

  if (!authorityId) {
    throw new Error(botsText().group.hostRejectedCommand)
  }

  return {
    authorityId,
    authorityEpoch: Math.max(1, Number(room?.authority_epoch || 1)),
    connectionId: route.connectionId
  }
}

export async function createAutonomousHostedGroupChat({
  probe,
  roomId,
  name,
  members
}: AutonomousHostedRoomCreateInput) {
  const plan = probe.route
  const homeConnectionId = String(plan.homeConnectionId || '')
  const homeRoute = probe.routes[homeConnectionId]
  const homeCapability = probe.capabilities[homeConnectionId]

  if (!probe.eligible || !homeConnectionId || !homeRoute || !homeCapability?.authorityId) {
    throw new Error('This Group Chat cannot continue without Desktop yet.')
  }

  if (plan.kind === 'multi-gateway' && !homeCapability.peerGrantRenewal) {
    throw new Error(botsText().group.hostUpdateNeeded(homeConnectionId))
  }

  const hostedMembers: Array<Record<string, unknown>> = []
  const peerRegistrations: PreparedHostedPeer[] = []

  try {
    await addHostedRoomCleanup({
      operationId: `${roomId}:home-disband`,
      setupId: roomId,
      kind: 'home-disband',
      connectionId: homeConnectionId,
      roomId,
      cancelId: `rollback-${roomId}`
    })

    for (const [index, item] of members.entries()) {
      const connectionId = String(item.member.route?.connectionId || item.member.connectionId || '')
      const profile = String(item.member.targetProfile || item.profile || item.member.name || 'default')
      const memberId = `member-${index + 1}-${profile}`.replace(/[^A-Za-z0-9._:-]/g, '-').slice(0, 128)

      const descriptor: Record<string, unknown> = {
        member_id: memberId,
        profile,
        handle: item.handle,
        ...(item.displayName
          ? {
              display_name: item.displayName
            }
          : {})
      }

      if (connectionId === homeConnectionId) {
        hostedMembers.push(descriptor)

        continue
      }

      const invitation = record(
        await requestForBot(item.member, 'groups.peer.invite', {
          room_id: roomId,
          home_install_id: homeCapability.authorityId,
          authority_gateway_id: homeCapability.authorityId,
          authority_epoch: 1,
          member_id: memberId,
          ttl_seconds: ROOM_GRANT_TTL_SECONDS,
          status_ttl_seconds: ROOM_GRANT_STATUS_TTL_SECONDS,
          profile
        })
      )

      const catalog = record(invitation?.catalog)
      const invitedProfile = String(invitation?.target_profile || profile || '')

      const scopedTargetUrl = profileScopedRoomLinkEndpoint(
        probe.capabilities[connectionId]?.roomLink?.endpoint,
        invitation?.target_profile
      )

      if (invitation?.grant && invitedProfile) {
        await addHostedRoomCleanup({
          operationId: `${roomId}:peer-revoke:${memberId}`,
          setupId: roomId,
          kind: 'peer-revoke',
          connectionId,
          profile: invitedProfile,
          grant: String(invitation.grant)
        }).catch(async error => {
          try {
            await requestForBot(item.member, 'groups.peer.revoke', {
              grant: String(invitation.grant),
              profile: invitedProfile
            })
          } catch {
            throw Object.assign(new Error('Peer grant cleanup failed.'), { fallbackSafe: false })
          }

          throw error
        })
      }

      if (!hasRequestedRoomGrantLifetime(invitation)) {
        throw new Error(botsText().group.hostUpdateNeeded(item.displayName || item.handle || profile))
      }

      if (
        !scopedTargetUrl ||
        !invitation?.grant ||
        !catalog?.installation_id ||
        !catalog.catalog_digest ||
        !invitation.target_profile
      ) {
        throw new Error('One selected Bot could not prepare this Group Chat.')
      }

      hostedMembers.push({
        ...descriptor,
        profile: invitation.target_profile,
        target: {
          kind: 'peer',
          peer_id: catalog.installation_id,
          installation_id: catalog.installation_id,
          profile: invitation.target_profile,
          capability_digest: catalog.catalog_digest
        }
      })
      peerRegistrations.push({
        capability: probe.capabilities[connectionId],
        requestPeer: (method, params) => requestForBot(item.member, method, params),
        registration: {
          room_id: roomId,
          member_id: memberId,
          target_url: scopedTargetUrl,
          target_profile: invitation.target_profile,
          grant: invitation.grant,
          catalog
        }
      })
    }

    const created = await createHostedGroupChat({
      route: plan,
      roomId,
      name,
      members: hostedMembers as HostedRoomCreateInput['members']
    })

    await registerHostedPeers({ probe, roomId, name, members }, created, peerRegistrations)

    await releaseHostedRoomCleanup(roomId)

    return {
      ...created,
      continuityMode: plan.kind === 'multi-gateway' ? ('distributed' as const) : ('gateway' as const)
    }
  } catch (error) {
    await armHostedRoomCleanup(roomId).catch(() => undefined)
    await dispatchHostedRoomCleanup().catch(() => undefined)

    if (hostedRoomCleanupPending(roomId)) {
      throw Object.assign(
        new Error('Some selected Bots could not finish cleanup. Reconnect them before trying again.', {
          cause: error
        }),
        {
          fallbackSafe: false
        }
      )
    }

    throw error
  }
}

async function enqueueHostedGroupChatSend(group: string, message: GroupMessage, thread: string) {
  // Capture before waiting for command order, discovery or any upload. A
  // restarted runtime/renamed-or-replaced room cannot adopt this producer.
  const room = $groupChats.get()[group]
  const roomId = String(room?.roomId || '')
  const authorityId = groupChatHostedGateway(room)
  const connectionId = String(room?.hostedConnectionId || '')
  const epoch = room?.hostedEpoch
  const lifecycle = hostedRoomLifecycleToken()
  const storage = hostedRoomStorage
  let retired = false

  const matchesRoom = () => {
    const live = $groupChats.get()[group]

    return Boolean(
      live &&
      roomId &&
      authorityId &&
      live.roomId === roomId &&
      groupChatHostedGateway(live) === authorityId &&
      live.hostedEpoch === epoch &&
      String(live.hostedConnectionId || '') === connectionId &&
      live.hostedStatus?.state !== 'deleted' &&
      !hostedRoomLocallyDeleted.has(roomId)
    )
  }

  const assertProducerCurrent = () => {
    if (
      retired ||
      !matchesRoom() ||
      !hostedRoomLifecycleIsCurrent(lifecycle) ||
      hostedRoomStorage !== storage ||
      !storage
    ) {
      throw new Error('The Group Chat file send no longer belongs to this room and runtime.')
    }
  }

  // Remember an observed removal even if the exact logical id is re-added.
  const unlisten = $groupChats.listen(() => {
    retired ||= !matchesRoom()
  })

  try {
    assertProducerCurrent()

    if (!storage) {
      throw new Error('Desktop storage is unavailable, so Group Chat changes cannot be secured.')
    }

    return await withHostedRoomCommandOrder(roomId, async () => {
      assertProducerCurrent()
      const commandId = String(message.id || '')

      if (message.from.kind === 'user' && !message.seq && !message.eventId && room.log.includes(message)) {
        updateGroupChat(group, current => ({
          ...current,
          log: (current.log || []).map(entry =>
            entry === message ? outgoingHostedUserEvent(entry, roomId, commandId) : entry
          )
        }))
      }

      const attachments = Array.isArray(message.images)
        ? message.images.filter((attachment): attachment is Attachment => Boolean(attachment?.data))
        : []

      let lease: Awaited<ReturnType<typeof acquireHostedAttachmentRoute>> | undefined

      try {
        let route: ProfileRoute | null

        if (attachments.length) {
          const routes = await hostedDefaultRoutes()
          assertProducerCurrent()
          lease = await acquireHostedAttachmentRoute(routes, authorityId, connectionId, assertProducerCurrent)
          route = lease.route
        } else {
          route = await hostedRouteForRoom(room)
        }

        assertProducerCurrent()

        const assertCurrent = () => {
          assertProducerCurrent()
          lease?.assertCurrent()
        }

        const resolvedConnectionId = String(route?.connectionId || connectionId)

        if (!resolvedConnectionId) {
          throw new Error(botsText().group.hostRouteMissing)
        }

        if (attachments.length) {
          const parity = await probeHostedRoomMembers(room.members || [])
          assertCurrent()

          if (!parity.attachmentParity) {
            throw new Error(
              botsText().group.hostedAttachmentMemberUnavailable(parity.attachmentUnavailableMembers.join(', '))
            )
          }
        }

        const manifest = lease
          ? await stageHostedMessageAttachments({ request: lease.request, assertCurrent }, roomId, attachments)
          : []

        assertCurrent()

        const command = {
          commandId,
          kind: 'send' as const,
          roomId,
          authorityId,
          connectionId: resolvedConnectionId,
          payload: {
            text: message.text || '',
            thread_id: thread,
            ...(manifest.length ? { attachments: manifest } : {})
          }
        }

        // A mutation can wait on the shared outbox lock or an async read. Guard
        // the write itself, not only the renderer publication after persistence.
        // An already-issued write retains its original storage/intent; never
        // compensate by deleting a row that another command may now own.
        const enqueueStorage: PluginContext['storage'] = {
          get: (key, fallback) => storage.get(key, fallback),
          set: (key, value) => {
            assertCurrent()

            return storage.set(key, value)
          },
          remove: key => storage.remove(key)
        }

        await transitionHostedRoomOutbox({ type: 'enqueue', command }, enqueueStorage, () => {
          try {
            assertCurrent()

            return true
          } catch {
            return false
          }
        })
        assertCurrent()

        return command
      } finally {
        lease?.release()
      }
    })
  } finally {
    unlisten()
  }
}

export async function queueHostedGroupChat(group: string, message: GroupMessage, thread: string) {
  await enqueueHostedGroupChatSend(group, message, thread)
  await dispatchHostedRoomOutbox()

  if (await reportImmediateHostedRoomCommandFailure(message.id)) {
    throw new Error(botsText().group.hostRejectedCommand)
  }

  const pending = $hostedRoomOutbox.get().commands.find(entry => entry.commandId === message.id)

  scheduleHostedRoomSync(0)

  return !pending
}

export async function sendHostedGroupChat(group: string, message: GroupMessage, thread: string) {
  const command = await enqueueHostedGroupChatSend(group, message, thread)
  await dispatchHostedRoomOutbox()

  if (await reportImmediateHostedRoomCommandFailure(command.commandId)) {
    throw new Error(botsText().group.hostRejectedCommand)
  }

  const pending = $hostedRoomOutbox.get().commands.find(entry => entry.commandId === command.commandId)

  scheduleHostedRoomSync(0)

  return !pending
}

export async function readHostedGroupChatAttachment(group: string, message: GroupMessage, attachment: Attachment) {
  const room = $groupChats.get()[group]
  const route = room ? await hostedRouteForRoom(room) : null
  const roomId = String(room?.roomId || '')
  const eventId = String(message.eventId || message.id || '')

  if (!roomId || !route) {
    throw new Error('This Group Chat attachment is unavailable.')
  }

  return readHostedMessageAttachment(requestHostedConnection, route, roomId, eventId, attachment)
}

export async function stopHostedGroupChat(group: string) {
  const room = $groupChats.get()[group]

  if (!room?.roomId || !groupChatHostedGateway(room)) {
    return false
  }

  const route = await hostedRouteForRoom(room)
  const connectionId = String(route?.connectionId || room.hostedConnectionId || '')

  if (!connectionId) {
    throw new Error(botsText().group.hostRouteMissing)
  }

  return enqueueHostedRoomCommand({
    commandId: crypto.randomUUID(),
    kind: 'stop',
    roomId: room.roomId,
    authorityId: groupChatHostedGateway(room),
    connectionId,
    payload: {}
  })
}

export async function retryHostedGroupChat(group: string, taskId: string) {
  const room = $groupChats.get()[group]

  if (!room?.roomId || !groupChatHostedGateway(room) || !String(taskId || '').trim()) {
    return false
  }

  const route = await hostedRouteForRoom(room)
  const connectionId = String(route?.connectionId || room.hostedConnectionId || '')

  if (!connectionId) {
    throw new Error(botsText().group.hostRouteMissing)
  }

  return enqueueHostedRoomCommand({
    commandId: crypto.randomUUID(),
    kind: 'retry',
    roomId: room.roomId,
    authorityId: groupChatHostedGateway(room),
    connectionId,
    payload: { task_id: String(taskId).trim() }
  })
}

/** Resume bounded history replay without retrying any Bot work. */
export async function retryHostedRoomReplay(group: string) {
  const room = $groupChats.get()[group]
  const roomId = String(room?.roomId || '')

  if (!roomId || !groupChatHostedGateway(room)) {
    return false
  }

  hostedRoomPollCache.delete(roomId)
  await refreshHostedRooms()
  scheduleHostedRoomSync(0)

  return true
}

export async function retryFailedHostedRoomCommand(group: string, commandId: string) {
  const room = $groupChats.get()[group]
  const failed = failedHostedRoomCommand($hostedRoomOutbox.get(), String(room?.roomId || ''))

  if (!room || !failed || failed.commandId !== String(commandId || '') || !hostedCommandCanRetry(failed)) {
    return false
  }

  await transitionHostedRoomOutbox({ type: 'retry', commandId: failed.commandId })
  updateGroupChat(
    group,
    current => ({
      ...current,
      hostedStatus: {
        state: 'queued',
        label: botsText().group.hostedQueued(sourceLabel(current.hostedConnectionId || ''))
      },
      continuityIssue: null
    }),
    { sync: false }
  )
  await dispatchHostedRoomOutbox()
  scheduleHostedRoomSync(0)

  return !failedHostedRoomCommand($hostedRoomOutbox.get(), failed.roomId)
}

export async function renameHostedGroupChat(group: string, name: string) {
  const room = $groupChats.get()[group]

  if (!room?.roomId || !groupChatHostedGateway(room)) {
    return true
  }

  // A refresh may already be replaying the pre-rename server snapshot. Advance
  // the room fence before the request so that stale replay cannot restore the
  // old map key after the local rename completes or is queued for retry.
  beginHostedRoomMutation(room.roomId)

  const route = await hostedRouteForRoom(room)
  const connectionId = String(route?.connectionId || room.hostedConnectionId || '')

  if (!connectionId) {
    throw new Error(botsText().group.hostRouteMissing)
  }

  return enqueueHostedRoomCommand({
    commandId: crypto.randomUUID(),
    kind: 'rename',
    roomId: room.roomId,
    authorityId: groupChatHostedGateway(room),
    connectionId,
    payload: {
      name
    }
  })
}

export async function disbandHostedGroupChat(group: string) {
  const room = $groupChats.get()[group]

  if (!room?.roomId || !groupChatHostedGateway(room)) {
    return false
  }

  const route = await hostedRouteForRoom(room)

  if (!route) {
    throw new Error(
      botsText().group.hostedReconnectToDelete(
        sourceLabel(String(room.hostedConnectionId || '')) || botsText().group.thisHost
      )
    )
  }

  return enqueueHostedRoomCommand({
    commandId: crypto.randomUUID(),
    kind: 'disband',
    roomId: room.roomId,
    authorityId: groupChatHostedGateway(room),
    connectionId: route.connectionId,
    payload: {}
  })
}

export async function startHostedRoomRuntime(storage: PluginContext['storage'], hooks: HostedRoomRuntimeHooks = {}) {
  const lifecycleGeneration = ++hostedRoomLifecycleGeneration
  hostedRoomStorage = storage
  hostedRoomHooks = hooks
  hostedRoomSyncDisposed = false
  hostedRoomMutationGenerations.clear()
  hostedRoomLocallyDeleted.clear()
  hostedRoomObservations.invalidateAll()
  let persisted = createHostedRoomOutbox()

  try {
    persisted = await recoverHostedRoomOutbox(storage)
  } catch {
    /* an empty outbox is the safe fallback */
  }

  if (hostedRoomSyncDisposed || lifecycleGeneration !== hostedRoomLifecycleGeneration) {
    return
  }

  try {
    $hostedRoomOutbox.set(persisted)
  } catch {
    $hostedRoomOutbox.set(createHostedRoomOutbox())
  }

  await startHostedRoomCleanup(storage)

  if (hostedRoomSyncDisposed || lifecycleGeneration !== hostedRoomLifecycleGeneration) {
    return
  }

  await refreshHostedRooms().catch(() => undefined)
  await dispatchHostedRoomOutbox().catch(() => undefined)
  scheduleHostedRoomSync()
}

export function stopHostedRoomRuntime() {
  hostedRoomLifecycleGeneration += 1
  hostedRoomSyncDisposed = true
  hostedRoomRefreshPromise = null
  hostedRoomManualCheckTail = Promise.resolve()
  hostedRoomManualChecks.clear()
  hostedRoomSyncRunning = false
  stopHostedRoomCleanup()
  hostedRoomStorage = null
  hostedRoomHooks = {}
  hostedRoomPollCache.clear()
  hostedRoomPollGenerations.clear()
  hostedRoomMutationGenerations.clear()
  hostedRoomLocallyDeleted.clear()
  hostedRoomObservations.invalidateAll()
  hostedUnsupportedUntil.clear()

  if (hostedRoomSyncTimer) {
    clearTimeout(hostedRoomSyncTimer)
  }

  hostedRoomSyncTimer = null
}

/** Test-only lifecycle reset through the same public stop door. */
export function resetHostedRoomRuntimeForTests() {
  stopHostedRoomRuntime()
  hostedRoomObservations.retain([], [])
  hostedRoomSyncRunning = false
  hostedOutboxDispatchPromise = null
  resetHostedRoomOutboxLocksForTests()
  resetHostedRoomCleanupForTests()
  resetHostedRoomApprovalState()
  $hostedRoomCapabilities.set({})
  $hostedRoomOutbox.set(createHostedRoomOutbox())
}

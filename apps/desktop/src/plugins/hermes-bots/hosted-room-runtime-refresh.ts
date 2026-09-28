import { gatewayActivationEpoch, host } from '@hermes/plugin-sdk'

import {
  $groupChats,
  applyHostedRoomAuthority,
  mergeGroupChatRoomEntries,
  uniqueGroupChatName,
  updateGroupChat
} from './group-chat'
import { clearHostedRoomApprovalState, syncHostedRoomApprovals } from './hosted-room-approval-state'
import { $hostedRoomCapabilities } from './hosted-room-capability-state'
import type { HostedRoomCapability } from './hosted-room-client'
import {
  classifyHostedRoomCapability,
  createHostedRoomReplayState,
  deriveFriendlyHostedRoomStatus,
  isHostedRoomContinuityEligible,
  isHostedRoomReadEligible,
  replayHostedRoomPages
} from './hosted-room-client'
import { failedHostedRoomCommand } from './hosted-room-command-failures'
import {
  hostedReadOnlyState,
  hostedRoomContinuityMode,
  hostedRoomDriverDisplayStatus,
  hostedRoomPollFingerprint,
  hostedStatus,
  readHostedInventoryState,
  readHostedRoomInventory,
  hostedReplayMessages as replayMessages
} from './hosted-room-inventory'
import { revokeInvalidHostedMemberRoutes } from './hosted-room-member-inventory'
import { hostedMemberDescriptors } from './hosted-room-members'
import {
  $hostedRoomOutbox,
  activeConnectionId,
  finishHostedRoomRefresh,
  HOSTED_ROOM_UNSUPPORTED_REPROBE_MS,
  hostedDefaultRoutes,
  hostedRoomHooks,
  hostedRoomLifecycleGeneration,
  hostedRoomLocallyDeleted,
  hostedRoomMutationGeneration,
  hostedRoomMutationIsCurrent,
  hostedRoomObservations,
  hostedRoomPollCache,
  hostedRoomPollGenerations,
  type HostedRoomServerState,
  hostedRoomSyncDisposed,
  hostedUnsupportedUntil,
  invalidateHostedRoomPoll,
  invalidateHostedRoomsForConnection,
  isDisbanded,
  markHostedConnectionUnavailable,
  record,
  shouldRefreshHostedRoom,
  sourceLabel,
  storeHostedCapabilities
} from './hosted-room-runtime'
import { requestHostedConnection } from './hosted-room-transport'
import { restoreHostedUserOutboxIntents } from './hosted-user-events'
import { botsText } from './i18n'

export async function performHostedRoomRefresh(stillCurrent?: () => boolean) {
  const lifecycleGeneration = hostedRoomLifecycleGeneration

  const source = {
    activation: gatewayActivationEpoch(),
    connectionId: activeConnectionId(),
    gateway: String(host.state.gateway?.get?.() || ''),
    profile: String(host.state.profile?.get?.() || '')
  }

  const syncStale = () =>
    hostedRoomSyncDisposed ||
    lifecycleGeneration !== hostedRoomLifecycleGeneration ||
    gatewayActivationEpoch() !== source.activation ||
    activeConnectionId() !== source.connectionId ||
    String(host.state.gateway?.get?.() || '') !== source.gateway ||
    String(host.state.profile?.get?.() || '') !== source.profile ||
    stillCurrent?.() === false

  try {
    const routes = await hostedDefaultRoutes()

    if (syncStale()) {
      return
    }

    const routesByConnection = Object.fromEntries(routes.map(route => [String(route.connectionId || ''), route]))

    for (const id of Object.keys($hostedRoomCapabilities.get())) {
      if (!routesByConnection[id]) {
        invalidateHostedRoomsForConnection(id)
      }
    }

    const capabilities = Object.fromEntries(
      Object.entries($hostedRoomCapabilities.get()).filter(([id]) => routesByConnection[id])
    )

    hostedRoomObservations.retain(Object.keys(routesByConnection), Object.values($groupChats.get()))
    storeHostedCapabilities(capabilities, true)

    if (typeof host.profileRoutes === 'function') {
      revokeInvalidHostedMemberRoutes(routesByConnection, capabilities, invalidateHostedRoomPoll)
    }

    for (const route of routes) {
      if (syncStale()) {
        return
      }

      const connectionId = String(route.connectionId)
      let observation = hostedRoomObservations.capture(connectionId)
      let capability: HostedRoomCapability

      const cached = capabilities[connectionId]

      if (cached?.kind === 'unsupported' && Number(hostedUnsupportedUntil.get(connectionId) || 0) > Date.now()) {
        capability = cached
      } else {
        observation = hostedRoomObservations.captureCapability(connectionId)

        try {
          capability = classifyHostedRoomCapability(await requestHostedConnection(route, 'groups.capabilities'), {
            connectionId
          })
        } catch (error) {
          capability = classifyHostedRoomCapability(
            {
              ok: false,
              error
            },
            {
              connectionId
            }
          )
        }
      }

      if (syncStale()) {
        return
      }

      if (!hostedRoomObservations.current(observation)) {
        capabilities[connectionId] = $hostedRoomCapabilities.get()[connectionId]

        continue
      }

      if (capability !== cached) {
        if (capability.kind === 'unsupported') {
          hostedUnsupportedUntil.set(connectionId, Date.now() + HOSTED_ROOM_UNSUPPORTED_REPROBE_MS)
        } else {
          hostedUnsupportedUntil.delete(connectionId)
        }
      }

      storeHostedCapabilities({ [connectionId]: capability })
      capabilities[connectionId] = capability
      revokeInvalidHostedMemberRoutes(routesByConnection, capabilities, invalidateHostedRoomPoll)
    }

    if (syncStale()) {
      return
    }

    connectionLoop: for (const route of routes) {
      if (syncStale()) {
        return
      }

      const connectionId = String(route.connectionId)
      const capability = $hostedRoomCapabilities.get()[connectionId]

      if (!capability) {
        continue
      }

      const observation = hostedRoomObservations.capture(connectionId)
      const stale = () => syncStale() || !hostedRoomObservations.current(observation)

      const read = <T>(method: string, params: Record<string, unknown>) =>
        hostedRoomObservations.read(observation, () => requestHostedConnection<T>(route, method, params))

      if (!isHostedRoomReadEligible(capability)) {
        markHostedConnectionUnavailable(connectionId)

        if (capability.reason === 'old-gateway') {
          hostedRoomObservations.publish(hostedRoomObservations.capture(connectionId), new Set(), true)
        }

        continue
      }

      let inventory: Awaited<ReturnType<typeof readHostedRoomInventory>>

      try {
        inventory = await readHostedRoomInventory(
          params => read('groups.list', params),
          ids => {
            if (!stale()) {
              hostedRoomObservations.observe(observation, ids)
            }
          }
        )
      } catch {
        if (stale()) {
          continue
        }

        invalidateHostedRoomsForConnection(connectionId)
        markHostedConnectionUnavailable(connectionId)

        continue
      }

      if (stale()) {
        continue
      }

      const listedRooms = inventory.rooms

      // IDs establish absence independently of each known room's display replay.
      hostedRoomObservations.publish(observation, inventory.ids, inventory.complete)

      const disbandedIds = new Set(
        listedRooms
          .map(raw => (record(raw) || {}) as HostedRoomServerState)
          .filter(isDisbanded)
          .map(room => String(room.room_id || ''))
          .filter(Boolean)
      )

      const caughtUpDisbandedIds = new Set<string>()

      for (const listedRaw of listedRooms) {
        if (stale()) {
          continue connectionLoop
        }

        const listedRoom = (record(listedRaw) || {}) as HostedRoomServerState
        const roomId = String(listedRoom.room_id || '')
        const serverName = String(listedRoom.name || '').trim()

        if (!roomId || !serverName || hostedRoomLocallyDeleted.has(roomId)) {
          continue
        }

        const existingEntry = Object.entries($groupChats.get()).find(
          ([, room]) => String(room?.roomId || '') === roomId
        )

        const includeDisbanded = isDisbanded(listedRoom)

        // A client that already joined the room must replay terminal events
        // committed while it was offline before painting the remote disband.
        // Unknown disbanded rooms remain invisible on newly connected clients.
        if (includeDisbanded && !existingEntry) {
          continue
        }

        if (!shouldRefreshHostedRoom(existingEntry?.[1], listedRoom)) {
          if (
            includeDisbanded &&
            Math.max(0, Number(existingEntry?.[1]?.hostedSeq || 0)) >= Math.max(0, Number(listedRoom.latest_seq || 0))
          ) {
            caughtUpDisbandedIds.add(roomId)
          }

          continue
        }

        const refreshGeneration = hostedRoomMutationGeneration(roomId)
        const pollGeneration = Number(hostedRoomPollGenerations.get(roomId) || 0)

        let stateResponse: Record<string, unknown>
        let serverRoom: Record<string, unknown>

        try {
          stateResponse = await read('groups.state', {
            room_id: roomId,
            ...(includeDisbanded ? { include_disbanded: true } : {})
          })
          serverRoom = readHostedInventoryState(stateResponse, roomId)
        } catch {
          if (stale()) {
            continue connectionLoop
          }

          markHostedConnectionUnavailable(connectionId)

          continue
        }

        if (stale()) {
          continue connectionLoop
        }

        if (!hostedRoomMutationIsCurrent(roomId, refreshGeneration)) {
          continue
        }

        const ownership = applyHostedRoomAuthority(
          existingEntry?.[1] || { roomId, log: [], watermarks: {} },
          serverRoom
        )

        if (
          ownership.hosted !== serverRoom.authority_gateway_id ||
          ownership.hostedEpoch !== serverRoom.authority_epoch
        ) {
          continue
        }

        const writable =
          isHostedRoomContinuityEligible(capability) && capability.authorityId === serverRoom.authority_gateway_id

        let existingName = existingEntry?.[0]
        let existing = existingEntry?.[1]
        const taken = new Set(Object.keys($groupChats.get()))

        let localName =
          existingName ||
          (taken.has(serverName)
            ? uniqueGroupChatName(`${serverName} (${sourceLabel(connectionId)})`, taken)
            : serverName)

        const renamePending = $hostedRoomOutbox
          .get()
          .commands.some(
            command => command.kind === 'rename' && command.roomId === roomId && command.status !== 'failed'
          )

        if (existingName && existingName !== serverName && !renamePending && hostedRoomHooks.renameGroupChat) {
          const occupant = $groupChats.get()[serverName]
          const renameTaken = new Set(taken)

          renameTaken.delete(existingName)

          const targetName =
            occupant && occupant.roomId !== roomId
              ? uniqueGroupChatName(`${serverName} (${sourceLabel(connectionId)})`, renameTaken)
              : serverName

          const renamed = await hostedRoomHooks.renameGroupChat(
            existingName,
            targetName,
            Array.isArray(existing?.members) ? existing.members : []
          )

          if (renamed) {
            existingName = renamed
            localName = renamed
            existing = $groupChats.get()[renamed]
          }

          if (stale()) {
            continue connectionLoop
          }

          if (!hostedRoomMutationIsCurrent(roomId, refreshGeneration)) {
            continue
          }
        }

        const replay = await replayHostedRoomPages({
          state: createHostedRoomReplayState({
            roomId,
            name: serverName,
            members: Array.isArray(serverRoom.members) ? (serverRoom.members as Array<Record<string, unknown>>) : [],
            authorityId: String(serverRoom.authority_gateway_id || capability.authorityId),
            authorityEpoch: Number(serverRoom.authority_epoch || 1),
            connectionId,
            cursor: Number(existing?.hostedSeq || 0)
          }),
          fetchPage: request =>
            read('groups.log', {
              room_id: roomId,
              since_seq: request.sinceSeq,
              limit: request.limit,
              ...(includeDisbanded ? { include_disbanded: true } : {})
            }),
          pageSize: capability.maxLogLimit || 100
        })

        if (stale()) {
          continue connectionLoop
        }

        if (!hostedRoomMutationIsCurrent(roomId, refreshGeneration)) {
          continue
        }

        const replayStatus = deriveFriendlyHostedRoomStatus(replay.state)
        const driver = record(stateResponse.driver_status)

        const reconnectRoute = (Array.isArray(driver?.peer_routes) ? driver.peer_routes : [])
          .map(record)
          .find(route => route?.status === 'needs_reauthorization' && String(route?.member_id || ''))

        const reconnectMemberId = String(reconnectRoute?.member_id || '')

        const reconnectMember = (Array.isArray(serverRoom.members) ? serverRoom.members : [])
          .map(record)
          .find(member => String(member?.member_id || '') === reconnectMemberId)

        const reconnectName = String(
          reconnectMember?.display_name || reconnectMember?.handle || reconnectMember?.profile || botsText().group.aBot
        )

        const reconnectTarget = record(reconnectMember?.target)
        const reconnectAuthority = String(reconnectTarget?.installation_id || reconnectTarget?.peer_id || '')

        const reconnectPrior = (existing?.members || []).find(
          member =>
            String(member.handle || member.name || '') ===
              String(reconnectMember?.handle || reconnectMember?.profile || '') &&
            String(member.targetProfile || member.name || '') ===
              String(reconnectMember?.profile || reconnectMember?.member_id || '')
        )

        const reconnectHint = existing?.peerProbeHint

        const reconnectHintMatches = Boolean(
          reconnectMemberId &&
          reconnectAuthority &&
          reconnectHint?.memberId === reconnectMemberId &&
          reconnectHint.installationId === reconnectAuthority
        )

        const reconnectFallbackConnections = Object.keys(capabilities).filter(id => id !== connectionId)

        const reconnectConnectionId =
          Object.entries(capabilities).find(([, candidate]) => candidate.authorityId === reconnectAuthority)?.[0] ||
          (reconnectHintMatches ? String(reconnectHint?.connectionId || '') : '') ||
          String(reconnectPrior?.route?.connectionId || reconnectPrior?.connectionId || '') ||
          (reconnectFallbackConnections.length === 1 ? reconnectFallbackConnections[0] : '')

        const reconnectCapability = reconnectConnectionId ? capabilities[reconnectConnectionId] : undefined
        const reconnectCapabilityKnown = Boolean(reconnectCapability)

        const reconnectIdentityVerified = Boolean(
          reconnectCapability?.authorityId && reconnectCapability.authorityId === reconnectAuthority
        )

        const reconnectIdentityMismatch = Boolean(
          reconnectAuthority &&
          reconnectCapability?.kind === 'driver-capable' &&
          reconnectCapability.authorityId &&
          reconnectCapability.authorityId !== reconnectAuthority
        )

        const reconnectSupported = Boolean(
          capability.routeGrantFingerprint &&
          reconnectConnectionId &&
          reconnectIdentityVerified &&
          reconnectCapability?.kind === 'driver-capable' &&
          reconnectCapability.exactPeerGrantRevoke
        )

        const reconnectUpdateConnectionId =
          reconnectMemberId && !capability.routeGrantFingerprint
            ? connectionId
            : reconnectCapability?.kind === 'unsupported' ||
                (reconnectIdentityVerified &&
                  reconnectCapability?.kind === 'driver-capable' &&
                  !reconnectCapability.exactPeerGrantRevoke)
              ? reconnectConnectionId
              : ''

        const reconnectCheckConnectionId =
          reconnectMemberId && reconnectConnectionId && !reconnectSupported
            ? reconnectConnectionId
            : reconnectUpdateConnectionId

        const stopping = $hostedRoomOutbox
          .get()
          .commands.some(
            command =>
              command.roomId === roomId && ['disband', 'stop'].includes(command.kind) && command.status !== 'failed'
          )

        const friendly = reconnectMemberId
          ? {
              ...replayStatus,
              kind: 'needs-attention' as const,
              member: reconnectName,
              canRetry: false,
              canStop: false
            }
          : hostedRoomDriverDisplayStatus(replayStatus, driver, { stopping })

        const running = ['queued', 'stopping', 'working'].includes(friendly.kind)

        const pendingActions = Array.isArray(driver?.pending_actions) ? driver.pending_actions : []

        const retryAction = pendingActions
          .map(record)
          .find(action => action?.kind === 'retry' && String(action?.task_id || ''))

        const commandFailure = failedHostedRoomCommand($hostedRoomOutbox.get(), roomId)

        const memberDescriptors = hostedMemberDescriptors(
          serverRoom,
          connectionId,
          existing?.members || [],
          capabilities,
          sourceLabel
        )

        updateGroupChat(
          localName,
          current => {
            if (stale()) {
              return current
            }

            const authoritative = applyHostedRoomAuthority(current, serverRoom as Record<string, unknown>)

            return {
              ...authoritative,
              roomId,
              members: memberDescriptors,
              hostedMembersVerified: true,
              hostedMembersNeedRefresh: false,
              peerProbeHint:
                reconnectMemberId && reconnectAuthority && reconnectConnectionId
                  ? {
                      connectionId: reconnectConnectionId,
                      installationId: reconnectAuthority,
                      memberId: reconnectMemberId
                    }
                  : undefined,
              log: mergeGroupChatRoomEntries(
                current,
                restoreHostedUserOutboxIntents(current, $hostedRoomOutbox.get()),
                replayMessages(replay.state.messages)
              ),
              hostedConnectionId: connectionId,
              hostedSeq: replay.state.cursor,
              hostedStatus: commandFailure
                ? {
                    canRetry: true,
                    canStop: friendly.canStop,
                    label: botsText().group.hostedNeedsAttention,
                    retryCommandId: commandFailure.commandId,
                    state: 'failed'
                  }
                : {
                    ...hostedStatus(friendly, sourceLabel(connectionId)),
                    ...(retryAction && !reconnectMemberId ? { taskId: String(retryAction.task_id) } : {}),
                    ...(reconnectMemberId ? { canReconnect: reconnectSupported } : {}),
                    ...(reconnectMemberId && reconnectSupported ? { reconnectMemberId } : {}),
                    ...(reconnectCheckConnectionId ? { checkConnectionId: reconnectCheckConnectionId } : {}),
                    ...(reconnectMemberId &&
                    (!reconnectCapabilityKnown || reconnectCapability?.kind === 'transient-failure')
                      ? { canRetry: true }
                      : {}),
                    ...(!replay.complete && !reconnectMemberId ? { canRetry: true } : {})
                  },
              continuityMode: hostedRoomContinuityMode(serverRoom),
              continuityIssue: commandFailure
                ? botsText().group.hostRejectedCommand
                : reconnectMemberId
                  ? !reconnectCapabilityKnown || reconnectCapability?.kind === 'transient-failure'
                    ? botsText().group.reconnectFailed
                    : reconnectCapability?.kind === 'auth-failure'
                      ? botsText().group.hostReauthNeeded(sourceLabel(reconnectConnectionId))
                      : reconnectIdentityMismatch
                        ? botsText().group.memberCorrectDevice(reconnectName)
                        : reconnectSupported
                          ? botsText().group.memberReconnectToContinue(reconnectName)
                          : botsText().group.hostUpdateNeeded(
                              reconnectUpdateConnectionId ? sourceLabel(reconnectUpdateConnectionId) : reconnectName
                            )
                  : replay.complete
                    ? null
                    : botsText().group.hostedSyncing,
              running,
              ...(!writable ? hostedReadOnlyState() : {})
            }
          },
          {
            sync: false
          }
        )

        if (stale()) {
          continue connectionLoop
        }

        if (writable) {
          syncHostedRoomApprovals(localName, serverRoom, memberDescriptors, pendingActions)
        } else {
          clearHostedRoomApprovalState(localName)
        }

        if (stale()) {
          continue connectionLoop
        }

        if (
          replay.complete &&
          (!reconnectMemberId || Boolean(reconnectCheckConnectionId) || reconnectSupported) &&
          Number(hostedRoomPollGenerations.get(roomId) || 0) === pollGeneration
        ) {
          hostedRoomPollCache.set(roomId, hostedRoomPollFingerprint(listedRoom))

          if (includeDisbanded) {
            caughtUpDisbandedIds.add(roomId)
          }
        } else {
          hostedRoomPollCache.delete(roomId)
        }
      }

      // Keep the local shell long enough to explain a disband observed on
      // another client. Silently deleting only the room atom would strand an
      // open workspace and leave membership metadata half-cleaned. The normal
      // local disband action performs the complete cross-module cleanup.
      if (disbandedIds.size) {
        for (const [name, room] of Object.entries($groupChats.get())) {
          if (stale()) {
            continue connectionLoop
          }

          if (
            room.roomId &&
            disbandedIds.has(room.roomId) &&
            caughtUpDisbandedIds.has(room.roomId) &&
            room.hostedConnectionId === connectionId
          ) {
            updateGroupChat(
              name,
              current => ({
                ...current,
                running: false,
                hostedStatus: {
                  state: 'deleted',
                  label: botsText().group.hostedDeleted
                },
                continuityIssue: botsText().group.hostedDeleteLocally
              }),
              {
                sync: false
              }
            )
            clearHostedRoomApprovalState(name)
          }
        }
      }

      if (inventory.complete) {
        const listedIds = new Set(listedRooms.map(raw => String(record(raw)?.room_id || '')).filter(Boolean))

        for (const [name, room] of Object.entries($groupChats.get())) {
          if (stale()) {
            continue connectionLoop
          }

          const roomId = String(room?.roomId || '')

          if (!roomId || room.hostedConnectionId !== connectionId || listedIds.has(roomId)) {
            continue
          }

          try {
            await read('groups.state', {
              room_id: roomId,
              include_disbanded: true
            })

            continue
          } catch (error) {
            if (stale()) {
              continue connectionLoop
            }

            const message = String(record(error)?.message || record(record(error)?.error)?.message || error || '')

            if (!/history expired|permanently retired|hosted room not found/i.test(message)) {
              continue
            }
          }

          hostedRoomPollCache.delete(roomId)
          updateGroupChat(
            name,
            current => ({
              ...current,
              running: false,
              hostedStatus: {
                state: 'deleted',
                label: botsText().group.hostedDeleted
              },
              continuityIssue: botsText().group.hostedDeleteLocally
            }),
            { sync: false }
          )
          clearHostedRoomApprovalState(name)
        }
      }
    }
  } finally {
    finishHostedRoomRefresh()
  }
}

/**
 * Gateway-hosted Group Chat runtime.
 *
 * RPC ownership lives here: capability negotiation, the durable command
 * outbox, monotonic replay, and the bounded refresh loop. Group state remains
 * owned by `group-chat.ts`; creation, round routing, and room UI call this
 * module through narrow verbs.
 */

import { atom, host } from '@hermes/plugin-sdk'
import type { PluginContext } from '@hermes/plugin-sdk'

import { $lastRoster } from './data'
import {
  $groupChats,
  applyHostedRoomAuthority,
  groupChatHostedGateway,
  mergeGroupChatSyncEntries,
  uniqueGroupChatName,
  updateGroupChat
} from './group-chat'
import {
  classifyHostedRoomCapability,
  createHostedRoomOutbox,
  createHostedRoomReplayState,
  deriveFriendlyHostedRoomStatus,
  isHostedRoomContinuityEligible,
  reduceHostedRoomOutbox,
  replayHostedRoomPages,
  resolveSingleGatewayRoute
} from './hosted-room-client'
import type {
  FriendlyHostedRoomStatus,
  HostedRoomCapability,
  HostedRoomCommand,
  HostedRoomOutbox,
  HostedRoomRouteResolution
} from './hosted-room-client'
import { botsText } from './i18n'
import type { Attachment, GroupChat, GroupMember, GroupMessage, ProfileRoute } from './types'

const HOSTED_ROOM_OUTBOX_KEY = 'hosted-room-outbox-v1'
const HOSTED_ROOM_SYNC_INTERVAL_MS = 5000
const GROUP_ATTACHMENT_CACHE_CHARS = 50_000_000

export const $hostedRoomCapabilities = atom<Record<string, HostedRoomCapability>>({})
export const $hostedRoomOutbox = atom<HostedRoomOutbox>(createHostedRoomOutbox())

const hostedAuthorityRoutes = new Map<string, ProfileRoute>()
let hostedRoomSyncTimer: ReturnType<typeof setTimeout> | null = null
let hostedRoomSyncRunning = false
let hostedRoomSyncDisposed = true
let hostedOutboxDispatching = false
let hostedRoomStorage: null | PluginContext['storage'] = null
let hostedRoomHooks: HostedRoomRuntimeHooks = {}
const groupAttachmentDataCache = new Map<string, string>()
let groupAttachmentDataCacheChars = 0

export interface HostedRoomRuntimeHooks {
  renameGroupChat?: (oldName: string, newName: string, members: GroupMember[]) => Promise<null | string>
}

export interface HostedRoomProbe {
  capability: HostedRoomCapability | null
  eligible: boolean
  route: HostedRoomRouteResolution
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

interface HostedRoomServerMember {
  display_name?: unknown
  handle?: unknown
  member_id?: unknown
  profile?: unknown
}

interface HostedRoomServerState {
  authority_epoch?: unknown
  authority_gateway_id?: unknown
  disbanded_at?: unknown
  members?: unknown
  name?: unknown
  room_id?: unknown
}

function record(value: unknown): Record<string, unknown> | null {
  return value && typeof value === 'object' && !Array.isArray(value) ? (value as Record<string, unknown>) : null
}

function activeConnectionId() {
  return String(host.state.connectionId?.get?.() || host.activeConnectionId?.() || '')
}

async function hostedDefaultRoutes(): Promise<ProfileRoute[]> {
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

async function requestHostedConnection<T>(
  route: ProfileRoute,
  method: string,
  params: Record<string, unknown> = {}
): Promise<T> {
  if (!route?.connectionId || typeof host.requestProfile !== 'function') {
    throw new Error(botsText().group.hostRouteMissing)
  }

  return host.requestProfile(route, method, params) as Promise<T>
}

function sourceLabel(connectionId: string) {
  const source = ($lastRoster.get() || []).find(row => String(row?.connectionId || '') === connectionId)

  return String(source?.connectionLabel || botsText().group.thisHost)
}

function hostedMemberDescriptors(
  room: HostedRoomServerState,
  connectionId: string,
  connectionLabel: string
): GroupMember[] {
  return (Array.isArray(room?.members) ? room.members : []).map(raw => {
    const member = (record(raw) || {}) as HostedRoomServerMember
    const profile = String(member.profile || member.member_id || 'default')

    return {
      name: profile,
      handle: String(member.handle || member.profile || 'hermes'),
      title: String(member.display_name || ''),
      connectionId,
      connectionLabel,
      remoteSource: true,
      sourceScoped: true,
      sourceReachable: true,
      targetProfile: profile
    }
  })
}

function markHostedConnectionUnavailable(connectionId: string, unsupported = false) {
  const connectionName = sourceLabel(connectionId)

  for (const [name, room] of Object.entries($groupChats.get())) {
    if (String(room?.hostedConnectionId || '') !== connectionId) {
      continue
    }

    updateGroupChat(
      name,
      current => ({
        ...current,
        running: false,
        hostedStatus: {
          state: unsupported ? 'unsupported' : 'offline',
          label: unsupported
            ? botsText().group.hostUpdateNeeded(connectionName)
            : botsText().group.hostedUnavailable(connectionName)
        },
        continuityIssue: unsupported ? null : botsText().group.hostReconnectToContinue(connectionName)
      }),
      {
        sync: false
      }
    )
  }
}

function hostedStatus(status: FriendlyHostedRoomStatus, connectionName: string) {
  const b = botsText()
  const member = status.member || b.group.aBot

  const labels: Record<string, string> = {
    deleted: b.group.hostedDeleted,
    offline: b.group.hostedUnavailable(connectionName),
    ready: b.roster.ready,
    working: b.group.memberThinking(member),
    'member-unavailable': b.group.memberUnavailable(member),
    'needs-attention': b.group.memberNeedsAttention(member),
    failed: b.group.memberCouldNotRespond(member),
    waiting: b.group.memberRetryWhenOnline(member),
    stopped: b.group.hostedStopped,
    'needs-you': b.group.waitingForAnswer
  }

  return {
    state: status.kind,
    label: labels[status.kind] || b.roster.statusUnknown,
    ...(status.canRetry === undefined
      ? {}
      : {
          canRetry: status.canRetry
        }),
    ...(status.canStop === undefined
      ? {}
      : {
          canStop: status.canStop
        })
  }
}

function replayMessages(
  messages: ReturnType<typeof createHostedRoomReplayState>['messages'],
  connectionId: string
): GroupMessage[] {
  return messages.map(message => ({
    at: message.at,
    from: message.from,
    id: message.eventId,
    eventId: message.eventId,
    seq: message.seq,
    text: message.text,
    thread: message.thread,
    ...(message.attachments?.length
      ? {
          attachmentMeta: message.attachments.map(attachment => ({
            ...attachment,
            connectionId
          }))
        }
      : {})
  }))
}

function isDisbanded(room: HostedRoomServerState) {
  return room.disbanded_at !== null && room.disbanded_at !== undefined
}

/** Replay every hosted room only after plugin storage/ui_meta hydration has
 * settled. The contiguous cursor is persisted with the room, so reconnects
 * fetch only missing events and a gap never skips unseen history. */
export async function refreshHostedRooms() {
  if (hostedRoomSyncDisposed || hostedRoomSyncRunning) {
    return
  }

  hostedRoomSyncRunning = true

  try {
    const routes = await hostedDefaultRoutes()

    const capabilities = {
      ...$hostedRoomCapabilities.get()
    }

    for (const route of routes) {
      if (hostedRoomSyncDisposed) {
        return
      }

      const connectionId = String(route.connectionId)
      let capability: HostedRoomCapability

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

      if (hostedRoomSyncDisposed) {
        return
      }

      capabilities[connectionId] = capability

      if (!isHostedRoomContinuityEligible(capability) || !capability.authorityId) {
        markHostedConnectionUnavailable(connectionId, capability.kind === 'unsupported')

        continue
      }

      hostedAuthorityRoutes.set(capability.authorityId, route)
      let listed: Record<string, unknown>

      try {
        listed = await requestHostedConnection(route, 'groups.list', {
          include_disbanded: true
        })
      } catch {
        markHostedConnectionUnavailable(connectionId)

        continue
      }

      if (hostedRoomSyncDisposed) {
        return
      }

      const listedRooms = Array.isArray(listed?.rooms) ? listed.rooms : []

      const disbandedIds = new Set(
        listedRooms
          .map(raw => (record(raw) || {}) as HostedRoomServerState)
          .filter(isDisbanded)
          .map(room => String(room.room_id || ''))
          .filter(Boolean)
      )

      for (const listedRaw of listedRooms) {
        const listedRoom = (record(listedRaw) || {}) as HostedRoomServerState

        if (isDisbanded(listedRoom)) {
          continue
        }

        const roomId = String(listedRoom.room_id || '')
        const serverName = String(listedRoom.name || '').trim()

        if (!roomId || !serverName) {
          continue
        }

        let stateResponse: Record<string, unknown>

        try {
          stateResponse = await requestHostedConnection(route, 'groups.state', {
            room_id: roomId
          })
        } catch {
          markHostedConnectionUnavailable(connectionId)

          continue
        }

        if (hostedRoomSyncDisposed) {
          return
        }

        const serverRoom = (record(stateResponse.room) || listedRoom) as unknown as HostedRoomServerState

        const existingEntry = Object.entries($groupChats.get()).find(
          ([, room]) => String(room?.roomId || '') === roomId
        )

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

          if (hostedRoomSyncDisposed) {
            return
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
            requestHostedConnection(route, 'groups.log', {
              room_id: roomId,
              since_seq: request.sinceSeq,
              limit: request.limit
            })
        })

        if (hostedRoomSyncDisposed) {
          return
        }

        const friendly = deriveFriendlyHostedRoomStatus(replay.state)
        const driver = record(stateResponse.driver_status)
        const running = driver?.working === true || friendly.kind === 'working'

        updateGroupChat(
          localName,
          current => {
            const authoritative = applyHostedRoomAuthority(current, serverRoom as Record<string, unknown>)

            return {
              ...authoritative,
              roomId,
              members: hostedMemberDescriptors(serverRoom, connectionId, sourceLabel(connectionId)),
              log: mergeGroupChatSyncEntries(current.log || [], replayMessages(replay.state.messages, connectionId)),
              hostedConnectionId: connectionId,
              hostedSeq: replay.state.cursor,
              hostedStatus: hostedStatus(friendly, sourceLabel(connectionId)),
              continuityMode: 'gateway',
              continuityIssue: replay.complete ? null : botsText().group.hostedSyncing,
              running
            }
          },
          {
            sync: false
          }
        )
      }

      // Keep the local shell long enough to explain a disband observed on
      // another client. Silently deleting only the room atom would strand an
      // open workspace and leave membership metadata half-cleaned. The normal
      // local disband action performs the complete cross-module cleanup.
      if (disbandedIds.size) {
        for (const [name, room] of Object.entries($groupChats.get())) {
          if (room.roomId && disbandedIds.has(room.roomId) && room.hostedConnectionId === connectionId) {
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
          }
        }
      }
    }

    if (!hostedRoomSyncDisposed) {
      $hostedRoomCapabilities.set(capabilities)
    }
  } finally {
    hostedRoomSyncRunning = false
  }
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
    void refreshHostedRooms()
      .catch(() => undefined)
      .then(() => dispatchHostedRoomOutbox())
      .catch(() => undefined)
      .then(() => scheduleHostedRoomSync())
  }, delay)

  const timer = hostedRoomSyncTimer as ReturnType<typeof setTimeout> & { unref?: () => void }
  timer?.unref?.()
}

async function persistHostedRoomOutbox() {
  if (typeof hostedRoomStorage?.set !== 'function') {
    throw new Error(botsText().group.desktopStorageUnavailable)
  }

  await hostedRoomStorage.set(HOSTED_ROOM_OUTBOX_KEY, $hostedRoomOutbox.get())
}

function transitionHostedRoomOutbox(action: Parameters<typeof reduceHostedRoomOutbox>[1]) {
  const next = reduceHostedRoomOutbox($hostedRoomOutbox.get(), action)

  $hostedRoomOutbox.set(next)

  return next
}

function terminalCommandFailure(error: unknown) {
  const candidate = record(error)
  const nested = record(candidate?.error)
  const code = Number(candidate?.code ?? nested?.code)

  return Number.isInteger(code) && code >= 4100 && code < 4200 && code !== 4115
}

export async function dispatchHostedRoomOutbox() {
  if (hostedOutboxDispatching || hostedRoomSyncDisposed) {
    return
  }

  hostedOutboxDispatching = true

  try {
    let state = $hostedRoomOutbox.get()

    for (const command of state.commands.filter(entry => entry.status === 'pending')) {
      const route = (await hostedDefaultRoutes()).find(candidate => candidate.connectionId === command.connectionId)

      if (hostedRoomSyncDisposed) {
        return
      }

      if (!route) {
        continue
      }

      state = transitionHostedRoomOutbox({
        type: 'dispatch',
        commandId: command.commandId
      })
      await persistHostedRoomOutbox()

      const method: Record<HostedRoomCommand['kind'], string> = {
        create: 'groups.create',
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
            : command.kind === 'stop' || command.kind === 'disband'
              ? {
                  room_id: command.roomId,
                  cancel_id: command.commandId
                }
              : command.payload

      try {
        await requestHostedConnection(route, method[command.kind], params)

        // Keep the persisted in-flight command untouched when the window is
        // disposed mid-request. Rehydration returns it to pending with the
        // same idempotency key, covering an unknown server outcome safely.
        if (hostedRoomSyncDisposed) {
          return
        }

        state = transitionHostedRoomOutbox({
          type: 'acknowledge',
          commandId: command.commandId
        })
        await persistHostedRoomOutbox()
      } catch (error) {
        state = transitionHostedRoomOutbox(
          terminalCommandFailure(error)
            ? {
                type: 'terminal-failure',
                commandId: command.commandId,
                failureCode: String(record(error)?.code || 'command-rejected')
              }
            : {
                type: 'transient-failure',
                commandId: command.commandId
              }
        )
        await persistHostedRoomOutbox()
      }
    }
  } finally {
    hostedOutboxDispatching = false
  }
}

async function enqueueHostedRoomCommand(command: Partial<HostedRoomCommand>) {
  transitionHostedRoomOutbox({
    type: 'enqueue',
    command
  })
  await persistHostedRoomOutbox()
  await dispatchHostedRoomOutbox()

  const pending = $hostedRoomOutbox.get().commands.find(entry => entry.commandId === command.commandId)

  if (pending?.status === 'failed') {
    throw new Error(botsText().group.hostRejectedCommand)
  }

  scheduleHostedRoomSync(0)

  return !pending
}

async function hostedRouteForRoom(room: GroupChat) {
  const connectionId = String(room?.hostedConnectionId || '')
  const routes = await hostedDefaultRoutes()

  if (connectionId) {
    const exact = routes.find(candidate => candidate.connectionId === connectionId)

    if (exact) {
      return exact
    }
  }

  return hostedAuthorityRoutes.get(groupChatHostedGateway(room)) || null
}

export async function probeHostedRoomMembers(members: GroupMember[]): Promise<HostedRoomProbe> {
  const route = resolveSingleGatewayRoute(members, {
    activeConnectionId: activeConnectionId()
  })

  if (route.kind !== 'single-gateway' || !route.connectionId) {
    return {
      route,
      capability: null,
      eligible: false
    }
  }

  const profileRoute = (await hostedDefaultRoutes()).find(candidate => candidate.connectionId === route.connectionId)

  if (!profileRoute) {
    return {
      route,
      capability: classifyHostedRoomCapability(
        {
          ok: false,
          error: new Error('Gateway route unavailable')
        },
        {
          connectionId: route.connectionId
        }
      ),
      eligible: false
    }
  }

  let capability: HostedRoomCapability

  try {
    capability = classifyHostedRoomCapability(await requestHostedConnection(profileRoute, 'groups.capabilities'), {
      connectionId: route.connectionId
    })
  } catch (error) {
    capability = classifyHostedRoomCapability(
      {
        ok: false,
        error
      },
      {
        connectionId: route.connectionId
      }
    )
  }

  $hostedRoomCapabilities.set({
    ...$hostedRoomCapabilities.get(),
    [route.connectionId]: capability
  })

  if (capability.authorityId && isHostedRoomContinuityEligible(capability)) {
    hostedAuthorityRoutes.set(capability.authorityId, profileRoute)
  }

  return {
    route,
    capability,
    eligible: isHostedRoomContinuityEligible(capability)
  }
}

export async function createHostedGroupChat({ route, roomId, name, members }: HostedRoomCreateInput): Promise<{
  authorityEpoch: number
  authorityId: string
  connectionId: string
}> {
  if (route.kind !== 'single-gateway' || !route.connectionId) {
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

  hostedAuthorityRoutes.set(authorityId, profileRoute)

  return {
    authorityId,
    authorityEpoch: Math.max(1, Number(room?.authority_epoch || 1)),
    connectionId: route.connectionId
  }
}

export function hostedRoomAcceptsAttachments(room: GroupChat) {
  if (!groupChatHostedGateway(room)) {
    return true
  }

  const connectionId = String(room.hostedConnectionId || '')

  return Boolean(connectionId && $hostedRoomCapabilities.get()[connectionId]?.limits.attachments)
}

function attachmentMime(attachment: Attachment) {
  const dataMime = /^data:([^;,]+)/i.exec(String(attachment?.data || ''))?.[1]

  return (
    dataMime ||
    String(attachment?.mime || '') ||
    (attachment?.kind === 'pdf'
      ? 'application/pdf'
      : attachment?.kind === 'image'
        ? 'image/png'
        : 'application/octet-stream')
  ).toLowerCase()
}

function attachmentBase64(attachment: Attachment) {
  const match = /^data:([^;,]+);base64,([A-Za-z0-9+/=]+)$/i.exec(String(attachment?.data || ''))

  if (!match) {
    throw new Error(`${attachment?.name || 'Attachment'} could not be encoded for upload.`)
  }

  return {
    contentBase64: match[2],
    mime: match[1].toLowerCase()
  }
}

async function stageHostedAttachments(
  route: ProfileRoute,
  roomId: string,
  eventId: string,
  attachments: Attachment[]
) {
  const picked = Array.isArray(attachments) ? attachments : []

  if (picked.length > 8) {
    throw new Error('A Group Chat message supports at most 8 attachments.')
  }

  const manifests: Array<Record<string, unknown>> = []
  let total = 0

  for (let index = 0; index < picked.length; index += 1) {
    const attachment = picked[index]
    const encoded = attachmentBase64(attachment)
    const mime = attachmentMime(attachment)

    if (encoded.mime !== mime) {
      throw new Error(`${attachment.name || 'Attachment'} has conflicting MIME metadata.`)
    }

    const result = await requestHostedConnection<Record<string, unknown>>(route, 'groups.attachment.put', {
      room_id: roomId,
      upload_id: `${eventId}:upload:${index}`,
      kind: attachment.kind,
      name: attachment.name || 'attachment',
      mime,
      content_base64: encoded.contentBase64
    })

    const stored = record(result.attachment)
    const size = Number(stored?.size)

    if (!stored?.attachment_id || !Number.isSafeInteger(size) || size < 0) {
      throw new Error(`${attachment.name || 'Attachment'} was not accepted by the Group Chat gateway.`)
    }

    total += size

    if (total > 25_000_000) {
      throw new Error('Attachments exceed the 25MB message limit.')
    }

    manifests.push({
      attachment_id: stored.attachment_id,
      kind: stored.kind,
      name: stored.name,
      size,
      mime: stored.mime
    })
  }

  return manifests
}

function cacheGroupAttachmentData(key: string, dataUrl: string) {
  if (!key || !dataUrl || dataUrl.length > GROUP_ATTACHMENT_CACHE_CHARS) {
    return
  }

  const prior = groupAttachmentDataCache.get(key)

  if (prior) {
    groupAttachmentDataCacheChars -= prior.length
  }

  groupAttachmentDataCache.delete(key)
  groupAttachmentDataCache.set(key, dataUrl)
  groupAttachmentDataCacheChars += dataUrl.length

  while (groupAttachmentDataCacheChars > GROUP_ATTACHMENT_CACHE_CHARS && groupAttachmentDataCache.size > 1) {
    const oldest = groupAttachmentDataCache.keys().next().value

    if (!oldest) {
      break
    }

    const removed = groupAttachmentDataCache.get(oldest) || ''

    groupAttachmentDataCache.delete(oldest)
    groupAttachmentDataCacheChars -= removed.length
  }
}

export async function loadHostedGroupAttachmentData(
  room: GroupChat,
  entry: GroupMessage,
  attachment: Attachment
) {
  const attachmentId = String(attachment.attachment_id || '')
  const connectionId = String(attachment.connectionId || room.hostedConnectionId || '')
  const roomId = String(room.roomId || '')
  const eventId = String(entry.id || entry.eventId || '')

  if (!/^att_[0-9a-f]{32}$/.test(attachmentId) || !connectionId || !roomId || !eventId) {
    throw new Error('This attachment is not available from its source.')
  }

  const cacheKey = `${connectionId}:${roomId}:${eventId}:${attachmentId}`
  const cached = groupAttachmentDataCache.get(cacheKey)

  if (cached) {
    return cached
  }

  const route = (await hostedDefaultRoutes()).find(candidate => candidate.connectionId === connectionId)

  if (!route) {
    throw new Error('Reconnect the Group Chat gateway to open this attachment.')
  }

  const result = await requestHostedConnection<Record<string, unknown>>(route, 'groups.attachment.read', {
    purpose: 'viewer',
    room_id: roomId,
    event_id: eventId,
    attachment_id: attachmentId
  })
  const stored = record(result.attachment)

  if (
    stored?.attachment_id !== attachmentId ||
    stored?.kind !== attachment.kind ||
    stored?.name !== attachment.name ||
    stored?.size !== attachment.size ||
    stored?.mime !== attachment.mime ||
    typeof result.content_base64 !== 'string'
  ) {
    throw new Error('This attachment failed its integrity check.')
  }

  const dataUrl = `data:${attachment.mime};base64,${result.content_base64}`

  cacheGroupAttachmentData(cacheKey, dataUrl)

  return dataUrl
}

export async function sendHostedGroupChat(
  group: string,
  message: GroupMessage,
  thread: string,
  attachments: Attachment[] = []
) {
  const room = $groupChats.get()[group]

  if (!room?.roomId || !groupChatHostedGateway(room)) {
    throw new Error(botsText().group.hostRouteMissing)
  }

  const route = await hostedRouteForRoom(room)
  const connectionId = String(route?.connectionId || room.hostedConnectionId || '')

  if (!route || !connectionId) {
    throw new Error(botsText().group.hostRouteMissing)
  }

  const manifests = await stageHostedAttachments(route, String(room.roomId), String(message.id || ''), attachments)

  return enqueueHostedRoomCommand({
    commandId: String(message.id || ''),
    kind: 'send',
    roomId: room.roomId,
    authorityId: groupChatHostedGateway(room),
    connectionId,
    payload: {
      text: message.text || '',
      thread_id: thread,
      ...(manifests.length
        ? {
            attachments: manifests
          }
        : {})
    }
  })
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

export async function renameHostedGroupChat(group: string, name: string) {
  const room = $groupChats.get()[group]

  if (!room?.roomId || !groupChatHostedGateway(room)) {
    return true
  }

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

  await enqueueHostedRoomCommand({
    commandId: crypto.randomUUID(),
    kind: 'disband',
    roomId: room.roomId,
    authorityId: groupChatHostedGateway(room),
    connectionId: route.connectionId,
    payload: {}
  })

  return true
}

export async function startHostedRoomRuntime(storage: PluginContext['storage'], hooks: HostedRoomRuntimeHooks = {}) {
  hostedRoomStorage = storage
  hostedRoomHooks = hooks
  hostedRoomSyncDisposed = false
  let persisted: unknown = null

  try {
    persisted = await storage?.get?.(HOSTED_ROOM_OUTBOX_KEY, null)
  } catch {
    /* an empty outbox is the safe fallback */
  }

  try {
    $hostedRoomOutbox.set(createHostedRoomOutbox(persisted))
  } catch {
    $hostedRoomOutbox.set(createHostedRoomOutbox())
  }

  await refreshHostedRooms().catch(() => undefined)
  await dispatchHostedRoomOutbox().catch(() => undefined)
  scheduleHostedRoomSync()
}

export function stopHostedRoomRuntime() {
  hostedRoomSyncDisposed = true
  hostedRoomStorage = null
  hostedRoomHooks = {}
  hostedAuthorityRoutes.clear()
  groupAttachmentDataCache.clear()
  groupAttachmentDataCacheChars = 0

  if (hostedRoomSyncTimer) {
    clearTimeout(hostedRoomSyncTimer)
  }

  hostedRoomSyncTimer = null
}

/** Test-only lifecycle reset through the same public stop door. */
export function resetHostedRoomRuntimeForTests() {
  stopHostedRoomRuntime()
  hostedRoomSyncRunning = false
  hostedOutboxDispatching = false
  $hostedRoomCapabilities.set({})
  $hostedRoomOutbox.set(createHostedRoomOutbox())
}

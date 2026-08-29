import { host } from '@hermes/plugin-sdk'
import type { PluginContext } from '@hermes/plugin-sdk'

import { $botMeta, $lastRoster, botRosterKey, botSourceStatus, cachedUnionRoster } from './data'
import { runDesktopRoomCommandCycle } from './desktop-room-command-client'
import type {
  DesktopRoomCommand,
  DesktopRoomDescriptor
} from './desktop-room-command-client'
import { $groupChats, boundedDesktopCommandSettled, groupChatHostedGateway, scheduleGroupChatServerSync, updateGroupChat } from './group-chat'
import {
  groupChatBotsFromDescriptors,
  groupChatMemberBots,
  resolveLegacyMemberDescriptor
} from './group-membership'
import { cancelGroupThreadForLeaseLoss, sendToGroupChat, stopGroupThread } from './group-rounds'
import { requestForBot } from './routing'
import type { Attachment, GroupChat, GroupMember, ProfileRoute, RosterRow } from './types'

const DESKTOP_ROOM_COMMAND_CONSUMER_KEY = 'desktop-room-command-consumer-v1'
const DESKTOP_ROOM_COMMAND_INTERVAL_MS = 60_000
const DESKTOP_ROOM_COMMAND_PUSH_DEBOUNCE_MS = 250

let desktopRoomStorage: null | PluginContext['storage'] = null
let desktopRoomCommandConsumerId = ''
let desktopRoomCommandConsumerPromise: null | Promise<string> = null
let desktopRoomCommandTimer: null | ReturnType<typeof setInterval> = null
let desktopRoomCommandPushTimer: null | ReturnType<typeof setTimeout> = null
let desktopRoomCommandPushUnsub: null | (() => void) = null
let desktopRoomCommandRunning = false
let desktopRoomStopRunning = false
let desktopRoomCommandDisposed = true
let desktopRoomCommandRerun = false
let desktopRoomStopRerun = false

const desktopRoomCommandPendingConnections = new Set<string>()
const desktopRoomStopPendingConnections = new Set<string>()
const desktopRoomCommandRetentions = new Map<string, () => void>()
const activeDesktopRoomCommands = new Map<string, { commandId: string; controller: AbortController }>()

function mintConsumerId() {
  if (globalThis.crypto && typeof globalThis.crypto.randomUUID === 'function') {
    return `desktop:${globalThis.crypto.randomUUID()}`
  }

  return `desktop:${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`
}

function mintAuthorityToken() {
  if (globalThis.crypto && typeof globalThis.crypto.randomUUID === 'function') {
    return `authority:${globalThis.crypto.randomUUID()}`
  }

  return `authority:${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`
}

async function authorityHash(token: string) {
  if (!token || !globalThis.crypto?.subtle || typeof TextEncoder === 'undefined') {
    return null
  }

  const digest = await globalThis.crypto.subtle.digest('SHA-256', new TextEncoder().encode(token))

  return [...new Uint8Array(digest)].map(value => value.toString(16).padStart(2, '0')).join('')
}

async function ensureDesktopRoomCommandConsumerId() {
  if (desktopRoomCommandConsumerId) {
    return desktopRoomCommandConsumerId
  }

  if (desktopRoomCommandConsumerPromise) {
    return desktopRoomCommandConsumerPromise
  }

  desktopRoomCommandConsumerPromise = (async () => {
    let stored = ''

    try {
      // @ts-expect-error PluginStorage.get requires a fallback in the SDK type; existing plugin storage accepts omission.
      stored = String((await desktopRoomStorage?.get?.(DESKTOP_ROOM_COMMAND_CONSUMER_KEY)) || '').trim()
    } catch {
      stored = ''
    }

    if (stored) {
      desktopRoomCommandConsumerId = stored

      return stored
    }

    desktopRoomCommandConsumerId = mintConsumerId()

    try {
      await desktopRoomStorage?.set?.(DESKTOP_ROOM_COMMAND_CONSUMER_KEY, desktopRoomCommandConsumerId)
    } catch {
      /* this window still owns rooms it creates */
    }

    return desktopRoomCommandConsumerId
  })().finally(() => {
    desktopRoomCommandConsumerPromise = null
  })

  return desktopRoomCommandConsumerPromise
}

export async function prepareDesktopRoomAuthority() {
  const desktopCoordinatorId = await ensureDesktopRoomCommandConsumerId()
  const desktopAuthorityToken = mintAuthorityToken()
  const desktopAuthorityHash = await authorityHash(desktopAuthorityToken)

  if (!desktopAuthorityHash) {
    throw new Error('Secure Group Chat control is unavailable in this Desktop build.')
  }

  return {
    desktopAuthorityHash,
    desktopAuthorityToken,
    desktopCoordinatorId
  }
}

/** Make an explicitly opened classic Group Chat controllable from messaging.
 * A projected hash without the local token belongs to another Desktop and is
 * never replaced. */
export async function ensureDesktopRoomAuthority(group: string) {
  const room = $groupChats.get()[group]

  if (!room || groupChatHostedGateway(room) || room.tombstone) {
    return false
  }

  if (room.desktopAuthorityHash && !room.desktopAuthorityToken) {
    return false
  }

  const consumerId = await ensureDesktopRoomCommandConsumerId()
  const sameOwner = String(room.desktopCoordinatorId || '') === consumerId
  const token = sameOwner && room.desktopAuthorityToken ? room.desktopAuthorityToken : mintAuthorityToken()
  const hash = await authorityHash(token)

  if (!hash) {
    return false
  }

  updateGroupChat(group, current => ({
    ...current,
    desktopAuthorityHash: hash,
    desktopAuthorityToken: token,
    desktopCoordinatorId: consumerId
  }))

  return true
}

function hasDesktopRoomExecutionEvidence(room: GroupChat) {
  return Object.keys(room?.sessions || {}).length > 0 || Object.keys(room?.sessionOwners || {}).length > 0
}

async function adoptExistingDesktopRooms() {
  const consumerId = await ensureDesktopRoomCommandConsumerId()
  let changed = false

  for (const [name, room] of Object.entries($groupChats.get())) {
    if (groupChatHostedGateway(room) || room.tombstone) {
      continue
    }

    let coordinatorId = String(room.desktopCoordinatorId || '')

    if (!coordinatorId && hasDesktopRoomExecutionEvidence(room)) {
      coordinatorId = consumerId
    }

    if (coordinatorId !== consumerId) {
      continue
    }

    const token = String(room.desktopAuthorityToken || '') || mintAuthorityToken()
    const hash = await authorityHash(token)

    if (!hash) {
      continue
    }

    if (
      room.desktopCoordinatorId !== coordinatorId ||
      room.desktopAuthorityToken !== token ||
      room.desktopAuthorityHash !== hash
    ) {
      changed = true
      updateGroupChat(
        name,
        current => ({
          ...current,
          desktopAuthorityHash: hash,
          desktopAuthorityToken: token,
          desktopCoordinatorId: coordinatorId
        }),
        {
          sync: false
        }
      )
    }
  }

  if (changed) {
    scheduleGroupChatServerSync($groupChats.get())
  }
}

function desktopRoomEntry(roomId: string, descriptors: DesktopRoomDescriptor[]) {
  const descriptor = descriptors.find(room => room.roomId === roomId)

  if (!descriptor) {
    return null
  }

  const room = $groupChats.get()[descriptor.name]

  return room && !groupChatHostedGateway(room) ? ([descriptor.name, room] as const) : null
}

function desktopCommandEligibleRooms(connectionIds: string[]) {
  const known = new Set(connectionIds.map(value => String(value || '')))
  const coordinator = String(desktopRoomCommandConsumerId || '')

  return Object.fromEntries(
    Object.entries($groupChats.get()).filter(([, room]) => {
      if (groupChatHostedGateway(room) || room?.tombstone) {
        return false
      }

      if (!coordinator || String(room?.desktopCoordinatorId || '') !== coordinator) {
        return false
      }

      if (!room.desktopAuthorityToken || !room.desktopAuthorityHash) {
        return false
      }

      const members = Array.isArray(room?.members) ? room.members : []

      if (!members.length) {
        return false
      }

      return members.every(member => {
        if (member?.sourceMissing) {
          return false
        }

        const connectionId = String(member?.route?.connectionId || member?.connectionId || '')

        return !connectionId || known.has(connectionId)
      })
    })
  )
}

function retryableDesktopRoomCommand(message: string) {
  return Object.assign(new Error(message), {
    retryable: true
  })
}

async function waitForDesktopRoomCommandSettlement(group: string, commandId: string, signal: AbortSignal | null) {
  while (!desktopRoomCommandDisposed) {
    if (signal?.aborted) {
      throw retryableDesktopRoomCommand('The command moved to another Desktop.')
    }

    const room = $groupChats.get()[group]

    if (!room) {
      throw new Error('This Group Chat is no longer available.')
    }

    if (room.desktopCommandSettled?.[commandId]) {
      return true
    }

    if (!room.running) {
      return false
    }

    await new Promise(resolve => setTimeout(resolve, 250))
  }

  throw retryableDesktopRoomCommand('Desktop closed before the Group Chat settled.')
}

interface CommandExecutionContext {
  consumerId: string
  request: (method: string, params: Record<string, unknown>) => Promise<unknown>
  route: ProfileRoute
  signal: AbortSignal | null
}

async function executeDesktopRoomCommand(
  command: DesktopRoomCommand,
  descriptors: DesktopRoomDescriptor[],
  { signal, request, consumerId, route }: CommandExecutionContext
) {
  const assertLiveLease = () => {
    if (signal?.aborted) {
      throw retryableDesktopRoomCommand('The command moved to another Desktop.')
    }
  }

  assertLiveLease()

  const roomId = String(command.room_id || '')
  const entry = desktopRoomEntry(roomId, descriptors)

  if (!entry) {
    throw new Error('This Group Chat is no longer available on this Desktop.')
  }

  const [group, room] = entry
  const cached = cachedUnionRoster()
  const roster = Array.isArray(cached?.profiles) ? cached.profiles : $lastRoster.get()
  let members = groupChatMemberBots(group, roster, $botMeta.get())

  if (command.action === 'send') {
    const payload = command.payload || {}
    const frozenRecipients =
      Array.isArray(payload.recipients) && payload.recipients.length ? (payload.recipients as GroupMember[]) : room.members || []

    members = groupChatBotsFromDescriptors(frozenRecipients, roster)

    const liveRoster = (Array.isArray(roster) ? roster : []).filter(bot => !bot?.ghost)
    const completeRoster = frozenRecipients.every(descriptor => {
      const resolved = resolveLegacyMemberDescriptor(descriptor as RosterRow, liveRoster)

      return liveRoster.some(bot => botRosterKey(bot) === botRosterKey(resolved))
    })

    if (!members.length || !completeRoster || members.some(member => !botSourceStatus(member).available)) {
      throw retryableDesktopRoomCommand('Waiting for every Bot in this Group Chat to reconnect.')
    }

    try {
      await Promise.all(members.map(member => requestForBot(member, 'profiles.describe', { name: member.name })))
    } catch {
      throw retryableDesktopRoomCommand('Waiting for every Bot in this Group Chat to reconnect.')
    }

    assertLiveLease()

    const message = String(payload.message || '').trim()
    const manifest = Array.isArray(payload.attachments) ? payload.attachments : []

    if (!message && !manifest.length) {
      throw new Error('The Group Chat message is empty.')
    }

    const commandId = String(command.command_id || '')
    const localAbort = new AbortController()
    const onLeaseAbort = () => localAbort.abort('lease-lost')

    signal?.addEventListener('abort', onLeaseAbort, {
      once: true
    })

    if (signal?.aborted) {
      localAbort.abort('lease-lost')
    }

    activeDesktopRoomCommands.set(roomId, {
      commandId,
      controller: localAbort
    })

    const transientAttachments: Attachment[] = []

    try {
      for (const raw of manifest) {
        if (localAbort.signal.aborted) {
          throw retryableDesktopRoomCommand('The command moved to another Desktop.')
        }

        const attachment = raw as Attachment
        const result = (await request('groups.attachment.read', {
          room_id: roomId,
          attachment_id: attachment.attachment_id,
          purpose: 'desktop-command',
          event_id: commandId,
          consumer_id: consumerId,
          lease_token: String(command.lease_token || ''),
          authority_token: String(room.desktopAuthorityToken || '')
        })) as Record<string, unknown>

        if (localAbort.signal.aborted) {
          throw retryableDesktopRoomCommand('The command moved to another Desktop.')
        }

        const stored = result.attachment as Record<string, unknown> | undefined

        if (
          !stored ||
          stored.attachment_id !== attachment.attachment_id ||
          stored.kind !== attachment.kind ||
          stored.name !== attachment.name ||
          stored.size !== attachment.size ||
          stored.mime !== attachment.mime ||
          typeof result.content_base64 !== 'string'
        ) {
          throw new Error('A messaging attachment failed its integrity check.')
        }

        transientAttachments.push({
          ...attachment,
          connectionId: String(route.connectionId || ''),
          data: `data:${attachment.mime};base64,${result.content_base64}`
        })
      }

      while (!desktopRoomCommandDisposed) {
        if (localAbort.signal.aborted) {
          throw retryableDesktopRoomCommand('The command moved to another Desktop.')
        }

        const thread = await Promise.resolve(
          sendToGroupChat(group, members, message, null, transientAttachments, {
            entryId: commandId,
            userName: String(payload.actor_display_name || 'Messaging'),
            omitAttachmentDataFromLog: true
          })
        )

        if (!thread) {
          throw new Error('The Group Chat could not accept this message.')
        }

        if (await waitForDesktopRoomCommandSettlement(group, commandId, localAbort.signal)) {
          return {
            room_name: group,
            thread_id: thread
          }
        }
      }
    } catch (error) {
      if (localAbort.signal.aborted) {
        if (localAbort.signal.reason === 'room-stop') {
          return {
            room_name: group,
            stopped: true
          }
        }

        await cancelGroupThreadForLeaseLoss(group, members)
        throw retryableDesktopRoomCommand('The command moved to another Desktop.')
      }

      throw error
    } finally {
      signal?.removeEventListener('abort', onLeaseAbort)

      if (activeDesktopRoomCommands.get(roomId)?.controller === localAbort) {
        activeDesktopRoomCommands.delete(roomId)
      }
    }

    throw retryableDesktopRoomCommand('Desktop closed before the Group Chat settled.')
  }

  if (command.action === 'stop') {
    const commandId = String(command.command_id || '')

    if (room.desktopCommandSettled?.[commandId]) {
      return {
        room_name: group,
        stopped: true
      }
    }

    const latestThread = [...room.log].reverse().find(item => item?.thread)?.thread || null
    const active = activeDesktopRoomCommands.get(roomId)

    active?.controller.abort('room-stop')
    await stopGroupThread(group, latestThread, members)
    updateGroupChat(group, current => ({
      ...current,
      desktopCommandSettled: boundedDesktopCommandSettled({
        ...(current.desktopCommandSettled || {}),
        ...(active?.commandId
          ? {
              [active.commandId]: Date.now()
            }
          : {}),
        [commandId]: Date.now()
      })
    }))

    return {
      room_name: group,
      stopped: true
    }
  }

  throw new Error('Unsupported Group Chat command.')
}

async function desktopRoomCommandConnections() {
  const byConnection = new Map<string, ProfileRoute>()

  if (typeof host.profileRoutes === 'function') {
    try {
      const routes = await host.profileRoutes()

      for (const route of Array.isArray(routes) ? routes : []) {
        const profile = String(route?.targetProfile || route?.profile || '')
        const connectionId = String(route?.connectionId || '')

        if (profile === 'default' && !byConnection.has(connectionId)) {
          byConnection.set(connectionId, route as ProfileRoute)
        }
      }
    } catch {
      /* the active route below remains a compatibility fallback */
    }
  }

  const active = String(host.state.connectionId?.get?.() || host.activeConnectionId?.() || '')

  if (!byConnection.has(active)) {
    byConnection.set(active, {
      connectionId: active,
      mode: 'remote',
      profile: 'default',
      targetProfile: 'default'
    })
  }

  return [...byConnection.entries()].map(([id, route]) => ({
    id,
    route
  }))
}

function syncDesktopRoomCommandRetention(connections: Array<{ id: string; route: ProfileRoute }>) {
  if (typeof host.retainProfileSocket !== 'function') {
    return
  }

  const live = new Set(connections.map(connection => connection.id))

  for (const [id, release] of [...desktopRoomCommandRetentions]) {
    if (!live.has(id)) {
      desktopRoomCommandRetentions.delete(id)

      try {
        release()
      } catch {
        /* teardown stays best-effort */
      }
    }
  }

  if (desktopRoomCommandDisposed) {
    return
  }

  for (const connection of connections) {
    if (!desktopRoomCommandRetentions.has(connection.id)) {
      desktopRoomCommandRetentions.set(connection.id, host.retainProfileSocket(connection.route))
    }
  }
}

function releaseDesktopRoomCommandRetention() {
  for (const release of desktopRoomCommandRetentions.values()) {
    try {
      release()
    } catch {
      /* teardown stays best-effort */
    }
  }

  desktopRoomCommandRetentions.clear()
}

function scheduleDesktopRoomCommandPump(connectionId: null | string = null) {
  if (desktopRoomCommandDisposed || typeof setTimeout !== 'function') {
    return
  }

  const key = connectionId === null ? '*' : String(connectionId)

  desktopRoomCommandPendingConnections.add(key)
  desktopRoomStopPendingConnections.add(key)

  if (desktopRoomCommandPushTimer !== null) {
    return
  }

  desktopRoomCommandPushTimer = setTimeout(() => {
    desktopRoomCommandPushTimer = null

    const pending = new Set(desktopRoomCommandPendingConnections)
    const stopPending = new Set(desktopRoomStopPendingConnections)

    desktopRoomCommandPendingConnections.clear()
    desktopRoomStopPendingConnections.clear()
    void runDesktopRoomCommandPump(pending.has('*') ? null : pending)
    void runDesktopRoomStopPump(stopPending.has('*') ? null : stopPending)
  }, DESKTOP_ROOM_COMMAND_PUSH_DEBOUNCE_MS)
}

async function runDesktopRoomCommandPump(targetConnectionIds: null | Set<string> = null) {
  if (desktopRoomCommandDisposed) {
    return
  }

  if (desktopRoomCommandRunning) {
    desktopRoomCommandRerun = true

    if (targetConnectionIds === null) {
      desktopRoomCommandPendingConnections.add('*')
    } else {
      targetConnectionIds.forEach(id => desktopRoomCommandPendingConnections.add(String(id)))
    }

    return
  }

  desktopRoomCommandRunning = true

  try {
    await ensureDesktopRoomCommandConsumerId()

    const connections = await desktopRoomCommandConnections()
    const rooms = desktopCommandEligibleRooms(connections.map(connection => connection.id))

    if (!Object.keys(rooms).length) {
      syncDesktopRoomCommandRetention([])

      return
    }

    syncDesktopRoomCommandRetention(connections)

    const selected =
      targetConnectionIds === null
        ? connections
        : connections.filter(connection => targetConnectionIds.has(connection.id))

    await runDesktopRoomCommandCycle({
      routes: selected.map(connection => connection.route),
      consumerId: desktopRoomCommandConsumerId,
      rooms,
      request: (route, method, params) => host.requestProfile(route, method, params),
      execute: executeDesktopRoomCommand,
      actions: ['send'],
      shouldContinue: () => !desktopRoomCommandDisposed
    })
  } catch {
    // A reconnect or older backend leaves durable commands pending.
  } finally {
    desktopRoomCommandRunning = false

    if (desktopRoomCommandRerun && !desktopRoomCommandDisposed) {
      desktopRoomCommandRerun = false

      const pending = [...desktopRoomCommandPendingConnections]

      desktopRoomCommandPendingConnections.clear()

      if (!pending.length || pending.includes('*')) {
        scheduleDesktopRoomCommandPump()
      } else {
        pending.forEach(connectionId => scheduleDesktopRoomCommandPump(connectionId))
      }
    }
  }
}

async function runDesktopRoomStopPump(targetConnectionIds: null | Set<string> = null) {
  if (desktopRoomCommandDisposed) {
    return
  }

  if (desktopRoomStopRunning) {
    desktopRoomStopRerun = true

    if (targetConnectionIds === null) {
      desktopRoomStopPendingConnections.add('*')
    } else {
      targetConnectionIds.forEach(id => desktopRoomStopPendingConnections.add(String(id)))
    }

    return
  }

  desktopRoomStopRunning = true

  try {
    await ensureDesktopRoomCommandConsumerId()

    const connections = await desktopRoomCommandConnections()
    const rooms = desktopCommandEligibleRooms(connections.map(connection => connection.id))
    const selected =
      targetConnectionIds === null
        ? connections
        : connections.filter(connection => targetConnectionIds.has(connection.id))

    if (!Object.keys(rooms).length) {
      return
    }

    await runDesktopRoomCommandCycle({
      routes: selected.map(connection => connection.route),
      consumerId: desktopRoomCommandConsumerId,
      rooms,
      request: (route, method, params) => host.requestProfile(route, method, params),
      execute: executeDesktopRoomCommand,
      actions: ['stop'],
      shouldContinue: () => !desktopRoomCommandDisposed
    })
  } catch {
    // A reconnect or older backend leaves durable Stops pending.
  } finally {
    desktopRoomStopRunning = false

    if (desktopRoomStopRerun && !desktopRoomCommandDisposed) {
      desktopRoomStopRerun = false

      const pending = [...desktopRoomStopPendingConnections]

      desktopRoomStopPendingConnections.clear()
      void runDesktopRoomStopPump(!pending.length || pending.includes('*') ? null : new Set(pending))
    }
  }
}

export async function startDesktopRoomCommandRuntime(storage: PluginContext['storage']) {
  desktopRoomStorage = storage
  desktopRoomCommandDisposed = false

  await adoptExistingDesktopRooms()

  if (desktopRoomCommandDisposed || desktopRoomStorage !== storage) {
    return
  }

  if (typeof setInterval !== 'function' || desktopRoomCommandTimer !== null) {
    return
  }

  void runDesktopRoomCommandPump()
  void runDesktopRoomStopPump()
  desktopRoomCommandTimer = setInterval(() => {
    void runDesktopRoomCommandPump()
    void runDesktopRoomStopPump()
  }, DESKTOP_ROOM_COMMAND_INTERVAL_MS)

  if (desktopRoomCommandPushUnsub === null && typeof host.onEvent === 'function') {
    desktopRoomCommandPushUnsub = host.onEvent('desktop_rooms.commands.pending', event =>
      scheduleDesktopRoomCommandPump(event?.connectionId ?? null)
    )
  }
}

export function stopDesktopRoomCommandRuntime() {
  desktopRoomCommandDisposed = true
  desktopRoomCommandRerun = false
  desktopRoomStopRerun = false
  desktopRoomCommandPendingConnections.clear()
  desktopRoomStopPendingConnections.clear()
  releaseDesktopRoomCommandRetention()

  if (desktopRoomCommandTimer !== null) {
    clearInterval(desktopRoomCommandTimer)
    desktopRoomCommandTimer = null
  }

  if (desktopRoomCommandPushTimer !== null) {
    clearTimeout(desktopRoomCommandPushTimer)
    desktopRoomCommandPushTimer = null
  }

  if (desktopRoomCommandPushUnsub !== null) {
    try {
      desktopRoomCommandPushUnsub()
    } catch {
      /* older host disposer */
    }

    desktopRoomCommandPushUnsub = null
  }

  desktopRoomStorage = null
}

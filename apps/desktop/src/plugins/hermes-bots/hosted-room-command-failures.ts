/** Surface-specific command outcomes: transport failure is not non-admission. */
import { $groupChats, updateGroupChat } from './group-chat'
import type { HostedRoomCommand, HostedRoomOutbox, HostedRoomOutboxAction } from './hosted-room-client'
import { botsText } from './i18n'

const MAX_ATTEMPTS = 5
const REFUSAL_CODES = new Set([-32601, -32602, 4000])
const WORKER_UNAVAILABLE = new Set([4115, 4123])

function record(value: unknown): Record<string, unknown> | null {
  return value && typeof value === 'object' && !Array.isArray(value) ? (value as Record<string, unknown>) : null
}

export function hostedRoomCommandFailure(error: unknown, command: HostedRoomCommand): HostedRoomOutboxAction {
  const candidate = record(error)
  const nested = record(candidate?.error)
  const code = Number(candidate?.code ?? nested?.code)
  const commandId = command.commandId

  // These are dispatcher refusals or the service-unavailable return BEFORE
  // the handler runs. Generic 5xxx exceptions can follow committed effects.
  if (REFUSAL_CODES.has(code) || (WORKER_UNAVAILABLE.has(code) && command.attempts >= MAX_ATTEMPTS)) {
    return { type: 'terminal-failure', commandId, failureCode: String(code) }
  }

  if (WORKER_UNAVAILABLE.has(code)) {
    return { type: 'transient-failure', commandId, failureCode: String(code) }
  }

  const replayable = ['send', 'rename', 'create'].includes(command.kind)

  if (replayable && command.attempts < MAX_ATTEMPTS) {
    return { type: 'transient-failure', commandId }
  }

  return { type: 'unknown-outcome', commandId, failureCode: 'outcome-unknown' }
}

export function failedHostedRoomCommand(outbox: HostedRoomOutbox, roomId: string) {
  return outbox.commands.find(command => command.roomId === roomId && ['failed', 'unknown'].includes(command.status))
}

export function hostedCommandCanRetry(command: HostedRoomCommand) {
  return command.status === 'failed' || (command.status === 'unknown' && command.kind !== 'retry')
}

export function hostedCommandStatus(command: HostedRoomCommand, canStop?: boolean) {
  const unresolved = command.status === 'unknown'
  const pending = command.status === 'pending' || command.status === 'in-flight'

  return {
    canRetry: hostedCommandCanRetry(command),
    canStop,
    label: botsText().group.hostedNeedsAttention,
    ...(hostedCommandCanRetry(command) ? { retryCommandId: command.commandId } : {}),
    ...(command.status === 'failed' ? { dismissCommandId: command.commandId } : {}),
    state: unresolved ? 'indeterminate' : pending ? 'queued' : 'failed'
  }
}

export function hostedCommandIssue(command: HostedRoomCommand) {
  if (command.status === 'unknown') {
    return 'The outcome is unknown. Your intent is saved. Check the owning device before taking further action; an unconfirmed Retry cannot safely be sent again.'
  }

  return command.status === 'failed' ? botsText().group.hostRejectedCommand : 'This action is saved and pending on the owning device. It has not been confirmed.'
}

export function surfaceHostedRoomCommandFailure(command: HostedRoomCommand) {
  const roomName = Object.entries($groupChats.get()).find(([, room]) => room.roomId === command.roomId)?.[0]

  if (!roomName) {
    return
  }

  updateGroupChat(
    roomName,
    room => ({
      ...room,
      hostedStatus: hostedCommandStatus(command, room.hostedStatus?.canStop),
      continuityIssue: hostedCommandIssue(command)
    }),
    { sync: false }
  )
}

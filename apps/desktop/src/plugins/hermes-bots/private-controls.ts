/**
 * Desktop/shared shape of the public A3 control contracts.
 *
 * Permission `3b0d88e044f4a689def4ae1fee45186aafb51e11` and Messaging product
 * `8f5338e6a7699c615112e2e2ef50136f31f8e466` (recipe head `eea4a0c96d`).
 * Plugins keep a behavior-matched copy because they cannot import this package.
 * Stop, approval grant/revoke, and the approval choice are different methods.
 * The displayed selector is `pa-` plus 64 hex digits. A list position is not a selector.
 */

const APPROVAL_SELECTOR = /^pa-[0-9a-f]{64}$/
const ROOM_READ_BINDING = /^mrr-[0-9a-f]{32}$/
const CONTROL_BINDING = /^mrc-[0-9a-f]{32}$/
const LIST_POSITION = /^[0-9]+$/
const RECIPIENT_KEYS = ['chat_id', 'platform', 'runtime_profile', 'scope_id', 'thread_id', 'transport_profile', 'user_id'] as const

export type ControlScope = 'stop' | 'approval'
export type ControlVerb = 'grant' | 'revoke'
export type ApprovalChoice = 'once' | 'deny'

export interface MessagingRecipient {
  platform: string
  user_id: string
  chat_id: string
  thread_id: string | null
  scope_id: string | null
  transport_profile: string
  runtime_profile: string
}

export interface DisplayedApproval {
  selector: string
  member_id: string
  task_id: string
  request_id: string
  execution_generation: number
}

export interface ControlConsentInput {
  requestId: string
  recipient: MessagingRecipient
  roomId: string
  roomReadBindingId: string
  roomReadGeneration: number
  expectedGeneration: number
  bindingId?: string
}

export function isApprovalSelector(value: unknown): value is string {
  return typeof value === 'string' && APPROVAL_SELECTOR.test(value)
}

function text(value: unknown, maximum = 128): string {
  if (typeof value !== 'string' || value.length < 1 || value.length > maximum || value !== value.trim()) {
    throw new Error('Control field is not an exact string')
  }

  return value
}

function generation(value: unknown, minimum: number): number {
  if (typeof value !== 'number' || !Number.isSafeInteger(value) || value < minimum) {
    throw new Error('Control generation is not an exact integer')
  }

  return value
}

export function exactDisplayedApproval<T extends DisplayedApproval>(actions: readonly T[], selector: string): T {
  if (!isApprovalSelector(selector)) {
    throw new Error('Approval selector must be the displayed pa- token')
  }

  const selected = actions.filter(action => action.selector === selector)

  if (selected.length !== 1) {
    throw new Error('Approval selector does not match one displayed request')
  }

  const action = selected[0]
  text(action.member_id, 512)
  text(action.task_id, 512)
  text(action.request_id, 512)
  generation(action.execution_generation, 1)

  return action
}

export function groupsApproveParams(roomId: string, action: DisplayedApproval, choice: ApprovalChoice): {
  room_id: string
  member_id: string
  task_id: string
  execution_generation: number
  request_id: string
  choice: ApprovalChoice
} {
  const exact = exactDisplayedApproval([action], action.selector)

  if (choice !== 'once' && choice !== 'deny') {
    throw new Error('Approval choice must be once or deny')
  }

  return {
    room_id: text(roomId),
    member_id: exact.member_id,
    task_id: exact.task_id,
    execution_generation: exact.execution_generation,
    request_id: exact.request_id,
    choice
  }
}

export function groupsStopParams(roomId: string, cancelId: string): { room_id: string; cancel_id: string } {
  const cancel = text(cancelId, 256)

  if (isApprovalSelector(cancel) || LIST_POSITION.test(cancel)) {
    throw new Error('Stop cancel id is not an approval selector or list position')
  }

  return { room_id: text(roomId), cancel_id: cancel }
}

export function controlMethod(scope: ControlScope, verb: ControlVerb): string {
  if (scope !== 'stop' && scope !== 'approval') {
    throw new Error('Control scope must be stop or approval')
  }

  if (verb !== 'grant' && verb !== 'revoke') {
    throw new Error('Control verb must be grant or revoke')
  }

  return `groups.messaging.room.${scope}.${verb}`
}

function messagingRecipient(value: MessagingRecipient): MessagingRecipient {
  if (value === null || typeof value !== 'object') {
    throw new Error('Recipient is not the persisted messaging binding')
  }

  const keys = Object.keys(value).sort()

  if (keys.length !== RECIPIENT_KEYS.length || keys.some((key, index) => key !== RECIPIENT_KEYS[index])) {
    throw new Error('Recipient is not the persisted messaging binding')
  }

  const nullable = (field: string | null) => field === null ? null : text(field, 256)
  const result: MessagingRecipient = {
    platform: text(value.platform, 64),
    user_id: text(value.user_id, 256),
    chat_id: text(value.chat_id, 256),
    thread_id: nullable(value.thread_id),
    scope_id: nullable(value.scope_id),
    transport_profile: text(value.transport_profile, 64),
    runtime_profile: text(value.runtime_profile, 64)
  }

  if (result.runtime_profile !== 'default') {
    throw new Error('Recipient runtime profile must be default')
  }

  return result
}

function commonConsent(input: ControlConsentInput) {
  const readId = text(input.roomReadBindingId, 36)

  if (!ROOM_READ_BINDING.test(readId)) {
    throw new Error('Room read binding id is not an mrr- token')
  }

  return {
    request_id: text(input.requestId),
    recipient: messagingRecipient(input.recipient),
    room_id: text(input.roomId),
    room_read_binding_id: readId,
    room_read_generation: generation(input.roomReadGeneration, 1),
    expected_generation: generation(input.expectedGeneration, 0)
  }
}

export function controlGrantParams(input: ControlConsentInput): ReturnType<typeof commonConsent> {
  return commonConsent(input)
}

export function controlRevokeParams(input: ControlConsentInput): ReturnType<typeof commonConsent> & { binding_id: string } {
  const binding = text(input.bindingId, 36)

  if (!CONTROL_BINDING.test(binding)) {
    throw new Error('Control binding id is not an mrc- token')
  }

  return { ...commonConsent(input), binding_id: binding }
}

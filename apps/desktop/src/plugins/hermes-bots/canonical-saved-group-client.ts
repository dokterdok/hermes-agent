/** Read-only metadata projection of cf4/0242 recovery evidence. Never a room binding. */
import { withFilesDeadline } from './canonical-files-client'
import { canonicalGroupRequest } from './canonical-groups'
import type { CanonicalGroupRoute } from './canonical-groups'

export interface SavedGroupCopy {
  roomId: string
  name: string
  authority: { gatewayId: string; epoch: number }
  savedThrough: number
  advertisedLatest: number
  updatedAt: number
  ended: boolean
  status: 'saved' | 'needs_review' | 'retired'
}

export interface SavedGroupPage {
  copies: SavedGroupCopy[]
  nextRoomId: string | null
  targetGatewayId: string
}

export interface SavedGroupPreview {
  name: string
  updatedAt: number
  incomplete: boolean
  needsReview: boolean
  ended: boolean
  workUnknown: boolean
  reconciliationRequired: boolean
  taskCount?: number
  receiptCount?: number
}

function invalid(): never { throw new Error('Invalid saved-copy evidence') }

function record(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {return invalid()}

  return value as Record<string, unknown>
}

function literal(value: unknown): string {
  if (typeof value !== 'string' || !value.trim() || value.trim() !== value || value.length > 1024) {return invalid()}

  return value
}

function integer(value: unknown): number {
  if (typeof value !== 'number' || !Number.isSafeInteger(value) || value < 0) {return invalid()}

  return value
}

function timestamp(value: unknown): number {
  if (typeof value !== 'number' || !Number.isFinite(value) || value < 0 || !Number.isFinite(new Date(value * 1000).getTime())) {return invalid()}

  return value * 1000
}

function authority(value: unknown): SavedGroupCopy['authority'] {
  const data = record(value)
  const epoch = integer(data.epoch)

  if (epoch === 0) {return invalid()}

  return { gatewayId: literal(data.gateway_id), epoch }
}

function readonly(value: unknown, object: string) {
  const data = record(value)

  if (data.object !== object || data.execution_authorized !== false || data.accepted_tail !== 'unverified') {return invalid()}

  return data
}

// SQLite's UTF-8 binary key order follows code points, not UTF-16 surrogate order.
function compareIds(left: string, right: string) {
  const a = Array.from(left, char => char.codePointAt(0)!)
  const b = Array.from(right, char => char.codePointAt(0)!)

  for (let i = 0; i < Math.min(a.length, b.length); i++) {
    if (a[i] !== b[i]) {return a[i] - b[i]}
  }

  return a.length - b.length
}

export function parseSavedGroupPage(value: unknown, afterRoomId: string | null): SavedGroupPage {
  const data = readonly(value, 'hermes.group_recovery.copies')

  if (!Array.isArray(data.copies) || data.copies.length > 20) {return invalid()}
  let previous = afterRoomId

  const copies = data.copies.map(item => {
    const copy = record(item)
    const roomId = literal(copy.room_id)

    if ((previous !== null && compareIds(roomId, previous) <= 0) || typeof copy.group_ended !== 'boolean' ||
      typeof copy.copy_status !== 'string' || !['saved', 'needs_review', 'retired'].includes(copy.copy_status)) {return invalid()}

    previous = roomId

    return {
      roomId, name: literal(copy.name), authority: authority(copy.source_authority),
      savedThrough: integer(copy.saved_through_seq), advertisedLatest: integer(copy.advertised_latest_seq),
      updatedAt: timestamp(copy.copy_updated_at), ended: copy.group_ended,
      status: copy.copy_status as SavedGroupCopy['status']
    }
  })

  const nextRoomId = data.next_room_id === null ? null : literal(data.next_room_id)

  if (nextRoomId !== null && (nextRoomId !== copies.at(-1)?.roomId || nextRoomId === afterRoomId)) {return invalid()}

  return { copies, nextRoomId, targetGatewayId: literal(data.target_gateway_id) }
}

export function parseSavedGroupPreview(value: unknown, selected: SavedGroupCopy, holder: string): SavedGroupPreview {
  const data = readonly(value, 'hermes.group_recovery.preview')
  const source = authority(data.source_authority)

  if (data.room_id !== selected.roomId || data.target_gateway_id !== holder ||
    source.gatewayId !== selected.authority.gatewayId || source.epoch !== selected.authority.epoch ||
    typeof data.reconciliation_required !== 'boolean' || !Array.isArray(data.blockers) ||
    data.blockers.some(item => typeof item !== 'string')) {return invalid()}

  const work = record(data.work_records)
  const scopes = work.scopes === undefined ? [work] : work.scopes

  if (work.source_loss_safe !== false || !Array.isArray(scopes)) {return invalid()}

  const workUnknown = work.availability !== 'available' || scopes.some(item => {
    const scope = record(item)

    if (scope.source_loss_safe !== false) {return invalid()}

    return scope.availability !== 'available' || !Array.isArray(scope.incompleteness) || scope.incompleteness.length > 0
  })

  return {
    name: literal(data.name), updatedAt: timestamp(data.copy_updated_at),
    incomplete: integer(data.saved_through_seq) < integer(data.advertised_latest_seq),
    ended: selected.ended || data.blockers.includes('group_disbanded'),
    needsReview: data.blockers.length > 0, workUnknown,
    reconciliationRequired: data.reconciliation_required,
    ...(work.availability === 'available' ? { taskCount: integer(work.task_count), receiptCount: integer(work.receipt_count) } : {})
  }
}

export async function savedGroupHolder(route: CanonicalGroupRoute, signal: AbortSignal): Promise<string | null> {
  const data = record(await withFilesDeadline(canonicalGroupRequest<unknown>(route, 'groups.capabilities'), signal))
  const methods = data.methods

  if (!Array.isArray(methods) || !['groups.recovery.list', 'groups.recovery.prepare'].every(method => methods.includes(method))) {return null}

  return literal(data.authority_gateway_id)
}

export async function readSavedGroups(route: CanonicalGroupRoute, afterRoomId: string | null, signal: AbortSignal) {
  return parseSavedGroupPage(await withFilesDeadline(canonicalGroupRequest<unknown>(route, 'groups.recovery.list', {
    limit: 20, after_room_id: afterRoomId
  }), signal), afterRoomId)
}

export async function readSavedGroupPreview(route: CanonicalGroupRoute, selected: SavedGroupCopy, holder: string, signal: AbortSignal) {
  return parseSavedGroupPreview(await withFilesDeadline(canonicalGroupRequest<unknown>(route, 'groups.recovery.prepare', {
    room_id: selected.roomId
  }), signal), selected, holder)
}

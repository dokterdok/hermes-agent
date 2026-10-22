/** Client for a gateway room's Files catalog (`groups.attachment.list`): pages of exact versions. */
import { type CanonicalGroupBinding, canonicalGroupRequest } from './canonical-groups'

export const FILES_PAGE_SIZE = 8
export const FILES_MAX_QUERY = 255
const FILES_DEADLINE_MS = 10_000

/** One shared version: the message that shared it and its attachment id name it exactly. */
export interface CanonicalFile {
  eventId: string
  attachmentId: string
  seq: number
  index: number
  kind: string
  name: string
  mime: string
  size: number
  sharer: { kind: 'member' | 'user'; id: string; label: string }
  sharedAt: number
}

export interface CanonicalFilesPage {
  items: CanonicalFile[]
  nextCursor: null | string
  snapshotSeq: number
  authority: string
}

export type FilesFailure = 'access' | 'cursor' | 'error' | 'timeout' | 'unavailable' | 'verification'

export class CanonicalFilesError extends Error {
  constructor(readonly kind: FilesFailure) {
    super(kind)
  }
}

const REASONS: Record<string, FilesFailure> = {
  attachment_cursor_invalid: 'cursor',
  authority_conflict: 'access',
  permission_denied: 'access',
  profile_mismatch: 'access',
  room_history_expired: 'access',
  runtime_coordination_required: 'unavailable',
  storage_unavailable: 'unavailable'
}

/** The gateway names its refusal in `error.data.reason`; a server without the method answers -32601. */
export function filesFailure(error: unknown): FilesFailure {
  if (error instanceof CanonicalFilesError) {return error.kind}
  const failure = error as { code?: unknown; data?: { reason?: unknown } } | null
  const reason = String(failure?.data?.reason ?? '')

  if (Object.hasOwn(REASONS, reason)) {return REASONS[reason]}

  return failure?.code === -32601 ? 'unavailable' : 'error'
}

/** A retired read rejects with AbortError; a gateway that never answers fails with `timeout`. */
export async function withFilesDeadline<T>(task: Promise<T>, signal: AbortSignal): Promise<T> {
  let timer: ReturnType<typeof setTimeout> | undefined
  let abort: (() => void) | undefined

  try {
    return await Promise.race([task, new Promise<never>((_resolve, reject) => {
      abort = () => reject(new DOMException('Cancelled', 'AbortError'))

      if (signal.aborted) {abort()} else {signal.addEventListener('abort', abort, { once: true })}
      timer = setTimeout(() => reject(new CanonicalFilesError('timeout')), FILES_DEADLINE_MS)
    })])
  } finally {
    clearTimeout(timer)

    if (abort) {signal.removeEventListener('abort', abort)}
  }
}

/** Files needs the gateway's catalog: only a method list that names it offers Files. */
export function offersFilesCatalog(capabilities: unknown): boolean {
  const methods = capabilities && typeof capabilities === 'object'
    ? (capabilities as Record<string, unknown>).methods : undefined

  return Array.isArray(methods) && methods.includes('groups.attachment.list')
}

function record(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {}
}

const text = (value: unknown): value is string => typeof value === 'string' && value.length > 0
const whole = (value: unknown, minimum = 0): value is number => Number.isSafeInteger(value) && (value as number) >= minimum

/** A finite server timestamp can still exceed the dates the UI can format. */
const timestamp = (value: unknown): value is number =>
  typeof value === 'number' && Number.isFinite(new Date(value * 1000).getTime())

function parseFile(value: unknown, snapshotSeq: number): CanonicalFile {
  const item = record(value)
  const sharer = record(item.producer)

  if (!text(item.event_id) || !text(item.attachment_id) || !whole(item.seq, 1) || item.seq > snapshotSeq ||
    !whole(item.manifest_index) || !text(item.kind) || !text(item.name) || !text(item.mime) || !whole(item.size, 1) ||
    (sharer.kind !== 'member' && sharer.kind !== 'user') || !text(sharer.id) || !text(sharer.label) ||
    !timestamp(item.shared_at)) {
    throw new CanonicalFilesError('verification')
  }

  return { eventId: item.event_id, attachmentId: item.attachment_id, seq: item.seq, index: item.manifest_index,
    kind: item.kind, name: item.name, mime: item.mime, size: item.size,
    sharer: { kind: sharer.kind, id: sharer.id, label: sharer.label }, sharedAt: item.shared_at }
}

/** True when `next` comes strictly after `previous` in newest-first, message-order listing. */
export function filesFollow(previous: CanonicalFile, next: CanonicalFile) {
  return next.seq < previous.seq || (next.seq === previous.seq && next.index > previous.index)
}

/** Newest share first, then message order; anything else means the page cannot be trusted. */
export function parseFilesPage(value: unknown, roomId: string): CanonicalFilesPage {
  const page = record(value)
  const authority = record(page.authority)
  const cursor = page.next_cursor === null || text(page.next_cursor) ? page.next_cursor : undefined

  if (page.room_id !== roomId || !text(authority.gateway_id) || !whole(authority.epoch, 1) ||
    !whole(page.snapshot_seq) || !Array.isArray(page.items) || page.items.length > FILES_PAGE_SIZE ||
    cursor === undefined || page.has_more !== (cursor !== null)) {
    throw new CanonicalFilesError('verification')
  }

  const snapshotSeq = page.snapshot_seq
  const items = page.items.map(item => parseFile(item, snapshotSeq))

  if (items.some((item, index) => index > 0 && !filesFollow(items[index - 1], item))) {
    throw new CanonicalFilesError('verification')
  }

  return { items, nextCursor: cursor, snapshotSeq, authority: JSON.stringify([authority.gateway_id, authority.epoch]) }
}

export async function listCanonicalFiles(
  binding: CanonicalGroupBinding, input: { cursor?: string; query?: string }, signal: AbortSignal
): Promise<CanonicalFilesPage> {
  const response = await withFilesDeadline(canonicalGroupRequest<unknown>(binding, 'groups.attachment.list', {
    room_id: binding.roomId,
    limit: FILES_PAGE_SIZE,
    ...(input.cursor ? { cursor: input.cursor } : {}),
    ...(input.query ? { query: input.query } : {})
  }), signal)

  return parseFilesPage(response, binding.roomId)
}

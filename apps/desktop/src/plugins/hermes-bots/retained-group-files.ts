/** #104199's bounded, window-local classic snapshot, without producer RPCs. */
import { downloadCanonicalAttachment } from './canonical-attachment-download'
import { $groupChats, GROUP_CHAT_HISTORY_LIMIT } from './group-chat'
import { GROUP_FILES_MAX_PAGE_SIZE, GROUP_FILES_MAX_QUERY_LENGTH, GROUP_FILES_PAGE_SIZE } from './group-files-parser'
import type { AttachmentKind, GroupChat, GroupMessage, GroupMessageAuthor } from './types'

const MAX_BYTES = 15_000_000
const MIME = /^[a-z0-9][a-z0-9!#$&^_.+-]*\/[a-z0-9][a-z0-9!#$&^_.+-]*$/i
const BASE64 = /^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/

export interface RetainedAttachment {
  name: string
  kind: AttachmentKind
  data?: string
  mime?: string
  size?: number
  sha256?: string
  attachmentId?: string
  uploadId?: string
  classicExport?: unknown
}
export interface RetainedMessage extends Omit<GroupMessage, 'images'> {
  images?: RetainedAttachment[]
  eventId?: string
  roomId?: string
}
export interface RetainedRoom extends Omit<GroupChat, 'log'> {
  log: RetainedMessage[]
  hosted?: unknown
  hostedEpoch?: unknown
  hostedConnectionId?: unknown
  continuityMode?: unknown
}
export interface RetainedRoomBinding {
  group: string
  roomId: string | null
  origin: string
  record: RetainedRoom
}
export class RetainedFileError extends Error {
  constructor(readonly kind: 'gone' | 'cursor' | 'unavailable' | 'verification') {
    super(kind)
  }
}

function origin(room: RetainedRoom): string {
  return JSON.stringify([
    room.roomId ?? null,
    room.hosted ?? null,
    room.hostedEpoch ?? null,
    room.hostedConnectionId ?? null,
    room.continuityMode ?? null,
    (room.members || []).map(m => [
      m.name,
      m.connectionId ?? null,
      m.targetProfile ?? null,
      m.remoteSource ?? null,
      m.sourceScoped ?? null,
      m.route ?? null
    ])
  ])
}

export function captureRetainedRoom(group: string, room: RetainedRoom): RetainedRoomBinding {
  return {
    group,
    roomId: typeof room.roomId === 'string' && room.roomId ? room.roomId : null,
    origin: origin(room),
    record: room
  }
}

export function currentRetainedRoom(binding: RetainedRoomBinding): RetainedRoom | null {
  const room: RetainedRoom | undefined = $groupChats.get()[binding.group]

  if (
    !room ||
    room.tombstone ||
    !Array.isArray(room.log) ||
    origin(room) !== binding.origin ||
    (!binding.roomId && room !== binding.record)
  ) {
    return null
  }

  return room
}

export function retainedEntries(room: RetainedRoom): RetainedMessage[] {
  return Array.isArray(room.log) ? room.log.slice(-GROUP_CHAT_HISTORY_LIMIT * 4) : []
}

export function retainedSpeaker(author: GroupMessageAuthor): string {
  // Saved labels belong to this record, never today's same-named foreground Bot.
  return `${author.name || (author.kind === 'user' ? 'You' : 'Bot')}${author.source ? ` (${author.source})` : ''}`
}

export function foldRetainedFileSearch(value: string): string {
  return value
    .normalize('NFKD')
    .toLowerCase()
    .split('\u0131')
    .map(part => part.toUpperCase().toLowerCase())
    .join('\u0131')
    .replace(/\p{M}/gu, '')
}

function localData(file: RetainedAttachment): { mime: string; size: number; encoded: string } | null {
  if (typeof file.data !== 'string' || !file.data.startsWith('data:')) {
    return null
  }

  if (file.data.length > 4 * Math.ceil(MAX_BYTES / 3) + 256) {
    return null
  }

  const header = /^data:([^;,]{1,127});base64,/.exec(file.data)

  if (!header || !MIME.test(header[1])) {
    return null
  }

  const encoded = file.data.slice(header[0].length)
  const size = Math.floor((encoded.length * 3) / 4) - (encoded.endsWith('==') ? 2 : encoded.endsWith('=') ? 1 : 0)

  if (
    !encoded ||
    !BASE64.test(encoded) ||
    size < 1 ||
    size > MAX_BYTES ||
    !['file', 'pdf', 'image'].includes(file.kind) ||
    typeof file.name !== 'string' ||
    !file.name.trim() ||
    file.name.length > 255 ||
    /[/\\]/.test(file.name) ||
    [...file.name].some(char => char.charCodeAt(0) < 32 || char.charCodeAt(0) === 127) ||
    (file.mime !== undefined && file.mime !== header[1]) ||
    (file.size !== undefined && file.size !== size)
  ) {
    return null
  }

  return { mime: header[1], size, encoded }
}

function messageIdentity(entry: RetainedMessage): string {
  return JSON.stringify([
    entry.id ?? null,
    entry.eventId ?? null,
    entry.roomId ?? null,
    entry.at,
    entry.from,
    entry.thread ?? null
  ])
}

export interface RetainedFileItem {
  key: string
  attachment: Readonly<RetainedAttachment>
  speaker: string
  at: number
  mime: string | null
  size: number | null
  available: boolean
  current: () => boolean
}
export interface RetainedFilesPage {
  items: RetainedFileItem[]
  nextCursor: string | null
}

export function retainedFileItem(
  binding: RetainedRoomBinding,
  entry: RetainedMessage,
  position: number,
  key: string
): RetainedFileItem {
  const raw = entry.images![position]
  const attachment: RetainedAttachment = { ...raw }
  const fileIdentity = JSON.stringify({ ...raw, data: undefined })
  const entryIdentity = messageIdentity(entry)
  const data = localData(attachment)

  const exported =
    attachment.classicExport && typeof attachment.classicExport === 'object'
      ? (attachment.classicExport as Record<string, unknown>)
      : null

  const scopeMatches =
    (!entry.roomId || entry.roomId === binding.roomId) && (!exported?.group || exported.group === binding.roomId)

  return {
    key,
    attachment: Object.freeze(attachment),
    speaker: retainedSpeaker(entry.from),
    at: entry.at,
    mime: data?.mime ?? attachment.mime ?? null,
    size: data?.size ?? attachment.size ?? null,
    available: data !== null && scopeMatches,
    current: () => {
      const room = currentRetainedRoom(binding)

      return (
        scopeMatches &&
        !!room &&
        retainedEntries(room).some(
          candidate =>
            (entry.id || entry.eventId ? messageIdentity(candidate) === entryIdentity : candidate === entry) &&
            messageIdentity(candidate) === entryIdentity &&
            candidate.images?.[position]?.data === attachment.data &&
            JSON.stringify({ ...candidate.images?.[position], data: undefined }) === fileIdentity
        )
      )
    }
  }
}

export function createRetainedFilesLoader(binding: RetainedRoomBinding) {
  const instance = crypto.randomUUID()
  let generation = 0
  let snapshot: RetainedFileItem[] = []
  let query = ''

  const load = (input: { cursor?: string; query?: string; limit?: number } = {}): RetainedFilesPage => {
    const room = currentRetainedRoom(binding)

    if (!room) {
      throw new RetainedFileError('gone')
    }

    if ([...(input.query || '')].length > GROUP_FILES_MAX_QUERY_LENGTH) {
      throw new RetainedFileError('verification')
    }

    const requestedQuery = foldRetainedFileSearch(input.query || '')
    const limit = input.limit ?? GROUP_FILES_PAGE_SIZE

    if (!Number.isSafeInteger(limit) || limit < 1 || limit > GROUP_FILES_MAX_PAGE_SIZE) {
      throw new RetainedFileError('verification')
    }

    let offset = 0

    if (input.cursor) {
      const cursor = input.cursor.split(':')

      if (
        cursor.length !== 4 ||
        cursor[0] !== 'retained' ||
        cursor[1] !== instance ||
        cursor[2] !== String(generation) ||
        query !== requestedQuery
      ) {
        throw new RetainedFileError('cursor')
      }

      offset = Number(cursor[3])

      if (!Number.isSafeInteger(offset) || String(offset) !== cursor[3] || offset < 1 || offset >= snapshot.length) {
        throw new RetainedFileError('cursor')
      }
    } else {
      generation++
      query = requestedQuery
      snapshot = []
      const entries = retainedEntries(room)

      for (let index = entries.length - 1; index >= 0; index--) {
        const entry = entries[index]

        for (const [position, file] of (entry.images || []).slice(0, 8).entries()) {
          if (query && !foldRetainedFileSearch(`${file.name || ''} ${retainedSpeaker(entry.from)}`).includes(query)) {
            continue
          }

          snapshot.push(
            retainedFileItem(binding, entry, position, `retained:${instance}:${generation}:${index}:${position}`)
          )
        }
      }
    }

    return {
      items: snapshot.slice(offset, offset + limit),
      nextCursor: offset + limit < snapshot.length ? `retained:${instance}:${generation}:${offset + limit}` : null
    }
  }

  return Object.assign(load, {
    clear: () => {
      generation++
      snapshot = []
      query = ''
    }
  })
}

export async function saveRetainedFile(item: RetainedFileItem, signal: AbortSignal) {
  const current = () => {
    if (signal.aborted || !item.current()) {
      throw new RetainedFileError('gone')
    }
  }

  current()
  const data = localData(item.attachment)

  if (!data) {
    throw new RetainedFileError('unavailable')
  }

  const raw = atob(data.encoded)

  if (raw.length !== data.size || btoa(raw) !== data.encoded) {
    throw new RetainedFileError('verification')
  }

  const bytes = Uint8Array.from(raw, c => c.charCodeAt(0))

  const exported =
    item.attachment.classicExport && typeof item.attachment.classicExport === 'object'
      ? (item.attachment.classicExport as Record<string, unknown>)
      : null

  const expectedDigest = item.attachment.sha256 ?? exported?.sha256

  if (expectedDigest !== undefined) {
    const digest = await crypto.subtle.digest('SHA-256', bytes)
    const actual = Array.from(new Uint8Array(digest), b => b.toString(16).padStart(2, '0')).join('')

    if (actual !== expectedDigest || (exported?.sha256 !== undefined && actual !== exported.sha256)) {
      throw new RetainedFileError('verification')
    }
  }

  current()
  downloadCanonicalAttachment(bytes, item.attachment.name, data.mime, signal)
}

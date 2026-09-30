import type { GroupChat } from './types'

const STORAGE_KEY = 'hermes.plugin.hermes-bots.group-chats'

/** Read-back compares every field, not object insertion order. */
export function equalGroupRoomSnapshot(left: unknown, right: unknown) {
  const ordered = (_key: string, value: unknown) => value && typeof value === 'object' && !Array.isArray(value)
    ? Object.fromEntries(Object.entries(value).sort(([a], [b]) => a.localeCompare(b))) : value

  return JSON.stringify(left, ordered) === JSON.stringify(right, ordered)
}

export function groupRoomOwnerKey(group: string, room: Pick<GroupChat, 'roomId' | 'desktopAuthorityHash'>) {
  return `${STORAGE_KEY}.room:${encodeURIComponent(room.roomId || room.desktopAuthorityHash || `name:${group}`)}`
}

export function acquireGroupRoomOwner(group: string, room: Pick<GroupChat, 'roomId' | 'desktopAuthorityHash'>) {
  const bridge = window.hermesDesktop?.roomSecrets

  if (!bridge?.check) { throw new Error('Group Chat cross-window ownership is unavailable.') }
  const key = groupRoomOwnerKey(group, room)
  const token = bridge.lock(key)

  return Object.assign(() => bridge.unlock(key, token), { key, token })
}

export function checkGroupRoomOwner(group: string, room: Pick<GroupChat, 'roomId' | 'desktopAuthorityHash'>) {
  const bridge = window.hermesDesktop?.roomSecrets

  if (!bridge?.check) { throw new Error('Group Chat cross-window ownership is unavailable.') }
  bridge.check(groupRoomOwnerKey(group, room))
}

export function retainedRoomOwner(room: Pick<GroupChat, 'shippedAdoption' | 'hosted'> | undefined) {
  return Boolean(room?.shippedAdoption || room?.hosted)
}

function sameStoredRoom(name: string, room: GroupChat, key: string, candidate: GroupChat) {
  if (room.roomId && candidate?.roomId) { return candidate.roomId === room.roomId }

  if (room.desktopAuthorityHash && candidate?.desktopAuthorityHash) {
    return room.desktopAuthorityHash === candidate.desktopAuthorityHash
  }

  return !room.roomId && !room.desktopAuthorityHash && key === name
}

/** A checkpoint is not a revision. Unknown checkpoints remain fences. */
export function sameRoomImport(left: GroupChat, right: GroupChat) {
  const a = left.shippedAdoption
  const b = right.shippedAdoption

  return Boolean(a?.version === 1 && b?.version === 1 && a.sourceId && a.roomId && a.requestHash &&
    ['waiting', 'prepared', 'adopted'].includes(a.state) && ['waiting', 'prepared', 'adopted'].includes(b.state) &&
    a.sourceId === b.sourceId && a.roomId === b.roomId && a.requestHash === b.requestHash &&
    a.route?.connectionId === b.route?.connectionId && a.route?.profile === b.route?.profile &&
    a.route?.authorityGatewayId === b.route?.authorityGatewayId)
}

export function staleRoomOwner(candidate: GroupChat | undefined, current: GroupChat) {
  if (!retainedRoomOwner(current)) { return false }

  if (!candidate || candidate.roomId !== current.roomId) { return true }

  if (current.shippedAdoption && !sameRoomImport(candidate, current)) { return true }

  if (current.shippedAdoption?.state === 'adopted' && candidate.shippedAdoption?.state !== 'adopted') { return true }

  return Boolean(current.hosted && (candidate.hosted !== current.hosted ||
    candidate.hostedConnectionId !== current.hostedConnectionId ||
    Number(candidate.hostedEpoch || 0) < Number(current.hostedEpoch || 0)))
}

// Provenance belongs to the payload's source row, never the last storage read.
// Strings freeze nested data as well: delayed maps cannot acquire newer provenance.
interface RoomSnapshot { name: string; raw: string; projection: string }
const snapshots = new WeakMap<GroupChat, RoomSnapshot>()

export function rememberGroupRoomSnapshot(name: string, room: GroupChat, raw: GroupChat, projection: GroupChat) {
  snapshots.set(room, { name, raw: JSON.stringify(raw), projection: JSON.stringify(projection) })
}

export function inheritGroupRoomSnapshot(source: GroupChat | undefined, target: GroupChat) {
  const snapshot = source && snapshots.get(source)

  if (snapshot) { snapshots.set(target, snapshot) }
}

/** A received rename keeps its actual old row's lineage, not its new key's.
 * Ambiguous identities cannot acquire a baseline by choosing the first row. */
export function inheritGroupRoomSnapshots(rooms: Record<string, GroupChat>, expected: Record<string, GroupChat>) {
  for (const [name, room] of Object.entries(rooms)) {
    const sources = Object.entries(expected).filter(([key, source]) => sameStoredRoom(name, room, key, source))

    if (sources.length === 1) { inheritGroupRoomSnapshot(sources[0][1], room) }
  }
}

export interface GroupRoomWriteOptions {
  target?: string
  renameFrom?: string
  preparation?: { key: string; token: string }
  acknowledgement?: boolean
}
interface RoomWrite extends GroupRoomWriteOptions {
  baseline: Record<string, GroupChat>
  projections: Map<string, RoomSnapshot>
  sealedBaseline?: Record<string, GroupChat>
  sealedProjections?: Record<string, GroupChat>
  committed?: string | null
  decode?: (value: string) => string
}
let activeWrite: RoomWrite | undefined

/** The public storage contract commits synchronously. Keep mutation intent only
 * across that call, not across an await or a later write of a captured map. */
export function withGroupRoomWrite<T>(rooms: Record<string, GroupChat>, options: GroupRoomWriteOptions, save: () => T) {
  const baseline: Record<string, GroupChat> = {}
  const projections = new Map<string, RoomSnapshot>()

  for (const [name, room] of Object.entries(rooms)) {
    const snapshot = snapshots.get(room)

    if (snapshot) {
      baseline[snapshot.name] = JSON.parse(snapshot.raw) as GroupChat
      projections.set(name, snapshot)
    }
  }

  const write: RoomWrite = { ...options, baseline, projections }
  const previous = activeWrite
  activeWrite = write

  try {
    const value = save()

    const committed = write.committed === undefined ? undefined
      : JSON.parse(write.committed === null ? '{}' : write.decode!(write.committed)) as Record<string, GroupChat>

    return { value, committed }
  } finally { activeWrite = previous }
}

/** Seal provenance before acquiring the commit lock; never exchange secrets
 * from inside compare/check/commit. Called by the production room codec. */
export function sealGroupRoomWriteBaseline(encode: (value: string) => string, decode: (value: string) => string) {
  if (activeWrite && !activeWrite.sealedBaseline) {
    activeWrite.sealedBaseline = JSON.parse(encode(JSON.stringify(activeWrite.baseline))) as Record<string, GroupChat>
    activeWrite.sealedProjections = JSON.parse(encode(JSON.stringify(Object.fromEntries(
      [...activeWrite.projections].map(([name, snapshot]) => [name, JSON.parse(snapshot.projection)])
    )))) as Record<string, GroupChat>
    activeWrite.decode = decode
  }
}

function allowedTransition(name: string, candidate: GroupChat, current: GroupChat, write: RoomWrite | undefined) {
  if (!write || write.target !== name) { return false }
  const a = current.shippedAdoption
  const b = candidate.shippedAdoption

  if (write.preparation && a?.version === 1 && a.state === 'waiting' && !a.route &&
      b?.version === 1 && b.state === 'prepared' && a.sourceId === b.sourceId && a.roomId === b.roomId &&
      candidate.roomId === current.roomId && b.requestHash && b.route?.connectionId &&
      b.route.profile && b.route.authorityGatewayId && !current.hosted) {
    const bridge = window.hermesDesktop?.roomSecrets

    if (!bridge?.check || write.preparation.key !== groupRoomOwnerKey(name, current)) { return false }
    // Unlike an ordinary conflict check, this requires this exact admission.
    bridge.check(write.preparation.key, write.preparation.token)

    return true
  }

  return Boolean(write.acknowledgement && !current.roomId && !current.hosted &&
    a?.state === 'prepared' && b?.state === 'adopted' && sameRoomImport(candidate, current) &&
    candidate.roomId === a.roomId && candidate.hosted === a.route?.authorityGatewayId &&
    candidate.hostedConnectionId === a.route?.connectionId && Number(candidate.hostedEpoch) >= 1)
}

/** Runs on sealed rows under the main-owned compare-and-commit lock. Only
 * payload-qualified mutations may change retained rooms; untouched rows stay
 * byte-equivalent, including their native credential references. */
export function reconcileGroupRoomWrite(value: string | null, previous: string | null, observed: string | null) {
  const incoming = (value === null ? {} : JSON.parse(value)) as Record<string, GroupChat>
  const current = (previous === null ? {} : JSON.parse(previous)) as Record<string, GroupChat>
  const write = activeWrite
  const observedBaseline = (observed === null ? {} : JSON.parse(observed)) as Record<string, GroupChat>
  const baseline = write?.sealedBaseline || observedBaseline
  const result = write?.target !== undefined ? { ...current } : { ...incoming }

  if (write?.target !== undefined && incoming[write.target]) { result[write.target] = incoming[write.target] }

  for (const [name, room] of Object.entries(current)) {
    const rename = write?.renameFrom === name ? write.target : undefined

    const entries = Object.entries(incoming).filter(([key, candidate]) => sameStoredRoom(name, room, key, candidate) ||
      (!room.roomId && key === name && sameRoomImport(candidate, room)) ||
      (!room.roomId && !room.desktopAuthorityHash && key === rename))

    const entry = entries.find(([key]) => key === rename) || entries.find(([key]) => key === name) || entries[0] ||
      (!retainedRoomOwner(room) && incoming[name] ? [name, incoming[name]] as const : undefined)

    const baseEntry = Object.entries(baseline).find(([key, candidate]) => sameStoredRoom(name, room, key, candidate)) ||
      (!retainedRoomOwner(room) && observedBaseline[name] ? [name, observedBaseline[name]] as const : undefined)

    const [target, candidate] = entry || [name, undefined]
    const snapshot = write?.projections.get(target)
    const unrelated = write?.target !== undefined && write.target !== target
    // A delayed source alias cannot rename the durable identity back. A read
    // of its new alias does not grant rename intent to the old payload.
    const unqualifiedRename = target !== name && (!snapshot || snapshot.name !== name || baseEntry?.[0] !== name)

    const untouched = snapshot && snapshot.name === target &&
      equalGroupRoomSnapshot(candidate, write?.sealedBaseline?.[snapshot.name])

    // Projection defaults can differ from released bytes. Compare the actual
    // incoming projection too, rather than mistaking hydration for an edit.
    const projectedUntouched = snapshot && snapshot.name === target &&
      equalGroupRoomSnapshot(candidate, write?.sealedProjections?.[target])

    for (const [duplicate] of entries) {
      if (duplicate !== target) { delete result[duplicate] }
    }

    if (unrelated || unqualifiedRename || (!entry && !baseEntry && (write || observed !== null)) ||
        (write && (untouched || projectedUntouched) && !rename) ||
        (!write && retainedRoomOwner(room)) ||
        (staleRoomOwner(candidate, room) && !(candidate && allowedTransition(name, candidate, room, write)))) {
      if (target !== name) { delete result[target] }
      result[name] = room

      continue
    }

    const changed = target !== name || !equalGroupRoomSnapshot(candidate, room)

    if (changed) {
      checkGroupRoomOwner(name, room)

      if (candidate && candidate.roomId !== room.roomId) { checkGroupRoomOwner(target, candidate) }

      if ((baseEntry && !equalGroupRoomSnapshot(room, baseEntry[1])) ||
          (write && retainedRoomOwner(room) && !baseEntry)) {
        throw new Error('Group Chat changed in another window; reload before editing it.')
      }
    }

    if (target !== name) { delete result[name] }

    if (!candidate) { delete result[name] }
  }

  const committed = value === null && !Object.keys(result).length ? null : JSON.stringify(result)

  if (write) { write.committed = committed }

  return committed
}

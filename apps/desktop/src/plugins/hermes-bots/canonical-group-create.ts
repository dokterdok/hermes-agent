import { canonicalGroupRequest } from './canonical-groups'
import type { CanonicalGroupBinding, CanonicalGroupRoute, CanonicalRoom, CanonicalRoomMember } from './canonical-groups'

const PREFIX = 'canonical-group-create-v1'
const STORAGE_KEY = 'hermes.desktop.canonicalGroupCreates.v1'

export function normalizeCanonicalGroupName(name: string): string {
  // Match Python str.strip, including its four information-separator characters.
  // eslint-disable-next-line no-control-regex -- Persisted names follow the server's whitespace rules.
  return name.replace(/^[\p{White_Space}\u001c-\u001f]+|[\p{White_Space}\u001c-\u001f]+$/gu, '')
}

function nativeJournal(writable = false) {
  if (window.hermesDesktop === undefined) {return undefined}
  const native = window.hermesDesktop?.preparedSubmissions
  if (typeof native?.read !== 'function' || (writable && typeof native.compareAndSet !== 'function')) {
    throw new Error('Update Hermes Desktop before continuing this Group Chat setup.')
  }
  return native
}

function requireSetupStorage() {
  if (!nativeJournal(true) && !navigator.locks?.request) {
    throw new Error('This browser cannot safely retain Group Chat setup. Use Hermes Desktop.')
  }
}

export interface PreparedCanonicalGroupCreate {
  version: 1
  binding: CanonicalGroupBinding
  authorityId: string
  params: { room_id: string; name: string; members: CanonicalRoomMember[] }
}

function key(route: CanonicalGroupRoute): string {
  if (![route.connectionId, route.profile].every(value => typeof value === 'string' && value.trim() && value.length <= 512)) {
    throw new Error('Group setup needs its original connection and profile.')
  }
  return JSON.stringify([PREFIX, route.connectionId, route.profile])
}

async function journal(): Promise<Record<string, unknown>> {
  const native = nativeJournal()
  const value: unknown = JSON.parse(native ? await native.read() : localStorage.getItem(STORAGE_KEY) || '{}')
  if (!value || typeof value !== 'object' || Array.isArray(value)) {throw new Error('Could not read saved Group Chat setup.')}
  return value as Record<string, unknown>
}

async function compareAndSet(route: CanonicalGroupRoute, expected: PreparedCanonicalGroupCreate | null, entry: PreparedCanonicalGroupCreate | null): Promise<boolean> {
  const entryKey = key(route)
  const native = nativeJournal(true)
  if (native) {
    return native.compareAndSet!(entryKey, expected === null ? null : JSON.stringify(expected), entry === null ? null : JSON.stringify(entry))
  }
  if (!navigator.locks?.request) {throw new Error('This browser cannot safely retain Group Chat setup. Use Hermes Desktop.')}
  // Browser mode retains reload recovery, not the native journal's process-crash guarantee.
  return navigator.locks.request(STORAGE_KEY, async () => {
    const values = await journal()
    if (JSON.stringify(values[entryKey] ?? null) !== JSON.stringify(expected)) {return false}
    if (entry === null) {delete values[entryKey]} else {values[entryKey] = entry}
    localStorage.setItem(STORAGE_KEY, JSON.stringify(values))
    return true
  })
}

function validMember(value: unknown): value is CanonicalRoomMember {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {return false}
  const member = value as CanonicalRoomMember
  return [member.member_id, member.profile, member.handle].every(part => typeof part === 'string' && part.trim() && part.length <= 512)
    && !!member.target && typeof member.target === 'object' && !Array.isArray(member.target)
}

export async function readCanonicalGroupCreate(route: CanonicalGroupRoute): Promise<PreparedCanonicalGroupCreate | undefined> {
  const entryKey = key(route)
  const saved = (await journal())[entryKey]
  if (saved === undefined) {return undefined}
  const entry = saved as PreparedCanonicalGroupCreate
  if (!entry || entry.version !== 1 || !entry.binding || key(entry.binding) !== entryKey
    || typeof entry.binding.roomId !== 'string' || !entry.binding.roomId || entry.binding.roomId.length > 128
    || typeof entry.authorityId !== 'string' || !entry.authorityId || entry.authorityId.length > 512
    || !entry.params || entry.params.room_id !== entry.binding.roomId || typeof entry.params.name !== 'string'
    || !entry.params.name.trim() || entry.params.name.length > 64 || !Array.isArray(entry.params.members)
    || entry.params.members.length < 2 || entry.params.members.length > 6 || !entry.params.members.every(validMember)
    || JSON.stringify(entry).length > 128 * 1024) {
    throw new Error('Saved Group Chat setup is invalid. It has been kept for recovery.')
  }
  return entry
}

async function authority(route: CanonicalGroupRoute): Promise<string> {
  const value = await canonicalGroupRequest<{ driver?: boolean; authority_gateway_id?: string }>(route, 'groups.capabilities')
  if (value.driver !== true || typeof value.authority_gateway_id !== 'string' || !value.authority_gateway_id || value.authority_gateway_id.length > 512) {
    throw new Error('The group gateway is not ready. Reconnect it and continue setup.')
  }
  return value.authority_gateway_id
}

export async function prepareCanonicalGroupCreate(route: CanonicalGroupRoute, name: string, members: CanonicalRoomMember[]): Promise<PreparedCanonicalGroupCreate> {
  requireSetupStorage()
  name = normalizeCanonicalGroupName(name)
  const bindingRoute = { connectionId: route.connectionId, profile: route.profile }
  key(bindingRoute)
  const authorityId = await authority(bindingRoute)
  const params = JSON.parse(JSON.stringify({ name, members })) as { name: string; members: CanonicalRoomMember[] }
  const existing = await readCanonicalGroupCreate(bindingRoute)
  const assertSameIntent = (entry: PreparedCanonicalGroupCreate) => {
    if (entry.authorityId !== authorityId || JSON.stringify({ name: normalizeCanonicalGroupName(entry.params.name), members: entry.params.members }) !== JSON.stringify(params)) {
      throw new Error(`Finish setting up "${entry.params.name}" before creating another group on this gateway.`)
    }
    return entry
  }
  if (existing) {return assertSameIntent(existing)}
  if (Object.keys(await journal()).filter(value => value.startsWith('["' + PREFIX + '"')).length >= 32) {
    throw new Error('Finish an existing Group Chat setup before creating another.')
  }
  const roomId = crypto.randomUUID()
  const entry: PreparedCanonicalGroupCreate = { version: 1, binding: { ...bindingRoute, roomId }, authorityId,
    params: { room_id: roomId, ...params } }
  if (await compareAndSet(bindingRoute, null, entry)) {return entry}
  const winner = await readCanonicalGroupCreate(bindingRoute)
  if (!winner) {throw new Error('Group Chat setup changed in another window. Try again.')}
  return assertSameIntent(winner)
}

export async function resumeCanonicalGroupCreate(route: CanonicalGroupRoute, expectedRoomId: string): Promise<{ binding: CanonicalGroupBinding; room: CanonicalRoom }> {
  requireSetupStorage()
  const entry = await readCanonicalGroupCreate(route)
  if (!entry) {throw new Error('No unfinished Group Chat setup remains on this gateway.')}
  if (entry.binding.roomId !== expectedRoomId) {throw new Error('Group Chat setup changed in another window. Refresh before continuing.')}
  if (await authority(entry.binding) !== entry.authorityId) {throw new Error('This gateway has changed. Reconnect the original gateway to continue setup.')}
  const result = await canonicalGroupRequest<{ room: CanonicalRoom & { authority_gateway_id?: string } }>(entry.binding, 'groups.create', entry.params)
  const room = result?.room
  if (!room || room.room_id !== entry.params.room_id || room.authority_gateway_id !== entry.authorityId
    || room.name !== normalizeCanonicalGroupName(entry.params.name) || room.disbanded_at != null || !Array.isArray(room.members)
    || room.members.length !== entry.params.members.length || !room.members.every(validMember)) {
    throw new Error('Group creation could not be confirmed. Continue the saved setup before creating another group.')
  }
  for (const member of entry.params.members) {
    const current = room.members.find(value => value.member_id === member.member_id)
    if (!current || current.profile !== member.profile || current.handle !== member.handle
      || JSON.stringify(Object.entries(current.target!).sort()) !== JSON.stringify(Object.entries(member.target!).sort())) {
      throw new Error('The created group does not match the selected Bots. Its saved setup has been kept.')
    }
  }
  // Another window may already have cleared this exact intent; never erase its replacement.
  await compareAndSet(entry.binding, entry, null)
  return { binding: entry.binding, room }
}

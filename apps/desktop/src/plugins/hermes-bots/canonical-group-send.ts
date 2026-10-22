import type { CanonicalGroupBinding } from './canonical-groups'

const STORAGE_KEY = 'hermes.desktop.canonicalGroupSends.v1'

export interface PreparedCanonicalGroupSend {
  binding: CanonicalGroupBinding
  params: {
    room_id: string
    event_id: string
    payload: Record<string, unknown>
  }
  /** False until a request first carries this intent. Older entries lack it and count as uncertain. */
  attempted?: boolean
}

function journalKey(binding: CanonicalGroupBinding): string {
  if (![binding.connectionId, binding.profile, binding.roomId].every(value => typeof value === 'string' && value.trim())) {
    throw new Error('Canonical group Send requires an explicit connection, profile and room')
  }

  // A separate namespace inside the existing origin-scoped native journal.
  return JSON.stringify(['canonical-group-send-v1', binding.connectionId, binding.profile, binding.roomId])
}

async function readJournal(): Promise<Record<string, PreparedCanonicalGroupSend>> {
  const native = window.hermesDesktop?.preparedSubmissions

  const parsed: unknown = JSON.parse(native
    ? await native.read()
    : window.localStorage.getItem(STORAGE_KEY) || '{}')

  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
    throw new Error('Invalid canonical group Send journal')
  }

  return parsed as Record<string, PreparedCanonicalGroupSend>
}

/** Replace `key` only while it holds exactly `expected` (null = absent); returns the stored record.
 *  Native: one IPC op the Electron main process applies atomically, so every window of the origin
 *  is serialized at the journal owner — renderer locks cannot order separate windows. */
async function compareAndSet(
  key: string,
  expected: string | null,
  entry: string | null
): Promise<{ applied: boolean; current: string | null }> {
  const native = window.hermesDesktop?.preparedSubmissions

  // Await private atomic-file publication before the caller may send. Never
  // downgrade a native write failure to Chromium's deferred localStorage.
  if (native) {
    // An older preload has no atomic op: refuse rather than fall back to a racy read-then-write.
    if (!native.compareAndSet) {throw new Error('Group Send needs an updated Desktop; restart Hermes Desktop')}

    return native.compareAndSet(key, expected, entry)
  }

  // Browser-only fallback guarantees reload recovery, not process-crash safety.
  const journal = await readJournal()
  const current = Object.hasOwn(journal, key) ? JSON.stringify(journal[key]) : null

  if (current !== (expected === null ? null : JSON.stringify(JSON.parse(expected)))) {return { applied: false, current }}

  if (entry === null) {delete journal[key]}
  else {journal[key] = JSON.parse(entry)}

  window.localStorage.setItem(STORAGE_KEY, JSON.stringify(journal))

  return { applied: true, current: entry }
}

function validEntry(binding: CanonicalGroupBinding, key: string, entry: PreparedCanonicalGroupSend | undefined) {
  if (entry && (journalKey(entry.binding) !== key || entry.params.room_id !== binding.roomId ||
    !entry.params.event_id || !entry.params.payload || typeof entry.params.payload !== 'object' ||
    (entry.attempted !== undefined && typeof entry.attempted !== 'boolean'))) {
    throw new Error('Invalid canonical group Send entry')
  }

  return entry
}

export async function readCanonicalGroupSend(binding: CanonicalGroupBinding): Promise<PreparedCanonicalGroupSend | undefined> {
  const key = journalKey(binding)

  return validEntry(binding, key, (await readJournal())[key])
}

// One unresolved intent per room. This is not an automatic outbox: callers show
// the recovered payload and explicitly retry it rather than silently replacing it.
// Create-if-absent at the journal owner: when another window won the slot first,
// every caller converges on that durable winner instead of acking its own ID.
export async function prepareCanonicalGroupSend(
  binding: CanonicalGroupBinding,
  payload: Record<string, unknown>
): Promise<PreparedCanonicalGroupSend> {
  const existing = await readCanonicalGroupSend(binding)

  if (existing) {return existing}

  const eventId = crypto.randomUUID()

  const entry: PreparedCanonicalGroupSend = JSON.parse(JSON.stringify({
    binding,
    params: {
      room_id: binding.roomId,
      event_id: eventId,
      payload: { ...payload, thread_id: payload.thread_id ?? eventId }
    },
    attempted: false
  }))

  const key = journalKey(binding)
  const { current } = await compareAndSet(key, null, JSON.stringify(entry))

  return validEntry(binding, key, current === null ? undefined : JSON.parse(current)) ?? entry
}

// Call only after a definitive groups.send ACK; timeout/unknown retains the
// exact event and payload. Compare-and-delete at the journal owner: a delayed or
// wrong ACK whose intent was already replaced (fresh event_id) deletes nothing.
export async function retireCanonicalGroupSend(binding: CanonicalGroupBinding, eventId: string): Promise<void> {
  const entry = await readCanonicalGroupSend(binding)

  if (entry?.params.event_id === eventId) {await compareAndSet(journalKey(binding), JSON.stringify(entry), null)}
}

/** Record a dispatch before it happens. `first` is true only for the intent's first request: a
 *  definitive refusal of that one proves nothing was posted, while a refusal of any later request
 *  leaves the earlier one uncertain. Null when the slot no longer holds this intent: another window
 *  finished with it (a valid receipt, or a refusal of its first request). */
export async function attemptCanonicalGroupSend(
  binding: CanonicalGroupBinding,
  eventId: string
): Promise<{ first: boolean } | null> {
  const key = journalKey(binding)
  const entry = await readCanonicalGroupSend(binding)

  if (entry?.params.event_id !== eventId) {return null}

  if (entry.attempted !== false) {return { first: false }}
  const { applied, current } = await compareAndSet(key, JSON.stringify(entry), JSON.stringify({ ...entry, attempted: true }))

  if (applied) {return { first: true }}

  // Another window dispatched it first, or already finished with it.
  return validEntry(binding, key, current === null ? undefined : JSON.parse(current))?.params.event_id === eventId
    ? { first: false } : null
}

/** A definitive refusal of the first request proves the intent posted nothing. When no window is
 *  left to edit its words, it waits unattempted, so the next Retry gets an honest outcome again. */
export async function reopenCanonicalGroupSend(binding: CanonicalGroupBinding, eventId: string): Promise<void> {
  const entry = await readCanonicalGroupSend(binding)

  if (entry?.params.event_id === eventId && entry.attempted === true) {
    await compareAndSet(journalKey(binding), JSON.stringify(entry), JSON.stringify({ ...entry, attempted: false }))
  }
}

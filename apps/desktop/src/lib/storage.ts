// ── Persistence choke point ─────────────────────────────────────────────────
// Every persisted read/write in the app funnels through readKey/writeKey, so a
// single subscriber (telemetry, cross-window sync, an audit log) can observe all
// of it without instrumenting each call site. No listeners by default → no cost.

export interface PersistenceEvent {
  key: string
  op: 'read' | 'remove' | 'write'
  value: null | string
}

type PersistenceListener = (event: PersistenceEvent) => void

const persistenceListeners = new Set<PersistenceListener>()

/** Owners install codecs before hydration. Durable bytes and event payloads use
 * the encoded representation; callers receive the decoded, ephemeral value. */
export interface PersistenceCodec {
  encode(value: string): string
  decode(value: string): string
  exclusive<T>(commit: () => T): T
  reconcile?(value: string | null, previous: string | null, observed: string | null): string | null
}
const persistenceCodecs = new Map<string, PersistenceCodec>()
const failedReads = new Set<string>()
const observedValues = new Map<string, string | null>()

export function registerPersistenceCodec(key: string, codec: PersistenceCodec) {
  persistenceCodecs.set(key, codec)
}

function writeRequired(key: string, value: string | null) {
  if (value === null) {
    window.localStorage.removeItem(key)
  } else {
    window.localStorage.setItem(key, value)
  }

  if (window.localStorage.getItem(key) !== value) {
    throw new Error('Protected storage readback failed')
  }
}

/** Observe every persisted get/set (e.g. pipe into telemetry/sync). */
export function onPersistenceEvent(listener: PersistenceListener): () => void {
  persistenceListeners.add(listener)

  return () => void persistenceListeners.delete(listener)
}

function emitPersistence(event: PersistenceEvent) {
  for (const listener of persistenceListeners) {
    listener(event)
  }
}

/** Raw read. Returns null when absent or storage is unavailable. */
export function readKey(key: string): null | string {
  let value: null | string = null

  try {
    value = window.localStorage.getItem(key)
  } catch (error) {
    if (persistenceCodecs.has(key)) {
      failedReads.add(key)
      emitPersistence({ key, op: 'read', value: null })
      throw error
    }
    // Restricted contexts (private mode, disabled storage) read as absent.
  }

  const codec = persistenceCodecs.get(key)

  if (codec && value !== null) {
    try {
      // Native work is outside the short shared commit lock. Another renderer
      // may progress meanwhile. Compare AND commit under that same main-owned
      // lock used by every protected write/remove; never compare then unlock.
      for (let attempt = 0; attempt < 8; attempt += 1) {
        const snapshot = value
        const encoded = snapshot === null ? null : codec.encode(snapshot)
        const decoded = encoded === null ? null : codec.decode(encoded)

        const committed = codec.exclusive(() => {
          value = window.localStorage.getItem(key)

          if (value !== snapshot) {
            return false
          }

          if (encoded !== snapshot) {
            writeRequired(key, encoded)
          }

          return true
        })

        if (committed) {
          failedReads.delete(key)
          observedValues.set(key, encoded)
          emitPersistence({ key, op: 'read', value: encoded })

          return decoded
        }
      }

      throw new Error('Protected storage changed during migration; retry hydration')
    } catch (error) {
      failedReads.add(key)
      emitPersistence({ key, op: 'read', value: null })
      throw error
    }
  }

  failedReads.delete(key)
  observedValues.set(key, value)
  emitPersistence({ key, op: 'read', value })

  return value
}

/** Raw write. A null value removes the key. Best-effort. */
export function writeKey(key: string, value: null | string) {
  const codec = persistenceCodecs.get(key)

  if (codec) {
    if (failedReads.has(key)) {
      throw new Error('Protected storage must be recovered before it can be replaced')
    }

    // A write without prior hydration must not erase an unmigrated credential.
    const previous = window.localStorage.getItem(key)

    if (previous !== null) {
      codec.decode(codec.encode(previous))
    }

    let encoded = value === null ? null : codec.encode(value)
    codec.exclusive(() => {
      if (window.localStorage.getItem(key) !== previous) {
        throw new Error('Protected storage changed during write; reload before retrying')
      }

      if (codec.reconcile) {
        encoded = codec.reconcile(encoded, previous, observedValues.get(key) ?? null)
      }

      writeRequired(key, encoded)
    })
    observedValues.set(key, encoded)
    emitPersistence({ key, op: encoded === null ? 'remove' : 'write', value: encoded })

    return
  }

  try {
    if (value === null) {
      window.localStorage.removeItem(key)
    } else {
      window.localStorage.setItem(key, value)
    }
  } catch {
    // Storage is best-effort; never let a quota/permission error break the UI.
  }

  emitPersistence({ key, op: value === null ? 'remove' : 'write', value })
}

/** Parsed JSON read. Returns null on absence, unavailable storage, OR malformed
 *  JSON — callers layer their own shape validation on the parsed value. */
export function readJson<T>(key: string): T | null {
  const raw = readKey(key)

  if (raw === null) {
    return null
  }

  try {
    return JSON.parse(raw) as T
  } catch {
    return null
  }
}

/** JSON write; a null value removes the key. Best-effort (see writeKey). */
export function writeJson(key: string, value: unknown) {
  writeKey(key, value === null ? null : JSON.stringify(value))
}

export function storedBoolean(key: string, fallback: boolean): boolean {
  const value = readKey(key)

  return value === null ? fallback : value === 'true'
}

export function persistBoolean(key: string, value: boolean) {
  writeKey(key, String(value))
}

export function storedString(key: string): null | string {
  return readKey(key)
}

export function persistString(key: string, value: null | string) {
  writeKey(key, value)
}

export function storedStringArray(key: string): string[] {
  const value = readKey(key)

  if (!value) {
    return []
  }

  try {
    const parsed = JSON.parse(value)

    if (!Array.isArray(parsed)) {
      return []
    }

    return parsed.filter((item): item is string => typeof item === 'string' && item.length > 0)
  } catch {
    return []
  }
}

export function persistStringArray(key: string, value: string[]) {
  writeKey(key, value.length === 0 ? null : JSON.stringify(value))
}

export function storedStringRecord(key: string): Record<string, string> {
  const value = readKey(key)

  if (!value) {
    return {}
  }

  try {
    const parsed = JSON.parse(value)

    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
      return {}
    }

    return Object.fromEntries(
      Object.entries(parsed).filter((entry): entry is [string, string] => typeof entry[1] === 'string')
    )
  } catch {
    return {}
  }
}

export function persistStringRecord(key: string, value: Record<string, string>) {
  writeKey(key, JSON.stringify(value))
}

export function arraysEqual(left: string[], right: string[]) {
  return left.length === right.length && left.every((item, index) => item === right[index])
}

export function insertUniqueId(ids: string[], id: string, index: number) {
  const next = ids.filter(item => item !== id)
  const boundedIndex = Math.min(Math.max(index, 0), next.length)
  next.splice(boundedIndex, 0, id)

  return next
}

import { randomUUID } from 'node:crypto'
import fs from 'node:fs'
import path from 'node:path'

import { encryptDesktopSecret, writeSecretFileAtomic } from './hardening'
import type { RoomSecretRequest } from './room-secret-types'

interface SafeStorage {
  isEncryptionAvailable(): boolean
  getSelectedStorageBackend?(): string
  encryptString(value: string): Buffer
  decryptString(value: Buffer): string
}
interface SecretRecord {
  scope: string[]
  value: string
}
interface SecretStore {
  version: 1
  installationId: string
  records: Record<string, SecretRecord>
}
const MAX_BYTES = 8 * 1024 * 1024
const MAX_RECORDS = 4096
const REF = /^room-secret:[0-9a-f-]{36}$/

function validScope(scope: unknown): scope is string[] {
  return (
    Array.isArray(scope) &&
    scope.length >= 3 &&
    scope.length <= 20 &&
    ['classic-authority', 'hosted-grant'].includes(scope[0]) &&
    scope.every(part => typeof part === 'string' && part.length <= 2048)
  )
}

/** One Electron main process owns this file. No plaintext policy or OAuth ACKs.
 * Immutable records are retained: an uncertain renderer commit must never make
 * an older reference (including a pending compensation) unusable. */
export function createRoomSecretStore(options: {
  directory: string
  installationId: string
  safeStorage: SafeStorage
  io?: typeof fs
}) {
  const io = options.io || fs
  const file = path.join(options.directory, 'room-secrets-v1.json')

  const secure = () => {
    if (
      !options.safeStorage.isEncryptionAvailable() ||
      options.safeStorage.getSelectedStorageBackend?.() === 'basic_text'
    ) {
      throw new Error('Secure room credential encryption unavailable')
    }
  }

  const read = (): SecretStore => {
    secure()
    let bytes: string

    try {
      const stat = io.lstatSync(file)

      if (
        !stat.isFile() ||
        stat.isSymbolicLink() ||
        stat.size > MAX_BYTES ||
        (typeof process.getuid === 'function' && stat.uid !== process.getuid())
      ) {
        throw new Error('Invalid room credential file')
      }

      bytes = io.readFileSync(file, 'utf8')
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code === 'ENOENT') {
        return { version: 1, installationId: options.installationId, records: {} }
      }

      throw error
    }

    const envelope = JSON.parse(bytes)

    if (envelope?.encoding !== 'safeStorage' || typeof envelope.value !== 'string') {
      throw new Error('Invalid encrypted room credential envelope')
    }

    const store = JSON.parse(options.safeStorage.decryptString(Buffer.from(envelope.value, 'base64'))) as SecretStore

    if (
      store?.version !== 1 ||
      store.installationId !== options.installationId ||
      !store.records ||
      typeof store.records !== 'object' ||
      Array.isArray(store.records) ||
      Object.keys(store.records).length > MAX_RECORDS ||
      Object.entries(store.records).some(
        ([ref, entry]) =>
          !REF.test(ref) ||
          !validScope(entry?.scope) ||
          typeof entry.value !== 'string' ||
          !entry.value ||
          entry.value.length > 16384
      )
    ) {
      throw new Error('Invalid room credential store')
    }

    return store
  }

  const resolve = (store: SecretStore, ref: string, scope: string[]) => {
    const entry = REF.test(ref) ? store.records[ref] : undefined

    if (!entry || JSON.stringify(entry.scope) !== JSON.stringify(scope)) {
      throw new Error('Room credential reference scope mismatch')
    }

    return entry.value
  }

  return {
    exchange(request: RoomSecretRequest): string[] {
      if (
        !request ||
        !['seal', 'open', 'preflight'].includes(request.action) ||
        !Array.isArray(request.entries) ||
        request.entries.length > 1024 ||
        JSON.stringify(request).length > MAX_BYTES ||
        request.entries.some(
          entry =>
            !validScope(entry?.scope) ||
            (entry.ref === undefined) === (entry.value === undefined) ||
            (entry.ref !== undefined && typeof entry.ref !== 'string') ||
            (entry.value !== undefined &&
              (typeof entry.value !== 'string' || !entry.value || entry.value.length > 16384))
        )
      ) {
        throw new Error('Invalid room credential request')
      }

      const store = read()

      if (request.action === 'preflight') {
        // No synthetic credential/reservation or durable fallback. This detects
        // known unavailability/exhaustion; later I/O still requires a retry owner.
        if (
          request.entries.length ||
          Object.keys(store.records).length >= MAX_RECORDS ||
          Buffer.byteLength(JSON.stringify(encryptDesktopSecret(JSON.stringify(store), options.safeStorage))) + 131072 >
            MAX_BYTES
        ) {
          throw new Error('Room credential capacity exhausted')
        }

        return []
      }

      let changed = false

      const refs = request.entries.map(entry => {
        if (entry.ref !== undefined) {
          resolve(store, entry.ref, entry.scope)

          return entry.ref
        }

        if (request.action !== 'seal') {
          throw new Error('Cannot open a raw credential')
        }

        const existing = Object.entries(store.records).find(
          ([, record]) => record.value === entry.value && JSON.stringify(record.scope) === JSON.stringify(entry.scope)
        )

        if (existing) {
          return existing[0]
        }

        if (Object.keys(store.records).length >= MAX_RECORDS) {
          throw new Error('Room credential capacity exhausted')
        }

        const ref = `room-secret:${randomUUID()}`
        store.records[ref] = { scope: [...entry.scope], value: entry.value! }
        changed = true

        return ref
      })

      if (changed) {
        const encrypted = JSON.stringify(encryptDesktopSecret(JSON.stringify(store), options.safeStorage))

        if (Buffer.byteLength(encrypted) > MAX_BYTES) {
          throw new Error('Room credential capacity exhausted')
        }

        io.mkdirSync(options.directory, { recursive: true, mode: 0o700 })
        writeSecretFileAtomic(file, encrypted, { fs: io, encoding: 'utf8' })
        // ACK only a real decrypt/readback, never the in-memory candidate.
        const saved = read()
        refs.forEach((ref, index) => {
          if (resolve(saved, ref, request.entries[index].scope) !== resolve(store, ref, request.entries[index].scope)) {
            throw new Error('Room credential readback failed')
          }
        })
      }

      return request.action === 'seal'
        ? refs
        : refs.map((ref, index) => resolve(store, ref, request.entries[index].scope))
    }
  }
}

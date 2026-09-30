// Isolated adapter: real authenticated encryption, real private temp files,
// never Electron's keychain. Used only by custody tests.
import { createCipheriv, createDecipheriv, randomBytes } from 'node:crypto'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'

import { createRoomSecretStore } from './room-secret-store'

export function roomSecretFixture() {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'room-custody-'))
  const key = randomBytes(32)
  let available = true
  let backend = 'test-encrypted'
  let failWrite = false
  let failReadback = false
  let wrote = false

  const safeStorage = {
    isEncryptionAvailable: () => available,
    getSelectedStorageBackend: () => backend,
    encryptString(value: string) {
      const nonce = randomBytes(12)
      const cipher = createCipheriv('aes-256-gcm', key, nonce)
      const ciphertext = Buffer.concat([cipher.update(value, 'utf8'), cipher.final()])

      return Buffer.concat([nonce, cipher.getAuthTag(), ciphertext])
    },
    decryptString(value: Buffer) {
      const decipher = createDecipheriv('aes-256-gcm', key, value.subarray(0, 12))
      decipher.setAuthTag(value.subarray(12, 28))

      return Buffer.concat([decipher.update(value.subarray(28)), decipher.final()]).toString('utf8')
    }
  }

  const io = {
    ...fs,
    renameSync: (...args: Parameters<typeof fs.renameSync>) => {
      if (failWrite) {
        throw new Error('synthetic atomic rename failure')
      }

      fs.renameSync(...args)
      wrote = true
    },
    readFileSync: ((...args: Parameters<typeof fs.readFileSync>) => {
      if (failReadback && wrote) {
        throw new Error('synthetic readback failure')
      }

      return fs.readFileSync(...args)
    }) as typeof fs.readFileSync
  }

  const store = (installationId = 'synthetic-desktop-A') =>
    createRoomSecretStore({ directory, installationId, safeStorage, io })

  return {
    directory,
    store,
    safeStorage,
    file: path.join(directory, 'room-secrets-v1.json'),
    available: (value: boolean) => {
      available = value
    },
    backend: (value: string) => {
      backend = value
    },
    failWrite: (value: boolean) => {
      failWrite = value
    },
    failReadback: (value: boolean) => {
      failReadback = value
      wrote = false
    },
    dispose: () => fs.rmSync(directory, { recursive: true, force: true })
  }
}

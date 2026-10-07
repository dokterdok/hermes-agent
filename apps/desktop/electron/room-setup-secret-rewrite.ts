import fs from 'node:fs'
import path from 'node:path'

import { writeSecretFileAtomic } from './hardening'
import { RoomSetupError } from './room-setup-store'

/** Rewrite the native setup obligations using the same credential encoding policy and durable verification. */
export function rewriteRoomSetupSecrets(options: {
  directory: string
  shouldRewrite: (secret: any) => boolean
  reencode: (secret: any) => any
  decrypt: (secret: any) => string
  encryptionOn: () => boolean
  encrypt: (text: string) => unknown
}): boolean {
  let touched = false
  // Setup obligations are per-record native secrets too. Corruption remains
  // unknown and refuses a policy-change ACK; never drop the recovery journal.
  const roomDirectory = options.directory

  if (fs.existsSync(roomDirectory)) {
    const directory = fs.lstatSync(roomDirectory)

    if (
      !directory.isDirectory() ||
      directory.isSymbolicLink() ||
      (process.getuid && (directory.uid !== process.getuid() || (directory.mode & 0o077) !== 0))
    ) {
      throw new RoomSetupError('setup_journal_unreadable')
    }

    for (const name of fs.readdirSync(roomDirectory).filter(name => /^[0-9a-f-]{36}\.json$/.test(name))) {
      const file = path.join(roomDirectory, name)
      const stat = fs.lstatSync(file)

      if (!stat.isFile() || stat.isSymbolicLink() || stat.size > 65536) {
        throw new RoomSetupError('setup_journal_unreadable')
      }

      const secret = JSON.parse(fs.readFileSync(file, 'utf8'))

      if (options.shouldRewrite(secret)) {
        const original = options.decrypt(secret)

        if (!original) {
          throw new RoomSetupError('setup_journal_unreadable')
        }

        const next = options.encryptionOn() ? options.encrypt(original) : options.reencode(secret)

        if (next === secret) {
          throw new RoomSetupError('setup_journal_unreadable')
        }

        writeSecretFileAtomic(file, JSON.stringify(next), {
          encoding: 'utf8',
          durable: {
            verify: bytes => {
              if (options.decrypt(JSON.parse(bytes.toString('utf8'))) !== original) {
                throw new RoomSetupError('setup_journal_write_failed')
              }
            }
          }
        })
        touched = true
      }
    }
  }

  return touched
}

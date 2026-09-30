import { randomUUID } from 'node:crypto'

import { app, BrowserWindow, ipcMain, safeStorage } from 'electron'
import type { IpcMain, IpcMainEvent } from 'electron'

import { createRoomSecretStore } from './room-secret-store'
import { ROOM_SECRET_CHANNEL } from './room-secret-types'
import type { RoomSecretRequest, RoomStorageLockRequest } from './room-secret-types'

/** The registration used by main and by isolated transport tests. */
export function registerRoomSecretIpc(options: {
  rendererUrl: string
  installationId: string
  ipc?: Pick<IpcMain, 'on'>
  windowFor?: typeof BrowserWindow.fromWebContents
  store?: ReturnType<typeof createRoomSecretStore>
}) {
  const expected = new URL(options.rendererUrl)

  const store =
    options.store ||
    createRoomSecretStore({
      directory: app.getPath('userData'),
      installationId: options.installationId,
      safeStorage
    })

  const windowFor = options.windowFor || BrowserWindow.fromWebContents

  // Shared by all honest renderer clients. Never time out a live holder: that
  // would let an old renderer resume and write after a replacement acquired it.
  const locks = new Map<string, { sender: IpcMainEvent['sender']; frame: IpcMainEvent['senderFrame']; token: string }>()

  ;(options.ipc || ipcMain).on(
    ROOM_SECRET_CHANNEL,
    (event: IpcMainEvent, request: RoomSecretRequest | RoomStorageLockRequest) => {
      try {
        const win = windowFor(event.sender)
        const url = new URL(event.senderFrame?.url || '')

        if (
          !win ||
          win.isDestroyed() ||
          event.senderFrame !== event.sender.mainFrame ||
          url.protocol !== expected.protocol ||
          url.host !== expected.host ||
          url.pathname !== expected.pathname
        ) {
          throw new Error('Untrusted room credential sender')
        }

        if (request?.action === 'lock' || request?.action === 'unlock' || request?.action === 'check') {
          const roomKey = request.key.startsWith('hermes.plugin.hermes-bots.group-chats.room:') &&
            request.key.length <= 1024 && request.key.length > 'hermes.plugin.hermes-bots.group-chats.room:'.length

          if (
            !roomKey && !['hermes.plugin.hermes-bots.group-chats', 'hermes.plugin.hermes-bots.hosted-room-cleanup-v1'].includes(
              request.key
            )
          ) {
            throw new Error('Invalid protected storage key')
          }

          let held = locks.get(request.key)

          if (held && (held.sender.isDestroyed() || held.sender.mainFrame !== held.frame)) {
            locks.delete(request.key)
            held = undefined
          }

          if (request.action === 'check') {
            if (!roomKey || (held && (held.sender !== event.sender || held.frame !== event.senderFrame)) ||
                (request.token !== undefined && (!held || held.token !== request.token))) {
              throw new Error('Room execution belongs to another window')
            }

            event.returnValue = { ok: true, values: [] }
          } else if (request.action === 'lock') {
            // No room admission may interleave with a protected map's
            // compare/check/commit. The shared lock remains short and sync.
            const writing = locks.get('hermes.plugin.hermes-bots.group-chats')

            if (roomKey && writing && !writing.sender.isDestroyed() && writing.sender.mainFrame === writing.frame &&
                (writing.sender !== event.sender || writing.frame !== event.senderFrame)) {
              throw new Error('Room persistence is committing')
            }

            if (held) {
              throw new Error('Protected storage busy')
            }

            const token = randomUUID()
            locks.set(request.key, { sender: event.sender, frame: event.senderFrame, token })
            event.returnValue = { ok: true, values: [token] }
          } else {
            if (
              !held ||
              held.sender !== event.sender ||
              held.frame !== event.senderFrame ||
              held.token !== request.token
            ) {
              throw new Error('Invalid protected storage owner')
            }

            locks.delete(request.key)
            event.returnValue = { ok: true, values: [] }
          }
        } else {
          event.returnValue = { ok: true, values: store.exchange(request as RoomSecretRequest) }
        }
      } catch {
        // Neither platform errors nor malformed requests may echo bearer bytes.
        event.returnValue = { ok: false, error: 'Room credential custody unavailable' }
      }
    }
  )
}

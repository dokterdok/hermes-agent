import type { BrowserWindow, IpcMain, WebContents } from 'electron'

import { roomSetupCoordinator } from './room-setup'
import { RoomSetupError, roomSetupStore } from './room-setup-store'
import type { SetupRoute } from './room-setup-types'

/** Native credential custody and document gates shared by all setup commands. */
export function registerRoomSetupIpc(options: {
  directory: string
  ipc: IpcMain
  rendererUrl: () => string
  windowFor: (contents: WebContents) => BrowserWindow | null
  recoverStorage: () => void
  encrypt: (text: string) => unknown
  decrypt: (sealed: any) => string
  connect: (
    route: SetupRoute
  ) => Promise<{ request: (method: string, params?: Record<string, unknown>) => Promise<any>; close: () => void }>
}) {
  const nativeRoomSetup = roomSetupCoordinator({
    beforeOperation: () => {
      try {
        options.recoverStorage()
      } catch {
        throw new RoomSetupError('setup_journal_unreadable')
      }
    },
    store: roomSetupStore({
      directory: options.directory,
      // Follow the existing explicit native storage policy. OFF deliberately uses
      // private plain files with zero keychain calls; ON never downgrades on error.
      encrypt: text => JSON.stringify(options.encrypt(text)),
      decrypt: text => {
        const sealed = JSON.parse(text)

        if (!['plain', 'safeStorage'].includes(sealed.encoding) || typeof sealed.value !== 'string') {
          throw new RoomSetupError('setup_journal_unreadable')
        }

        const value = options.decrypt(sealed)

        if (!value) {
          throw new RoomSetupError('setup_journal_unreadable')
        }

        return value
      }
    }),
    connect: options.connect
  })

  for (const operation of ['create', 'recover', 'addBackup'] as const) {
    options.ipc.handle(`hermes:room-setup:${operation}`, async (event, input) => {
      const frame = event.senderFrame
      const expected = new URL(options.rendererUrl())
      const sender = new URL(frame?.url || 'about:blank')
      const win = options.windowFor(event.sender)

      if (
        !win ||
        win.isDestroyed() ||
        frame !== event.sender.mainFrame ||
        sender.protocol !== expected.protocol ||
        sender.host !== expected.host ||
        sender.pathname !== expected.pathname
      ) {
        return { ok: false, reason: 'untrusted_setup_sender' }
      }

      let retired = false

      const navigating = (_event, _url, sameDocument, mainFrame) => {
        if (mainFrame && !sameDocument) {
          retired = true
        }
      }

      event.sender.on('did-start-navigation', navigating)

      const assertCurrent = () => {
        if (retired || event.sender.isDestroyed() || event.sender.mainFrame !== frame) {
          throw new RoomSetupError('setup_document_retired')
        }
      }

      try {
        assertCurrent()

        const result =
          operation === 'create'
            ? await nativeRoomSetup.create(input, assertCurrent)
            : operation === 'addBackup'
              ? await nativeRoomSetup.addBackup(input, assertCurrent)
              : await nativeRoomSetup.recover()

        assertCurrent()

        return { ok: true, ...result }
      } catch (error) {
        return { ok: false, reason: error instanceof RoomSetupError ? error.reason : 'setup_failed' }
      } finally {
        event.sender.removeListener('did-start-navigation', navigating)
      }
    })
  }

  return nativeRoomSetup
}

import { EventEmitter } from 'node:events'
import fs from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'

import type { IpcMainInvokeEvent } from 'electron'
import { expect, test, vi } from 'vitest'

import { registerRoomSetupIpc } from './room-setup-ipc'
import { roomSetupStore } from './room-setup-store'

const URL = 'http://localhost:5174/'

function sender() {
  const frame = { url: URL }
  const contents = Object.assign(new EventEmitter(), { mainFrame: frame, isDestroyed: () => false })

  return { frame, contents, event: { sender: contents, senderFrame: frame } as unknown as IpcMainInvokeEvent }
}

test('a same-URL document reload compensates a late grant on its original gateways and returns no credential', async () => {
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), 'room-ipc-document-'))
  const effects: string[] = []
  const handlers = new Map<string, (event: IpcMainInvokeEvent, input: unknown) => Promise<unknown>>()
  const { contents, event } = sender()
  let release!: (value: unknown) => void
  const issued = new Promise(resolve => {
    release = resolve
  })
  const catalog = {
    installation_id: 'peer-install',
    persistent_process: true,
    text: true,
    attachments: false,
    catalog_digest: 'digest'
  }

  const clients = {
    home: {
      close: vi.fn(),
      request: async (method: string, params?: Record<string, unknown>) => {
        effects.push(`home:${method}`)

        if (method === 'groups.capabilities') {
          return {
            driver: true,
            persistent_process: true,
            methods: ['groups.discard'],
            authority_gateway_id: 'home-install'
          }
        }

        if (method === 'groups.create') {
          return { room: { ...params, authority_gateway_id: 'home-install', authority_epoch: 1 } }
        }

        if (method === 'groups.disband') {
          return { tombstone: { room_id: params?.room_id, disbanded_at: 123 } }
        }
        throw new Error('Unexpected home mutation')
      }
    },
    peer: {
      close: vi.fn(),
      request: async (method: string, params?: Record<string, unknown>) => {
        effects.push(`peer:${method}`)

        if (method === 'groups.capabilities') {
          return {
            driver: true,
            persistent_process: true,
            methods: ['groups.discard'],
            authority_gateway_id: 'peer-install',
            server_time: 123,
            features: ['peer_setup_recovery'],
            room_link: {
              enabled: true,
              authentication: 'proof-v2',
              endpoint: { available: true, url: 'https://peer.invalid' },
              catalog
            }
          }
        }

        if (method === 'groups.peer.invite') {
          return issued
        }

        if (method === 'groups.peer.revoke') {
          expect(params?.grant).toBe('private-setup-grant')

          return { revoked: true }
        }

        throw new Error('Unexpected peer mutation')
      }
    }
  }

  const encrypt = (value: string) => ({ encoding: 'plain', value })
  const decrypt = (sealed: { value: string }) => sealed.value

  try {
    registerRoomSetupIpc({
      directory,
      ipc: { handle: (name, handler) => handlers.set(name, handler) } as never,
      rendererUrl: () => URL,
      windowFor: () => ({ isDestroyed: () => false }) as never,
      recoverStorage: () => undefined,
      encrypt,
      decrypt,
      connect: async route => clients[route.connectionId as 'home' | 'peer']
    })

    const result = handlers.get('hermes:room-setup:create')!(event, {
      home: { connectionId: 'home', profile: 'default' },
      name: 'Document',
      members: [
        { member_id: 'one', handle: 'one', connectionId: 'home', profile: 'default' },
        { member_id: 'two', handle: 'two', connectionId: 'peer', profile: 'default' }
      ]
    })

    await vi.waitFor(() => expect(effects).toContain('peer:groups.peer.invite'), { timeout: 5000 })
    contents.emit('did-start-navigation', {}, URL, false, true)
    release({
      grant: 'private-setup-grant',
      target_profile: 'default',
      endpoint: { url: 'https://peer.invalid' },
      catalog
    })
    expect(await result).toEqual({ ok: false, reason: 'setup_document_retired' })
    expect(effects).toContain('peer:groups.peer.revoke')
    expect(effects).toContain('home:groups.disband')
    expect(effects).not.toContain('home:groups.peer.register')
    expect(contents.listenerCount('did-start-navigation')).toBe(0)
    const store = roomSetupStore({
      directory,
      encrypt: value => JSON.stringify(encrypt(value)),
      decrypt: value => decrypt(JSON.parse(value))
    })
    expect(await store.list()).toEqual({ records: [], unreadable: [] })
  } finally {
    await fs.rm(directory, { recursive: true, force: true })
  }
})

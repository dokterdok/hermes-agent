import fs from 'node:fs'

import type { BrowserWindow, IpcMain, IpcMainEvent } from 'electron'
import { afterEach, expect, it, vi } from 'vitest'

import { registerRoomSecretIpc } from './room-secret-ipc'
import { createRoomSecretBridge } from './room-secret-preload'
import { roomSecretFixture } from './room-secret-test-fixture'
import type { RoomSecretRequest } from './room-secret-types'

vi.mock('electron', () => ({ app: {}, BrowserWindow: {}, ipcMain: {}, safeStorage: {} }))
const fixtures: ReturnType<typeof roomSecretFixture>[] = []
afterEach(() => {
  fixtures.splice(0).forEach(fixture => fixture.dispose())
})

function setup() {
  const fixture = roomSecretFixture()
  fixtures.push(fixture)

  return fixture
}

const scope = ['hosted-grant', 'room', 'incarnation', 'installation-A', 'profile-A']
const seal = (value: string): RoomSecretRequest => ({ action: 'seal', entries: [{ scope, value }] })
const open = (ref: string, bound = scope): RoomSecretRequest => ({ action: 'open', entries: [{ scope: bound, ref }] })

it('retains immutable references through replacement, native reload and atomic failure, but refuses other scopes/installations', () => {
  const fixture = setup()
  const store = fixture.store()
  const [first] = store.exchange(seal('synthetic-first-bearer'))
  const before = fs.readFileSync(fixture.file, 'utf8')
  expect(before).not.toContain('synthetic-first-bearer')
  expect(fs.statSync(fixture.file).mode & 0o777).toBe(0o600)
  expect(store.exchange(seal('synthetic-first-bearer'))).toEqual([first])
  fixture.failWrite(true)
  expect(() => store.exchange(seal('synthetic-second-bearer'))).toThrow()
  expect(fs.readFileSync(fixture.file, 'utf8')).toBe(before)
  expect(fixture.store().exchange(open(first))).toEqual(['synthetic-first-bearer'])
  fixture.failWrite(false)
  const [second] = store.exchange(seal('synthetic-second-bearer'))
  expect(second).not.toBe(first)
  expect(fixture.store().exchange(open(first))).toEqual(['synthetic-first-bearer'])
  expect(fixture.store().exchange(open(second))).toEqual(['synthetic-second-bearer'])

  for (let i = 0; i < scope.length; i++) {
    const wrong = [...scope]
    wrong[i] = 'other'
    expect(() => store.exchange(open(first, wrong))).toThrow()
  }

  expect(() => fixture.store('synthetic-desktop-B').exchange(open(first))).toThrow()
})

it('never ACKs failed readback, unavailable encryption, basic_text or malformed/plain stores', () => {
  const fixture = setup()
  fixture.available(false)
  expect(() => fixture.store().exchange(seal('secret'))).toThrow()
  expect(fs.existsSync(fixture.file)).toBe(false)
  fixture.available(true)
  fixture.backend('basic_text')
  expect(() => fixture.store().exchange(seal('secret'))).toThrow()
  expect(fs.existsSync(fixture.file)).toBe(false)
  fixture.backend('test-encrypted')
  fixture.failReadback(true)
  expect(() => fixture.store().exchange(seal('secret'))).toThrow('readback')
  fixture.failReadback(false)
  expect(fixture.store().exchange(seal('secret'))[0]).toMatch(/^room-secret:/)
  fs.writeFileSync(fixture.file, JSON.stringify({ encoding: 'plain', value: 'secret' }))
  const before = fs.readFileSync(fixture.file, 'utf8')
  expect(() => fixture.store().exchange(seal('replacement'))).toThrow()
  expect(fs.readFileSync(fixture.file, 'utf8')).toBe(before)
})

it('routes the actual registered private IPC through preload and rejects untrusted frames/URLs without echoing payloads', () => {
  const fixture = setup()

  let handler: (event: IpcMainEvent, request: unknown) => void = () => {
    throw new Error('unregistered')
  }

  const frame = { url: 'file:///app/index.html' }
  const event = { senderFrame: frame, sender: { mainFrame: frame } } as unknown as IpcMainEvent
  registerRoomSecretIpc({
    rendererUrl: 'file:///app/index.html',
    installationId: 'synthetic-desktop-A',
    store: fixture.store(),
    ipc: {
      on: (_channel, callback) => {
        handler = callback
      }
    } as Pick<IpcMain, 'on'>,
    windowFor: (() => ({ isDestroyed: () => false })) as unknown as typeof BrowserWindow.fromWebContents
  })

  const bridge = createRoomSecretBridge((_channel, request) => {
    handler(event, request)

    return event.returnValue
  })

  const [ref] = bridge.exchange(seal('synthetic-private'))
  expect(bridge.exchange(open(ref))).toEqual(['synthetic-private'])
  frame.url = 'file:///attacker/index.html'
  expect(() => bridge.exchange(open(ref))).toThrow()
  expect(JSON.stringify(event.returnValue)).not.toContain('synthetic-private')
  frame.url = 'file:///app/index.html'
  Object.assign(event, { senderFrame: { url: frame.url } })
  expect(() => bridge.exchange(open(ref))).toThrow()
})

it('refuses oversized batches and corrupt native ciphertext without replacing retained bytes', () => {
  const fixture = setup()

  const oversized: RoomSecretRequest = {
    action: 'seal',
    entries: Array.from({ length: 1025 }, () => ({ scope, value: 'synthetic' }))
  }

  expect(() => fixture.store().exchange(oversized)).toThrow('Invalid room credential request')
  expect(fs.existsSync(fixture.file)).toBe(false)
  const [ref] = fixture.store().exchange(seal('synthetic'))
  const envelope = JSON.parse(fs.readFileSync(fixture.file, 'utf8'))
  const ciphertext = Buffer.from(envelope.value, 'base64')
  ciphertext[ciphertext.length - 1] ^= 1
  envelope.value = ciphertext.toString('base64')
  const corrupted = JSON.stringify(envelope)
  fs.writeFileSync(fixture.file, corrupted)
  expect(() => fixture.store().exchange(open(ref))).toThrow()
  expect(() => fixture.store().exchange(seal('replacement'))).toThrow()
  expect(fs.readFileSync(fixture.file, 'utf8')).toBe(corrupted)
})

it('shares protected commit ownership across windows, rejects foreign release, and reclaims a destroyed holder', () => {
  const fixture = setup()
  let handler!: (event: IpcMainEvent, request: unknown) => void
  registerRoomSecretIpc({
    rendererUrl: 'file:///app/index.html',
    installationId: 'synthetic-desktop-A',
    store: fixture.store(),
    ipc: {
      on: (_channel, callback) => {
        handler = callback
      }
    } as Pick<IpcMain, 'on'>,
    windowFor: (() => ({ isDestroyed: () => false })) as unknown as typeof BrowserWindow.fromWebContents
  })

  const client = () => {
    let destroyed = false
    const frame = { url: 'file:///app/index.html' }

    const event = {
      senderFrame: frame,
      sender: { mainFrame: frame, isDestroyed: () => destroyed }
    } as unknown as IpcMainEvent

    return {
      frame,
      destroy: () => {
        destroyed = true
      },
      bridge: createRoomSecretBridge((_channel, request) => {
        handler(event, request)

        return event.returnValue
      })
    }
  }

  const a = client(),
    b = client()

  const key = 'hermes.plugin.hermes-bots.group-chats'
  const held = a.bridge.lock(key)
  expect(() => b.bridge.lock(key)).toThrow()
  expect(() => b.bridge.unlock(key, held)).toThrow()
  expect(() => b.bridge.lock('unrelated-key')).toThrow()
  a.bridge.unlock(key, held)
  const next = b.bridge.lock(key)
  b.destroy()
  const recovered = a.bridge.lock(key)
  expect(recovered).not.toBe(next)
  a.bridge.unlock(key, recovered)
})

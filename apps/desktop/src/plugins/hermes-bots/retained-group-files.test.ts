import { beforeEach, expect, it, vi } from 'vitest'

import { $groupChats } from './group-chat'
import {
  captureRetainedRoom,
  createRetainedFilesLoader,
  currentRetainedRoom,
  foldRetainedFileSearch,
  type RetainedMessage,
  type RetainedRoom,
  saveRetainedFile
} from './retained-group-files'
import type { GroupChat } from './types'

const request = vi.hoisted(() => vi.fn())
vi.mock('@hermes/plugin-sdk', async () => {
  const { pluginSdkMock } = await import('./group-test-utils')

  return pluginSdkMock({ requestProfile: request })
})

const entry = (index: number, name = `file-${index}.txt`, producer = 'Writer'): RetainedMessage => ({
  id: `local-message-${index}`,
  at: 1_700_000_000_000 + index,
  from: { kind: 'member', name: producer, source: 'Saved host' },
  text: '',
  images: [{ kind: 'file', name, data: 'data:text/plain;base64,YQ==' }]
})

const room = (log: RetainedMessage[]): RetainedRoom => ({ log, roomId: 'classic-room', watermarks: {} })

const install = (value: RetainedRoom) => {
  $groupChats.set({ Core: value as GroupChat })

  return captureRetainedRoom('Core', value)
}

beforeEach(() => {
  $groupChats.set({})
  request.mockReset()
})

it('ports the donor bounded snapshot and cursor behavior without RPC or copying bytes', () => {
  const original = room(Array.from({ length: 9 }, (_, index) => entry(index, 'report.txt')))
  const binding = install(original)
  const load = createRetainedFilesLoader(binding)
  const first = load()
  expect(first.items).toHaveLength(8)
  expect(first.items[0].attachment.data).toBe(original.log[8].images![0].data)
  expect(new Set(first.items.map(item => item.key)).size).toBe(8)
  $groupChats.set({ Core: room([...original.log, entry(10)]) as GroupChat })
  expect(load({ cursor: first.nextCursor! }).items[0].attachment.name).toBe('report.txt')
  expect(load().items[0].attachment.name).toBe('file-10.txt')
  expect(() => load({ cursor: first.nextCursor! })).toThrow('cursor')
  expect(request).not.toHaveBeenCalled()
})

it('keeps all known source/room coordinates and refuses same-name replacement', () => {
  const original = { ...room([entry(1)]), hosted: 'install:a', hostedEpoch: 1, hostedConnectionId: 'source-a' }
  const binding = install(original)
  const item = createRetainedFilesLoader(binding)().items[0]

  for (const changed of [
    { roomId: 'other' },
    { hostedConnectionId: 'source-b' },
    { hostedEpoch: 2 },
    { hosted: 'install:b' }
  ]) {
    $groupChats.set({ Core: { ...original, ...changed } as GroupChat })
    expect(currentRetainedRoom(binding)).toBeNull()
    expect(item.current()).toBe(false)
  }
})

it('does not rebind an ID-less old room to another record under the same label', () => {
  const original = { ...room([entry(1)]), roomId: null }
  const binding = install(original)
  expect(currentRetainedRoom(binding)).toBe(original)
  $groupChats.set({ Core: { ...original } as GroupChat })
  expect(currentRetainedRoom(binding)).toBeNull()
})

it('folds accents and case in saved filename/sharer without foreground name resolution', () => {
  const load = createRetainedFilesLoader(
    install(room([entry(1, 'Re\u0301sume\u0301.pdf', 'José'), entry(2, 'notes.md', 'STRASSE')]))
  )

  expect(load({ query: 'RESUME' }).items).toHaveLength(1)
  expect(load({ query: 'jose' }).items).toHaveLength(1)
  expect(load({ query: 'straße' }).items).toHaveLength(1)
  expect(load({ query: 'saved host' }).items).toHaveLength(2)
  expect(foldRetainedFileSearch('ẞ')).toBe(foldRetainedFileSearch('ß'))
})

it('limits its scan to the donor retained window and eight attachments per message', () => {
  const log = Array.from({ length: 100 }, (_, index) => entry(index))
  log[99].images = Array.from({ length: 20 }, () => entry(1).images![0])
  const load = createRetainedFilesLoader(install(room(log)))
  let page = load()
  let count = page.items.length

  while (page.nextCursor) {
    page = load({ cursor: page.nextCursor })
    count += page.items.length
  }

  expect(count).toBe(95 + 8)
})

it('keeps metadata-only references unavailable and rejects invalid/contradictory data URLs', async () => {
  const missing = {
    kind: 'file' as const,
    name: 'private.txt',
    classicExport: { source: { connectionId: 'other', name: 'writer' }, artifactId: 'art' }
  }

  const bad = [
    'file:///private/file',
    'https://example.invalid/file',
    'data:text/plain;base64,@@==',
    'data:text/plain;base64,'
  ]

  const images = [
    missing,
    ...bad.map(data => ({ kind: 'file' as const, name: 'bad.txt', data })),
    { kind: 'file' as const, name: 'mismatch.txt', data: 'data:text/plain;base64,YQ==', size: 999 },
    { kind: 'file' as const, name: 'mismatch.txt', data: 'data:text/plain;base64,YQ==', mime: 'image/png' }
  ]

  const items = createRetainedFilesLoader(install(room([{ ...entry(1), images }])))().items
  expect(items).toHaveLength(7)

  for (const item of items) {
    expect(item.available).toBe(false)
    await expect(saveRetainedFile(item, new AbortController().signal)).rejects.toThrow('unavailable')
  }

  expect(request).not.toHaveBeenCalled()
})

it('invalidates exact file identity when retained bytes, entry author or artifact reference changes', () => {
  const original = room([entry(1)])
  const item = createRetainedFilesLoader(install(original))().items[0]
  original.log[0].images![0].data = 'data:text/plain;base64,Yg=='
  expect(item.current()).toBe(false)
  original.log[0].images![0].data = 'data:text/plain;base64,YQ=='
  original.log[0].from.source = 'Different host'
  expect(item.current()).toBe(false)
})

it('does not borrow another declared room even when its bytes occur in the retained log', async () => {
  const log = [
    { ...entry(1), roomId: 'other-room' },
    { ...entry(2), images: [{ ...entry(2).images![0], classicExport: { group: 'other-room' } }] }
  ]

  const items = createRetainedFilesLoader(install(room(log)))().items

  for (const item of items) {
    expect(item.available).toBe(false)
    await expect(saveRetainedFile(item, new AbortController().signal)).rejects.toThrow('gone')
  }

  expect(request).not.toHaveBeenCalled()
})

it.each([5_000_000, 15_000_000])('loads canonical base64 at %i bytes without reducing the retained limit', size => {
  const message = entry(1, 'large.bin')
  const data = `data:application/octet-stream;base64,${btoa('x'.repeat(size))}`
  message.images = [{ kind: 'file', name: 'large.bin', data, size }]
  const item = createRetainedFilesLoader(install(room([message])))().items[0]
  expect(item.available).toBe(true)
  expect(item.size).toBe(size)
  expect(item.attachment.data === data).toBe(true)
  expect(item.current()).toBe(true)
  expect(request).not.toHaveBeenCalled()
})

it('still refuses bytes above the retained 15 MB limit', () => {
  const message = entry(1, 'oversized.bin')
  message.images = [{
    kind: 'file', name: 'oversized.bin',
    data: `data:application/octet-stream;base64,${btoa('x'.repeat(15_000_001))}`
  }]
  expect(createRetainedFilesLoader(install(room([message])))().items[0].available).toBe(false)
  expect(request).not.toHaveBeenCalled()
})

it.each(['AA==', 'AAA=', 'AAAA', '+/8=', '////'])('accepts canonical alphabet and padding: %s', encoded => {
  const message = entry(1)
  message.images![0].data = `data:application/octet-stream;base64,${encoded}`
  const item = createRetainedFilesLoader(install(room([message])))().items[0]
  expect(item.available).toBe(true)
  expect(item.size).toBe(atob(encoded).length)
})

it.each(['A', 'AAA', 'A===', '=AAA', 'AA=A', 'AAAA====', 'AAAA\n', 'AA-_', 'AA A', 'AA\u00e9=', 'AB==', 'AAB='])(
  'refuses invalid alphabet, padding or nonzero padding bits: %s', async encoded => {
    const message = entry(1)
    message.images![0].data = `data:application/octet-stream;base64,${encoded}`
    const item = createRetainedFilesLoader(install(room([message])))().items[0]
    expect(item.available).toBe(false)
    await expect(saveRetainedFile(item, new AbortController().signal)).rejects.toThrow('unavailable')
    expect(request).not.toHaveBeenCalled()
  }
)

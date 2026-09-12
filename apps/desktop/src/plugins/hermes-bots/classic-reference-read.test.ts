import { afterEach, beforeEach, expect, it, vi } from 'vitest'

import { expectDownloaded, observeDownloads } from './canonical-download-test-utils'
import { classicReference } from './classic-reference-read'
import { $groupChats } from './group-chat'
import { captureRetainedRoom, createRetainedFilesLoader, saveRetainedFile } from './retained-group-files'
import type { RetainedRoom } from './retained-group-files'
import type { GroupChat } from './types'

const mocks = vi.hoisted(() => ({ request: vi.fn(), epoch: 1 }))
vi.mock('@hermes/plugin-sdk', async () => {
  const { pluginSdkMock } = await import('./group-test-utils')

  return {
    ...await pluginSdkMock({ requestProfile: mocks.request }),
    gatewayActivationEpoch: () => mocks.epoch
  }
})

const sha = 'ca978112ca1bbdcafac231b39a23dc4da786eff8147c4e72b9807785afee48bb'
let observed: ReturnType<typeof observeDownloads>

const exported = () => ({
  kind: 'file' as const, name: 'old.txt', size: 1, mime: 'text/plain',
  classicExport: {
    group: 'original-room', exportId: `ce_${'1'.repeat(64)}`, artifactId: `rart_${'2'.repeat(32)}`,
    generation: 1, installation: 'install:original', source: { connectionId: 'producer-a', name: 'default' },
    session: 'original-session', sha256: sha,
    recipients: [{ installation: 'install:original', profile: 'default' }]
  }
})

function fixture() {
  const attachment = exported()

  const room: RetainedRoom = {
    roomId: 'original-room', watermarks: {},
    log: [{ id: 'original-message', at: 1000, from: { kind: 'member', name: 'Saved producer' }, text: '', images: [attachment] }]
  }

  $groupChats.set({ Room: room as GroupChat })
  const item = createRetainedFilesLoader(captureRetainedRoom('Room', room))().items[0]
  const ref = attachment.classicExport

  const response = {
    session_id: ref.session, installation: ref.installation, export_id: ref.exportId,
    group_id: ref.group, generation: ref.generation, state: 'published', recipients: ref.recipients,
    item: { artifact_id: ref.artifactId, name: attachment.name, size: attachment.size,
      mime: attachment.mime, kind: attachment.kind, sha256: sha }, content_base64: 'YQ=='
  }

  return { attachment, room, item, response }
}

beforeEach(() => {
  $groupChats.set({})
  mocks.request.mockReset()
  mocks.epoch = 1
  vi.restoreAllMocks()
  observed = observeDownloads()
})
afterEach(() => {
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

it('downloads an exact published reference from its original explicit source without resuming or creating', async () => {
  const f = fixture()
  mocks.request.mockResolvedValue(f.response)
  expect(f.item.available).toBe(true)
  await saveRetainedFile(f.item, new AbortController().signal)
  expect(mocks.request).toHaveBeenCalledOnce()
  expect(mocks.request).toHaveBeenCalledWith({ connectionId: 'producer-a', profile: 'default', targetProfile: 'default', mode: 'remote' },
    'session.export.read', { session_id: 'original-session', profile: 'default', installation: 'install:original',
      export_id: f.attachment.classicExport.exportId, artifact_id: f.attachment.classicExport.artifactId,
      group_id: 'original-room', generation: 1 })
  expect(HTMLAnchorElement.prototype.click).toHaveBeenCalledOnce()
  await expectDownloaded(observed, new Uint8Array([97]), 'old.txt', 'text/plain')
  expect(f.attachment).not.toHaveProperty('data')
})

it.each([
  { source: { name: 'default' } },
  { source: { connectionId: 'producer-a', name: 'default', route: [] } },
  { source: { connectionId: 'producer-a', name: 'default', route: {} } },
  { source: { connectionId: 'producer-a', name: 'named' } },
  { source: { connectionId: 'producer-a', name: 'default', route: { connectionId: 'other', profile: 'default' } } },
  { generation: true }, { generation: 0 }, { exportId: 'unknown' }, { artifactId: 'unknown' },
  { installation: '' }, { session: '' }, { sha256: 'bad' }, { recipients: [] }
])('leaves invalid or unsupported producer references unavailable: %j', async change => {
  const f = fixture()
  const attachment = { ...f.attachment, classicExport: { ...f.attachment.classicExport, ...change } }
  expect(classicReference(attachment)).toBeNull()
  expect(mocks.request).not.toHaveBeenCalled()
})

it('preserves an explicit root-target alias without borrowing a foreground route', () => {
  const file = exported()
  const route = { connectionId: 'producer-a', profile: 'Saved alias', targetProfile: 'default', mode: 'local' }

  const ref = classicReference({ ...file, classicExport: {
    ...file.classicExport, source: { name: 'Saved alias', connectionId: 'producer-a', route }
  } })

  expect(ref?.route).toEqual(route)
})

it('does not use a producer reference as a fallback for an invalid local path/data field', () => {
  const file = exported()
  expect(classicReference({ ...file, data: '/private/source.txt' })).toBeNull()
  expect(classicReference({ ...file, size: 15_000_001 })).toBeNull()
})

it.each([
  { session_id: 'replacement' }, { installation: 'install:other' }, { group_id: 'other-room' },
  { export_id: 'ce_other' }, { generation: 2 }, { state: 'running' }, { recipients: [] },
  { content_base64: 'Yg==' }, { content_base64: 'YR==' }, { content_base64: 'bad' }
])('refuses changed scope or unverified bytes: %j', async change => {
  const f = fixture()
  mocks.request.mockResolvedValue({ ...f.response, ...change })
  await expect(saveRetainedFile(f.item, new AbortController().signal)).rejects.toThrow('verification')
  expect(HTMLAnchorElement.prototype.click).not.toHaveBeenCalled()
})

it.each([
  { artifact_id: 'another' }, { name: 'other.txt' }, { kind: 'image' }, { mime: 'image/png' },
  { size: 2 }, { sha256: '0'.repeat(64) }
])('requires the exact retained item metadata: %j', async change => {
  const f = fixture()
  mocks.request.mockResolvedValue({ ...f.response, item: { ...f.response.item, ...change } })
  await expect(saveRetainedFile(f.item, new AbortController().signal)).rejects.toThrow('verification')
  expect(HTMLAnchorElement.prototype.click).not.toHaveBeenCalled()
})

it.each(['abort', 'room', 'source', 'version', 'activation'] as const)(
  'never saves a late reply after %s changes', async change => {
    const f = fixture()
    const abort = new AbortController()
    let resolve!: (value: unknown) => void
    mocks.request.mockImplementation(() => new Promise(done => { resolve = done }))
    const saved = saveRetainedFile(f.item, abort.signal)
    const rejection = expect(saved).rejects.toThrow('gone')

    if (change === 'abort') {abort.abort()}

    if (change === 'room') {f.room.roomId = 'replacement'}

    if (change === 'source') {f.attachment.classicExport.source.connectionId = 'other'}

    if (change === 'version') {f.attachment.classicExport.generation = 2}

    if (change === 'activation') {mocks.epoch += 2}
    resolve(f.response)
    await rejection
    expect(HTMLAnchorElement.prototype.click).not.toHaveBeenCalled()
    expect(mocks.request).toHaveBeenCalledOnce()
  }
)

it('keeps the activation fence through digest verification', async () => {
  const f = fixture()
  mocks.request.mockResolvedValue(f.response)
  let finish!: (value: ArrayBuffer) => void
  const digest = vi.spyOn(crypto.subtle, 'digest').mockImplementationOnce(() => new Promise(done => { finish = done }))
  const saved = saveRetainedFile(f.item, new AbortController().signal)
  const rejection = expect(saved).rejects.toThrow('gone')
  await vi.waitFor(() => expect(digest).toHaveBeenCalledOnce())
  mocks.epoch++
  finish(Uint8Array.from(sha.match(/../g)!, hex => Number.parseInt(hex, 16)).buffer)
  await rejection
  expect(HTMLAnchorElement.prototype.click).not.toHaveBeenCalled()
})

it.each(['permission_denied', 'classic_export_unavailable', 'not_found', 'invalid_params', 'socket closed'])(
  'reports unavailable authority/schema/source without a fallback: %s', async reason => {
    const f = fixture()
    mocks.request.mockRejectedValue(new Error(reason))
    await expect(saveRetainedFile(f.item, new AbortController().signal)).rejects.toThrow('unavailable')
    expect(mocks.request).toHaveBeenCalledOnce()
    expect(HTMLAnchorElement.prototype.click).not.toHaveBeenCalled()
  }
)

it('reads a valid 5 MB producer file without unbounded regex or byte-argument spreading', async () => {
  const f = fixture()
  const raw = 'x'.repeat(5_000_000)
  const bytes = Uint8Array.from(raw, char => char.charCodeAt(0))
  const hash = Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', bytes)), b => b.toString(16).padStart(2, '0')).join('')
  f.attachment.size = raw.length
  f.attachment.classicExport.sha256 = hash
  const item = createRetainedFilesLoader(captureRetainedRoom('Room', f.room))().items[0]
  mocks.request.mockResolvedValue({ ...f.response, item: { ...f.response.item, size: raw.length, sha256: hash }, content_base64: btoa(raw) })
  await saveRetainedFile(item, new AbortController().signal)
  expect(HTMLAnchorElement.prototype.click).toHaveBeenCalledOnce()
  expect(vi.mocked(URL.createObjectURL).mock.calls[0][0]).toHaveProperty('size', raw.length)
  expect(f.attachment).not.toHaveProperty('data')
})

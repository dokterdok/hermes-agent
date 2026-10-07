import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'

import { act, cleanup, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

import type { ClientSessionState } from '@/app/types'
import { en } from '@/i18n/en'
import { createClientSessionState } from '@/lib/chat-runtime'
import { $activeGatewayProfile } from '@/store/profile'
import { $connection, $sessions } from '@/store/session'
import { $sessionStates } from '@/store/session-states'

import { claimPreparedSubmission, preparedSubmissionKey, readPreparedSubmission, removePreparedSubmission, writePreparedSubmission } from './prepared-submissions'
import { captureSubmissionDestination } from './submission-destination'
import { useSubmitPrompt } from './submit'
import { clearSubmitInFlight, type GatewayRequest } from './utils'

vi.mock('@/store/gateway', async original => ({
  ...(await original<Record<string, unknown>>()),
  retainGatewayForSessionTurn: vi.fn(async () => () => {})
}))

let restoreNative: typeof window.hermesDesktop
let home: string

beforeEach(() => {
  restoreNative = window.hermesDesktop
  home = fs.mkdtempSync(path.join(os.tmpdir(), 'send-journal-'))
  localStorage.clear()
  clearSubmitInFlight()
  $connection.set(null)
  $sessions.set([])
  $sessionStates.set({})
  $activeGatewayProfile.set('default')
})

afterEach(() => {
  cleanup()
  clearSubmitInFlight()
  window.hermesDesktop = restoreNative
  fs.rmSync(home, { recursive: true, force: true })
  vi.doUnmock('electron')
  vi.restoreAllMocks()
})

async function storeFor(owner: string) {
  vi.doMock('electron', () => ({ app: {}, ipcMain: {} }))
  const nativeModule = '../../../../../electron/prepared-submissions'
  const { preparedJournal } = await import(nativeModule)
  const store = preparedJournal(home, 'http://same-renderer-origin')

  const bridge = {
    owner: async () => owner,
    read: async () => JSON.stringify(store.read()),
    update: async (key: string, entry: string | null) => store.update(key, entry === null ? null : JSON.parse(entry)),
    compareSend: vi.fn(async (key: string, expected: string | null, entry: string | null) =>
      store.compareAndSet(key, expected === null ? null : JSON.parse(expected), entry === null ? null : JSON.parse(entry)))
  }

  return { store, bridge, bind: () => {window.hermesDesktop = { ...restoreNative, preparedSubmissions: bridge }} }
}

function composer(request: GatewayRequest) {
  let state = createClientSessionState()

  const deps = {
    activeSessionIdRef: { current: 'runtime' as string | null },
    selectedStoredSessionIdRef: { current: 'stored' as string | null },
    busyRef: { current: false },
    copy: en.desktop,
    createBackendSessionForSend: vi.fn(async () => null),
    getRoutedStoredSessionId: () => null,
    getRuntimeIdForStoredSession: () => 'runtime',
    getRouteToken: () => 'same-route',
    requestGateway: request,
    runtimeIdByStoredSessionIdRef: { current: new Map([['stored', 'runtime']]) },
    resumeStoredSession: vi.fn(),
    syncAttachmentsForSubmit: vi.fn(async (sessionId: string) => ({ sessionId, attachments: [] })),
    updateSessionState: (_sid: string, update: (state: ClientSessionState) => ClientSessionState) => { state = update(state);

 return state }
  }

  const hook = renderHook(() => useSubmitPrompt(deps))

  return { hook, state: () => state }
}

it('keeps independent window intents and reuses only the original window retry', async () => {
  const a = await storeFor('window-a')
  const b = await storeFor('window-b')
  const submissions: Record<string, unknown>[] = []
  let refuse = true

  const request = vi.fn(async (method: string, params?: Record<string, unknown>) => {
    if (method !== 'prompt.submit') {return {} as never}
    submissions.push(params!)

    if (refuse) {throw new Error('connection closed')}

    return { admission_id: params?.submission_id, status: 'started' } as never
  })

  a.bind()
  let view = composer(request)
  await act(async () => {expect(await view.hook.result.current('same text')).toBe(false)})
  view.hook.unmount()
  b.bind()
  refuse = false
  view = composer(request)
  await act(async () => {expect(await view.hook.result.current('same text')).toBe(true)})
  view.hook.unmount()
  expect(submissions[1].submission_id).not.toBe(submissions[0].submission_id)
  expect(Object.values(a.store.read())).toEqual([expect.objectContaining({ id: submissions[0].submission_id })])
  a.bind()
  view = composer(request)
  await act(async () => {expect(await view.hook.result.current('same text')).toBe(true)})
  expect(submissions[2]).toEqual(submissions[0])
  expect(a.store.read()).toEqual({})
})

it('atomically transfers an explicitly chosen draft without retargeting or letting the prior owner delete it', async () => {
  const a = await storeFor('window-a')
  const b = await storeFor('window-b')
  const request = vi.fn() as GatewayRequest
  const destination = captureSubmissionDestination('stored', request)
  const key = preparedSubmissionKey('stored', destination, 'same text', [])
  a.bind()
  const first = { id: 'intent-a', text: 'same text', attachments: [], owner: destination.owner, params: { session_id: 'runtime', submission_id: 'intent-a' } }
  await writePreparedSubmission(key, first)
  const retainedA = (await readPreparedSubmission(key))!
  b.bind()
  expect(await readPreparedSubmission(key)).toBeUndefined()
  await writePreparedSubmission(key, { ...first, id: 'intent-b', journal: undefined, params: { ...first.params, submission_id: 'intent-b' } })
  await claimPreparedSubmission(retainedA.journal!.storageKey)
  const recovered = (await readPreparedSubmission(key))!
  expect(recovered.id).toBe('intent-a')
  expect(recovered.params).toEqual(first.params)
  a.bind()
  await expect(removePreparedSubmission(key, retainedA)).rejects.toThrow('changed before retirement')
  expect(Object.values(a.store.read()).map((entry: any) => entry.id).sort()).toEqual(['intent-a', 'intent-b'])
  b.bind()
  await removePreparedSubmission(key, recovered)
  expect(Object.values(b.store.read()).map((entry: any) => entry.id)).toEqual(['intent-b'])
})

it.each(['queued', 'started', 'terminal'])('keeps a valid %s ACK successful when removal fails and never resubmits its explicit retry', async status => {
  const a = await storeFor('window-a')
  a.bind()
  const compare = a.bridge.compareSend.getMockImplementation()!
  a.bridge.compareSend.mockImplementation(async (key, expected, entry) => {
    if (entry === null) {throw new Error('fixture remove EACCES')}

    return compare(key, expected, entry)
  })

  const request = vi.fn(async (_method: string, params?: Record<string, unknown>) => ({
    admission_id: 'canonical-admission', submission_id: params?.submission_id, session_id: params?.session_id, status
  }) as never)

  const view = composer(request)
  const options = { submission_id: 'accepted-id' }
  await act(async () => {expect(await view.hook.result.current('accepted text', options)).toBe(true)})
  expect(view.state().messages.filter(message => message.error)).toEqual([])
  expect(view.state().busy).toBe(status !== 'terminal')
  expect(Object.values(a.store.read())).toEqual([expect.objectContaining({ id: 'accepted-id', acknowledged: true })])
  await act(async () => {expect(await view.hook.result.current('accepted text', options)).toBe(true)})
  expect(request).toHaveBeenCalledOnce()
})

it('keeps accepted identity in memory even when both ACK marking and removal fail', async () => {
  const a = await storeFor('window-a')
  a.bind()
  const compare = a.bridge.compareSend.getMockImplementation()!
  a.bridge.compareSend.mockImplementation(async (key, expected, entry) => {
    if (entry === null || JSON.parse(entry).acknowledged) {throw new Error('fixture disk unavailable after ACK')}

    return compare(key, expected, entry)
  })
  const request = vi.fn(async (_method: string, params?: Record<string, unknown>) => ({ admission_id: params?.submission_id, status: 'started' }) as never)
  const view = composer(request)
  const options = { submission_id: 'memory-accepted' }
  await act(async () => {expect(await view.hook.result.current('accepted', options)).toBe(true)})
  await act(async () => {expect(await view.hook.result.current('accepted', options)).toBe(true)})
  expect(request).toHaveBeenCalledOnce()
  expect(view.state().messages.filter(message => message.error)).toEqual([])
  expect(Object.values(a.store.read())).toEqual([expect.objectContaining({ id: 'memory-accepted' })])
})

it('gives a later independent Send a new ID even when the preceding accepted journal could not be removed', async () => {
  const a = await storeFor('window-a')
  a.bind()
  const compare = a.bridge.compareSend.getMockImplementation()!
  a.bridge.compareSend.mockImplementation(async (key, expected, entry) => {
    if (entry === null) {throw new Error('fixture remove EACCES')}

    return compare(key, expected, entry)
  })
  const request = vi.fn(async (_method: string, params?: Record<string, unknown>) => ({ admission_id: params?.submission_id, status: 'terminal' }) as never)
  const view = composer(request)
  await act(async () => {expect(await view.hook.result.current('same text')).toBe(true)})
  await act(async () => {expect(await view.hook.result.current('same text')).toBe(true)})
  expect(request.mock.calls[0][1]?.submission_id).not.toBe(request.mock.calls[1][1]?.submission_id)
})

it.each(['[]', ''])('refuses admission when the browser journal becomes malformed between reading and the atomic preparation lock: %j', async corrupt => {
  Object.defineProperty(window, 'hermesDesktop', {configurable: true, writable: true, value: undefined})
  const storageKey = 'hermes.desktop.preparedSubmissions.v1'
  vi.spyOn(navigator.locks, 'request').mockImplementation(async (...args) => {
    localStorage.setItem(storageKey, corrupt)
    const callback = args.at(-1) as (lock: Lock | null) => unknown

    return await callback(null)
  })
  const request = vi.fn(async (_method: string, params?: Record<string, unknown>) => ({admission_id: params?.submission_id, status: 'terminal'}) as never)
  const view = composer(request)
  await act(async () => {expect(await view.hook.result.current('Keep this draft')).toBe(false)})
  expect(request).not.toHaveBeenCalled()
  expect(localStorage.getItem(storageKey)).toBe(corrupt)
})

it.each([
  'submission_id was admitted before this handler failed',
  'submission_id must be unique',
  'invalid params for prompt.submit: another_field: Extra inputs are not permitted; submission_id retained'
])('does not downgrade a generic validation failure mentioning submission identity: %s', async message => {
  const store = await storeFor('window-a')
  store.bind()
  let failed = true

  const request = vi.fn(async (method: string, params?: Record<string, unknown>) => {
    if (method !== 'prompt.submit') {return {} as never}

    if (failed) {throw Object.assign(new Error(message), {code: 4000})}

    return {admission_id: params?.submission_id, status: 'terminal'} as never
  })

  const view = composer(request)
  await act(async () => {expect(await view.hook.result.current('Preserve this intent')).toBe(false)})
  expect(request).toHaveBeenCalledOnce()
  const original = request.mock.calls[0][1]
  expect(original?.submission_id).toBeTruthy()
  failed = false
  await act(async () => {expect(await view.hook.result.current('Preserve this intent')).toBe(true)})
  expect(request).toHaveBeenCalledTimes(2)
  expect(request.mock.calls[1][1]).toEqual(original)
})

it.each(['explicit', 'automatic'] as const)('a later exact legacy refusal cannot erase an earlier ambiguous ID-bearing submission: %s', async mode => {
  const store = await storeFor('window-a')
  store.bind()
  let phase = 'lost'
  let submitted = 0

  const request = vi.fn(async (method: string, params?: Record<string, unknown>) => {
    if (method === 'session.resume') {return {session_id: 'runtime'} as never}

    if (method !== 'prompt.submit') {return {} as never}
    expect(Object.values(store.store.read())).toEqual([expect.objectContaining({attempted: true, id: params?.submission_id})])

    if (phase === 'lost' && ++submitted === 1) {throw new Error(mode === 'automatic' ? 'request timed out: prompt.submit' : 'connection closed after server admission')}

    if (params?.submission_id) {throw Object.assign(new Error('invalid params for prompt.submit: submission_id: Extra inputs are not permitted'), {code: 4000})}

    return {status: 'terminal'} as never
  })

  const view = composer(request)
  await act(async () => {expect(await view.hook.result.current('Do this once')).toBe(false)})
  const submits = () => request.mock.calls.filter(call => call[0] === 'prompt.submit')
  const original = submits()[0][1]

  if (mode === 'explicit') {
    expect(submits()).toHaveLength(1)
    phase = 'older-server'
    await act(async () => {expect(await view.hook.result.current('Do this once')).toBe(false)})
  }

  expect(submits()).toHaveLength(2)
  expect(submits()[1][1]).toEqual(original)
})

it('a historical draft without dispatch provenance cannot downgrade even an exact legacy refusal', async () => {
  const store = await storeFor('window-a')
  store.bind()
  const request = vi.fn(async () => {throw Object.assign(new Error('invalid params for prompt.submit: submission_id: Extra inputs are not permitted'), {code: 4000})}) as GatewayRequest
  const destination = captureSubmissionDestination('stored', request)
  const key = preparedSubmissionKey('stored', destination, 'Historical draft', [])
  await writePreparedSubmission(key, {id: 'historical', owner: destination.owner, text: 'Historical draft', attachments: [], params: {session_id: 'runtime', submission_id: 'historical', text: 'Historical draft'}})
  const view = composer(request)
  await act(async () => {expect(await view.hook.result.current('Historical draft')).toBe(false)})
  expect(request).toHaveBeenCalledOnce()
  expect(request).toHaveBeenCalledWith('prompt.submit', expect.objectContaining({submission_id: 'historical'}), expect.any(Number))
})

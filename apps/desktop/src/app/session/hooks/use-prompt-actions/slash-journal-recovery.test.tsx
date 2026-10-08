import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

import { PreparedImageRecovery } from '@/app/chat/composer/prepared-image-recovery'
import { $composerAttachments, setComposerDraft } from '@/store/composer'
import { $activeGatewayProfile } from '@/store/profile'
import { $connection, $sessions } from '@/store/session'
import { $sessionStates } from '@/store/session-states'

import { actRender, Harness, type HarnessHandle } from './index-test-utils'
import {
  listPreparedDrafts,
  type PreparedSubmission,
  readPreparedSubmission,
  writePreparedSubmission
} from './prepared-submissions'
import { clearSingleFlightSessionResumeState } from './single-flight-resume'
import { clearSubmitInFlight } from './utils'

vi.mock('@/hermes', () => ({
  getLatestSessionMessages: vi.fn(async () => ({ messages: [], session_id: 'session' })),
  getProfiles: vi.fn(async () => ({ profiles: [] })),
  getSession: vi.fn(),
  PROMPT_SUBMIT_REQUEST_TIMEOUT_MS: 1_800_000,
  setApiRequestProfile: vi.fn(),
  transcribeAudio: vi.fn()
}))
vi.mock('@/store/gateway', async original => ({
  ...(await original<Record<string, unknown>>()),
  requestGatewayForAgent: vi.fn()
}))

let previousNative: typeof window.hermesDesktop
const invocation = '/work fix it'

beforeEach(() => {
  previousNative = window.hermesDesktop
  localStorage.clear()
  clearSubmitInFlight()
  clearSingleFlightSessionResumeState()
  $connection.set(null)
  $sessions.set([])
  $sessionStates.set({})
  $activeGatewayProfile.set('default')
  $composerAttachments.set([])
  setComposerDraft('')
})
afterEach(() => {
  cleanup()
  window.hermesDesktop = previousNative
  clearSubmitInFlight()
  vi.restoreAllMocks()
})

function nativeJournal() {
  const entries = new Map<string, string>()

  const compareSend = vi.fn(async (key: string, expected: string | null, next: string | null) => {
    if ((entries.get(key) ?? null) !== expected) {
      return false
    }

    if (next === null) {
      entries.delete(key)
    } else {
      entries.set(key, next)
    }

    return true
  })

  const bind = (owner: string) => {
    window.hermesDesktop = {
      ...previousNative,
      preparedSubmissions: {
        owner: async () => owner,
        read: async () =>
          JSON.stringify(Object.fromEntries([...entries].map(([key, value]) => [key, JSON.parse(value)]))),
        update: async () => {
          throw new Error('The exact atomic journal is required')
        },
        compareSend
      }
    }
  }

  return { entries, compareSend, bind }
}

function uncertainSkill() {
  const submits: Record<string, unknown>[] = []
  let expansions = 0

  const request = vi.fn(async (method: string, params?: Record<string, unknown>) => {
    if (method === 'slash.exec') {
      return { type: 'skill', name: 'work', message: `frozen expansion ${++expansions}`, display: invocation } as never
    }

    if (method === 'prompt.submit') {
      submits.push(params!)
      throw new Error('acknowledgement lost')
    }

    return {} as never
  })

  return { request, submits, expansions: () => expansions }
}

async function composer(request: ReturnType<typeof uncertainSkill>['request']) {
  let handle: HarnessHandle | null = null

  const view = await actRender(
    <Harness
      onReady={next => {
        handle = next
      }}
      rawAdmissionReceipts
      refreshSessions={async () => undefined}
      requestGateway={request}
    />
  )

  return { ...view, send: (text: string) => handle!.submitText(text) }
}

it('explicit Restore after a closed window retries the frozen slash payload and request ID', async () => {
  const journal = nativeJournal()
  journal.bind(crypto.randomUUID())
  const skill = uncertainSkill()
  const first = await composer(skill.request)
  expect(await first.send(invocation)).toBe(false)
  first.unmount()
  const serialized = [...journal.entries.values()][0]
  const retained = JSON.parse(serialized) as PreparedSubmission
  const [scope, session] = JSON.parse(retained.journal!.lookup)
  journal.bind(crypto.randomUUID())
  const [draft] = await listPreparedDrafts(session, scope)
  expect(draft?.text).toBe(invocation)
  expect(await readPreparedSubmission(retained.journal!.lookup)).toBeUndefined()
  const replacement = await composer(skill.request)
  const restore = vi.fn()

  const recovery = render(
    <PreparedImageRecovery occupied onRestore={restore} request={skill.request} sessionKey={session} />
  )

  const button = await screen.findByRole('button', { name: 'Restore draft' })
  expect((button as HTMLButtonElement).disabled).toBe(true)
  expect(screen.getByText(invocation)).toBeTruthy()
  expect(screen.queryByText(retained.text)).toBeNull()
  fireEvent.click(button)
  expect(restore).not.toHaveBeenCalled()
  expect(journal.entries.get(draft.key)).toBe(serialized)
  recovery.rerender(
    <PreparedImageRecovery occupied={false} onRestore={restore} request={skill.request} sessionKey={session} />
  )
  const beforeClaim = journal.compareSend.mock.calls.length
  await act(async () => {
    fireEvent.click(button)
  })
  expect(restore).toHaveBeenCalledExactlyOnceWith(invocation, retained.attachments)
  expect(journal.compareSend.mock.calls[beforeClaim]?.slice(0, 2)).toEqual([draft.key, draft.expected])
  expect(await replacement.send(restore.mock.calls[0][0])).toBe(false)
  expect(skill.submits).toHaveLength(2)
  expect(skill.submits[1]).toEqual(skill.submits[0])
  expect(skill.expansions()).toBe(1)
})

it('another live window does not automatically adopt a slash by matching its text', async () => {
  const journal = nativeJournal()
  const firstOwner = crypto.randomUUID()
  journal.bind(firstOwner)
  const skill = uncertainSkill()
  const first = await composer(skill.request)
  expect(await first.send(invocation)).toBe(false)
  const [originalKey, original] = [...journal.entries][0]
  const retained = JSON.parse(original) as PreparedSubmission
  journal.bind(crypto.randomUUID())
  expect(await readPreparedSubmission(retained.journal!.lookup)).toBeUndefined()
  const other = await composer(skill.request)
  expect(await other.send(invocation)).toBe(false)
  expect(skill.submits).toHaveLength(2)
  expect(skill.submits[1].submission_id).not.toBe(skill.submits[0].submission_id)
  expect(skill.expansions()).toBe(2)
  expect(journal.entries.size).toBe(2)
  expect(journal.entries.get(originalKey)).toBe(original)
  journal.bind(firstOwner)
  expect((await readPreparedSubmission(retained.journal!.lookup))?.id).toBe(retained.id)
})

it('a live owner update invalidates the displayed slash recovery before ownership changes', async () => {
  const journal = nativeJournal()
  const firstOwner = crypto.randomUUID()
  const otherOwner = crypto.randomUUID()
  journal.bind(firstOwner)
  const skill = uncertainSkill()
  const first = await composer(skill.request)
  expect(await first.send(invocation)).toBe(false)
  const retained = JSON.parse([...journal.entries.values()][0]) as PreparedSubmission
  const [, session] = JSON.parse(retained.journal!.lookup)
  journal.bind(otherOwner)
  const restore = vi.fn()
  render(<PreparedImageRecovery occupied={false} onRestore={restore} request={skill.request} sessionKey={session} />)
  const button = await screen.findByRole('button', { name: 'Restore draft' })
  journal.bind(firstOwner)
  const changed = (await readPreparedSubmission(retained.journal!.lookup))!
  changed.params = { ...changed.params, session_id: 'recovered-runtime' }
  await writePreparedSubmission(retained.journal!.lookup, changed)
  const newer = journal.entries.get(retained.journal!.storageKey)
  journal.bind(otherOwner)
  const writes = journal.compareSend.mock.calls.length
  await act(async () => {
    fireEvent.click(button)
  })
  await waitFor(() => expect((button as HTMLButtonElement).disabled).toBe(false))
  expect(restore).not.toHaveBeenCalled()
  expect(journal.compareSend).toHaveBeenCalledTimes(writes)
  expect(journal.entries.get(retained.journal!.storageKey)).toBe(newer)
  expect(await readPreparedSubmission(retained.journal!.lookup)).toBeUndefined()
  expect(skill.submits).toHaveLength(1)
})

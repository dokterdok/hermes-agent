import { cleanup } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

import { actRender, Harness, type HarnessHandle, RUNTIME_SESSION_ID } from './index-test-utils'
import { clearSingleFlightSessionResumeState } from './single-flight-resume'

vi.mock('@/hermes', () => ({
  getLatestSessionMessages: vi.fn(async () => ({ messages: [], session_id: 'session' })),
  getProfiles: vi.fn(async () => ({ profiles: [] })),
  getSession: vi.fn(),
  PROMPT_SUBMIT_REQUEST_TIMEOUT_MS: 1_800_000,
  setApiRequestProfile: vi.fn(),
  transcribeAudio: vi.fn()
}))

vi.mock('@/store/gateway', async importOriginal => ({
  ...(await importOriginal<Record<string, unknown>>()),
  requestGatewayForAgent: vi.fn()
}))

beforeEach(() => {
  clearSingleFlightSessionResumeState()
  window.localStorage.clear()
})
afterEach(cleanup)

it('a journal retirement that fails after admission still reports the delivered prompt and never reuses its identity', async () => {
  const entries = new Map<string, string>()
  const previous = window.hermesDesktop
  // The private-file journal accepts the preparation write, then the disk fills before retirement.
  window.hermesDesktop = {
    ...previous,
    preparedSubmissions: {
      read: async () =>
        JSON.stringify(Object.fromEntries([...entries].map(([key, entry]) => [key, JSON.parse(entry)]))),
      owner: async () => 'retirement-test-window',
      update: async () => {
        throw new Error('Use the atomic Send journal bridge')
      },
      compareSend: async (key, expected, entry) => {
        if ((entries.get(key) ?? null) !== expected) {
          return false
        }
        if (entry === null) {
          throw Object.assign(new Error('ENOSPC: no space left on device'), { code: 'ENOSPC' })
        }
        entries.set(key, entry)
        return true
      }
    }
  }
  const ids: unknown[] = []

  const requestGateway = vi.fn(async (method: string, params?: Record<string, unknown>) => {
    if (method !== 'prompt.submit') {
      return {} as never
    }
    ids.push(params?.submission_id)

    return { admission_id: params?.submission_id, status: 'started' } as never
  })

  try {
    let handle: HarnessHandle | null = null
    await actRender(
      <Harness
        onReady={h => (handle = h)}
        rawAdmissionReceipts
        refreshSessions={async () => undefined}
        requestGateway={requestGateway}
      />
    )
    const accepted = vi.fn()
    expect(await handle!.submitText('delivered once', { onAccepted: accepted })).toBe(true)
    expect(accepted).toHaveBeenCalledTimes(1)
    expect(handle!.state().messages.some(message => message.error)).toBe(false)
    // A later identical send is a new turn, never deduplicated into the spent admission.
    handle!.handleEvent({
      type: 'message.complete',
      session_id: RUNTIME_SESSION_ID,
      payload: { text: 'done' }
    } as never)
    expect(await handle!.submitText('delivered once')).toBe(true)
    expect(ids).toHaveLength(2)
    expect(ids[1]).not.toBe(ids[0])
  } finally {
    window.hermesDesktop = previous
  }
})

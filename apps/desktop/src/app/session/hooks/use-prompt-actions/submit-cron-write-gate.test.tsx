import { cleanup } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { getSession } from '@/hermes'
import { $notifications } from '@/store/notifications'
import { $cronRunReadOnlyVerdicts } from '@/store/read-only-transcript'
import { $cronSessions, $sessions } from '@/store/session'

import { actRender, Harness, type HarnessHandle, sessionInfo } from './index-test-utils'

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

// A unified-runtime run keeps its local-* id. Opening it while the scheduler
// owns it records a writable (false) verdict. A later sidebar refresh replaces
// $cronSessions with its latest page and the open run is no longer in it.
describe('cron write gate after a local run leaves the cached page', () => {
  const storedId = 'local-7e105fd6cca5c3d1'

  afterEach(() => {
    cleanup()
    $notifications.set([])
    $cronRunReadOnlyVerdicts.set(new Map())
    $cronSessions.set([])
    $sessions.set([])
    vi.mocked(getSession).mockReset()
  })

  it('refuses a send once the scheduler no longer owns a run that left the page', async () => {
    $cronRunReadOnlyVerdicts.set(new Map([[storedId, false]]))
    $cronSessions.set([])
    vi.mocked(getSession).mockResolvedValue(
      sessionInfo({ ended_at: null, id: storedId, scheduler_owned: false, source: 'cron' })
    )

    let handle: HarnessHandle | null = null
    const requestGateway = vi.fn(async () => ({}) as never)

    await actRender(
      <Harness
        onReady={h => (handle = h)}
        refreshSessions={async () => undefined}
        requestGateway={requestGateway as never}
        storedSessionId={storedId}
      />
    )

    expect(await handle!.submitText('into the dead cron session')).toBe(false)
    expect(getSession).toHaveBeenCalledWith(storedId, expect.anything())
    expect(requestGateway).not.toHaveBeenCalledWith('prompt.submit', expect.anything(), expect.anything())
    expect($notifications.get().some(note => note.kind === 'info')).toBe(true)
  })
})

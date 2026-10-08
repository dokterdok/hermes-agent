import type { PromptSubmitResult } from '@hermes/shared'

import { PROMPT_SUBMIT_REQUEST_TIMEOUT_MS } from '@/hermes'

import { writePreparedSubmission } from './prepared-submissions'
import type { PreparedSubmission } from './prepared-submissions'
import { SessionRecoveryAborted } from './utils'
import type { GatewayRequest } from './utils'

export type PreparedPromptReceipt = Pick<PromptSubmitResult, 'user_row_id'> & {
  admission_id?: string
  submission_id?: string
  session_id?: string
  status?: string
}

/** Stored wire identity is evidence, never something a retry can reconstruct or replace. */
export function assertPreparedPromptIdentity(prepared: PreparedSubmission) {
  const params = prepared.params

  if (
    typeof prepared.id !== 'string' ||
    !prepared.id.trim() ||
    !params ||
    typeof params !== 'object' ||
    Array.isArray(params) ||
    !Object.hasOwn(params, 'submission_id') ||
    params.submission_id !== prepared.id
  ) {
    throw new Error('Saved Send delivery identity is inconsistent; retry was blocked')
  }
}

/** The prompt preparation barrier validates evidence before changing any stored draft. */
export async function persistIdentifiedPrompt(key: string, prepared: PreparedSubmission): Promise<void> {
  assertPreparedPromptIdentity(prepared)
  await writePreparedSubmission(key, prepared)
}

/** The legacy schema validator issues this exact refusal before the prompt handler runs. */
function unsupportedSubmissionIdentity(error: unknown): boolean {
  const refusal = error as { code?: unknown; message?: unknown } | null

  return (
    refusal?.code === 4000 &&
    typeof refusal.message === 'string' &&
    refusal.message.startsWith('invalid params for prompt.submit: submission_id: Extra inputs are not permitted')
  )
}

function assertDestinationCurrent(driftReason: () => string | null, sessionId: string) {
  const reason = driftReason()

  if (reason) {
    throw new SessionRecoveryAborted(reason, sessionId)
  }
}

/** Each actual request durably records uncertainty before dispatch. A later refusal cannot erase
 * an earlier possibly admitted request, including callback re-entry after session recovery. */
export async function requestPreparedPrompt(
  request: GatewayRequest,
  prepared: PreparedSubmission,
  retryKey: string,
  sessionId: string,
  driftReason: () => string | null
): Promise<{ result: PreparedPromptReceipt; legacyAccepted: boolean }> {
  if (prepared.legacyAttempted) {
    throw new Error('Legacy submission acknowledgement is unknown; automatic retry is unsafe')
  }

  assertPreparedPromptIdentity(prepared)
  // Missing historical markers do not prove an old draft was never dispatched.
  const firstAttempt = prepared.attempted === false
  prepared.attempted = true
  await persistIdentifiedPrompt(retryKey, prepared)
  assertDestinationCurrent(driftReason, sessionId)
  const params: Record<string, unknown> = { ...prepared.params, session_id: sessionId }

  try {
    return {
      result: await request<PreparedPromptReceipt>('prompt.submit', params, PROMPT_SUBMIT_REQUEST_TIMEOUT_MS),
      legacyAccepted: false
    }
  } catch (error) {
    if (!firstAttempt || !unsupportedSubmissionIdentity(error) || prepared.legacyAttempted) {
      throw error
    }

    prepared.legacyAttempted = true
    await persistIdentifiedPrompt(retryKey, prepared)
    assertDestinationCurrent(driftReason, sessionId)
    const { submission_id: _id, ...legacyParams } = params

    return {
      result: await request<PreparedPromptReceipt>('prompt.submit', legacyParams, PROMPT_SUBMIT_REQUEST_TIMEOUT_MS),
      legacyAccepted: true
    }
  }
}

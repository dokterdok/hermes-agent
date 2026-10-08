import type { Translations } from '@/i18n'
import { notifyError } from '@/store/notifications'
import { requestDesktopOnboarding } from '@/store/onboarding'

import type { PromptSessionUpdater } from './prompt-submit-receipt'
import { inlineErrorMessage, isProviderSetupError, isSessionBusyError, isSessionNotOwnedError } from './utils'

/** Failed foreground and queued admissions have distinct UI owners; neither can reset a different session. */
export function reportPromptSubmissionFailure(
  err: unknown,
  context: {
    fromQueue?: boolean
    queueAdmission: boolean
    copy: Translations['desktop']
    sessionId: string
    storedSessionId: string | null
    updateSessionState: PromptSessionUpdater
    targetIsCurrentView: () => boolean
  }
) {
  const { fromQueue, queueAdmission, copy, sessionId, storedSessionId, updateSessionState, targetIsCurrentView } =
    context

  // A queued drain that raced a not-yet-settled turn gets a transient
  // "session busy" (4009). Don't surface an error bubble/toast — the entry
  // stays queued and the composer's bounded auto-drain retries when idle.
  if (fromQueue && isSessionBusyError(err)) {
    return
  }

  if (queueAdmission) {
    notifyError(err, copy.promptFailed)

    return
  }

  const message = inlineErrorMessage(err, copy.promptFailed)
  const occurredAt = Date.now() / 1000
  // Another surface owns the session (#106217): a deterministic gateway
  // refusal, so the error card drops Retry and offers a new session.
  const notOwned = isSessionNotOwnedError(err)

  updateSessionState(
    sessionId,
    state => ({
      ...state,
      messages: [
        ...state.messages,
        {
          id: `assistant-error-${Date.now()}`,
          role: 'assistant',
          parts: [],
          error: message || copy.promptFailed,
          ...(notOwned && { errorSurface: { layer: 'gateway', code: 'SESSION_NOT_OWNED', retryable: false } }),
          branchGroupId: state.pendingBranchGroup ?? undefined,
          completedAt: occurredAt,
          timestamp: occurredAt
        }
      ],
      busy: false,
      awaitingResponse: false,
      pendingBranchGroup: null,
      sawAssistantPayload: true,
      // The failed submit's clock seed dies with the turn it never got.
      turnStartedAt: null
    }),
    storedSessionId
  )

  if (targetIsCurrentView() && isProviderSetupError(err)) {
    requestDesktopOnboarding(copy.providerCredentialRequired)

    return
  }

  if (targetIsCurrentView()) {
    notifyError(err, copy.promptFailed)
  }

  return
}

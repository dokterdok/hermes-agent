import type { ClientSessionState } from '@/app/types'
import { trackPendingSubmission } from '@/store/pending-submissions'

import type { PreparedPromptReceipt } from './prepared-prompt-request'

export type PromptSessionUpdater = (
  sessionId: string,
  update: (state: ClientSessionState) => ClientSessionState,
  storedSessionId?: string | null
) => ClientSessionState

/** A matching admission binds one optimistic occurrence; a terminal duplicate never clears another live turn. */
export function applyPromptSubmissionReceipt(
  result: PreparedPromptReceipt,
  context: {
    id: string
    text: string
    displayText?: string
    receivedSessionId: string
    liveSessionId: string
    storedSessionId: string | null
    optimisticId: string
    legacyAccepted: boolean
    updateSessionState: PromptSessionUpdater
    releaseBusy: () => void
  }
): boolean {
  const {
    id,
    text,
    displayText,
    receivedSessionId,
    liveSessionId,
    storedSessionId,
    optimisticId,
    legacyAccepted,
    updateSessionState,
    releaseBusy
  } = context

  if (
    !legacyAccepted &&
    ((result?.submission_id ?? result?.admission_id) !== id ||
      (result?.session_id !== undefined && result.session_id !== receivedSessionId) ||
      !['queued', 'started', 'terminal'].includes(result?.status ?? ''))
  ) {
    return false
  }

  if ((result?.submission_id ?? result?.admission_id) === id) {
    trackPendingSubmission(storedSessionId ?? liveSessionId, {
      id: id,
      text,
      displayText,
      status: result.status
    })

    // Queued is also the initial receipt for an idle session's first
    // turn. Keep its input and any start event that raced this ACK;
    // explicit queue-only sends never inserted an optimistic bubble.
    if (result.status === 'terminal') {
      // Deduplication does not start a turn or promise another terminal
      // event. Remove our duplicate bubble, but preserve any live turn
      // that an owner event established while the receipt was in flight.
      const next = updateSessionState(
        receivedSessionId,
        state => ({
          ...state,
          messages: state.messages.filter(message => message.id !== optimisticId),
          ...(!state.turnLive &&
            !state.streamId &&
            !state.sawAssistantPayload && {
              busy: false,
              awaitingResponse: false,
              pendingBranchGroup: null,
              turnStartedAt: null
            })
        }),
        storedSessionId
      )

      if (!next.busy && !next.awaitingResponse) {
        releaseBusy()
      }
    }
  }

  const rowId = result?.user_row_id

  if (typeof rowId === 'number' && Number.isSafeInteger(rowId) && rowId > 0) {
    // The worker may finish before this acknowledgement arrives. Bind
    // only this send's optimistic occurrence; never reset live state or
    // assume the newest user row still belongs to this RPC.
    updateSessionState(receivedSessionId, state => {
      const index = state.messages.findIndex(message => message.id === optimisticId && message.role === 'user')

      if (index < 0 || state.messages[index].rowId === rowId) {
        return state
      }

      return {
        ...state,
        messages: state.messages.map((message, i) => (i === index ? { ...message, rowId } : message))
      }
    })
  }

  return true
}

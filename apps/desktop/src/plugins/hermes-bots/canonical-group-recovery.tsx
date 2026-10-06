import { Button } from '@hermes/plugin-sdk'

import { useCanonicalGroupLabels } from './canonical-group-labels'
import type { PreparedCanonicalGroupSend, RecoverableCanonicalGroupSend } from './canonical-group-send'
import { groupFailureDetail } from './group-activity'

/** Recovery is read-only; a saved message is sent only by a separate explicit action. */
export function CanonicalGroupRecoveryNotices({
  notice,
  readError,
  error,
  journalError,
  journalLoading,
  busy,
  unavailable,
  onRefresh,
  onJournalReload
}: {
  notice: string
  readError: string
  error: string
  journalError: string
  journalLoading: boolean
  busy: boolean
  unavailable: boolean
  onRefresh: () => void
  onJournalReload: () => void
}) {
  const labels = useCanonicalGroupLabels()

  return (
    <div className="grid gap-2 pb-2 text-xs text-(--ui-text-secondary)">
      {notice && <p aria-live="polite">{notice}</p>}
      {readError && (
        <div role="alert">
          <p>{labels.driverUnavailable}</p>
          <Button onClick={onRefresh} size="inline" variant="text">
            {labels.refresh}
          </Button>
          <details>
            <summary>{labels.setupDetails}</summary>
            <p className="whitespace-pre-wrap break-words">{groupFailureDetail(readError)}</p>
          </details>
        </div>
      )}
      {error && (
        <div role="alert">
          <p>{labels.pendingActionUnconfirmed}</p>
          <details>
            <summary>{labels.setupDetails}</summary>
            <p className="whitespace-pre-wrap break-words">{groupFailureDetail(error)}</p>
          </details>
        </div>
      )}
      {journalError && (
        <div role="alert">
          <p>{labels.journalLoadFailed}</p>
          <p>{labels.journalLoadHint}</p>
          <Button disabled={busy || journalLoading} onClick={onJournalReload} size="inline" variant="text">
            {labels.journalReload}
          </Button>
          <details>
            <summary>{labels.setupDetails}</summary>
            <p className="whitespace-pre-wrap break-words">{groupFailureDetail(journalError)}</p>
          </details>
        </div>
      )}
      {unavailable && <p>{labels.driverUnavailable}</p>}
    </div>
  )
}

export function CanonicalGroupSavedMessages({
  pending,
  hint,
  recoveries,
  disabled,
  onRestore
}: {
  pending: PreparedCanonicalGroupSend | null
  hint: string
  recoveries: RecoverableCanonicalGroupSend[]
  disabled: boolean
  onRestore: (recovery: RecoverableCanonicalGroupSend) => void
}) {
  const labels = useCanonicalGroupLabels()

  return (
    <>
      {pending && <p role="status">{labels.restoredPendingSend}</p>}
      {hint && <p aria-live="polite">{hint}</p>}
      {recoveries
        .filter(recovery => recovery.entry.params.event_id !== pending?.params.event_id)
        .map(recovery => (
          <div className="flex items-center gap-2" key={recovery.storageKey}>
            <span className="min-w-0 flex-1 truncate">
              {String(recovery.entry.params.payload.text || labels.groupMessage)}
            </span>
            <Button disabled={disabled} onClick={() => onRestore(recovery)}>
              {labels.restorePendingSend}
            </Button>
          </div>
        ))}
    </>
  )
}

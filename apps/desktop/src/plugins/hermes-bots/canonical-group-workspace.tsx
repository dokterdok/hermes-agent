import { useEffect, useRef, useState } from 'react'
import type { ReactNode } from 'react'

import { CanonicalGroupComposer } from './canonical-group-composer'
import { CanonicalGroupHeader } from './canonical-group-header'
import { type CanonicalGroupEvent, CanonicalGroupHistory } from './canonical-group-history'
import { useCanonicalGroupLabels } from './canonical-group-labels'
import { CanonicalGroupPendingActions } from './canonical-group-pending-actions'
import { CanonicalGroupRecoveryNotices, CanonicalGroupSavedMessages } from './canonical-group-recovery'
import { updateCanonicalGroupName } from './canonical-group-registry'
import { attemptCanonicalGroupSend, claimCanonicalGroupSend, listCanonicalGroupSends, prepareCanonicalGroupSend, readCanonicalGroupSend, retireCanonicalGroupSend, settleCanonicalGroupSend } from './canonical-group-send'
import type { PreparedCanonicalGroupSend, RecoverableCanonicalGroupSend } from './canonical-group-send'
import { actCanonicalGroup, canonicalGroupRequest } from './canonical-groups'
import type { CanonicalGroupBinding, CanonicalPendingAction, CanonicalRoomMember } from './canonical-groups'

type RoomEvent = CanonicalGroupEvent
interface Attachment {
  attachment_id?: string
  event_id?: string
  kind: string
  name: string
  mime: string
  size?: number }
interface DriverStatus {
  running?: boolean
  working?: boolean
  blocked?: boolean
  pending_actions?: CanonicalPendingAction[]
}
interface RoomState { room: { name: string; authority_epoch?: number; members?: CanonicalRoomMember[] }
  driver_status?: DriverStatus }
type Labels = ReturnType<typeof useCanonicalGroupLabels>

// These reasons prove only this attempt had no effect, not any earlier attempt.
const TERMINAL_SEND_REFUSALS = new Set(['invalid_params', 'permission_denied', 'unknown_execution', 'stale_generation'])

function sendOutcome(error: unknown): 'refused' | 'retryable' | 'unknown' {
  const failure = error as { code?: unknown; data?: { reason?: unknown } } | null

  if (failure?.code !== 4001) {return 'unknown'}

  return TERMINAL_SEND_REFUSALS.has(String(failure.data?.reason)) ? 'refused' : 'retryable'
}

/** A send is acknowledged only by a receipt for this exact durable event. */
async function requestPreparedSend(
  binding: CanonicalGroupBinding,
  exact: PreparedCanonicalGroupSend,
  unconfirmed: string
) {
  const result = await canonicalGroupRequest<{ accepted?: unknown; client_event_id?: unknown } | undefined>(
    binding,
    'groups.send',
    exact.params
  )

  if (
    !result ||
    typeof result !== 'object' ||
    Array.isArray(result) ||
    (Object.hasOwn(result, 'accepted') && result.accepted !== true) ||
    result.client_event_id !== exact.params.event_id
  ) {
    throw new Error(unconfirmed)
  }
}

/** Live work comes only from the driver; unresolved members are listed beside it, never instead. */
function roomStatus(status: DriverStatus, labels: Labels) {
  const actions = status.pending_actions || []
  const approvals = actions.filter(action => action.kind === 'approval').length
  const parts = [status.working ? labels.statusWorking : status.running === false ? labels.statusStopped : labels.statusIdle]

  if (status.blocked) {parts.push(labels.statusBlocked)}

  if (approvals) {parts.push(labels.statusApprovals.replace('{count}', String(approvals)))}

  if (actions.length > approvals) {parts.push(labels.statusAttention.replace('{count}', String(actions.length - approvals)))}

  return parts.join(' · ')
}

/** Room controls rendered by the owner of the binding (rename, disband). */
export type CanonicalRoomActions = (room: { name: string; refresh: () => void }) => ReactNode

export function CanonicalGroupWorkspace({ binding, visible = true, onBack, actions }: {
  binding: CanonicalGroupBinding
  visible?: boolean
  onBack?: () => void
  actions?: CanonicalRoomActions
}) {
  // Remount on identity changes: old polls and pending confirmations never cross rooms.
  return (
    <CanonicalRoomView actions={actions} binding={binding} key={JSON.stringify(binding)} onBack={onBack} visible={visible} />
  )
}

function CanonicalRoomView({ binding: initialBinding, visible, onBack, actions }: {
  binding: CanonicalGroupBinding
  visible: boolean
  onBack?: () => void
  actions?: CanonicalRoomActions
}) {
  const [binding] = useState(() => ({ ...initialBinding }))
  const labels = useCanonicalGroupLabels()
  const [state, setState] = useState<RoomState | null>(null)
  const [events, setEvents] = useState<RoomEvent[]>([])
  const [error, setError] = useState('')
  const [readError, setReadError] = useState('')
  const [draft, setDraft] = useState('')
  const [attachments, setAttachments] = useState<Attachment[]>([])
  const [uploading, setUploading] = useState(false)
  const uploadingRef = useRef(false)
  const [restored, setRestored] = useState(false)
  const [journalError, setJournalError] = useState('')
  const [journalLoading, setJournalLoading] = useState(true)
  const [journalReload, setJournalReload] = useState(0)
  const pendingRecord = useRef<PreparedCanonicalGroupSend | null>(null)
  const [pending, setPending] = useState<PreparedCanonicalGroupSend | null>(null)
  const [recoveries, setRecoveries] = useState<RecoverableCanonicalGroupSend[]>([])
  const inputRevision = useRef(0)
  const [busy, setBusy] = useState(false)
  const busyRef = useRef(false)
  const [stopping, setStopping] = useState(false)
  const stopPending = useRef(false)
  const stopIntent = useRef<string | null>(null)
  const [notice, setNotice] = useState('')
  const [sendHint, setSendHint] = useState('')
  const alive = useRef(true)
  const revision = useRef(0)
  // The log is append-only within one authority epoch: read only what is new.
  const seen = useRef<{ epoch?: number; seq: number }>({ seq: 0 })

  const showPending = (entry: PreparedCanonicalGroupSend | null) => {
    pendingRecord.current = entry
    setPending(entry)
  }

  // Mount lifetime is separate from a read-only journal retry.
  // eslint-disable-next-line no-restricted-syntax -- component lifetime, not a mirror of reactive atom values
  useEffect(() => {
    alive.current = true

    return () => {
      alive.current = false
      revision.current++
    }
  }, [])


  useEffect(() => {
    let cancelled = false
    const editing = inputRevision.current
    const emptyDraft = !draft.trim() && !attachments.length
    setJournalLoading(true)
    setJournalError('')
    setRestored(false)
    void Promise.all([readCanonicalGroupSend(binding), listCanonicalGroupSends(binding)]).then(([entry, recoverable]) => {
      if (cancelled) {return}

      if (entry && !pendingRecord.current && emptyDraft && inputRevision.current === editing) {
          showPending(entry)
        setDraft(String(entry.params.payload.text ?? ''))
        setAttachments((entry.params.payload.attachments as Attachment[] | undefined) ?? [])
      }

      setRecoveries(current => {
          const byKey = new Map(current.map(recovery => [recovery.storageKey, recovery]))

          for (const recovery of recoverable) {
            byKey.set(recovery.storageKey, recovery)
          }

          return [...byKey.values()]
        })
      setRestored(true)
    }).catch(e => { if (!cancelled) {
          setJournalError(e instanceof Error ? e.message : String(e))} })
      .finally(() => {
        if (!cancelled) {
          setJournalLoading(false)
        }
      })

    return () => {
      cancelled = true
    }
    // Capture the editor revision at the start; changing a draft never triggers another storage read.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [binding, journalReload])

  const refresh = async () => {
    const version = ++revision.current
    const snapshot = await canonicalGroupRequest<RoomState>(binding, 'groups.state', { room_id: binding.roomId })
    const epoch = snapshot.room?.authority_epoch
    const fresh = epoch !== seen.current.epoch
    const log: RoomEvent[] = []
    let cursor = fresh ? 0 : seen.current.seq

    for (;;) {
      const page = await canonicalGroupRequest<{ events: RoomEvent[]; has_more?: boolean; next_seq?: number }>(binding, 'groups.log', { room_id: binding.roomId, since_seq: cursor, limit: 100 })
      log.push(...page.events)

      if (!page.has_more) {break}
      const next = page.events.at(-1)?.seq

      if (!next || next <= cursor) {throw new Error(labels.invalidLogCursor)}
      cursor = next
    }

    if (alive.current && version === revision.current) {
      const last = seen.current.seq
      seen.current = { epoch, seq: log.at(-1)?.seq ?? cursor }
      updateCanonicalGroupName(binding, snapshot.room.name)
      setState(snapshot)
      setEvents(current => (fresh ? log : [...current, ...log.filter(event => event.seq > last)]))
      setReadError('')
    }
  }

  useEffect(() => {
    if (!visible) {return}
    let cancelled = false
    let timer: ReturnType<typeof setTimeout>

    const poll = async () => {
      try { await refresh() } catch (e) { if (!cancelled) {setReadError(String(e instanceof Error ? e.message : e))} }

      if (!cancelled) {timer = setTimeout(() => void poll(), 2000)}
    }

    void poll()

    return () => { cancelled = true
      revision.current++
      clearTimeout(timer) }
    // The keyed parent freezes the authority binding for this lifetime.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [visible])

  const mutate = async (operation: () => Promise<unknown>, rethrow = false) => {
    if (busyRef.current) {if (rethrow) {throw new Error(labels.pendingActionUnconfirmed)}

      return}

    busyRef.current = true
    setBusy(true)
    setError('')

    try { await operation()

      if (alive.current) {await refresh()} }
    catch (e) { if (alive.current) {setError(e instanceof Error ? e.message : String(e))}

      if (rethrow) {throw e} }
    finally {
      busyRef.current = false

      if (alive.current) {setBusy(false)}
    }
  }

  const adoptPrepared = (exact: PreparedCanonicalGroupSend, editing: number) => {
    if (!alive.current || inputRevision.current !== editing) {
      return
    }

    showPending(exact)
    setDraft(String(exact.params.payload.text ?? ''))
    setAttachments((exact.params.payload.attachments as Attachment[] | undefined) ?? [])
  }

  const clearPrepared = (exact: PreparedCanonicalGroupSend, editing: number) => {
    if (!alive.current) {
      return
    }

    if (pendingRecord.current?.params.event_id === exact.params.event_id) {
      showPending(null)
    }

    if (inputRevision.current === editing) {
      setDraft('')
      setAttachments([])
    }
  }

  const send = () => {
    if (
      !visible ||
      !restored ||
      busyRef.current ||
      uploadingRef.current || !state?.driver_status || (!pending && !draft.trim() && !attachments.length)) {return}

    setSendHint('')
    const editing = inputRevision.current
    void mutate(async () => {
      const exact = pending ?? (await prepareCanonicalGroupSend(binding, { text: draft, attachments }))

      if (!alive.current) {
        return
      }

      adoptPrepared(exact, editing)

      const freshAttempt = await attemptCanonicalGroupSend(binding, exact)

      if (!alive.current) {return}

      try {
        await requestPreparedSend(binding, exact, labels.unconfirmedSend)
      } catch (error) {
        const outcome = freshAttempt ? sendOutcome(error) : 'unknown'

        // Only the room that sent it gets the text back; a view that moved on keeps the journal entry.
        if (outcome === 'refused' && alive.current) {
          await retireCanonicalGroupSend(binding, exact.params.event_id, exact)

          if (alive.current) {
            if (pendingRecord.current?.params.event_id === exact.params.event_id) {
              showPending(null)
            }
          }
        }

        if (alive.current) {setSendHint(outcome === 'refused' ? labels.sendRefused : outcome === 'retryable' ? labels.sendNotYet : labels.sendMaybe)}
        throw error
      }

      await settleCanonicalGroupSend(binding, exact)

      if (alive.current) {
        clearPrepared(exact, editing)

        try {
          const recoverable = await listCanonicalGroupSends(binding)

          if (alive.current) {setRecoveries(recoverable)}
        } catch (error) {console.warn('Accepted group Send recovery journal could not be read', error)}
      }
    })
  }

  const restore = (recovery: RecoverableCanonicalGroupSend) => {
    if (pending || draft.trim() || attachments.length) {return}
    const editing = inputRevision.current
    void mutate(async () => {
      const exact = await claimCanonicalGroupSend(binding, recovery)

      if (!alive.current) {return}

      if (inputRevision.current === editing) {
        showPending(exact)
        setDraft(String(exact.params.payload.text ?? ''))
        setAttachments((exact.params.payload.attachments as Attachment[] | undefined) ?? [])
      }

      const recoverable = await listCanonicalGroupSends(binding)

      if (alive.current) {setRecoveries(recoverable)}
    })
  }

  const act = (action: CanonicalPendingAction, choice?: 'once' | 'deny') =>
    mutate(() => actCanonicalGroup(binding, action, choice), true)

  // Stop has its own busy state: it must stay available while a Send is in flight.
  const stop = async () => {
    if (stopPending.current) {return}
    stopPending.current = true
    stopIntent.current ??= crypto.randomUUID()
    setStopping(true)
    setNotice('')
    setError('')

    try {
      const result = await canonicalGroupRequest<{ cancelled?: number }>(binding, 'groups.stop', { room_id: binding.roomId, cancel_id: stopIntent.current })
      const cancelled = result?.cancelled

      if (typeof cancelled !== 'number' || !Number.isSafeInteger(cancelled) || cancelled < 0) {throw new Error(labels.pendingActionUnconfirmed)}
      stopIntent.current = null

      if (alive.current) {setNotice(cancelled ? labels.stopped.replace('{count}', String(cancelled)) : labels.nothingRunning)}

      if (alive.current) {await refresh()}
    } catch (e) {
      if (alive.current) {setError(e instanceof Error ? e.message : String(e))}
    } finally {
      stopPending.current = false

      if (alive.current) {setStopping(false)}
    }
  }

  const members = state?.room.members ?? []
  const name = state?.room.name || labels.loadingGroup

  return (
    <section className="flex h-full min-h-0 flex-col" data-slot="canonical-group-chat">
      <CanonicalGroupHeader
        members={members}
        name={name}
        onBack={onBack}
        status={state?.driver_status && roomStatus(state.driver_status, labels)}
        visible={visible}
        working={state?.driver_status?.working}
      >
        {visible &&
          state && actions?.({ name: state.room.name, refresh: () => void refresh().catch(e => setReadError(String(e))) })}
    </CanonicalGroupHeader>
      <div
        aria-label={labels.conversationHistory}
        className="min-h-0 flex-1 overflow-y-auto overscroll-y-contain px-2"
        role="log"
      >
        <div className="mx-auto w-full max-w-3xl pb-4">
          <CanonicalGroupHistory binding={binding} disabled={!visible} events={events} members={members} />
          {state && !events.length && (
            <div className="grid gap-1 px-3 py-10 text-center">
              <p className="text-sm text-(--ui-text-secondary)">{labels.emptyHistory}</p>
              <p className="text-xs text-(--ui-text-quaternary)">{labels.emptyHistoryHint}</p>
            </div>
          )}
        </div>
      </div>
      <div className="mx-auto w-full max-w-3xl shrink-0 px-4 pb-4">
        <CanonicalGroupRecoveryNotices
          busy={busy}
          error={error}
          journalError={journalError}
          journalLoading={journalLoading}
          notice={notice}
          onJournalReload={() => setJournalReload(current => current + 1)}
          onRefresh={() => void refresh().catch(e => setReadError(String(e)))}
          readError={readError}
          unavailable={Boolean(state && !state.driver_status)}
        />
        {visible && (
          <CanonicalGroupPendingActions
            actions={state?.driver_status?.pending_actions ?? []}
            busy={busy}
            members={members}
            onAction={act}
            onDiscard={action => mutate(() => actCanonicalGroup(binding, action), true)}
            onRefresh={refresh}
          />
        )}
        <CanonicalGroupSavedMessages
          disabled={!visible || busy || !!pending || !!draft.trim() || !!attachments.length}
          hint={sendHint}
          onRestore={restore}
          pending={pending}
          recoveries={recoveries}
        />
        <CanonicalGroupComposer
          attachments={attachments}
          binding={binding}
          busy={busy}
          draft={draft}
          hasDriver={Boolean(state?.driver_status)}
          members={members}
          name={name}
          onAttachments={value => {
            inputRevision.current++
            setAttachments(value)
          }}
          onDraft={value => {
            inputRevision.current++
            setDraft(value)
          }}
          onSend={send}
          onStop={() => void stop()}
          onUploading={value => {
            uploadingRef.current = value

            if (alive.current) {
              setUploading(value)
            }
          }}
          pending={Boolean(pending)}
          restored={restored}
          stopping={stopping}
          uploading={uploading}
          visible={visible}
        />
      </div>
    </section>
  )
}

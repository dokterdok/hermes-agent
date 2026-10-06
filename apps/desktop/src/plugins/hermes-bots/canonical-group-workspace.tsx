import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import type { ReactNode } from 'react'

import { CanonicalGroupBackups } from './canonical-group-backups'
import { CanonicalGroupComposer } from './canonical-group-composer'
import { carryOwnMessages, RoomComposerSlot, RoomContinuityBanners, useRoomContinuity } from './canonical-group-continuity'
import { CanonicalGroupHeader } from './canonical-group-header'
import { type CanonicalGroupEvent, CanonicalGroupHistory } from './canonical-group-history'
import { useCanonicalGroupLabels } from './canonical-group-labels'
import { CanonicalGroupPendingActions } from './canonical-group-pending-actions'
import { CanonicalGroupRecoveryNotices, CanonicalGroupSavedMessages } from './canonical-group-recovery'
import { moveCanonicalGroup, updateCanonicalGroupName } from './canonical-group-registry'
import { CanonicalGroupRetirementNotice, canonicalRetirementStatus } from './canonical-group-retirement'
import type { RetirementStatus } from './canonical-group-retirement'
import { acceptedCanonicalGroupSend, attemptCanonicalGroupSend, claimCanonicalGroupSend, listCanonicalGroupSends, prepareCanonicalGroupSend, readCanonicalGroupSend, rehomeCanonicalGroupSends, retireCanonicalGroupSend, sendOutcome } from './canonical-group-send'
import type { AcceptedCanonicalGroupSend, PreparedCanonicalGroupSend, RecoverableCanonicalGroupSend } from './canonical-group-send'
import { actCanonicalGroup, canonicalGroupRequest, isPendingFileAction } from './canonical-groups'
import type { CanonicalGroupBinding, CanonicalGroupRoute, CanonicalPendingAction, CanonicalRoomMember } from './canonical-groups'

type RoomEvent = CanonicalGroupEvent
interface Attachment { attachment_id?: string; event_id?: string; kind: string; name: string; mime: string; size?: number }
interface DriverStatus {
  retiring?: boolean
  peer_cleanup?: unknown
  running?: boolean
  working?: boolean
  blocked?: boolean
  counts?: Record<string, number>
  pending_actions?: CanonicalPendingAction[]
  /** Work that waits for the computer that has its Bot or file (`state: "waiting_for_host"`). */
  tasks?: { task_id?: unknown; member_id?: unknown; state?: unknown; resource?: unknown; host_name?: unknown }[]
}
interface RoomState { room: { room_id?: string; disbanded_at?: number; name: string; authority_epoch?: number; members?: CanonicalRoomMember[] }; driver_status?: DriverStatus }
type Labels = ReturnType<typeof useCanonicalGroupLabels>

function onlyPendingFiles(status: DriverStatus): boolean {
  const actions = status.pending_actions ?? []

  return actions.length > 0 && actions.every(isPendingFileAction) && !(status.counts?.unknown || status.counts?.stopping)
}

function needsAttention(status: DriverStatus): boolean {
  return Boolean(status.blocked && !onlyPendingFiles(status) ||
    status.pending_actions?.some(action => action.kind !== 'stopping' && !isPendingFileAction(action)))
}

/** Live work comes only from the driver; unresolved members are listed beside it, never instead. */
function roomStatus(status: DriverStatus, labels: Labels) {
  const actions = status.pending_actions || []
  const approvals = actions.filter(action => action.kind === 'approval').length
  const stopping = (status.counts?.stopping ?? 0) > 0 || actions.some(action => action.kind === 'stopping')
  const attention = actions.filter(action => action.kind !== 'approval' && action.kind !== 'stopping' && !isPendingFileAction(action)).length
  const parts = [stopping ? labels.statusStopping : status.working || actions.some(isPendingFileAction) ? labels.statusWorking : status.running === false ? labels.statusStopped : labels.statusIdle]

  if (status.blocked && !onlyPendingFiles(status)) {parts.push(labels.statusBlocked)}

  if (approvals) {parts.push(labels.statusApprovals.replace('{count}', String(approvals)))}

  if (attention) {parts.push(labels.statusAttention.replace('{count}', String(attention)))}

  return parts.join(' · ')
}

/** Pure presentation from this room's authoritative driver, never a foreground session. */
function roomPresentation(state: RoomState | null, labels: Labels) {
  const driver = state?.driver_status
  const pendingActions = driver?.pending_actions ?? []

  return {
    name: state?.room.name || labels.loadingGroup, members: state?.room.members ?? [], pendingActions,
    attention: driver ? needsAttention(driver) : false,
    status: driver ? roomStatus(driver, labels) : undefined,
    working: Boolean(driver?.working || pendingActions.some(isPendingFileAction))
  }
}

function roomHasWork(driver: DriverStatus | undefined, occupied: boolean): boolean {
  if (occupied || driver?.working) {return true}

  if (['queued', 'running', 'stopping'].some(status => (driver?.counts?.[status] ?? 0) > 0)) {return true}

  return driver?.pending_actions?.some(action => action.kind !== 'output_retry') ?? false
}

/** Room controls rendered by the owner of the binding (rename, disband). */
export type CanonicalRoomActions = (room: { name: string; refresh: () => void; latestFileSeq: number; visible: boolean; retirement: RetirementStatus | null; onRetirementRequested: () => void }) => ReactNode

export function CanonicalGroupWorkspace({ binding, visible = true, onBack, actions }: {
  binding: CanonicalGroupBinding; visible?: boolean; onBack?: () => void; actions?: CanonicalRoomActions
}) {
  // A group that continues on another computer keeps this view; only its route changes. The registry follows
  // too, so the parent then renders the moved binding and this override retires.
  const [moved, setMoved] = useState<{ from: string; to: string } | null>(null)
  const original = JSON.stringify(binding)
  const currentKey = moved?.from === original ? moved.to : original
  const current = useMemo(() => JSON.parse(currentKey) as CanonicalGroupBinding, [currentKey])

  const onMoved = useCallback((route: CanonicalGroupRoute) => {
    const to = { connectionId: route.connectionId, profile: route.profile, roomId: current.roomId }

    carryOwnMessages(current, to)
    void rehomeCanonicalGroupSends(current, to).catch(error => console.warn('Unsent group messages could not follow the group', error))
      .finally(() => {moveCanonicalGroup(current, to); setMoved({ from: original, to: JSON.stringify(to) })})
  }, [current, original])

  // Remount on identity changes: old polls and pending confirmations never cross rooms.
  return <CanonicalRoomView actions={actions} binding={current} key={currentKey} onBack={onBack} onMoved={onMoved} visible={visible} />
}

function CanonicalRoomView({ binding: initialBinding, visible, onBack, onMoved, actions }: {
  binding: CanonicalGroupBinding; visible: boolean; onBack?: () => void; onMoved: (route: CanonicalGroupRoute) => void
  actions?: CanonicalRoomActions
}) {
  const [binding] = useState(() => ({ ...initialBinding }))
  const labels = useCanonicalGroupLabels()
  const [state, setState] = useState<RoomState | null>(null)
  const [events, setEvents] = useState<RoomEvent[]>([])
  const [error, setError] = useState('')
  const [readError, setReadError] = useState('')
  const [retirementRequested, setRetirementRequested] = useState(false)
  const retirementIntent = useRef(false)
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
  const transcript = useRef<HTMLDivElement>(null)
  const following = useRef(true)
  const revision = useRef(0)
  // The log is append-only within one authority epoch: read only what is new.
  const seen = useRef<{ epoch?: number; seq: number }>({ seq: 0 })
  const continuity = useRoomContinuity({ binding, visible, readError, events, roomName: state?.room.name ?? '', onMoved: route => {if (!retirementIntent.current) {onMoved(route)}}, restored, composer: { setDraft, setAttachments, setHint: setSendHint } })

  const showPending = (entry: PreparedCanonicalGroupSend | null) => {
    pendingRecord.current = entry
    setPending(entry)
  }

  const clearPending = (entry: PreparedCanonicalGroupSend) => {
    if (alive.current && pendingRecord.current?.params.event_id === entry.params.event_id) {
      showPending(null)
    }
  }

  const show = (entry: PreparedCanonicalGroupSend) => {showPending(entry); setDraft(String(entry.params.payload.text ?? '')); setAttachments((entry.params.payload.attachments as Attachment[] | undefined) ?? [])}

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
    const snapshot = await canonicalGroupRequest<RoomState>(binding, 'groups.state', { room_id: binding.roomId, include_disbanded: true })

    // Background cleanup needs only state; preserve the transcript after its tombstone.
    if (canonicalRetirementStatus(binding.roomId, snapshot)?.retired) {
      if (alive.current && version === revision.current) {setState(snapshot); setReadError('')}

      return
    }

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
      setState(snapshot)
      updateCanonicalGroupName(binding, snapshot.room.name)
      setEvents(current => fresh ? log : [...current, ...log.filter(event => event.seq > last)])
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

    return () => { cancelled = true; revision.current++; clearTimeout(timer) }
    // The keyed parent freezes the authority binding for this lifetime.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [visible])

  // Follow new messages only while the viewer remains at the end of the chat.
  useLayoutEffect(() => {
    if (visible && following.current && transcript.current) {transcript.current.scrollTop = transcript.current.scrollHeight}
  }, [events, visible])

  const mutate = async (operation: () => Promise<unknown>, propagate = false) => {
    if (busyRef.current) {
      if (propagate) {throw new Error(labels.actionInFlight)}

      return
    }

    busyRef.current = true
    setBusy(true)
    setError('')

    try { await operation();

 if (alive.current && !continuity.paused) {await refresh()} }
    catch (e) {
      if (propagate) {throw e}

      if (alive.current) {setError(e instanceof Error ? e.message : String(e))}
    }
    finally {
      busyRef.current = false

      if (alive.current) {setBusy(false)}
    }
  }

  const send = (message = { text: draft, attachments }) => {
    if (retirementIntent.current || canonicalRetirementStatus(binding.roomId, state) || !visible || !restored || busyRef.current || uploadingRef.current || !(state?.driver_status || continuity.paused) || (!pending && !message.text.trim() && !message.attachments.length)) {return}
    setSendHint('')
    const editing = inputRevision.current
    const held = continuity.paused
    void mutate(async () => {
      const exact = pending ?? await prepareCanonicalGroupSend(binding, message, { held })

      if (!alive.current) {return}

      if (inputRevision.current === editing) {show(exact)}

      // Paused: the message stays durably in the journal and goes out once the group resumes, here or on its new host.
      if (held) {return}

      const freshAttempt = await attemptCanonicalGroupSend(binding, exact)

      if (!alive.current) {return}
      let accepted: AcceptedCanonicalGroupSend

      try {
        accepted = acceptedCanonicalGroupSend(await canonicalGroupRequest<unknown>(binding, 'groups.send', exact.params), exact, labels.unconfirmedSend)
      } catch (error) {
        const outcome = freshAttempt ? sendOutcome(error) : 'unknown'

        if (outcome === 'paused' && alive.current) {return await continuity.hold(exact)}

        // Only the room that sent it gets the text back; a view that moved on keeps the journal entry.
        if (outcome === 'refused' && alive.current) {
          await retireCanonicalGroupSend(binding, exact.params.event_id, exact)

          clearPending(exact)
        }

        if (alive.current) {setSendHint(outcome === 'refused' ? labels.sendRefused : outcome === 'retryable' ? labels.sendNotYet : labels.sendMaybe)}
        throw error
      }

      await continuity.settle(exact, accepted)

      if (alive.current) {
        clearPending(exact)
        if (inputRevision.current === editing) {setDraft(''); setAttachments([])}

        try {
          const recoverable = await listCanonicalGroupSends(binding)

          if (alive.current) {setRecoveries(recoverable)}
        } catch (error) {console.warn('Accepted group Send recovery journal could not be read', error)}
      }
    })
  }

  const restore = (recovery: RecoverableCanonicalGroupSend) => {
    if (retirementIntent.current || pending || draft.trim() || attachments.length || uploadingRef.current) {return}
    const editing = inputRevision.current
    void mutate(async () => {
      const exact = await claimCanonicalGroupSend(binding, recovery)

      if (!alive.current) {return}

      if (inputRevision.current === editing) {show(exact)}
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

  // A message held while paused goes out once, as soon as the group can take it again. One the new host refused for good
  // comes back as an editable draft once the composer is free, never over what you're writing.
  useEffect(() => {
    if (continuity.resumable() && restored && pending?.held && !pending.attempted && state?.driver_status && !busyRef.current) {send()}
    else if (restored && !pending && !draft.trim() && !attachments.length && !busyRef.current) {continuity.reclaim()}
  })

  const presentation = roomPresentation(state, labels)
  const {members, name, pendingActions} = presentation
  const retirement = canonicalRetirementStatus(binding.roomId, state, retirementRequested)
  const ready = !retirement && continuity.composable(state?.driver_status)
  const occupied = [Boolean(retirement), !visible, busy, uploading, !!pending, !!draft.trim(), !!attachments.length]
  const canStop = !retirement?.retired && continuity.stoppable(roomHasWork(state?.driver_status, Boolean(pending || busy || stopping)))

  return <section className="flex h-full min-h-0 flex-col" data-slot="canonical-group-chat">
    <CanonicalGroupHeader attention={presentation.attention} info={<CanonicalGroupBackups controller={continuity.controller} group={name} />}
      members={members} name={name} onBack={onBack} status={presentation.status} unavailable={continuity.unavailable}
      visible={visible} working={presentation.working}>
      {visible && state && actions?.({ name: state.room.name, latestFileSeq: events.reduce((latest, event) => event.payload.attachments?.length ? Math.max(latest, event.seq) : latest, 0), visible, retirement, onRetirementRequested: () => {retirementIntent.current = true; setRetirementRequested(true); void refresh().catch(e => setReadError(String(e)))}, refresh: () => void refresh().catch(e => setReadError(String(e))) })}
    </CanonicalGroupHeader>
    <RoomContinuityBanners binding={binding} continuity={continuity} events={events} group={name} members={members} visible={visible} />
    <div aria-label={labels.conversationHistory} className="min-h-0 flex-1 overflow-y-auto overscroll-y-contain px-2"
      onScroll={event => { const node = event.currentTarget; following.current = node.scrollHeight - node.scrollTop - node.clientHeight < 48 }} ref={transcript} role="log">
      <div className="mx-auto w-full max-w-3xl pb-4">
        <CanonicalGroupHistory binding={binding} computerName={continuity.computerName} disabled={!visible} events={events} members={members}
          missing={{ ...continuity.missing, blocked: occupied.some(Boolean), onSendAgain: event => send(continuity.resend(event)) }} unsaved={continuity.unsaved} />
        {state && !events.length && <div className="grid gap-1 px-3 py-10 text-center">
          <p className="text-sm text-(--ui-text-secondary)">{labels.emptyHistory}</p>
          <p className="text-xs text-(--ui-text-quaternary)">{labels.emptyHistoryHint}</p>
        </div>}
      </div>
    </div>
    <div className="mx-auto w-full max-w-3xl shrink-0 px-4 pb-4">
      <CanonicalGroupRetirementNotice onRefresh={() => void refresh().catch(e => setReadError(String(e)))} status={retirement} />
      <div className="max-h-[min(40vh,24rem)] overflow-y-auto">
        {visible && <CanonicalGroupPendingActions actions={pendingActions} busy={busy || Boolean(retirement)} members={members} onAction={act}
          onDiscard={action => act(action)} onRefresh={refresh} unknownTitle={continuity.unknownTitle} waiting={continuity.waiting(state?.driver_status?.tasks)} />}
      </div>
      <RoomHints error={error} explained={continuity.explained} labels={labels} notice={notice} onRefresh={() => void refresh().catch(e => setReadError(String(e)))}
        onRestore={restore} pausedHint={continuity.pausedHint} pending={pending} readError={readError} recoveries={recoveries}
        restoreBlocked={occupied} sendHint={sendHint} state={state} busy={busy} journalError={journalError}
        journalLoading={journalLoading} onJournalReload={() => setJournalReload(current => current + 1)} />
      <RoomComposerSlot continuity={continuity}>
        <CanonicalGroupComposer attachments={attachments} binding={binding} busy={busy} draft={draft} hasDriver={ready}
          members={members} name={name} canStop={canStop} onDraft={value => {inputRevision.current++; setDraft(value)}}
          onAttachments={value => {inputRevision.current++; setAttachments(value)}}
          onUploading={value => {uploadingRef.current = value; if (alive.current) {setUploading(value)}}}
          onSend={send} onStop={() => void stop()} pending={Boolean(pending)} restored={restored}
          stopping={stopping} uploading={uploading} visible={visible} />
      </RoomComposerSlot>
    </div>
  </section>
}

/** The lines between the pending work and the composer: notices, refusals, unconfirmed Sends and their recovery. */
function RoomHints({ notice, pausedHint, readError, error, explained, state, pending, sendHint, recoveries, restoreBlocked,
  onRefresh, onRestore, busy, journalError, journalLoading, onJournalReload }: {
  notice: string; pausedHint: string; readError: string; error: string; explained: boolean; state: RoomState | null
  pending: PreparedCanonicalGroupSend | null; sendHint: string; recoveries: RecoverableCanonicalGroupSend[]; restoreBlocked: boolean[]
  labels: Labels; onRefresh: () => void; onRestore: (recovery: RecoverableCanonicalGroupSend) => void
  busy: boolean; journalError: string; journalLoading: boolean; onJournalReload: () => void
}) {
  return <div className="grid gap-2 pb-2 text-xs text-(--ui-text-secondary)">
    <CanonicalGroupRecoveryNotices notice={notice} readError={explained ? '' : readError} error={error}
      journalError={journalError} journalLoading={journalLoading} busy={busy}
      unavailable={Boolean(state && !state.driver_status && !explained)} onRefresh={onRefresh} onJournalReload={onJournalReload} />
    {pausedHint && <p aria-live="polite" data-slot="paused-composer-hint">{pausedHint}</p>}
    <CanonicalGroupSavedMessages pending={pending} hint={sendHint} recoveries={recoveries}
      disabled={restoreBlocked.some(Boolean)} onRestore={onRestore} />
  </div>
}

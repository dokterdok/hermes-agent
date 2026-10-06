import { Button } from '@hermes/plugin-sdk'

import { useCanonicalGroupLabels } from './canonical-group-labels'

export interface RetirementRoomState {
  room?: { room_id?: unknown; disbanded_at?: unknown }
  driver_status?: { retiring?: unknown; peer_cleanup?: unknown }
}
export type RetirementPhase = 'stopping' | 'pending' | 'unreadable' | 'unknown' | 'complete'
export interface RetirementStatus { phase: RetirementPhase; retired: boolean }

/** A tombstone ends the chat; cleanup is complete only after the same room's readable empty queue. */
export function canonicalRetirementStatus(roomId: string, state: RetirementRoomState | null, requested = false): RetirementStatus | null {
  const retired = typeof state?.room?.disbanded_at === 'number' && Number.isFinite(state.room.disbanded_at) && state.room.disbanded_at > 0
  const retiring = state?.driver_status?.retiring === true

  if (!requested && !retired && !retiring) {return null}

  if (state?.room?.room_id !== roomId) {return {phase: 'unreadable', retired: false}}

  if (!retired) {return {phase: retiring ? 'stopping' : 'unknown', retired: false}}
  const cleanup = state.driver_status?.peer_cleanup

  if (!Array.isArray(cleanup)) {return {phase: 'unreadable', retired: true}}

  for (const row of cleanup) {
    if (!row || typeof row !== 'object' || row.status !== 'pending' || row.room_id !== roomId ||
        typeof row.member_id !== 'string' || !row.member_id.trim() || !['exact', 'scope', 'issuance'].includes(row.mode)) {
      return {phase: 'unreadable', retired: true}
    }
  }

  return {phase: cleanup.length ? 'pending' : 'complete', retired: true}
}

export function CanonicalGroupRetirementNotice({ status, onRefresh }: {status: RetirementStatus | null; onRefresh: () => void}) {
  const labels = useCanonicalGroupLabels()

  if (!status) {return null}

  if (status.phase === 'complete') {return <p className="py-3 text-sm text-(--ui-text-secondary)" role="status">{labels.activityEnded}</p>}

  const messages = {
    stopping: labels.retirementStopping, pending: labels.retirementCleanupPending,
    unreadable: labels.retirementCleanupUnreadable, unknown: labels.retirementUnconfirmed
  }

  return <div className="grid gap-2 border-t border-(--ui-stroke-secondary) py-3 text-sm text-(--ui-text-secondary)" role="status">
    <p>{messages[status.phase]}</p>
    <Button className="justify-self-start" onClick={onRefresh} size="inline" type="button" variant="text">{labels.refresh}</Button>
  </div>
}

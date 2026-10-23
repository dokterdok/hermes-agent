import { Button, ConfirmDialog, host, useValue } from '@hermes/plugin-sdk'
import { useEffect, useState } from 'react'

import { memberName } from './canonical-group-activity'
import { type GroupMethods, offersGroupMethod } from './canonical-group-capabilities'
import { useCanonicalGroupLabels } from './canonical-group-labels'
import { $peerJoinErrors, peerJoinKey, peerJoinText, peerMemberStates, rejoinPeer, renewPeers, startAgainWithout } from './canonical-group-peers'
import { registerCanonicalGroup } from './canonical-group-registry'
import type { CanonicalGroupBinding, CanonicalRoom, CanonicalRoomMember } from './canonical-groups'
import { cachedUnionRoster } from './data'
import { endCanonicalGroup } from './group-panes'

/** Keep this open room's members of other gateways seated (`renewPeers`) where its gateway registers them. */
export function usePeerRenewal(binding: CanonicalGroupBinding, state: { room: CanonicalRoom; driver_status?: { peer_routes?: unknown } } | null,
  methods: GroupMethods, visible: boolean) {
  useEffect(() => {
    if (visible && state?.driver_status && offersGroupMethod(methods, 'groups.peer.register')) {
      renewPeers(binding, state.room, state.driver_status.peer_routes, cachedUnionRoster()?.profiles ?? [])
    }
  }, [binding, methods, state, visible])
}

const STATUS_WORDS = { missing: 'peerNotJoined', needs_reauthorization: 'peerRejoin', unavailable: 'peerAway' } as const

interface JoinRowsProps {
  binding: CanonicalGroupBinding
  methods: GroupMethods
  onChanged: () => void
  onReplaced?: (key: string) => void
}

/** The members of another gateway that have not joined this room (no route yet, access ended, or
 *  their computer away), each with why when an attempt here failed, Retry (invite and register
 *  again) and Start new group without it (this room ends; earlier messages are not copied). Shown
 *  only where this gateway registers peers, and mounted only while one has not joined. */
export function CanonicalPeerJoinRows({ state, unjoined, ...props }: JoinRowsProps & {
  state: { room: CanonicalRoom; driver_status?: { peer_routes?: unknown } } | null
  unjoined: ReadonlySet<string>
}) {
  if (!state?.driver_status || !unjoined.size || !offersGroupMethod(props.methods, 'groups.peer.register')) {return null}

  return <PeerJoinRows {...props} room={state.room} routes={state.driver_status.peer_routes} />
}

function PeerJoinRows({ binding, room, routes, methods, onChanged, onReplaced }: JoinRowsProps & { room: CanonicalRoom; routes: unknown }) {
  const labels = useCanonicalGroupLabels()
  const errors = useValue($peerJoinErrors)
  const [busy, setBusy] = useState(false)
  const [removing, setRemoving] = useState<CanonicalRoomMember | null>(null)
  const away = peerMemberStates(room.members, routes).filter(({ status }) => status !== 'ready')
  const roster = () => cachedUnionRoster()?.profiles ?? []

  const retry = async (member: CanonicalRoomMember) => {
    setBusy(true)

    try {
      await rejoinPeer(binding, room, member, roster())
    } catch {
      // The row says why ($peerJoinErrors).
    } finally {
      setBusy(false)
      onChanged()
    }
  }

  // ConfirmDialog keeps itself open and shows a thrown message.
  const remove = async () => {
    if (!removing) {return}
    const next = await startAgainWithout(binding, room, removing.member_id, roster())
    const key = registerCanonicalGroup({ connectionId: binding.connectionId, profile: binding.profile }, next.room)

    if (next.ended) {endCanonicalGroup(binding)} else {host.notify({ kind: 'error', message: labels.disbandUnconfirmed })}

    setRemoving(null)
    onReplaced?.(key)
  }

  return <div className="grid gap-2" data-peer-join="">
    {away.map(({ member, status }) => {
      const name = memberName(member)
      const error = errors[peerJoinKey(binding, member.member_id)]
      const words = labels[STATUS_WORDS[status as keyof typeof STATUS_WORDS] ?? 'peerNotJoined']

      return <div className="grid gap-1 text-xs" data-member-id={member.member_id} key={member.member_id} role="status">
        <p>{words.replace('{name}', name)}</p>
        {error !== undefined && <p className="text-(--ui-text-secondary)">{peerJoinText(error, name, labels)}</p>}
        <div className="flex flex-wrap gap-2">
          <Button disabled={busy} onClick={() => void retry(member)}>{labels.retry}</Button>
          {room.members.length > 2 && offersGroupMethod(methods, 'groups.disband') &&
            <Button disabled={busy} onClick={() => setRemoving(member)}>{labels.peerStartWithout.replace('{name}', name)}</Button>}
        </div>
      </div>
    })}
    {removing && <ConfirmDialog cancelLabel={labels.cancel} confirmLabel={labels.startGatewayGroup} description={labels.peerStartWithoutConfirm}
      destructive onClose={() => setRemoving(null)}
      onConfirm={remove} open title={labels.peerStartWithout.replace('{name}', memberName(removing))} />}
  </div>
}

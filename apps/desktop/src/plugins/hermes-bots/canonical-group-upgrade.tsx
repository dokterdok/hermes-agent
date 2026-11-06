import { Button, host, useI18n, useValue } from '@hermes/plugin-sdk'
import { useRef, useState } from 'react'

import { groupCreationEngine, groupNeedsDesktopCourier, groupSeatsPeers, revalidateGroupCreation, useGroupCapability } from './canonical-group-capabilities'
import { createGroupAcrossConnections, type GroupCreationIntent, peerJoinText, usePeerTargets } from './canonical-group-peers'
import { registerCanonicalGroup } from './canonical-group-registry'
import { updateGroupChat } from './group-chat'
import { useBots } from './i18n'
import type { GroupMember } from './types'

/** A separate gateway conversation; the Desktop room and its history remain usable. */
export function GatewayGroupUpgrade({ group, members, visible, onCreated }: {
  visible: boolean; group: string; members: GroupMember[]; onCreated: (key: string) => void
}) {
  const { t } = useI18n()
  const b = useBots()
  const [peerCheck, setPeerCheck] = useState(0)
  const activeConnection = useValue(host.state.connectionId)
  const { capability, connectionId, mode, observedRoute, source } = useGroupCapability(visible && groupNeedsDesktopCourier(members, activeConnection))
  const targets = usePeerTargets(members, connectionId, groupSeatsPeers(capability?.snapshot), peerCheck)
  const plan = groupCreationEngine(mode, members, connectionId, capability?.snapshot, targets)
  const intent = useRef<GroupCreationIntent>({})
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  if (plan.engine === 'unavailable' && plan.reason === 'unreachable') {
    return <div className="grid gap-1 text-xs">
      <p role="status">{b.crossGateway.peerCheckFailed(plan.connection || '')}</p>
      <Button onClick={() => setPeerCheck(value => value + 1)}>{t.common.retry}</Button>
    </div>
  }

  if (plan.engine !== 'gateway-peer') {return null}

  const create = async () => {
    if (!observedRoute || busy) {return}
    setBusy(true)
    setError('')

    try {
      const fresh = await revalidateGroupCreation(observedRoute, capability, source, members, 'gateway-peer', b.canonical.driverUnavailable)

      if (!fresh.sourceCurrent()) {throw new Error(b.canonical.driverUnavailable)}
      const created = await createGroupAcrossConnections(observedRoute, group, members, fresh.targets, intent.current)

      if (!fresh.sourceCurrent()) {return}
      intent.current = {}
      updateGroupChat(created.room.name, room => ({ ...room, roomId: created.room.room_id,
        engine: { kind: 'gateway', ...observedRoute, roomId: created.room.room_id } }), { sync: false })

      for (const { member, error } of created.unjoined) {
        host.notify({ kind: 'error', message: peerJoinText(error, member.display_name || member.handle, b.canonical) })
      }

      if (fresh.sourceCurrent()) {onCreated(registerCanonicalGroup(observedRoute, created.room))}
    } catch (error) {
      setError(error instanceof Error ? error.message : String(error))
    } finally {setBusy(false)}
  }

  return <div className="grid gap-1 text-xs">
    <p>{b.crossGateway.upgradeHistory}</p>
    <Button disabled={busy} onClick={() => void create()}>{b.crossGateway.startGatewayGroup}</Button>
    {error && <p role="alert">{error}</p>}
  </div>
}

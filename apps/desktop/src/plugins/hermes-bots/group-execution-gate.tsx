import { host, useValue } from '@hermes/plugin-sdk'
import type { ReactNode } from 'react'
import { useEffect, useState } from 'react'

import { canonicalGroupRequest } from './canonical-groups'
import { $groupChats } from './group-chat'
import { captureRetainedRoom, currentRetainedRoom } from './retained-group-files'
import type { RetainedRoom } from './retained-group-files'
import { RetainedGroupWorkspace } from './retained-group-workspace'
import type { GroupMember } from './types'

export function hasLegacyGroupDriver(value: unknown): boolean {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    return false
  }

  const { driver, features } = value as { driver?: unknown; features?: unknown }

  // A canonical owner without a ready driver is unavailable, not a legacy owner.
  return driver === false && (features === undefined || (
    Array.isArray(features) && features.every(feature => typeof feature === 'string') &&
    !features.includes('canonical_session_owner')
  ))
}

function matchesLegacySource(room: RetainedRoom, members: GroupMember[], connectionId: string): boolean {
  if ([room.hosted, room.hostedEpoch, room.hostedConnectionId, room.continuityMode].some(value => value != null)) {
    return false
  }

  return [...members, ...(room.members || []), ...Object.values(room.sessionOwners || {})].every(member => {
    const source = member.route?.connectionId ?? member.connectionId

    if (source !== undefined) {
      return source === connectionId && (!member.connectionId || member.connectionId === source)
    }

    // Bare descriptors are the old local format, never evidence for a remote owner.
    return connectionId === 'local' && !member.remoteSource && !member.sourceScoped
  })
}

interface Props {
  group: string
  members: GroupMember[]
  onBack?: () => void
  visible?: boolean
  children: ReactNode
}

export function GroupExecutionGate({ group, members, onBack, visible = true, children }: Props) {
  const rooms = useValue($groupChats)
  const connectionId = useValue(host.state.connectionId)
  const profile = useValue(host.state.profile)
  const gateway = useValue(host.state.gateway)
  const [binding] = useState(() => rooms[group] ? captureRetainedRoom(group, rooms[group]) : null)
  const room = binding && currentRetainedRoom(binding)
  const eligible = !!room && !!connectionId && matchesLegacySource(room, members, connectionId)
  const routeKey = JSON.stringify([connectionId, profile, gateway, visible, eligible])
  const [legacyRoute, setLegacyRoute] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    setLegacyRoute(null)

    if (eligible && visible && gateway === 'open' && connectionId && profile) {
      void canonicalGroupRequest<unknown>({ connectionId, profile }, 'groups.capabilities')
        .then(result => {
          if (!cancelled && hasLegacyGroupDriver(result)) {
            setLegacyRoute(routeKey)
          }
        })
        .catch(() => { /* An unknown runtime remains a retained read-only view. */ })
    }

    return () => { cancelled = true }
  }, [connectionId, eligible, gateway, profile, routeKey, visible])

  if (eligible && visible && gateway === 'open' && legacyRoute === routeKey) {
    return children
  }

  return <RetainedGroupWorkspace binding={binding} group={group} onBack={onBack} visible={visible} />
}

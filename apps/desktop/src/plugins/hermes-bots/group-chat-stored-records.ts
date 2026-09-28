import type { GroupChat, HostedPeerProbeHint, ShippedGroupAdoption, ShippedGroupAdoptionIssueKind } from './types'

export function storedHostedPeerProbeHint(room: Partial<GroupChat>): HostedPeerProbeHint | undefined {
  const hint = room.peerProbeHint
  const connectionId = typeof hint?.connectionId === 'string' ? hint.connectionId.trim().slice(0, 256) : ''
  const installationId = typeof hint?.installationId === 'string' ? hint.installationId.trim().slice(0, 256) : ''
  const memberId = typeof hint?.memberId === 'string' ? hint.memberId.trim().slice(0, 256) : ''

  return connectionId && installationId && memberId ? { connectionId, installationId, memberId } : undefined
}

export function storedShippedGroupAdoption(room: Partial<GroupChat>): ShippedGroupAdoption | undefined {
  const value = room.shippedAdoption

  if (
    !value ||
    typeof value !== 'object' ||
    value.version !== 1 ||
    !['waiting', 'prepared', 'adopted'].includes(String(value.state || '')) ||
    typeof value.sourceId !== 'string' ||
    !value.sourceId.trim() ||
    value.sourceId.length > 512 ||
    typeof value.roomId !== 'string' ||
    !value.roomId.trim() ||
    value.roomId.length > 128 ||
    typeof value.requestHash !== 'string' ||
    !/^[0-9a-f]{64}$/.test(value.requestHash)
  ) {
    return undefined
  }

  const issueKinds = new Set<ShippedGroupAdoptionIssueKind>([
    'auth',
    'conflict',
    'offline',
    'owner-ambiguous',
    'owner-replaced',
    'storage',
    'update-required'
  ])

  const rawIssue = value.issue

  const issue =
    rawIssue &&
    issueKinds.has(rawIssue.kind) &&
    typeof rawIssue.message === 'string' &&
    rawIssue.message.trim() &&
    rawIssue.message.length <= 1000
      ? { kind: rawIssue.kind, message: rawIssue.message.trim() }
      : undefined

  const rawRoute = value.route

  const route =
    rawRoute &&
    typeof rawRoute.connectionId === 'string' &&
    rawRoute.connectionId.trim() &&
    rawRoute.connectionId.length <= 256 &&
    typeof rawRoute.profile === 'string' &&
    rawRoute.profile.trim() &&
    rawRoute.profile.length <= 128 &&
    typeof rawRoute.authorityGatewayId === 'string' &&
    rawRoute.authorityGatewayId.trim() &&
    rawRoute.authorityGatewayId.length <= 256
      ? {
          authorityGatewayId: rawRoute.authorityGatewayId.trim(),
          connectionId: rawRoute.connectionId.trim(),
          profile: rawRoute.profile.trim()
        }
      : undefined

  if ((value.state === 'prepared' || value.state === 'adopted') && !route) {
    return undefined
  }

  const count = (candidate: unknown) => {
    const number = Number(candidate)

    return Number.isSafeInteger(number) && number >= 0 ? number : undefined
  }

  return {
    version: 1,
    state: value.state,
    sourceId: value.sourceId.trim(),
    roomId: value.roomId.trim(),
    requestHash: value.requestHash,
    ...(value.ownerSelection === 'explicit' || value.ownerSelection === 'inferred'
      ? { ownerSelection: value.ownerSelection }
      : {}),
    ...(route ? { route } : {}),
    ...(issue ? { issue } : {}),
    ...(count(value.importedHistory) !== undefined ? { importedHistory: count(value.importedHistory) } : {}),
    ...(count(value.heldWork) !== undefined ? { heldWork: count(value.heldWork) } : {}),
    ...(count(value.heldMembers) !== undefined ? { heldMembers: count(value.heldMembers) } : {}),
    ...(count(value.retiredMembers) !== undefined ? { retiredMembers: count(value.retiredMembers) } : {}),
    ...(count(value.acknowledgedAt) !== undefined ? { acknowledgedAt: count(value.acknowledgedAt) } : {})
  }
}

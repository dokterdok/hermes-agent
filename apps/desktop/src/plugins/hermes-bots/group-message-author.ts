import type { GroupChat, GroupMember, GroupMessage, GroupMessageAuthor, HostedMessageIdentity } from './types'

function identityText(value: unknown): string | undefined {
  return typeof value === 'string' && value.trim() && value.length <= 128 ? value.trim() : undefined
}

export function normalizeHostedMessageIdentity(value: unknown): HostedMessageIdentity | undefined {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    return undefined
  }

  const identity = value as Record<string, unknown>
  const roomId = identityText(identity.roomId)
  const memberId = identityText(identity.memberId)
  const profile = identityText(identity.profile)

  if (!roomId || !memberId || (identity.profile != null && !profile)) {
    return undefined
  }

  return { roomId, memberId, ...(profile ? { profile } : {}) }
}

export function compactGroupMessageAuthor(author: GroupMessageAuthor): GroupMessageAuthor {
  const kind = author?.kind === 'member' ? 'member' : 'user'
  const identity = kind === 'member' ? normalizeHostedMessageIdentity(author?.hostedIdentity) : undefined

  return {
    kind,
    name: String(author?.name || (kind === 'member' ? 'Bot' : 'You')).slice(0, 128),
    ...(author?.source ? { source: String(author.source).slice(0, 128) } : {}),
    ...(identity ? { hostedIdentity: identity } : {})
  }
}

/** Hosted authors reach this merge only through event ID/sequence matching. */
export function mergeGroupMessageAuthor(prior: GroupMessage, incoming: GroupMessage): GroupMessageAuthor {
  const left = normalizeHostedMessageIdentity(prior.from?.hostedIdentity)
  const right = normalizeHostedMessageIdentity(incoming.from?.hostedIdentity)

  if (prior.from?.kind !== 'member' || incoming.from?.kind !== 'member' || (!left && !right)) {
    return incoming.from
  }

  let identity = right || left

  if (left && right) {
    const same =
      left.roomId === right.roomId &&
      left.memberId === right.memberId &&
      (!left.profile || !right.profile || left.profile === right.profile)

    const leftSequenced = Number.isSafeInteger(Number(prior.seq)) && Number(prior.seq) > 0
    const rightSequenced = Number.isSafeInteger(Number(incoming.seq)) && Number(incoming.seq) > 0

    // Compact mirrors cannot replace the replay actor. Conflicting equal-rank
    // records are ambiguous; neither display label gets to break the tie.
    identity = same
      ? { ...left, ...right }
      : leftSequenced !== rightSequenced
        ? leftSequenced
          ? left
          : right
        : undefined
  }

  const { hostedIdentity: _identity, ...author } = incoming.from

  return { ...author, ...(identity ? { hostedIdentity: identity } : {}) }
}

/** Null means classic. A hosted but unbound author keeps its saved label and
 * no handle; profile names, display names, and device labels are not IDs. */
export function hostedMessageSpeaker(author: GroupMessageAuthor, room: GroupChat, members: GroupMember[]) {
  if (author?.kind !== 'member' || (!room.hosted && !author.hostedIdentity)) {
    return null
  }

  const identity = normalizeHostedMessageIdentity(author.hostedIdentity)

  const candidates =
    identity && identity.roomId === room.roomId
      ? members.filter(
          member =>
            member.hostedIdentity?.roomId === identity.roomId && member.hostedIdentity.memberId === identity.memberId
        )
      : []

  const candidate = candidates.length === 1 ? candidates[0] : null

  const member =
    candidate && (!identity?.profile || candidate.hostedIdentity?.profile === identity.profile) ? candidate : null

  return {
    member,
    profile: member?.hostedIdentity?.profile || null,
    display: member?.display_name?.trim() || member?.title?.trim() || author.name || 'Bot',
    handle: member?.handle?.trim() || null
  }
}

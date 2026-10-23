import { host } from '@hermes/plugin-sdk'
import { atom } from 'nanostores'
import { useEffect, useState } from 'react'

import { canonicalGroupRequest, canonicalGroupRoster, createCanonicalGroup } from './canonical-groups'
import type { CanonicalGroupBinding, CanonicalGroupRoute, CanonicalRoom, CanonicalRoomMember } from './canonical-groups'
import { resolveBotConnectionRoute } from './routing'
import type { GroupMember, ProfileRoute, RosterRow } from './types'

/** Bots from other connections in a gateway Group Chat: Desktop runs the one-time setup (#130790)
 *  and keeps nothing. The room is created on this connection's gateway (the home) with the member
 *  pinned to its own gateway; `groups.peer.invite` there mints a grant for that room and member, and
 *  `groups.peer.register` on the home binds it. From then on the gateways run the member's turns.
 *  A setup that stops between its steps leaves a member that has not joined: `groups.state` shows
 *  it (`driver_status.peer_routes`), and running the steps again seats it. */

/** The one profile another gateway lends a room (gateway/session_group_peers.py `register`). */
const PEER_PROFILE = 'default'

/** The longest dispatch lifetime a member gateway grants, and, where it advertises
 *  `peer_renewal_horizon`, the longest horizon up to which the home may renew it. */
const LIFETIME = { ttl_seconds: 86400 }
const HORIZON = { status_ttl_seconds: 30 * 86400 }

/** Whether another connection's gateway can host a Bot in a room this connection owns, as its own
 *  `groups.capabilities` says. `gateway` is its authority id, the installation a pinned member names. */
export type PeerTargetState =
  | { ready: true; gateway: string; installationId: string; catalogDigest: string; renewal: boolean }
  | { ready: false; gateway?: string; reason: string }

type Answer = Record<string, unknown> & { room_link?: Record<string, any>; methods?: unknown; features?: unknown }

export function peerTargetState(value: unknown): PeerTargetState {
  const answer: Answer = value && typeof value === 'object' ? value as Answer : {}
  const gateway = typeof answer.authority_gateway_id === 'string' && answer.authority_gateway_id ? answer.authority_gateway_id : undefined
  const listed = (field: unknown, name: string) => Array.isArray(field) && field.includes(name)
  const refused = (reason: string): PeerTargetState => ({ ready: false, reason, ...(gateway ? { gateway } : {}) })

  if (!listed(answer.methods, 'groups.peer.invite')) {return refused('peer_invite_unsupported')}
  const link = answer.room_link

  if (link?.enabled !== true) {return refused(typeof link?.reason === 'string' && link.reason ? link.reason : 'room_link_unavailable')}
  const catalog = link.catalog ?? {}

  // What the home's registration requires of the member's gateway (`register`, `_probe`): the catalog
  // its endpoint answers with is the pinned one, it takes text only, lends its default profile, and the
  // home can reach it. A backend Desktop started (an SSH connection) says it ends with Desktop; the
  // gateway serving that endpoint, which Desktop didn't start, says it doesn't (`catalog_mapping`), so
  // a pin taken from that backend can't match.
  if (catalog.persistent_process !== true) {return refused('persistent_process_required')}

  if (catalog.text !== true || catalog.attachments !== false) {return refused('text_only_required')}

  if (catalog.execution_policy?.target_profile !== PEER_PROFILE) {return refused('default_profile_required')}

  if (catalog.endpoint?.available !== true || typeof catalog.endpoint.url !== 'string') {return refused('endpoint_required')}

  if (!gateway || catalog.installation_id !== gateway || typeof catalog.catalog_digest !== 'string') {return refused('room_link_unavailable')}

  return { ready: true, gateway, installationId: gateway, catalogDigest: catalog.catalog_digest, renewal: listed(answer.features, 'peer_renewal_horizon') }
}

/** A Bot's own connection route when it is a Bot another connection's room could seat: on another
 *  connection than `home`, consistently, and that connection's default profile. */
export function peerRoute(bot: Partial<RosterRow> | null | undefined, home: null | string): null | ProfileRoute {
  const { route } = resolveBotConnectionRoute(bot)

  if (bot?.ghost || !route || !home) {return null}

  return route.connectionId !== home && route.targetProfile === PEER_PROFILE &&
    (!bot?.connectionId || bot.connectionId === route.connectionId) ? route : null
}

function peerRequest<T>(route: ProfileRoute, method: string, params: Record<string, unknown> = {}): Promise<T> {
  return host.requestProfile<T>(route, method, { ...params, profile: route.targetProfile })
}

/** `groups.capabilities` of each connection the given Bots run on, while `active`: undefined while
 *  it is read, then that gateway's `peerTargetState`. Read again each time `active` turns on. */
export function usePeerTargets(bots: Partial<RosterRow>[], home: null | string, active: boolean): ReadonlyMap<string, PeerTargetState> {
  const routes = new Map<string, ProfileRoute>()

  for (const bot of bots) {
    const route = peerRoute(bot, home)

    if (route && !routes.has(route.connectionId)) {routes.set(route.connectionId, route)}
  }

  const key = JSON.stringify([...routes.values()].map(route => [route.connectionId, route.mode, route.profile, route.targetProfile]).sort())
  const [read, setRead] = useState<{ key: string; states: ReadonlyMap<string, PeerTargetState> } | null>(null)

  useEffect(() => {
    if (!active) {return}
    let cancelled = false
    const states = new Map<string, PeerTargetState>()
    setRead({ key, states })

    for (const [connectionId, mode, profile, targetProfile] of JSON.parse(key) as [string, 'local' | 'remote', string, string][]) {
      void peerRequest<unknown>({ connectionId, mode, profile, targetProfile }, 'groups.capabilities')
        .then(peerTargetState, () => ({ ready: false, reason: 'unreachable' }) as const)
        .then(state => {
          if (cancelled) {return}
          states.set(connectionId, state)
          setRead({ key, states: new Map(states) })
        })
    }

    return () => { cancelled = true }
  }, [active, key])

  return read?.key === key ? read.states : new Map()
}

/** Why the last attempt to seat a member failed, per room and member, until one succeeds. In memory
 *  only: after a restart the room still shows the member as not joined, without the reason. */
export const $peerJoinErrors = atom<Record<string, unknown>>({})

export const peerJoinKey = (binding: CanonicalGroupBinding, memberId: string) =>
  JSON.stringify([binding.connectionId, binding.profile, binding.roomId, memberId])

function noteJoin(binding: CanonicalGroupBinding, memberId: string, error?: unknown) {
  const { [peerJoinKey(binding, memberId)]: _previous, ...rest } = $peerJoinErrors.get()
  $peerJoinErrors.set(error === undefined ? rest : { ...rest, [peerJoinKey(binding, memberId)]: error })
}

/** A step Desktop can tell went wrong itself, beside the gateways' own refusals. */
export class PeerJoinError extends Error {
  constructor(readonly reason: 'peer_connection_missing' | 'peer_gateway_changed' | 'registration_unconfirmed' | string) {
    super(reason)
  }
}

interface PinnedPeer { kind: 'peer'; installation_id: string; capability_digest: string }
type RoomAuthority = Pick<CanonicalRoom, 'room_id' | 'authority_gateway_id' | 'authority_epoch'>
export interface PeerSeat { member: CanonicalRoomMember; route: ProfileRoute; renewal: boolean }

const pinned = (member: CanonicalRoomMember) => member.target?.kind === 'peer' ? member.target as unknown as PinnedPeer : null

/** Invite `seat.member` on its own gateway and register the grant on the home. The grant only
 *  passes through: if this stops halfway the member stays not joined and nothing here is owed. */
export async function seatPeer(home: CanonicalGroupBinding, room: RoomAuthority, seat: PeerSeat): Promise<void> {
  try {
    await invitedAndRegistered(home, room, seat)
    noteJoin(home, seat.member.member_id)
  } catch (error) {
    noteJoin(home, seat.member.member_id, error)
    throw error
  }
}

async function invitedAndRegistered(home: CanonicalGroupBinding, room: RoomAuthority, seat: PeerSeat) {
  const pin = pinned(seat.member)

  if (!pin || !room.authority_gateway_id || typeof room.authority_epoch !== 'number') {throw new PeerJoinError('peer_gateway_changed')}

  const invitation = await peerRequest<{ grant?: unknown; target_profile?: unknown; catalog?: Record<string, any>; endpoint?: { url?: unknown } }>(
    seat.route, 'groups.peer.invite', {
      room_id: room.room_id, member_id: seat.member.member_id, home_install_id: room.authority_gateway_id,
      authority_gateway_id: room.authority_gateway_id, authority_epoch: room.authority_epoch, ...LIFETIME, ...(seat.renewal ? HORIZON : {})
    })

  // Only the gateway the room pinned the member to may answer for it, and the grant goes only to the
  // endpoint in that pinned catalog (the home refuses any other `target_url`).
  const endpoint = invitation?.catalog?.endpoint?.url

  if (typeof invitation?.grant !== 'string' || !invitation.grant || invitation.catalog?.installation_id !== pin.installation_id ||
    invitation.catalog.catalog_digest !== pin.capability_digest || typeof endpoint !== 'string' || invitation.endpoint?.url !== endpoint) {
    throw new PeerJoinError('peer_gateway_changed')
  }

  const receipt = await canonicalGroupRequest<{ registered?: unknown; target_install_id?: unknown }>(home, 'groups.peer.register', {
    room_id: room.room_id, member_id: seat.member.member_id, target_url: endpoint,
    target_profile: invitation.target_profile, grant: invitation.grant, catalog: invitation.catalog
  }).catch(async error => {
    // The room ended before this grant was registered: revoke it at its gateway, so no authority
    // outlives the room. (For a live room the scope also holds its working grant: keep it.)
    if (!await roomLive(home, room).catch(() => true)) {
      await peerRequest(seat.route, 'groups.peer.revoke', { grant: invitation.grant }).catch(() => undefined)
    }

    throw error
  })

  if (receipt?.registered !== true || receipt.target_install_id !== pin.installation_id) {throw new PeerJoinError('registration_unconfirmed')}
}

async function seatPeers(home: CanonicalGroupBinding, room: RoomAuthority, seats: PeerSeat[]) {
  const unjoined: { member: CanonicalRoomMember; error: unknown }[] = []

  for (const seat of seats) {
    await seatPeer(home, room, seat).catch(error => { unjoined.push({ member: seat.member, error }) })
  }

  return unjoined
}

/** Create a gateway room on `route` with Bots from other connections, then seat each of those.
 *  `targets` is `usePeerTargets`' answer: a Bot whose gateway is not ready is refused before anything
 *  is created. The room exists once this returns; `unjoined` names the members not seated yet. */
export async function createGroupAcrossConnections(
  route: CanonicalGroupRoute,
  name: string,
  members: GroupMember[],
  targets: ReadonlyMap<string, PeerTargetState>
) {
  const seats = new Map<GroupMember, Omit<PeerSeat, 'member'>>()

  const checked = canonicalGroupRoster(route, members, member => {
    const own = peerRoute(member, route.connectionId)
    const target = own && targets.get(own.connectionId)

    if (!own || !target?.ready) {return undefined}
    seats.set(member, { route: own, renewal: target.renewal })

    return target
  })

  if (!name.trim() || 'problem' in checked) {
    throw Object.assign(new Error('A gateway group needs two to six unique members its gateway can seat'),
      { canonicalGroupProblem: 'problem' in checked ? checked.problem : 'groupSize' })
  }

  const { room } = await canonicalGroupRequest<{ room: CanonicalRoom }>(route, 'groups.create', {
    room_id: crypto.randomUUID(), name, members: checked.roster
  })

  const binding: CanonicalGroupBinding = { ...route, roomId: room.room_id }

  const unjoined = await seatPeers(binding, room, checked.peers.map(({ member, source }) => ({ member, ...seats.get(source)! })))

  return { binding, room, unjoined }
}

/** A gateway room from a selection: across connections when a Bot is on another connection, which
 *  needs a home that seats peers (`seatsPeers`), else exactly as `createCanonicalGroup`. */
export async function createGatewayGroup(route: CanonicalGroupRoute, name: string, members: GroupMember[],
  targets: ReadonlyMap<string, PeerTargetState>, seatsPeers: boolean) {
  if (!members.some(member => peerRoute(member, route.connectionId))) {
    return { ...await createCanonicalGroup(route, name, members), unjoined: [] as { member: CanonicalRoomMember; error: unknown }[] }
  }

  if (!seatsPeers) {
    throw Object.assign(new Error('This gateway seats no Bots of other gateways'), { canonicalGroupProblem: 'otherConnection' })
  }

  return createGroupAcrossConnections(route, name, members, targets)
}

/** How well a connection's answer serves a member pinned to `installation` and `digest`: 0 another
 *  gateway or no answer; 1 that gateway, unable to host (an SSH connection's backend); 2 ready with
 *  another catalog; 3 ready with the pinned one; 4 that, granting the renewal horizon too. */
function routeRank(state: PeerTargetState | null, installation: string, digest: string) {
  if (state?.gateway !== installation) {return 0}

  return !state.ready ? 1 : state.catalogDigest !== digest ? 2 : state.renewal ? 4 : 3
}

/** The best route (`routeRank`) whose gateway is `installation`, asking every route at once, so a
 *  connection that never answers holds up none of the others. The best kind wins as soon as it
 *  answers; otherwise the best answer once all have answered, so another connection to the same
 *  computer that can't host it, or a shorter grant, never wins needlessly; null when none matches. */
function routeTo(installation: string, digest: string, routes: ProfileRoute[]) {
  return new Promise<{ route: ProfileRoute; state: PeerTargetState } | null>(resolve => {
    let left = routes.length
    let best: { route: ProfileRoute; state: PeerTargetState; rank: number } | null = null

    if (!left) {resolve(null)}

    for (const route of routes) {
      void peerRequest<unknown>(route, 'groups.capabilities').then(peerTargetState, () => null).then(state => {
        const rank = routeRank(state, installation, digest)

        if (state && rank > (best?.rank ?? 0)) {best = { route, state, rank }}

        if (best?.rank === 4 || --left === 0) {resolve(best)}
      })
    }
  })
}

/** The connection this Desktop reaches `member`'s gateway on: among the roster's Bots on other
 *  connections, the one whose gateway is the installation the room pinned the member to. */
export async function findPeerSeat(home: CanonicalGroupRoute, member: CanonicalRoomMember, roster: Partial<RosterRow>[]): Promise<PeerSeat> {
  const pin = pinned(member)
  const routes = new Map<string, ProfileRoute>()

  for (const bot of roster) {
    const route = peerRoute(bot, home.connectionId)

    if (route && !routes.has(route.connectionId)) {routes.set(route.connectionId, route)}
  }

  const found = pin && await routeTo(pin.installation_id, pin.capability_digest, [...routes.values()])

  if (!pin || !found) {throw new PeerJoinError('peer_connection_missing')}

  if (!found.state.ready) {throw new PeerJoinError(found.state.reason)}

  // Its gateway lends a different profile or policy now: this room's pin can never match it.
  if (found.state.catalogDigest !== pin.capability_digest) {throw new PeerJoinError('peer_gateway_changed')}

  return { member, route: found.route, renewal: found.state.renewal }
}

/** Whether the home still holds `room` live under the authority it is seated against. */
async function roomLive(home: CanonicalGroupBinding, room: RoomAuthority) {
  const answer = await canonicalGroupRequest<{ room?: Partial<CanonicalRoom> }>(home, 'groups.state', {
    room_id: room.room_id, include_disbanded: true
  })

  return answer?.room?.disbanded_at == null && answer?.room?.authority_gateway_id === room.authority_gateway_id &&
    answer?.room?.authority_epoch === room.authority_epoch
}

/** Seat a member of an existing room again (Retry): find its connection, invite, register. */
export async function rejoinPeer(binding: CanonicalGroupBinding, room: RoomAuthority & { members?: CanonicalRoomMember[] },
  member: CanonicalRoomMember, roster: Partial<RosterRow>[]) {
  const seat = await findPeerSeat(binding, member, roster).then(async found => {
    // Finding the member's gateway can take a while: never ask it for a grant for a room that ended.
    if (!await roomLive(binding, room)) {throw new PeerJoinError('room_ended')}

    return found
  }).catch(error => {
    noteJoin(binding, member.member_id, error)
    throw error
  })

  await seatPeer(binding, room, seat)
}

/** Remove `memberId` the only way a gateway room's frozen roster allows: a new room with the other
 *  members (their pins unchanged, each peer seated again), then this room ends. The new room comes
 *  first, so a failure never leaves the Bots without a group. Earlier messages are not copied. */
export async function startAgainWithout(binding: CanonicalGroupBinding, room: CanonicalRoom, memberId: string, roster: Partial<RosterRow>[]) {
  const members = room.members.filter(member => member.member_id !== memberId)
    .map(({ member_id, profile, handle, display_name, target }) => ({ member_id, profile, handle, target, ...(display_name ? { display_name } : {}) }))

  const { room: next } = await canonicalGroupRequest<{ room: CanonicalRoom }>(binding, 'groups.create', {
    room_id: crypto.randomUUID(), name: room.name, members
  })

  const replacement: CanonicalGroupBinding = { connectionId: binding.connectionId, profile: binding.profile, roomId: next.room_id }
  const unjoined: { member: CanonicalRoomMember; error: unknown }[] = []

  for (const member of next.members.filter(pinned)) {
    await rejoinPeer(replacement, next, member, roster).catch(error => { unjoined.push({ member, error }) })
  }

  const ended = await canonicalGroupRequest<{ tombstone?: { room_id?: unknown; disbanded_at?: unknown } }>(binding, 'groups.disband', {
    room_id: binding.roomId, cancel_id: crypto.randomUUID()
  }).catch(() => undefined)

  // Without its tombstone this room stays, with End group chat, beside its replacement.
  const tombstone = ended?.tombstone

  return { binding: replacement, room: next, unjoined,
    ended: tombstone?.room_id === binding.roomId && typeof tombstone.disbanded_at === 'number' && Number.isFinite(tombstone.disbanded_at) }
}

/** The ids of the room's members of other gateways that have not joined (`peerMemberStates`). */
export function unjoinedPeers(room?: { members?: CanonicalRoomMember[] }, driver?: { peer_routes?: unknown }): ReadonlySet<string> {
  return new Set(peerMemberStates(room?.members, driver?.peer_routes).filter(({ status }) => status !== 'ready').map(({ member }) => member.member_id))
}

/** A member the room pinned to another gateway, and whether it has joined (`ready`), as `groups.state`
 *  reports its route: absent until it registers, `needs_reauthorization` once its access ended. */
export function peerMemberStates(members: CanonicalRoomMember[] = [], routes: unknown) {
  const statuses = new Map<string, string>()

  for (const route of Array.isArray(routes) ? routes : []) {
    if (route && typeof route.member_id === 'string' && typeof route.status === 'string') {statuses.set(route.member_id, route.status)}
  }

  return members.filter(member => pinned(member)).map(member => ({ member, status: statuses.get(member.member_id) ?? 'missing' }))
}

interface PeerLabels {
  peerTargetDesktopOwned: string
  peerJoinConnect: string
  peerJoinFailed: string
  peerJoinUnreachable: string
  peerJoinChanged: string
  peerTargetSetup: string
  peerTargetUnreachable: string
}

const fill = (words: string, values: Record<string, string>) =>
  Object.entries(values).reduce((text, [key, value]) => text.replace(`{${key}}`, value), words)

/** Why `name` has not joined, in the reader's words: Desktop's own finding or the gateway's refusal
 *  (`message` is its reason on the gateway, its detail on `hermes serve`). */
export function peerJoinText(error: unknown, name: string, labels: PeerLabels): string {
  const refusal = error as { data?: { reason?: unknown; detail?: unknown }; message?: unknown; reason?: unknown } | null

  const reason = error instanceof PeerJoinError ? error.reason
    : String(refusal?.data?.reason || refusal?.message || error)

  if (reason === 'peer_unreachable') {return fill(labels.peerJoinUnreachable, { name })}

  if (reason === 'peer_connection_missing') {return fill(labels.peerJoinConnect, { name })}

  if (['peer_gateway_changed', 'peer_target_mismatch', 'peer_target_unsupported'].includes(reason)) {return fill(labels.peerJoinChanged, { name })}
  const detail = typeof refusal?.data?.detail === 'string' && refusal.data.detail ? refusal.data.detail : reason

  return fill(labels.peerJoinFailed, { name, reason: detail })
}

/** Why another connection's gateway cannot host a Bot here, and what to do there. */
export function peerTargetText(state: PeerTargetState, connection: string, labels: PeerLabels): string {
  if (state.ready) {return ''}

  const words = { unreachable: labels.peerTargetUnreachable, persistent_process_required: labels.peerTargetDesktopOwned }[state.reason]

  return words ? fill(words, { connection }) : fill(labels.peerTargetSetup, { connection, reason: state.reason })
}

/** Whether a classic room's Bots can all join a new gateway room on `route` (#142926), those on other
 *  connections (`elsewhere`, from `dialablePeers`) through their own gateways: 'checking' while their
 *  gateways are read, then 'ready', or why not (once per connection, or the roster's own problem in
 *  `problemText`'s words). */
export function peerGatePlan(route: CanonicalGroupRoute, members: GroupMember[], elsewhere: GroupMember[],
  targets: ReadonlyMap<string, PeerTargetState>, labels: PeerLabels, problemText: (problem: string) => string): 'checking' | 'ready' | string[] {
  const states = elsewhere.map(member => {
    const own = peerRoute(member, route.connectionId)!

    return { connection: member.connectionLabel || own.connectionId, state: targets.get(own.connectionId) }
  })

  if (states.some(({ state }) => !state)) {return 'checking'}

  const problems = [...new Set(states.flatMap(({ connection, state }) => state!.ready ? [] : [peerTargetText(state!, connection, labels)]))]

  if (problems.length) {return problems}

  const checked = canonicalGroupRoster(route, members, member => {
    const own = peerRoute(member, route.connectionId)
    const state = own && targets.get(own.connectionId)

    return state?.ready ? state : undefined
  })

  return 'problem' in checked ? [problemText(checked.problem)] : 'ready'
}

/** The Bots of `elsewhere` this Desktop may dial to seat them here: each must be its connection's
 *  default Bot on a connection with a live roster row. Empty when any one is not. */
export function dialablePeers(elsewhere: GroupMember[], home: null | string, roster: Partial<RosterRow>[]): GroupMember[] {
  const live = new Set(roster.flatMap(row => peerRoute(row, home)?.connectionId ?? []))

  return elsewhere.length && elsewhere.every(member => live.has(peerRoute(member, home)?.connectionId ?? '')) ? elsewhere : []
}

import { canonicalGroupRequest } from './canonical-groups'
import type { CanonicalGroupRoute, CanonicalRoomMember } from './canonical-groups'
import type { GroupMember } from './types'

export interface CanonicalPeerPlan {
  memberId: string
  route: CanonicalGroupRoute
  installationId: string
  targetUrl: string
  catalog: Record<string, unknown>
}

export interface CanonicalPeerCapability {
  authorityId: string
  catalog: Record<string, unknown>
  endpoint: string
  features: string[]
}

function record(value: unknown): Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {}
}

function identifier(value: unknown): value is string {
  return typeof value === 'string' && value.length > 0 && value.length <= 256 && value.trim() === value
}

// Adapted from #97846's exact profile endpoint selection. Never append a second
// profile suffix, borrow a foreground route, or put credentials in a URL.
export function profileEndpoint(endpoint: unknown, profile: string): string {
  const value = record(endpoint)

  if (value.available !== true || typeof value.url !== 'string') {throw new Error('A Bot gateway needs a reachable Group Chat endpoint.')}
  const parsed = new URL(value.url)

  if (!['http:', 'https:'].includes(parsed.protocol) || parsed.username || parsed.password || parsed.search || parsed.hash) {
    throw new Error('A Bot gateway returned an invalid Group Chat endpoint.')
  }

  const parts = parsed.pathname.replace(/\/+$/, '').split('/')

  if (parts.at(-2) === 'p') {
    if (decodeURIComponent(parts.at(-1)!) !== profile) {throw new Error('The Bot endpoint belongs to another profile.')}
  } else {
    parsed.pathname = parts.join('/') + '/p/' + encodeURIComponent(profile)
  }

  return parsed.toString().replace(/\/+$/, '')
}

function checkedPeerCapability(route: CanonicalGroupRoute, value: Record<string, unknown>): CanonicalPeerCapability {
  const link = record(value.room_link), catalog = record(link.catalog)
  const features = Array.isArray(value.features) && value.features.every(item => typeof item === 'string') ? value.features as string[] : []
  const methods = Array.isArray(value.methods) ? value.methods : []

  if (!features.includes('peer_invitation_request_id') || !features.includes('reciprocal_room_control_setup')
    || !['groups.peer.invite', 'groups.control.register'].every(method => methods.includes(method))) {
    throw new Error('Update and reconnect the selected Bot gateways before creating this group.')
  }

  if (link.enabled !== true) {throw new Error('A selected Bot gateway cannot connect to other gateways yet. Check its Group Chat connection settings.')}

  if (!identifier(value.authority_gateway_id) || value.persistent_process !== true || link.enabled !== true
    || link.profile !== route.profile || catalog.installation_id !== value.authority_gateway_id
    || catalog.persistent_process !== true || catalog.text !== true
    || !Array.isArray(catalog.protocol_versions) || !catalog.protocol_versions.includes(2)
    || !Array.isArray(catalog.link_modes) || !catalog.link_modes.includes('direct')
    || typeof catalog.catalog_digest !== 'string' || !/^[a-f0-9]{64}$/.test(catalog.catalog_digest)) {
    throw new Error('A selected Bot gateway could not confirm its identity or capabilities. Refresh its connection before continuing.')
  }

  const endpoint = profileEndpoint(link.endpoint, route.profile)

  if (profileEndpoint(catalog.endpoint, route.profile) !== endpoint) {throw new Error('The Bot gateway endpoint changed during setup.')}

  return { authorityId: value.authority_gateway_id, catalog: JSON.parse(JSON.stringify(catalog)), endpoint, features }
}

export async function peerCapability(route: CanonicalGroupRoute): Promise<CanonicalPeerCapability> {
  return checkedPeerCapability(route, record(await canonicalGroupRequest(route, 'groups.capabilities')))
}

export async function planCrossGatewayMembers(home: CanonicalGroupRoute, selected: GroupMember[]): Promise<{ members: CanonicalRoomMember[]; peers: CanonicalPeerPlan[] }> {
  home = { ...home }

  if (home.profile !== 'default') {throw new Error('Open the host gateway\'s default profile to create a group across gateways.')}
  const frozen = JSON.parse(JSON.stringify(selected)) as GroupMember[]
  const homeCapabilities = record(await canonicalGroupRequest(home, 'groups.capabilities'))

  if (!identifier(homeCapabilities.authority_gateway_id) || homeCapabilities.driver !== true) {throw new Error('Reconnect the group host before continuing setup.')}
  const members: CanonicalRoomMember[] = [], peers: CanonicalPeerPlan[] = []
  const seen = new Set<string>(), handles = new Set(['all', 'everyone'])

  for (const [index, member] of frozen.entries()) {
    const connectionId = member.route?.connectionId ?? member.connectionId
    const profile = member.route?.targetProfile ?? member.targetProfile ?? member.name

    if (!identifier(connectionId) || !identifier(profile) || member.ghost || member.sourceMissing
      || (member.connectionId && member.connectionId !== connectionId)
      || (member.targetProfile && member.targetProfile !== profile)) {
      throw new Error('A selected Bot no longer has its original connection. Refresh the roster.')
    }

    const route = { connectionId, profile }
    const capabilities = connectionId === home.connectionId ? homeCapabilities : record(await canonicalGroupRequest(route, 'groups.capabilities'))

    if (!identifier(capabilities.authority_gateway_id)) {throw new Error('The Bot gateway identity could not be verified.')}
    const identity = JSON.stringify([capabilities.authority_gateway_id, profile.toLowerCase()])

    if (seen.has(identity)) {throw new Error('This Bot is already selected through another connection.')}
    seen.add(identity)
    const base = member.handle || (profile === 'default' ? 'hermes' : profile)

    if (!/^[A-Za-z0-9][A-Za-z0-9._:-]{0,95}$/.test(base) || ['all', 'everyone'].includes(base.toLowerCase())) {
      throw new Error('A selected Bot has an invalid or reserved mention name.')
    }

    let handle = base

    for (let suffix = 2; handles.has(handle.toLowerCase()); suffix++) {handle = `${base}-${suffix}`}
    handles.add(handle.toLowerCase())
    const memberId = `member-${index + 1}-${profile}`.replace(/[^A-Za-z0-9._:-]/g, '-').slice(0, 128)

    const descriptor: CanonicalRoomMember = { member_id: memberId, profile, handle,
      display_name: member.display_name || member.title || handle, target: { kind: 'local', profile } }

    if (capabilities.authority_gateway_id !== homeCapabilities.authority_gateway_id) {
      const capability = checkedPeerCapability(route, capabilities)
      descriptor.target = { kind: 'peer', peer_id: capability.authorityId, installation_id: capability.authorityId,
        profile, capability_digest: capability.catalog.catalog_digest }
      peers.push({ memberId, route, installationId: capability.authorityId, targetUrl: capability.endpoint, catalog: capability.catalog })
    }

    members.push(descriptor)
  }

  if (peers.length) {checkedPeerCapability(home, homeCapabilities)}

  return { members, peers }
}

async function currentPeerSetup(home: CanonicalGroupRoute, roomId: string, authorityId: string, peers: CanonicalPeerPlan[]) {
  const result = record(await canonicalGroupRequest(home, 'groups.state', { room_id: roomId }))
  const room = record(result.room)

  if (room.room_id !== roomId || room.authority_gateway_id !== authorityId || room.authority_epoch !== 1
    || room.disbanded_at != null || !Array.isArray(room.members)) {
    throw new Error('The Group Chat changed during setup. Its original setup has been kept.')
  }

  for (const peer of peers) {
    const member = room.members.map(record).find(value => value.member_id === peer.memberId)
    const target = record(member?.target)

    if (target.kind !== 'peer' || target.installation_id !== peer.installationId || target.profile !== peer.route.profile
      || target.capability_digest !== peer.catalog.catalog_digest) {
      throw new Error('A Group Chat member changed during setup.')
    }
  }

  return { room, routes: record(result.driver_status).peer_routes }
}

export async function connectCanonicalGroupPeers(home: CanonicalGroupRoute, roomId: string, authorityId: string, peers: CanonicalPeerPlan[]): Promise<void> {
  if (!peers.length) {return}
  const homeCapability = await peerCapability(home)

  if (home.profile !== 'default' || homeCapability.authorityId !== authorityId) {throw new Error('Reconnect the original group host to continue setup.')}

  for (const peer of peers) {
    const capability = await peerCapability(peer.route)

    if (capability.authorityId !== peer.installationId || capability.endpoint !== peer.targetUrl
      || capability.catalog.catalog_digest !== peer.catalog.catalog_digest) {
      throw new Error('A selected Bot gateway changed. Its original setup has been kept.')
    }

    const snapshot = await currentPeerSetup(home, roomId, authorityId, peers)
    const route = Array.isArray(snapshot.routes) ? snapshot.routes.map(record).find(value => value.member_id === peer.memberId) : undefined

    // Do not roll a successfully installed, subsequently renewed grant backward.
    if (route?.status !== 'ready' || typeof route.grant_sha256 !== 'string' || !/^[a-f0-9]{64}$/.test(route.grant_sha256)) {
      const invitation = record(await canonicalGroupRequest(peer.route, 'groups.peer.invite', {
        request_id: `desktop-peer:${roomId}:${peer.memberId}`, grant_id: `desktop-peer:${roomId}:${peer.memberId}`,
        room_id: roomId, home_install_id: authorityId, authority_gateway_id: authorityId, authority_epoch: 1,
        member_id: peer.memberId, ttl_seconds: 3600, status_ttl_seconds: 30 * 86400
      }))

      const catalog = record(invitation.catalog)

      if (typeof invitation.grant !== 'string' || !invitation.grant || invitation.grant.length > 32768
        || invitation.target_profile !== peer.route.profile || catalog.installation_id !== peer.installationId
        || catalog.catalog_digest !== peer.catalog.catalog_digest || profileEndpoint(invitation.endpoint, peer.route.profile) !== peer.targetUrl
        || typeof invitation.expires_at !== 'number' || !Number.isFinite(invitation.expires_at) || invitation.expires_at <= 0
        || typeof invitation.status_expires_at !== 'number' || !Number.isFinite(invitation.status_expires_at)
        || Math.abs(invitation.status_expires_at - invitation.expires_at - (30 * 86400 - 3600)) > 1) {
        throw new Error('The Bot invitation could not be verified. Continue the original setup; do not create another group.')
      }

      await currentPeerSetup(home, roomId, authorityId, peers)

      const registered = record(await canonicalGroupRequest(home, 'groups.peer.register', {
        room_id: roomId, member_id: peer.memberId, target_url: peer.targetUrl, target_profile: peer.route.profile,
        catalog, grant: invitation.grant, expected_grant_sha256: '',
        trace_id: `desktop-setup:${roomId}:${peer.memberId}`, cancellation_scope_id: `cancel-${roomId}`
      }))

      if (registered.registered !== true || registered.target_install_id !== peer.installationId || registered.target_profile !== peer.route.profile) {
        throw new Error('Connecting this Bot was not confirmed. Continue setup to check the same connection.')
      }
    }

    await currentPeerSetup(home, roomId, authorityId, peers)

    const control = record(await canonicalGroupRequest(home, 'groups.control.invite', {
      room_id: roomId, member_id: peer.memberId, caller_install_id: peer.installationId,
      request_id: `desktop-control:${roomId}:${peer.memberId}`, reuse_existing: true
    }))

    if (control.room_id !== roomId || control.member_id !== peer.memberId || control.authority_gateway_id !== authorityId
      || control.authority_epoch !== 1 || profileEndpoint({ available: true, url: control.home_url }, home.profile) !== homeCapability.endpoint
      || typeof control.control_token !== 'string' || !/^[A-Za-z0-9_-]{32,256}$/.test(control.control_token)
      || typeof control.expires_at !== 'number' || !Number.isFinite(control.expires_at) || control.expires_at <= 0) {
      throw new Error('Messaging access could not be verified for this Bot.')
    }

    const registered = record(await canonicalGroupRequest(peer.route, 'groups.control.register', {
      room_id: roomId, member_id: peer.memberId, home_url: control.home_url, authority_gateway_id: authorityId,
      authority_epoch: 1, room_name: control.room_name, member_count: control.member_count,
      control_token: control.control_token, expires_at: control.expires_at
    }))

    if (registered.registered !== true || registered.room_id !== roomId || registered.member_id !== peer.memberId) {
      throw new Error('Messaging access was not confirmed. Continue setup to check it.')
    }
  }

  const final = await currentPeerSetup(home, roomId, authorityId, peers)
  const routes = Array.isArray(final.routes) ? final.routes.map(record) : []

  if (peers.some(peer => !routes.some(route => route.member_id === peer.memberId && route.status === 'ready'))) {
    throw new Error('A Bot connection changed before setup finished. Continue setup to check it.')
  }
}

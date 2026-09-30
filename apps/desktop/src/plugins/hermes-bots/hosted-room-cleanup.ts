/** Durable compensation journal for hosted Group Chat setup and reconnect. */

import './room-secret-custody'

import { atom, host } from '@hermes/plugin-sdk'
import type { PluginContext } from '@hermes/plugin-sdk'

import { acquireHostedInstallationRoute, requestHostedInstallation } from './hosted-room-installation-route'
import type { HostedInstallationRequest } from './hosted-room-installation-route'
import { recoverHostedPeerControl, verifyHostedPeerControlScope } from './hosted-room-peer-setup'
import type { PeerControlRecoveryInput } from './hosted-room-peer-setup'
import type { ProfileRoute } from './types'

export const HOSTED_ROOM_CLEANUP_KEY = 'hosted-room-cleanup-v1'
const HOSTED_ROOM_CLEANUP_LIMIT = 64
const HOSTED_ROOM_CLEANUP_LOCK = 'hermes-bots-hosted-room-cleanup'
const HOSTED_ROOM_OWNER_LOCK_PREFIX = 'hermes-bots-hosted-room-owner:'
const HOSTED_ROOM_OWNER_LEASE_MS = 60_000

export interface HostedRoomCleanupOperation {
  armed: boolean
  cancelId?: null | string
  catalog?: null | Record<string, unknown>
  connectionId: string
  installationId?: string
  homeInstallationId?: string
  expectedGrantSha256?: null | string
  grant?: null | string
  grantSha256?: null | string
  homeConnectionId?: null | string
  homeProfile?: null | string
  kind: 'home-disband' | 'peer-reconnect' | 'peer-revoke' | 'peer-revoke-exact'
  memberId?: null | string
  operationId: string
  ownerId: string
  ownerLeaseUntil: number
  profile?: null | string
  reciprocalControl?: boolean
  controlAuthorityId?: null | string
  controlAuthorityEpoch?: null | number
  roomId?: null | string
  setupId: string
  targetUrl?: null | string
}

export interface HostedRoomCleanup {
  operations: HostedRoomCleanupOperation[]
  version: 1
}

export const $hostedRoomCleanup = atom<HostedRoomCleanup>({ version: 1, operations: [] })

interface VolatileGrantRecovery {
  operation: HostedRoomCleanupOperation
  // Exact normalized snapshot prepared before the fallible publication attempt.
  journalOperation?: HostedRoomCleanupOperation
  phase: 'publishing' | 'revoke-pending' | 'settlement-pending'
  task?: Promise<void>
  custodyCause: unknown
  revocationCause?: unknown
  settlementCause?: unknown
}
// Not persisted, expired, or cleared on lifecycle stop. Ownership transfers on
// custody ACK; failed publication requires BOTH exact revoke and journal settlement.
const volatileGrants = new Map<string, VolatileGrantRecovery>()
const grantAdmissions = new Set<string>()
export const $hostedRoomVolatileCleanup = atom<Array<{ setupId: string; roomId: string; durability: 'volatile' }>>([])

function publishVolatileCleanup() {
  $hostedRoomVolatileCleanup.set(
    [...volatileGrants.values()].map(({ operation }) => ({
      setupId: operation.setupId,
      roomId: operation.roomId || '',
      durability: 'volatile'
    }))
  )
}

async function revokeVolatileGrant(entry: VolatileGrantRecovery) {
  const operation = entry.operation
  const route = await routeForReference(operation.connectionId, String(operation.profile || 'default'))

  if (!route || !operation.installationId) {
    throw new Error('Original grant installation unavailable')
  }

  const result = record(
    await requestHostedInstallation(route, operation.installationId, 'groups.peer.revoke_exact', {
      grant: operation.grant,
      profile: operation.profile
    })
  )

  if (result?.revoked !== true) {
    throw new Error('Exact grant revocation is unconfirmed')
  }
}

/** Admission is bounded before minting; persistence failure cannot unwind away
 * the only grant copy. The invite callback must return the received response. */
export async function inviteHostedRoomGrant(
  operation: Omit<HostedRoomCleanupOperation, 'grant' | 'armed' | 'ownerId' | 'ownerLeaseUntil'>,
  invite: () => Promise<unknown>
) {
  if (!cleanupReady) {
    throw new Error('Group Chat cleanup recovery must finish before setup can be secured.')
  }

  if (
    grantAdmissions.has(operation.operationId) ||
    volatileGrants.has(operation.operationId) ||
    grantAdmissions.size + volatileGrants.size >= HOSTED_ROOM_CLEANUP_LIMIT
  ) {
    throw new Error('Group Chat volatile cleanup is pending. Keep Desktop open and reconnect the original hosts.')
  }

  grantAdmissions.add(operation.operationId)

  try {
    const current = await readPersistedCleanup()

    if (
      current.operations.some(entry => entry.operationId === operation.operationId) ||
      current.operations.length + grantAdmissions.size + volatileGrants.size > HOSTED_ROOM_CLEANUP_LIMIT
    ) {
      throw new Error('Group Chat cleanup capacity exhausted')
    }

    const native = window.hermesDesktop?.roomSecrets

    if (!native) {
      throw new Error('Group Chat secure credential storage is unavailable.')
    }

    native.exchange({ action: 'preflight', entries: [] })
    const invitation = record(await invite())
    const grant = String(invitation?.grant || '')

    if (!grant) {
      return invitation
    }

    const retained: VolatileGrantRecovery = {
      operation: {
        ...operation,
        grant,
        profile: String(invitation?.target_profile || operation.profile || 'default'),
        armed: true,
        ownerId: cleanupOwnerId,
        ownerLeaseUntil: 0
      },
      custodyCause: null,
      phase: 'publishing'
    }

    volatileGrants.set(operation.operationId, retained)
    publishVolatileCleanup()

    try {
      await addCleanupOperation(retained.operation, prepared => {
        retained.journalOperation = prepared
      })
      volatileGrants.delete(operation.operationId)
      publishVolatileCleanup()
    } catch (custodyCause) {
      retained.custodyCause = custodyCause
      retained.phase = 'revoke-pending'

      try {
        await settleVolatileGrant(retained)
      } catch {
        throw Object.assign(
          new Error(
            'Group Chat cleanup is pending in this window only. Keep Desktop open and reconnect the original hosts.',
            { cause: custodyCause }
          ),
          {
            cleanupPending: true,
            cleanupDurability: 'volatile',
            fallbackSafe: false,
            custodyCause,
            revocationCause: retained.revocationCause,
            settlementCause: retained.settlementCause
          }
        )
      }

      throw custodyCause
    }

    return invitation
  } finally {
    grantAdmissions.delete(operation.operationId)
  }
}

function volatileOwnsJournal(operation: HostedRoomCleanupOperation) {
  const retained = volatileGrants.get(operation.operationId)?.journalOperation

  return retained !== undefined && JSON.stringify(retained) === JSON.stringify(operation)
}

function settleVolatileGrant(entry: VolatileGrantRecovery): Promise<void> {
  if (entry.task) {
    return entry.task
  }

  const task = (async () => {
    if (entry.phase === 'revoke-pending') {
      try {
        await revokeVolatileGrant(entry)
        // Confirmation is monotonic even if subsequent settlement I/O fails.
        entry.phase = 'settlement-pending'
      } catch (error) {
        entry.revocationCause = error
        throw error
      }
    }

    try {
      // Never infer absence from the atom restored after a failed custody ACK.
      // The mutation lock covers fresh read, exact comparison, write and ACK.
      await mutateCleanup(current => ({
        version: 1,
        operations: current.operations.filter(
          operation => JSON.stringify(operation) !== JSON.stringify(entry.journalOperation)
        )
      }))
    } catch (error) {
      entry.settlementCause = error
      throw error
    }

    if (volatileGrants.get(entry.operation.operationId) === entry) {
      volatileGrants.delete(entry.operation.operationId)
    }

    publishVolatileCleanup()
  })().finally(() => {
    entry.task = undefined
  })

  entry.task = task

  return task
}

async function recoverVolatileGrants() {
  for (const entry of volatileGrants.values()) {
    // An invitation's persistence attempt still owns it until it fails.
    if (entry.phase === 'publishing') {
      continue
    }

    await settleVolatileGrant(entry).catch(() => undefined)
  }
}

let cleanupOwnerId = ''
let cleanupStorage: null | PluginContext['storage'] = null
let cleanupTask: Promise<void> | null = null
let cleanupRerun = false
const pendingArming = new Set<string>()
let cleanupDisposed = true
let cleanupReady = false
let cleanupGeneration = 0
let cleanupMutationTail: Promise<void> = Promise.resolve()
let cleanupOwnerLockRelease: null | (() => void) = null

interface CleanupLockManager {
  request<T>(
    name: string,
    options: { ifAvailable?: boolean; mode: 'exclusive' },
    callback: (lock: null | object) => Promise<T> | T
  ): Promise<T>
}

function newCleanupOwnerId() {
  return globalThis.crypto?.randomUUID?.() || `desktop-${Date.now()}-${Math.random().toString(36).slice(2)}`
}

function record(value: unknown): null | Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null
}

async function routeForReference(connectionId: string, profile = 'default') {
  if (typeof host.profileRoutes !== 'function') {
    return null
  }

  const routes = await host.profileRoutes()

  return ((Array.isArray(routes) ? routes : []).find(route => {
    const routeProfile = String(route?.targetProfile || route?.profile || '')

    return String(route?.connectionId || '') === connectionId && routeProfile === profile
  }) || null) as ProfileRoute | null
}

export function normalizeHostedRoomCleanup(value: unknown): HostedRoomCleanup {
  const candidate = record(value)
  const operations: HostedRoomCleanupOperation[] = []

  for (const raw of Array.isArray(candidate?.operations) ? candidate.operations : []) {
    const operation = record(raw)
    const operationId = String(operation?.operationId || '')
    const setupId = String(operation?.setupId || '')
    const kind = String(operation?.kind || '')
    const connectionId = String(operation?.connectionId || '')

    if (
      !operationId ||
      !setupId ||
      !connectionId ||
      !['home-disband', 'peer-reconnect', 'peer-revoke', 'peer-revoke-exact'].includes(kind)
    ) {
      continue
    }

    if (kind === 'home-disband' && !String(operation?.roomId || '')) {
      continue
    }

    if (
      ['peer-revoke', 'peer-revoke-exact'].includes(kind) &&
      (!String(operation?.grant || '') || !String(operation?.profile || ''))
    ) {
      continue
    }

    if (
      kind === 'peer-reconnect' &&
      (!String(operation?.grant || '') ||
        !/^[0-9a-f]{64}$/.test(String(operation?.grantSha256 || '')) ||
        !String(operation?.profile || '') ||
        !String(operation?.homeConnectionId || '') ||
        !String(operation?.homeProfile || '') ||
        !String(operation?.roomId || '') ||
        !String(operation?.memberId || '') ||
        !String(operation?.targetUrl || '') ||
        !record(operation?.catalog))
    ) {
      continue
    }

    const ownerId = String(operation?.ownerId || '')
    const ownerLeaseUntil = Number(operation?.ownerLeaseUntil || 0)

    operations.push({
      armed: operation?.armed === true || !ownerId,
      operationId,
      setupId,
      kind: kind as HostedRoomCleanupOperation['kind'],
      connectionId,
      installationId: String(operation?.installationId || ''),
      homeInstallationId: String(operation?.homeInstallationId || ''),
      ownerId,
      ownerLeaseUntil: Number.isFinite(ownerLeaseUntil) && ownerLeaseUntil > 0 ? ownerLeaseUntil : 0,
      roomId: ['home-disband', 'peer-reconnect'].includes(kind) ? String(operation?.roomId || '') : null,
      cancelId:
        kind === 'home-disband' ? String(operation?.cancelId || `rollback-${String(operation?.roomId || '')}`) : null,
      profile: String(operation?.profile || (kind === 'home-disband' ? 'default' : '')),
      reciprocalControl: kind === 'peer-reconnect' && operation?.reciprocalControl === true,
      controlAuthorityId: kind === 'peer-reconnect' ? String(operation?.controlAuthorityId || '') : null,
      controlAuthorityEpoch: kind === 'peer-reconnect' ? Number(operation?.controlAuthorityEpoch || 0) : null,
      grant: kind === 'home-disband' ? null : String(operation?.grant || ''),
      grantSha256: kind === 'peer-reconnect' ? String(operation?.grantSha256 || '') : null,
      expectedGrantSha256:
        kind === 'peer-reconnect' && /^[0-9a-f]{64}$/.test(String(operation?.expectedGrantSha256 || ''))
          ? String(operation?.expectedGrantSha256)
          : null,
      homeConnectionId: kind === 'peer-reconnect' ? String(operation?.homeConnectionId || '') : null,
      homeProfile: kind === 'peer-reconnect' ? String(operation?.homeProfile || '') : null,
      memberId: kind === 'peer-reconnect' ? String(operation?.memberId || '') : null,
      targetUrl: kind === 'peer-reconnect' ? String(operation?.targetUrl || '') : null,
      catalog: kind === 'peer-reconnect' ? record(operation?.catalog) : null
    })
  }

  return {
    version: 1,
    operations: operations.slice(-HOSTED_ROOM_CLEANUP_LIMIT)
  }
}

function processCleanupLock<T>(callback: () => Promise<T>) {
  const result = cleanupMutationTail.then(callback, callback)

  cleanupMutationTail = result.then(
    () => undefined,
    () => undefined
  )

  return result
}

function cleanupLockManager() {
  return (globalThis.navigator as (Navigator & { locks?: CleanupLockManager }) | undefined)?.locks
}

async function withCleanupLock<T>(callback: () => Promise<T>) {
  const locks = cleanupLockManager()

  return locks?.request
    ? locks.request(HOSTED_ROOM_CLEANUP_LOCK, { mode: 'exclusive' }, callback)
    : processCleanupLock(callback)
}

async function holdCleanupOwnerLock(ownerId: string) {
  cleanupOwnerLockRelease?.()
  cleanupOwnerLockRelease = null
  const locks = cleanupLockManager()

  if (!locks?.request) {
    return
  }

  let entered: () => void = () => undefined
  let release: () => void = () => undefined

  const acquired = new Promise<void>(resolve => {
    entered = resolve
  })

  const held = new Promise<void>(resolve => {
    release = resolve
  })

  cleanupOwnerLockRelease = release
  void locks
    .request(`${HOSTED_ROOM_OWNER_LOCK_PREFIX}${ownerId}`, { mode: 'exclusive' }, async () => {
      entered()
      await held
    })
    .catch(() => entered())
  await acquired
}

async function cleanupOwnerIsLive(operation: HostedRoomCleanupOperation) {
  if (!operation.ownerId) {
    return false
  }

  if (operation.ownerId === cleanupOwnerId) {
    return !operation.armed
  }

  const locks = cleanupLockManager()

  if (!locks?.request) {
    return operation.ownerLeaseUntil > Date.now()
  }

  try {
    let live = true

    await locks.request(
      `${HOSTED_ROOM_OWNER_LOCK_PREFIX}${operation.ownerId}`,
      { ifAvailable: true, mode: 'exclusive' },
      lock => {
        live = lock === null
      }
    )

    return live
  } catch {
    return operation.ownerLeaseUntil > Date.now()
  }
}

async function readPersistedCleanup() {
  if (!cleanupStorage?.get) {
    throw new Error('Desktop storage is unavailable, so Group Chat setup cannot be secured.')
  }

  return normalizeHostedRoomCleanup(await cleanupStorage.get(HOSTED_ROOM_CLEANUP_KEY, null))
}

async function replaceCleanup(previous: HostedRoomCleanup, next: HostedRoomCleanup) {
  if (!cleanupStorage?.set || !cleanupStorage?.get) {
    throw new Error('Desktop storage is unavailable, so Group Chat setup cannot be secured.')
  }

  $hostedRoomCleanup.set(next)

  try {
    await cleanupStorage.set(HOSTED_ROOM_CLEANUP_KEY, next)
    const persisted = normalizeHostedRoomCleanup(await cleanupStorage.get(HOSTED_ROOM_CLEANUP_KEY, null))

    if (JSON.stringify(persisted) !== JSON.stringify(next)) {
      throw new Error('Desktop storage did not persist Group Chat cleanup.')
    }
  } catch (error) {
    $hostedRoomCleanup.set(previous)
    throw error
  }
}

async function mutateCleanup(update: (current: HostedRoomCleanup) => HostedRoomCleanup) {
  return withCleanupLock(async () => {
    if (!cleanupReady) {
      throw new Error('Group Chat cleanup recovery must finish before setup can be secured.')
    }

    const current = await readPersistedCleanup()
    const next = normalizeHostedRoomCleanup(update(current))

    if (JSON.stringify(current) === JSON.stringify(next)) {
      $hostedRoomCleanup.set(current)

      return current
    }

    await replaceCleanup(current, next)

    return next
  })
}

export async function addHostedRoomCleanup(
  operation: Omit<HostedRoomCleanupOperation, 'armed' | 'ownerId' | 'ownerLeaseUntil'>
) {
  await addCleanupOperation(operation)
}

async function addCleanupOperation(
  operation: Omit<HostedRoomCleanupOperation, 'armed' | 'ownerId' | 'ownerLeaseUntil'>,
  prepared?: (operation: HostedRoomCleanupOperation) => void
) {
  await mutateCleanup(current => {
    const next = normalizeHostedRoomCleanup({
      version: 1,
      operations: [
        ...current.operations.filter(entry => entry.operationId !== operation.operationId),
        {
          ...operation,
          armed: false,
          ownerId: cleanupOwnerId,
          ownerLeaseUntil: Date.now() + HOSTED_ROOM_OWNER_LEASE_MS
        }
      ]
    })

    if (next.operations.length >= HOSTED_ROOM_CLEANUP_LIMIT && current.operations.length >= HOSTED_ROOM_CLEANUP_LIMIT) {
      throw new Error('Group Chat cleanup is pending. Reconnect the affected devices before creating another.')
    }

    const journalOperation = next.operations.find(entry => entry.operationId === operation.operationId)

    if (journalOperation) {
      prepared?.(journalOperation)
    }

    return next
  })
}

export async function releaseHostedRoomCleanup(setupId: string) {
  await mutateCleanup(current => ({
    version: 1,
    operations: current.operations.filter(operation => operation.setupId !== setupId)
  }))
}

export async function armHostedRoomCleanup(setupId: string) {
  // A failed renderer commit must not turn an abandoned setup back into a
  // live owner. Retry its arming intent before the next durable cleanup pass.
  if ($hostedRoomCleanup.get().operations.some(operation => operation.setupId === setupId)) {
    pendingArming.add(setupId)
  }

  await mutateCleanup(current => ({
    version: 1,
    operations: current.operations.map(operation =>
      operation.setupId === setupId && !volatileOwnsJournal(operation)
        ? {
            ...operation,
            armed: true,
            ownerId: '',
            ownerLeaseUntil: 0
          }
        : operation
    )
  }))
  pendingArming.delete(setupId)
}

export function hostedRoomCleanupPending(setupId: string) {
  return (
    [...volatileGrants.values()].some(
      ({ operation }) => operation.setupId === setupId || operation.roomId === setupId
    ) ||
    normalizeHostedRoomCleanup($hostedRoomCleanup.get()).operations.some(operation => operation.setupId === setupId)
  )
}

function homeDisbandAlreadySettled(operation: HostedRoomCleanupOperation, error: unknown) {
  const candidate = record(error)
  const inner = record(candidate?.error)
  const code = Number(candidate?.code ?? inner?.code)
  const message = String(candidate?.message || inner?.message || error || '')

  return operation.kind === 'home-disband' && code === 4113 && /hosted room not found|already disbanded/i.test(message)
}

async function peerRouteStatus(operation: HostedRoomCleanupOperation, request: HostedInstallationRequest) {
  const state = record(
    await request<Record<string, unknown>>('groups.state', {
      room_id: operation.roomId
    })
  )

  const driver = record(state?.driver_status)

  if (!driver || !Array.isArray(driver.peer_routes)) {
    return 'unknown' as const
  }

  const route = driver.peer_routes
    .map(record)
    .find(candidate => String(candidate?.member_id || '') === String(operation.memberId || ''))

  const status = String(route?.status || '')
  const grantSha256 = String(route?.grant_sha256 || '')
  const sameGrant = grantSha256 && grantSha256 === String(operation.grantSha256 || '')
  const expectedGrant = grantSha256 && grantSha256 === String(operation.expectedGrantSha256 || '')

  if (status === 'needs_reauthorization' && sameGrant) {
    return 'nonready' as const
  }

  if (sameGrant) {
    return 'matching' as const
  }

  if (expectedGrant || (!grantSha256 && !operation.expectedGrantSha256)) {
    return 'expected' as const
  }

  if (grantSha256) {
    return 'conflict' as const
  }

  if (status === 'needs_reauthorization') {
    return 'nonready' as const
  }

  return 'unknown' as const
}

async function settlePeerReconnect(
  operation: HostedRoomCleanupOperation,
  peerRoute: ProfileRoute,
  requestPeer: HostedInstallationRequest
) {
  const homeRoute = await routeForReference(
    String(operation.homeConnectionId || ''),
    String(operation.homeProfile || 'default')
  )

  if (!homeRoute || !operation.homeInstallationId) {
    return 'pending' as const
  }

  const homeLease = await acquireHostedInstallationRoute(homeRoute, operation.homeInstallationId)
  const request = homeLease.request

  try {
    let controlInput: PeerControlRecoveryInput | undefined

    if (operation.reciprocalControl) {
      if (
        !peerRoute ||
        !operation.controlAuthorityId ||
        !Number.isSafeInteger(operation.controlAuthorityEpoch) ||
        Number(operation.controlAuthorityEpoch) < 1
      ) {
        return 'pending' as const
      }

      controlInput = {
        homeRoute,
        peerRoute,
        requestHome: request,
        requestPeer,
        assertCurrent: homeLease.assertCurrent,
        roomId: String(operation.roomId),
        memberId: String(operation.memberId),
        authorityId: operation.controlAuthorityId,
        authorityEpoch: Number(operation.controlAuthorityEpoch),
        targetAuthority: String(operation.catalog?.installation_id || ''),
        targetProfile: String(operation.profile),
        requestId: operation.setupId
      }

      try {
        if ((await verifyHostedPeerControlScope(controlInput)) === 'gone') {
          return 'revoke' as const
        }
      } catch {
        return 'pending' as const
      }
    }

    // Await reciprocal recovery before this scope releases the home lease.
    const finishControl = async (outcome: 'pending' | 'settled' | 'revoke') => {
      if (!controlInput) {
        return outcome
      }

      try {
        return (await recoverHostedPeerControl(controlInput)) === 'gone' ? ('revoke' as const) : outcome
      } catch {
        return 'pending' as const
      }
    }

    try {
      if (['conflict', 'nonready'].includes(await peerRouteStatus(operation, request))) {
        return await finishControl('revoke')
      }
    } catch {
      /* registration remains the only safe settlement proof */
    }

    try {
      await request('groups.peer.register', {
        room_id: operation.roomId,
        member_id: operation.memberId,
        target_url: operation.targetUrl,
        target_profile: operation.profile,
        grant: operation.grant,
        catalog: operation.catalog,
        expected_grant_sha256: operation.expectedGrantSha256 || ''
      })

      return await finishControl('settled')
    } catch {
      try {
        return ['conflict', 'nonready'].includes(await peerRouteStatus(operation, request))
          ? await finishControl('revoke')
          : await finishControl('pending')
      } catch {
        return await finishControl('pending')
      }
    }
  } finally {
    homeLease.release()
  }
}

async function runBoundCleanup(
  operation: HostedRoomCleanupOperation,
  route: ProfileRoute,
  request: HostedInstallationRequest
) {
  if (operation.kind === 'peer-reconnect') {
    const outcome = await settlePeerReconnect(operation, route, request)

    if (outcome === 'settled') {
      return true
    }

    if (outcome === 'pending') {
      return false
    }
  }

  try {
    if (operation.kind === 'home-disband') {
      await request('groups.disband', {
        room_id: operation.roomId,
        cancel_id: operation.cancelId
      })
    } else {
      await request(operation.kind === 'peer-revoke' ? 'groups.peer.revoke' : 'groups.peer.revoke_exact', {
        grant: operation.grant,
        profile: operation.profile
      })
    }

    return true
  } catch (error) {
    return homeDisbandAlreadySettled(operation, error)
  }
}

async function runCleanup(operation: HostedRoomCleanupOperation) {
  if (!operation.installationId) {
    return false
  }

  const route = await routeForReference(operation.connectionId, String(operation.profile || 'default'))

  if (!route) {
    return false
  }

  try {
    const lease = await acquireHostedInstallationRoute(route, operation.installationId)

    try {
      const settled = await runBoundCleanup(operation, route, lease.request)
      lease.assertCurrent()

      return settled
    } finally {
      lease.release()
    }
  } catch {
    return false
  }
}

async function performCleanupPass(generation: number) {
  for (const setupId of pendingArming) {
    await armHostedRoomCleanup(setupId)
  }

  const snapshot = await mutateCleanup(current => ({
    version: 1,
    operations: current.operations.map(operation =>
      operation.ownerId === cleanupOwnerId && !operation.armed && !volatileOwnsJournal(operation)
        ? {
            ...operation,
            ownerLeaseUntil: Date.now() + HOSTED_ROOM_OWNER_LEASE_MS
          }
        : operation
    )
  }))

  for (const operation of snapshot.operations) {
    if (cleanupDisposed || generation !== cleanupGeneration) {
      return
    }

    if (volatileOwnsJournal(operation) || (await cleanupOwnerIsLive(operation))) {
      continue
    }

    let claimed: HostedRoomCleanupOperation | null = null

    await mutateCleanup(current => ({
      version: 1,
      operations: current.operations.map(entry => {
        if (JSON.stringify(entry) !== JSON.stringify(operation)) {
          return entry
        }

        claimed = {
          ...entry,
          armed: true,
          ownerId: cleanupOwnerId,
          ownerLeaseUntil: Date.now() + HOSTED_ROOM_OWNER_LEASE_MS
        }

        return claimed
      })
    }))

    if (!claimed || cleanupDisposed || generation !== cleanupGeneration) {
      continue
    }

    if (!(await runCleanup(claimed)) || cleanupDisposed || generation !== cleanupGeneration) {
      continue
    }

    await mutateCleanup(latest => ({
      version: 1,
      operations: latest.operations.filter(
        entry => entry.operationId !== claimed?.operationId || JSON.stringify(entry) !== JSON.stringify(claimed)
      )
    }))
  }
}

export function dispatchHostedRoomCleanup(): Promise<void> {
  if (cleanupDisposed || !cleanupReady) {
    return Promise.resolve()
  }

  if (cleanupTask) {
    cleanupRerun = true

    return cleanupTask
  }

  const generation = cleanupGeneration

  const task = (async () => {
    do {
      cleanupRerun = false
      await recoverVolatileGrants()
      await performCleanupPass(generation)
    } while (cleanupRerun && !cleanupDisposed && generation === cleanupGeneration)
  })().finally(() => {
    if (cleanupTask === task) {
      cleanupTask = null
    }
  })

  cleanupTask = task

  return task
}

export async function startHostedRoomCleanup(storage: PluginContext['storage']) {
  const generation = ++cleanupGeneration
  cleanupReady = false
  const previousOwnerId = cleanupOwnerId
  cleanupOwnerId = newCleanupOwnerId()
  cleanupStorage = storage
  cleanupDisposed = false
  await holdCleanupOwnerLock(cleanupOwnerId)

  await withCleanupLock(async () => {
    // A custody/read failure is not an empty journal. Never replace retained
    // pending operations with [] when the native keyring cannot be opened.
    const persisted = await storage.get(HOSTED_ROOM_CLEANUP_KEY, null)

    if (!cleanupDisposed && generation === cleanupGeneration) {
      const current = normalizeHostedRoomCleanup(persisted)

      const next = normalizeHostedRoomCleanup({
        version: 1,
        operations: current.operations.map(operation =>
          previousOwnerId && operation.ownerId === previousOwnerId && !volatileOwnsJournal(operation)
            ? {
                ...operation,
                armed: true,
                ownerId: '',
                ownerLeaseUntil: 0
              }
            : operation
        )
      })

      await replaceCleanup(current, next)
      cleanupReady = !cleanupDisposed && generation === cleanupGeneration
    }
  })

  if (cleanupDisposed || generation !== cleanupGeneration) {
    return
  }

  await dispatchHostedRoomCleanup().catch(() => undefined)
}

export function stopHostedRoomCleanup() {
  cleanupGeneration += 1
  cleanupDisposed = true
  cleanupOwnerLockRelease?.()
  cleanupOwnerLockRelease = null
}

export function resetHostedRoomCleanupForTests() {
  stopHostedRoomCleanup()
  volatileGrants.clear()
  pendingArming.clear()
  grantAdmissions.clear()
  publishVolatileCleanup()
  cleanupTask = null
  cleanupRerun = false
  cleanupOwnerId = ''
  cleanupStorage = null
  cleanupReady = false
  $hostedRoomCleanup.set({ version: 1, operations: [] })
}

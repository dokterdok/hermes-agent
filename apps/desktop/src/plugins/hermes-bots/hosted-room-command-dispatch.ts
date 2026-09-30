/** Older hosted controls stay on their own wire surface and captured route. */
import { host } from '@hermes/plugin-sdk'

import { classifyHostedRoomCapability, isHostedRoomContinuityEligible } from './hosted-room-client'
import type { HostedRoomCommand } from './hosted-room-client'
import type { ProfileRoute } from './types'

export async function requestHostedCommand(
  route: ProfileRoute,
  command: HostedRoomCommand,
  method: string,
  params: Record<string, unknown>,
  stillCurrent: () => boolean = () => true
) {
  // A registry descriptor can be retargeted between awaits. Older SDKs cannot
  // provide the physical-owner contract; never silently downgrade private work.
  if (!command.authorityId) {
    throw Object.assign(new Error('The saved action has no verified owning installation. Check the owning device.'), {
      code: 4000
    })
  }

  if (typeof host.acquireProfileRoute !== 'function') {
    throw Object.assign(new Error('Update Hermes Desktop before sending saved Group Chat actions.'), { code: 4000 })
  }

  const lease = await host.acquireProfileRoute(route)

  try {
    const assertRuntimeCurrent = () => {
      if (!stillCurrent()) {
        throw new Error('The hosted outbox runtime has been retired.')
      }
    }

    assertRuntimeCurrent()

    const request = <T>(name: string, body: Record<string, unknown> = {}) => lease.request<T>(name, body)

    const rawCapability = await request<Record<string, unknown>>('groups.capabilities')
    const capability = classifyHostedRoomCapability(rawCapability, { connectionId: route.connectionId })
    const link = rawCapability.room_link as Record<string, unknown> | undefined

    // Accepted surface qualification, kept here without importing the donor's
    // wider canonical/refresh composition into this public command-only port.
    const canonical =
      (Array.isArray(rawCapability.features) && rawCapability.features.includes('canonical_session_owner')) ||
      (Array.isArray(rawCapability.methods) && rawCapability.methods.includes('groups.discard')) ||
      link?.reason === 'canonical_driver_required'

    lease.assertCurrent()
    assertRuntimeCurrent()

    if (
      canonical ||
      !isHostedRoomContinuityEligible(capability) ||
      (command.authorityId && capability.authorityId !== command.authorityId)
    ) {
      throw Object.assign(new Error('The saved command does not belong to this hosted protocol or authority.'), {
        code: 4000
      })
    }

    const result = await request<Record<string, unknown>>(method, params)
    lease?.assertCurrent()
    assertRuntimeCurrent()
    validateHostedControlReceipt(command, result)

    return result
  } finally {
    lease?.release()
  }
}

function validateHostedControlReceipt(command: HostedRoomCommand, result: Record<string, unknown>) {
  if (!result || typeof result !== 'object') {
    throw new Error('Missing hosted command receipt')
  }

  // Same-key replay can settle earlier uncertainty only with the provider's
  // matching result identity, not a generic object or a later refusal.
  if (command.possibleAdmission && ['send', 'rename', 'create'].includes(command.kind)) {
    const event = result.event as Record<string, unknown> | undefined
    const room = result.room as Record<string, unknown> | undefined

    const matches =
      command.kind === 'send'
        ? result.accepted === true &&
          result.client_event_id === command.commandId &&
          event?.room_id === command.roomId &&
          event?.kind === 'message.user'
        : room?.room_id === command.roomId && (command.kind !== 'rename' || room.name === command.payload.name)

    if (!matches) {
      throw new Error('Unconfirmed hosted command receipt')
    }
  }

  const task = result.task as Record<string, unknown> | undefined
  const tombstone = result.tombstone as Record<string, unknown> | undefined

  const valid: Partial<Record<HostedRoomCommand['kind'], () => boolean>> = {
    retry: () =>
      result.retried === true &&
      task?.room_id === command.roomId &&
      task?.task_id === command.payload.task_id &&
      Number.isSafeInteger(task?.execution_generation) &&
      Number(task?.execution_generation) > 0,
    stop: () => Number.isSafeInteger(result.cancelled) && Number(result.cancelled) >= 0,
    disband: () => tombstone?.room_id === command.roomId
  }

  if (valid[command.kind]?.() === false) {
    throw new Error('Unconfirmed hosted command receipt')
  }
}

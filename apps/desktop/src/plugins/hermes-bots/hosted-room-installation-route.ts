/** Route owns socket lifetime; an installation probe must share that lease. */
import { host } from '@hermes/plugin-sdk'

import { classifyHostedRoomCapability } from './hosted-room-client'
import type { ProfileRoute } from './types'

export type HostedInstallationRequest = <T = unknown>(method: string, params?: Record<string, unknown>) => Promise<T>

export async function acquireHostedInstallationRoute(route: ProfileRoute, installationId: string) {
  if (!installationId || typeof host.acquireProfileRoute !== 'function') {
    throw new Error('Group Chat is waiting for its original installation and a supported Desktop route.')
  }

  const lease = await host.acquireProfileRoute(route)

  try {
    const capability = classifyHostedRoomCapability(await lease.request('groups.capabilities', {}), {
      connectionId: route.connectionId
    })

    lease.assertCurrent()

    if (capability.authorityId !== installationId) {
      throw new Error('Group Chat is waiting for its original installation.')
    }

    const request: HostedInstallationRequest = async (method, params = {}) => {
      lease.assertCurrent()

      try {
        return await lease.request(method, params)
      } finally {
        // A not-found reply from a retired socket is not settlement evidence.
        lease.assertCurrent()
      }
    }

    return { ...lease, request }
  } catch (error) {
    lease.release()
    throw error
  }
}

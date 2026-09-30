import { vi } from 'vitest'

import { createGroupGateway, scriptedStorage } from './group-test-utils'
import type { GatewayOptions } from './group-test-utils'
import type { GroupMember, ProfileRoute } from './types'

export const route: ProfileRoute = {
  connectionId: 'gateway-a', mode: 'remote', profile: 'default', targetProfile: 'default'
}

export const members: GroupMember[] = [{ name: 'reviewer', title: 'Reviewer' }]

/** Public engine only. This supplies no provider or private terminal-row contract. */
export async function loadRetentionEngine(host: Record<string, unknown>, options: GatewayOptions = {}) {
  vi.resetModules()
  const gateway = createGroupGateway(options)

  for (const key of Object.keys(host)) {
    delete host[key]
  }

  Object.assign(host, gateway.host)

  const [chat, client, rounds, runtime, data, shared] = await Promise.all([
    import('./group-chat'), import('./desktop-room-command-client'), import('./group-rounds'),
    import('./desktop-room-command-runtime'), import('./data'), import('./shared')
  ])

  shared.setPluginCtx(scriptedStorage(gateway.storage))
  data.$lastRoster.set(members)

  return { chat, client, gateway, rounds, runtime }
}

export type RetentionEngine = Awaited<ReturnType<typeof loadRetentionEngine>>

export async function settle<T>(promise: Promise<T>, limit = 120) {
  let settled = false
  void promise.then(() => { settled = true }, () => { settled = true })

  for (let index = 0; index < limit && !settled; index += 1) {
    await vi.advanceTimersByTimeAsync(250)
  }

  return promise
}

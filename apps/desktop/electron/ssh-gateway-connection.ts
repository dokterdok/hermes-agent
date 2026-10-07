import { resolveRemoteSshDashboardProfile } from './connection-config'
import { connect } from './remote-lifecycle'
import { attachSshGateway } from './ssh-gateway'
import { connectWindowsRemote } from './windows-remote-lifecycle'

/** Positive canonical capability proof is the only POSIX switch away from the existing classic lifecycle. */
export async function connectPreferredSshGateway(options: {
  lifecycle: Parameters<typeof connect>[0]
  requestedProfile?: string
  localProfile: unknown
}) {
  const { lifecycle } = options
  const platform = lifecycle.platform

  if (platform.os === 'Windows') {
    return connectWindowsRemote(lifecycle)
  }

  const result = await attachSshGateway({
    ssh: lifecycle.ssh,
    profile: lifecycle.profile,
    remoteHermesPath: lifecycle.remoteHermesPath,
    pickLocalPort: async () => Number(await lifecycle.pickLocalPort()),
    signal: lifecycle.signal,
    profileAlias: options.requestedProfile || resolveRemoteSshDashboardProfile('', options.localProfile) || 'default'
  })

  return result ? { ...result, platform } : connect(lifecycle)
}

export function sshConnectionKind(result: { canonical?: boolean; reused?: boolean }): string {
  return result.canonical ? 'attached canonical gateway' : result.reused ? 'REUSED dashboard' : 'spawned dashboard'
}

/** Native credentials stay pinned to the SSH descriptor; classic connections keep their existing builder. */
export async function sshConnectionDescriptor(
  result: any,
  source: string,
  hostLabel: string,
  classic: () => Promise<any>
) {
  return result.canonical
    ? {
        baseUrl: result.baseUrl,
        wsUrl: result.wsUrl,
        gatewayEndpoint: result.gatewayEndpoint,
        authMode: 'native',
        token: '',
        mode: 'remote',
        source,
        remoteHost: hostLabel,
        remoteKind: 'ssh'
      }
    : classic()
}

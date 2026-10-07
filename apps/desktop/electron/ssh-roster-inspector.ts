import { normalizeSshConfig } from './connection-config'
import { shouldRetrySshInventory } from './connection-registry'
import { listRemoteHermesProfiles, readRemoteInstallId } from './remote-lifecycle'
import { sshConfigFingerprint } from './ssh-bootstrap-coordinator'
import { createSshProbeConnection } from './ssh-connection'
import { inspectSshGatewayCommands } from './ssh-gateway'
import { readSshRosterInventory } from './ssh-roster-inventory'

/** Read-only source inventory retains its socket and registry fingerprint through every await. */
export function createSshRosterInspector(options: {
  cache: Map<string, string[]>
  attemptedAt: Map<string, number>
  retryMs: number
  installIds: Map<string, { id: string; ts: number }>
  states: Parameters<typeof readSshRosterInventory>[0]['states']
  request: Parameters<typeof readSshRosterInventory>[0]['request']
  currentConnection: (id: string) => any
  rememberLog: (message: string) => void
}) {
  async function probeSshProfileInventory(connection, isCurrent: () => boolean, knownClassic: boolean) {
    if (
      !shouldRetrySshInventory(
        options.cache.has(connection.id),
        options.attemptedAt.get(connection.id),
        Date.now(),
        options.retryMs
      )
    ) {
      return
    }

    options.attemptedAt.set(connection.id, Date.now())

    const sshConfig = normalizeSshConfig({
      mode: 'ssh',
      host: connection.host,
      user: connection.user,
      port: connection.port,
      keyPath: connection.keyPath,
      remoteHermesPath: connection.remoteHermesPath
    })

    if (!sshConfig) {
      return
    }

    const ssh = createSshProbeConnection(
      { host: sshConfig.host, user: sshConfig.user, port: sshConfig.port, keyPath: sshConfig.keyPath },
      { rememberLog: options.rememberLog }
    )

    try {
      await ssh.open()

      if (!knownClassic) {
        const commands = await inspectSshGatewayCommands(ssh, connection.remoteHermesPath || '')

        if (!isCurrent() || commands.canonical) {
          return
        }
      }

      const profiles = await listRemoteHermesProfiles(ssh)

      if (!isCurrent()) {
        return
      }

      if (profiles.length > 0) {
        options.cache.set(connection.id, profiles)
      }

      // Backend identity, on the session we already have open: without it an ssh connection has no
      // install id at all, so two addresses for one machine never collapse into one roster row
      // (#88828 wired this for remote/local only, through /api/status).
      const id = await readRemoteInstallId(ssh)

      if (isCurrent()) {
        options.installIds.set(connection.id, { id, ts: Date.now() })
      }
    } catch (error: any) {
      options.rememberLog(`[ssh] profile inventory failed for ${connection.id}: ${error?.message || error}`)
    } finally {
      try {
        await ssh.close()
      } catch {
        void 0
      }
    }
  }

  async function refreshSshProfileInventory(connection) {
    const fingerprint = sshConfigFingerprint(connection.id, connection)

    const inventory = await readSshRosterInventory({
      connectionId: connection.id,
      states: options.states,
      request: options.request
    })

    const isCurrent = () => {
      const current = options.currentConnection(connection.id)

      return (
        inventory.isCurrent() && current?.kind === 'ssh' && sshConfigFingerprint(current.id, current) === fingerprint
      )
    }

    if (!isCurrent()) {
      throw new Error('SSH inventory source changed during enumeration')
    }

    if (inventory.kind !== 'canonical') {
      await probeSshProfileInventory(connection, isCurrent, inventory.kind === 'classic')
    }

    if (!isCurrent()) {
      throw new Error('SSH inventory source changed during enumeration')
    }

    if (inventory.kind === 'canonical') {
      options.cache.set(connection.id, inventory.profiles)
      options.installIds.set(connection.id, { id: inventory.installId, ts: Date.now() })
    }

    return { ...inventory, isCurrent }
  }

  return refreshSshProfileInventory
}

import type * as clientModule from './desktop-room-command-client'

interface QueuedCommand extends clientModule.DesktopRoomCommand {
  action: 'send' | 'stop'
  command_id: string
  created: number
  payload: Record<string, unknown>
  room_id: string
  state: 'claimed' | 'completed' | 'failed' | 'pending'
  leaseOwner?: string
  leaseUntil?: number
}

export class ControlledMailbox {
  clock = 1_000
  completeFailures = 0
  failClaims = 0
  readonly calls: Array<{ method: string; params: Record<string, unknown> }> = []
  readonly commands = new Map<string, QueuedCommand>()
  readonly owners = new Map<string, { consumer: string; until: number }>()
  readonly tokens = new Map<string, string>()

  queue(command: Omit<QueuedCommand, 'created' | 'state'>) {
    this.commands.set(command.command_id, { ...command, created: this.clock, state: 'pending' })
  }

  advance(milliseconds: number) {
    this.clock += milliseconds
  }

  expire(commandId: string) {
    const command = this.commands.get(commandId)

    if (command) {
      command.leaseUntil = this.clock - 1
    }
  }

  async request(method: string, params: Record<string, unknown>) {
    this.calls.push({ method, params: structuredClone(params) })

    if (method === 'groups.desktop.presence') {
      return { room_ids: this.own(params) }
    }

    if (method === 'groups.desktop.claim') {
      if (this.failClaims-- > 0) {
        throw new Error('temporary route failure')
      }

      const owned = new Set(this.own(params))
      const actions = new Set(Array.isArray(params.actions) ? params.actions.map(String) : ['send', 'stop'])
      const limit = Math.max(1, Math.min(8, Number(params.limit || 8)))

      const available = [...this.commands.values()]
        .filter(
          command =>
            owned.has(command.room_id) &&
            actions.has(command.action) &&
            (command.state === 'pending' ||
              (command.state === 'claimed' && Number(command.leaseUntil || 0) <= this.clock))
        )
        .sort(
          (left, right) =>
            Number(right.action === 'stop') - Number(left.action === 'stop') || left.created - right.created
        )
        .slice(0, limit)

      for (const command of available) {
        command.state = 'claimed'
        command.leaseOwner = String(params.consumer_id)
        command.lease_token = `lease:${command.command_id}:${this.clock}`
        command.leaseUntil = this.clock + 45_000
        command.attempts = Number(command.attempts || 0) + 1
      }

      return { commands: structuredClone(available) }
    }

    const command = this.commands.get(String(params.command_id || ''))

    if (!command || command.leaseOwner !== params.consumer_id || command.lease_token !== params.lease_token) {
      throw new Error('command lease is no longer owned by this Desktop')
    }

    if (method === 'groups.desktop.renew') {
      if (Number(command.leaseUntil || 0) <= this.clock) {
        throw new Error('command lease expired')
      }

      command.leaseUntil = this.clock + 45_000

      return { command: structuredClone(command) }
    }

    if (method === 'groups.desktop.complete') {
      if (this.completeFailures-- > 0) {
        throw new Error('completion transport failed')
      }

      if (Number(command.leaseUntil || 0) <= this.clock) {
        throw new Error('command lease expired')
      }

      command.state = params.success === true ? 'completed' : 'failed'
      command.leaseOwner = undefined
      command.lease_token = undefined
      command.leaseUntil = undefined

      return { command: structuredClone(command) }
    }

    throw new Error(`unexpected RPC ${method}`)
  }

  private own(params: Record<string, unknown>) {
    const consumer = String(params.consumer_id || '')
    const owned: string[] = []

    for (const raw of Array.isArray(params.room_authorities) ? params.room_authorities : []) {
      const authority = raw as Record<string, unknown>
      const roomId = String(authority.room_id || '')
      const token = String(authority.authority_token || '')
      const expected = this.tokens.get(roomId)
      const current = this.owners.get(roomId)

      if (!expected || expected !== token) {
        continue
      }

      if (current && current.consumer !== consumer && current.until > this.clock) {
        continue
      }

      this.owners.set(roomId, { consumer, until: this.clock + 90_000 })
      owned.push(roomId)
    }

    return owned
  }
}

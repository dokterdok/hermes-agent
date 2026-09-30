import { registerPersistenceCodec } from '@hermes/plugin-sdk'
import type { RoomSecretEntry } from '@hermes/plugin-sdk'

// This module is loaded by both credential owners before plugin hydration.
// Protect at the actual persistence boundary, not just one serializer: reads,
// ordinary updates, required readback and compensation all use the same codec.
type RecordValue = Record<string, unknown>

function record(value: unknown): value is RecordValue {
  return !!value && typeof value === 'object' && !Array.isArray(value)
}

const text = (value: unknown) => (value === undefined || value === null ? '' : String(value))

function exchange(action: 'seal' | 'open', entries: RoomSecretEntry[]) {
  if (!entries.length) {
    return []
  }

  const bridge = window.hermesDesktop?.roomSecrets

  if (!bridge) {
    throw new Error('Group Chat secure credential storage is unavailable.')
  }

  const values = bridge.exchange({ action, entries })

  if (
    !Array.isArray(values) ||
    values.length !== entries.length ||
    values.some(value => typeof value !== 'string' || !value)
  ) {
    throw new Error('Group Chat credential readback failed')
  }

  return values
}

function transform(raw: string, family: 'rooms' | 'cleanup', action: 'seal' | 'open') {
  const parsed: unknown = JSON.parse(raw)

  if (!record(parsed)) {
    throw new Error('Invalid protected Group Chat record')
  }

  const targets: Array<{ owner: RecordValue; field: string; scope: string[] }> = []

  if (family === 'rooms') {
    for (const [name, room] of Object.entries(parsed)) {
      if (!record(room)) {
        continue
      }

      const roomId = text(room.roomId) || `name:${name}`
      targets.push({
        owner: room,
        field: 'desktopAuthorityToken',
        scope: ['classic-authority', roomId, text(room.desktopAuthorityHash)]
      })

      for (const candidate of Array.isArray(room.desktopAuthorityCandidates) ? room.desktopAuthorityCandidates : []) {
        if (!record(candidate)) {
          throw new Error('Invalid retained room credential')
        }

        targets.push({ owner: candidate, field: 'token', scope: ['classic-authority', roomId, text(candidate.hash)] })
      }
    }
  } else {
    for (const operation of Array.isArray(parsed.operations) ? parsed.operations : []) {
      if (!record(operation)) {
        continue
      }

      targets.push({
        owner: operation,
        field: 'grant',
        scope: [
          'hosted-grant',
          text(operation.roomId),
          text(operation.setupId),
          text(operation.operationId),
          text(operation.kind),
          text(operation.installationId),
          text(operation.connectionId),
          text(operation.profile),
          text(operation.homeInstallationId),
          text(operation.homeConnectionId),
          text(operation.homeProfile),
          text(operation.memberId),
          text(operation.controlAuthorityId),
          text(operation.controlAuthorityEpoch),
          text(operation.grantSha256),
          text(operation.targetUrl)
        ]
      })
    }
  }

  const active = targets.filter(({ owner, field }) => owner[field] || owner[`${field}Ref`])

  const entries = active.map(({ owner, field, scope }): RoomSecretEntry => {
    const ref = owner[`${field}Ref`]
    const value = owner[field]

    if (ref && value) {
      throw new Error('Ambiguous room credential record')
    }

    if (action === 'open' && !ref) {
      throw new Error('Unprotected room credential record')
    }

    if ((ref && typeof ref !== 'string') || (value && typeof value !== 'string')) {
      throw new Error('Invalid room credential record')
    }

    return ref ? { scope, ref: String(ref) } : { scope, value: String(value) }
  })

  const values = exchange(action, entries)
  active.forEach(({ owner, field }, index) => {
    const refField = `${field}Ref`
    const source = Object.entries(owner)

    // Preserve property order for existing exact-snapshot readback consumers.
    for (const key of Object.keys(owner)) {
      delete owner[key]
    }

    for (const [key, value] of source) {
      Object.defineProperty(owner, key === field || key === refField ? (action === 'seal' ? refField : field) : key, {
        value: key === field || key === refField ? values[index] : value,
        enumerable: true,
        configurable: true,
        writable: true
      })
    }
  })

  return JSON.stringify(parsed)
}

for (const [key, family] of [
  ['group-chats', 'rooms'],
  ['hosted-room-cleanup-v1', 'cleanup']
] as const) {
  registerPersistenceCodec(`hermes.plugin.hermes-bots.${key}`, {
    encode: value => transform(value, family, 'seal'),
    decode: value => transform(value, family, 'open'),
    exclusive: commit => {
      const bridge = window.hermesDesktop?.roomSecrets

      if (!bridge) {
        throw new Error('Group Chat secure credential storage is unavailable.')
      }

      const storageKey = `hermes.plugin.hermes-bots.${key}`
      const token = bridge.lock(storageKey)

      try {
        return commit()
      } finally {
        bridge.unlock(storageKey, token)
      }
    }
  })
}

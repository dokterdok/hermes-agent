/** #104199 reference-only retrieval; no classic producer or session lifecycle. */
import { gatewayActivationEpoch, host } from '@hermes/plugin-sdk'

import { downloadCanonicalAttachment } from './canonical-attachment-download'
import { withFilesDeadline } from './canonical-files-client'
import type { RetainedAttachment } from './retained-group-files'
import type { ProfileRoute } from './types'

interface Recipient { installation: string; profile: string }
interface ClassicReference {
  group: string
  exportId: string
  artifactId: string
  generation: number
  installation: string
  session: string
  sha256: string
  recipients: Recipient[]
  route: ProfileRoute
}

export class ClassicReferenceError extends Error {
  constructor(readonly kind: 'gone' | 'unavailable' | 'verification') {
    super(kind)
  }
}

function record(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {}
}

function identifier(value: unknown): value is string {
  return typeof value === 'string' && value.length > 0 && value.length <= 160 && value.trim() === value
    && ![...value].some(char => char.charCodeAt(0) < 32 || char.charCodeAt(0) === 127)
}

function recipients(value: unknown): Recipient[] | null {
  if (!Array.isArray(value) || value.length < 1 || value.length > 6) {return null}
  const result: Recipient[] = []

  for (const raw of value) {
    const item = record(raw)

    if (Object.keys(item).length !== 2 || !identifier(item.installation) || !identifier(item.profile)) {return null}
    result.push({ installation: item.installation, profile: item.profile })
  }

  return result
}

export function classicReference(file: Readonly<RetainedAttachment>): ClassicReference | null {
  const ref = record(file.classicExport)
  const source = record(ref.source)
  const savedRoute = record(source.route)
  const connectionId = savedRoute.connectionId ?? source.connectionId
  const profile = savedRoute.profile ?? source.name
  const target = savedRoute.targetProfile ?? source.targetProfile ?? profile
  const audience = recipients(ref.recipients)

  if (source.route !== undefined && (
    !source.route || typeof source.route !== 'object' || Array.isArray(source.route)
    || !identifier(savedRoute.connectionId) || !identifier(savedRoute.profile)
    || (savedRoute.targetProfile !== undefined && !identifier(savedRoute.targetProfile))
    || (savedRoute.mode !== undefined && !['local', 'remote'].includes(String(savedRoute.mode)))
  )) {return null}

  if (
    file.data !== undefined || !identifier(connectionId) || !identifier(profile) || target !== 'default'
    || !identifier(source.name) || source.name !== profile
    || (source.connectionId !== undefined && source.connectionId !== connectionId)
    || (source.targetProfile !== undefined && source.targetProfile !== target)
    || !identifier(ref.group) || !identifier(ref.installation) || !identifier(ref.session)
    || typeof ref.exportId !== 'string' || !/^ce_[0-9a-f]{64}$/.test(ref.exportId)
    || typeof ref.artifactId !== 'string' || !/^rart_[0-9a-f]{32}$/.test(ref.artifactId)
    || !Number.isSafeInteger(ref.generation) || Number(ref.generation) < 1
    || typeof ref.sha256 !== 'string' || !/^[0-9a-f]{64}$/.test(ref.sha256)
    || (file.sha256 !== undefined && file.sha256 !== ref.sha256) || !audience
    || !['file', 'pdf', 'image'].includes(file.kind)
    || typeof file.name !== 'string' || !file.name.trim() || file.name.length > 255 || /[/\\]/.test(file.name)
    || [...file.name].some(char => char.charCodeAt(0) < 32 || char.charCodeAt(0) === 127)
    || typeof file.mime !== 'string' || file.mime.length > 127
    || !/^[a-z0-9][a-z0-9!#$&^_.+-]*\/[a-z0-9][a-z0-9!#$&^_.+-]*$/i.test(file.mime)
    || (file.kind === 'image' && !file.mime.startsWith('image/')) || (file.kind === 'pdf' && file.mime !== 'application/pdf')
    || !Number.isSafeInteger(file.size) || Number(file.size) < 1 || Number(file.size) > 15_000_000
  ) {return null}

  return {
    group: ref.group, installation: ref.installation, session: ref.session,
    exportId: ref.exportId, artifactId: ref.artifactId, generation: Number(ref.generation),
    sha256: ref.sha256, recipients: audience,
    route: Object.freeze({ connectionId, profile, targetProfile: 'default',
      mode: savedRoute.mode === 'local' || connectionId === 'local' ? 'local' : 'remote' })
  }
}

export async function saveClassicReference(file: Readonly<RetainedAttachment>, current: () => boolean, signal: AbortSignal) {
  const ref = classicReference(file)

  if (!ref || typeof host.requestProfile !== 'function' || typeof gatewayActivationEpoch !== 'function') {
    throw new ClassicReferenceError('unavailable')
  }

  const epoch = gatewayActivationEpoch()

  const requireCurrent = () => {
    if (signal.aborted || !current() || gatewayActivationEpoch() !== epoch) {throw new ClassicReferenceError('gone')}
  }

  requireCurrent()

  let raw: unknown

  try {
    // The descriptor is from the retained reference, never the foreground Bot.
    raw = await withFilesDeadline(host.requestProfile(ref.route, 'session.export.read', {
      session_id: ref.session, profile: 'default', installation: ref.installation,
      group_id: ref.group, export_id: ref.exportId, artifact_id: ref.artifactId, generation: ref.generation
    }), signal)
  } catch {
    requireCurrent()
    throw new ClassicReferenceError('unavailable')
  }

  requireCurrent()
  const result = record(raw)
  const item = record(result.item)

  if (
    result.session_id !== ref.session || result.installation !== ref.installation
    || result.group_id !== ref.group || result.export_id !== ref.exportId
    || result.generation !== ref.generation || result.state !== 'published'
    || JSON.stringify(recipients(result.recipients)) !== JSON.stringify(ref.recipients)
    || item.artifact_id !== ref.artifactId || item.sha256 !== ref.sha256
    || item.name !== file.name || item.kind !== file.kind || item.mime !== file.mime || item.size !== file.size
    || typeof result.content_base64 !== 'string' || result.content_base64.length !== 4 * Math.ceil(file.size! / 3)
  ) {throw new ClassicReferenceError('verification')}

  let bytes: Uint8Array<ArrayBuffer>

  try {
    const decoded = atob(result.content_base64)

    if (decoded.length !== file.size || btoa(decoded) !== result.content_base64) {throw new Error('invalid encoding')}
    bytes = Uint8Array.from(decoded, char => char.charCodeAt(0))
  } catch {
    throw new ClassicReferenceError('verification')
  }

  const digest = await crypto.subtle.digest('SHA-256', bytes)
  const hex = Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, '0')).join('')

  if (hex !== ref.sha256) {throw new ClassicReferenceError('verification')}
  requireCurrent()
  downloadCanonicalAttachment(bytes, file.name, file.mime!, signal)
}

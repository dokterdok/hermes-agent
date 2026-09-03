import { describe, expect, it } from 'vitest'

import { classifyHostedRoomCapability } from './hosted-room-client'
import {
  hostedRoomCapabilityFingerprint,
  hostedRoomPollFingerprint,
  isHostedRoomDisbanded
} from './hosted-room-inventory'

describe('integration inventory contracts', () => {
  it('invalidates the capability observation when reciprocal controls change', () => {
    const base = classifyHostedRoomCapability({ ok: false, error: { code: -32601 } })
    const enabled = { ...base, reciprocalControl: true }
    const disabled = { ...base, reciprocalControl: false }

    expect(hostedRoomCapabilityFingerprint(enabled)).not.toBe(hostedRoomCapabilityFingerprint(disabled))
    expect(hostedRoomCapabilityFingerprint({ ...enabled })).toBe(hostedRoomCapabilityFingerprint(enabled))
  })

  it('tracks revision and event order independently of unrelated list metadata', () => {
    const room = { revision: 4, latest_seq: 9 }
    const fingerprint = hostedRoomPollFingerprint(room)

    expect(hostedRoomPollFingerprint({ ...room, name: 'Renamed display', unrelated: true })).toBe(fingerprint)
    expect(hostedRoomPollFingerprint({ ...room, revision: 5 })).not.toBe(fingerprint)
    expect(hostedRoomPollFingerprint({ ...room, latest_seq: 10 })).not.toBe(fingerprint)
    expect(hostedRoomPollFingerprint({ revision: -1, latest_seq: -2 })).toBe(hostedRoomPollFingerprint({}))
  })

  it('preserves the existing non-null tombstone contract', () => {
    expect(isHostedRoomDisbanded({})).toBe(false)
    expect(isHostedRoomDisbanded({ disbanded_at: null })).toBe(false)
    expect(isHostedRoomDisbanded({ disbanded_at: undefined })).toBe(false)
    expect(isHostedRoomDisbanded({ disbanded_at: 0 })).toBe(true)
    expect(isHostedRoomDisbanded({ disbanded_at: 123 })).toBe(true)
  })
})

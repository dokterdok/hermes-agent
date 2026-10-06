import { expect, it } from 'vitest'

import { canonicalRetirementStatus } from './canonical-group-retirement'

const ended = {room: {room_id: 'room', disbanded_at: 123}, driver_status: {retiring: true, peer_cleanup: [] as unknown}}

it.each([undefined, null, {}, [{status: 'unreadable'}], [{status: 'pending', room_id: 'other', member_id: 'm', mode: 'exact'}], [{status: 'done'}]])('never treats malformed or unattributable cleanup %j as complete', peer_cleanup => {
  expect(canonicalRetirementStatus('room', {...ended, driver_status: {...ended.driver_status, peer_cleanup}})).toEqual({phase: 'unreadable', retired: true})
})
it('ignores the persistent retiring marker after a readable empty cleanup queue and exact tombstone', () => {
  expect(canonicalRetirementStatus('room', ended)).toEqual({phase: 'complete', retired: true})
  expect(canonicalRetirementStatus('other', ended)).toEqual({phase: 'unreadable', retired: false})
  expect(canonicalRetirementStatus('room', {room: {room_id: 'room'}, driver_status: {retiring: true, peer_cleanup: []}})).toEqual({phase: 'stopping', retired: false})
})

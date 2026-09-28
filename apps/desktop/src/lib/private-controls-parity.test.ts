import { expect, it } from 'vitest'

import * as plugin from '../plugins/hermes-bots/private-controls'
import * as shared from '../../../shared/src/private-controls'

const copies = { plugin, shared }
const selector = `pa-${'ab'.repeat(32)}`
const other = `pa-${'cd'.repeat(32)}`
const action = {
  selector, member_id: 'member-1', task_id: 'task-1', request_id: 'request-1', execution_generation: 4
}
const recipient = {
  platform: 'telegram', user_id: 'user-1', chat_id: 'chat-1', thread_id: null, scope_id: null,
  transport_profile: 'telegram', runtime_profile: 'default'
}
const consent = {
  requestId: 'req-1', recipient, roomId: 'room-1',
  roomReadBindingId: `mrr-${'12'.repeat(16)}`, roomReadGeneration: 2, expectedGeneration: 1,
  bindingId: `mrc-${'34'.repeat(16)}`
}

it('keeps the shared and plugin control contracts on the same decisions', () => {
  for (const copy of Object.values(copies)) {
    expect(copy.exactDisplayedApproval([action, { ...action, selector: other }], selector)).toEqual(action)
    expect(() => copy.exactDisplayedApproval([action], '1')).toThrow(/selector/)
    expect(() => copy.exactDisplayedApproval([action, action], selector)).toThrow(/one displayed/)
    const approved = copy.groupsApproveParams('room-1', action, 'deny')
    expect(approved).toEqual({
      room_id: 'room-1', member_id: 'member-1', task_id: 'task-1',
      execution_generation: 4, request_id: 'request-1', choice: 'deny'
    })
    expect(approved).not.toHaveProperty('selector')
    expect(copy.groupsStopParams('room-1', '9b1deb4d-3b7d-4bad-9bdd-2b0d7b3dcb6d').cancel_id).toBe('9b1deb4d-3b7d-4bad-9bdd-2b0d7b3dcb6d')
    expect(() => copy.groupsStopParams('room-1', selector)).toThrow(/cancel id/)
    expect(() => copy.groupsStopParams('room-1', '1')).toThrow(/cancel id/)
    expect(copy.controlMethod('stop', 'grant')).toBe('groups.messaging.room.stop.grant')
    expect(copy.controlMethod('approval', 'revoke')).toBe('groups.messaging.room.approval.revoke')
    expect(copy.controlMethod('stop', 'grant')).not.toBe('groups.stop')
    const granted = copy.controlGrantParams(consent)
    expect(granted).not.toHaveProperty('binding_id')
    expect(Object.keys(granted.recipient).sort()).toEqual([
      'chat_id', 'platform', 'runtime_profile', 'scope_id', 'thread_id', 'transport_profile', 'user_id'
    ])
    expect(copy.controlRevokeParams(consent).binding_id).toBe(consent.bindingId)
    expect(() => copy.controlGrantParams({ ...consent, recipient: { ...recipient, runtime_profile: 'other' } })).toThrow(/default/)
  }

  expect(plugin.groupsApproveParams('room-1', action, 'once')).toEqual(shared.groupsApproveParams('room-1', action, 'once'))
  expect(plugin.controlRevokeParams(consent)).toEqual(shared.controlRevokeParams(consent))
})

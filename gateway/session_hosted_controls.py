"""Exact operator controls for canonical hosted attempts, never legacy replay."""
from gateway import hosted_room_driver as tasks
from hermes_state_runtime import RuntimeStoreError
from tui_gateway.hosted_room_driver import HostedRoomBinding


class HostedControls:
    def _set_pending_action(self, room_id, member_id, action):
        super()._set_pending_action(room_id, member_id, action)
        if action is not None and action.get('kind') == 'approval':
            from gateway.session_group_rules import apply_remembered
            try:
                apply_remembered(self, room_id, member_id)
            except (RuntimeStoreError, ValueError):
                # A missing/stale permission leaves the real prompt waiting.
                pass
            except Exception as exc:
                import logging
                logging.getLogger(__name__).warning('Remembered Group Chat decision remains pending: %s', type(exc).__name__)

    def approve_room_task(self, room_id, **params):
        from gateway.session_group_decisions import approve_task
        return approve_task(self, room_id, **params)

    def _control_task(self, room_id, member_id, task_id, execution_generation):
        if (type(execution_generation) is not int or execution_generation < 1
                or not isinstance(member_id, str) or not member_id
                or not isinstance(task_id, str) or not task_id):
            raise RuntimeStoreError('invalid_params')
        gateway, epoch = self._owned_authority(room_id)
        task = next((t for t in tasks.list_tasks(self.db_path, room_id=room_id)
                     if t['identity'].task_id == task_id), None)
        if (task is None or task['execution_generation'] != execution_generation
                or (task['payload'].get('target_member_id') or task['payload'].get('target_profile')) != member_id):
            raise RuntimeStoreError('stale_generation')
        if not any(m.get('member_id') == member_id
                   and m.get('profile') == task['payload']['target_profile']
                   for m in self._room(room_id)['members']):
            raise RuntimeStoreError('permission_denied')
        return task, HostedRoomBinding(room_id, gateway, epoch)

    def discard_room_task(self, room_id, *, member_id, task_id, execution_generation):
        with self._policy_lock:
            task, binding = self._control_task(room_id, member_id, task_id, execution_generation)
            if self._member_is_peer(room_id, member_id):
                raise RuntimeStoreError('unsupported_operation')
            cancel_id = f'discard:{execution_generation}'
            if task['status'] == 'cancelled' and task.get('cancel_id') == cancel_id:
                return task
            if task['status'] != 'indeterminate':
                raise RuntimeStoreError('stale_generation')
            rpc = self._resolve_member_transport(binding, task)
            # Canonical receipt commits first. If the driver write fails, exact
            # RPC replay must recover it rather than invent another execution.
            rpc.discard(profile=task['payload']['target_profile'], source='bot_room',
                        session_id=rpc.ref.session_id, expected_task_id=task_id,
                        execution_generation=execution_generation)
            lease = self.runtime._ensure_lease(binding)
            result = self.runtime._fenced(tasks.resolve_indeterminate_cancellation,
                binding, task, lease, cancel_id=cancel_id)
            self.runtime._set_blocked(room_id, False)
            self.runtime.wakeup()
            return result

    def retry_room_task(self, room_id, *, member_id, task_id, execution_generation):
        with self._policy_lock:
            task, binding = self._control_task(room_id, member_id, task_id, execution_generation)
            self._require_work_open(room_id)
            peer = self._member_is_peer(room_id, member_id)
            # Unknown is not non-admission. Never advance its hosted generation
            # while leaving the canonical unknown head behind it.
            if task['status'] == 'indeterminate':
                raise RuntimeStoreError('unknown_execution')
            if task['status'] not in ({'deferred', 'settled'} if peer else {'deferred'}):
                raise RuntimeStoreError('stale_generation')
            rpc = self._resolve_member_transport(binding, task)
            coords = {'profile': task['payload']['target_profile'], 'source': 'bot_room'}
            if peer:
                session = rpc.resolve_exact(**coords, title=f'Group: {room_id}')
                if session is None:
                    raise RuntimeStoreError('unknown_execution')
                info = rpc.info(**coords, session_id=session['session_id'], fresh=True)
            else:
                info = rpc.info(**coords, session_id=rpc.ref.session_id)
            if info.get('status') == 'unknown':
                raise RuntimeStoreError('unknown_execution')
            if info.get('active'):
                raise RuntimeStoreError('session_busy')
            if peer:
                if info.get('status') in {'failed', 'cancelled'}:
                    raise RuntimeStoreError('unsupported_operation')
                generation = info.get('canonical_execution_generation')
                known_generation = type(generation) is int and generation > 0
                if (info.get('task_id') != task_id or info.get('execution_generation') != execution_generation
                        or info.get('active') is not False or not info.get('run_id')
                        or 'canonical_execution_generation' not in info
                        or info.get('status') != 'completed' or not known_generation):
                    raise RuntimeStoreError('unknown_execution')
                terminal = self.runtime._terminal_from_history(
                    rpc, coords['profile'], session['session_id'], task)
                if (terminal is None or terminal.status != 'settled'
                        or terminal.settlement_id != 'peer-run:' + info['run_id']):
                    raise RuntimeStoreError('unknown_execution')
                lease = self.runtime._ensure_lease(binding)
                # Completion is receipt reconciliation, not permission to rerun.
                result = self.runtime._fenced(tasks.resolve_deferred_completion, binding, task, lease,
                    settlement_id=terminal.settlement_id, result=terminal.result)
                self.runtime._set_blocked(room_id, False)
                self.runtime.wakeup()
                return result
            lease = self.runtime._ensure_lease(binding)
            return self.runtime._requeue(tasks.requeue_deferred_task, task, lease, room_id)

    def status(self, room_id=None):
        result = super().status(room_id)
        if room_id is None:
            return result
        actions = [a for a in result['pending_actions'] if a['kind'] != 'retry']
        for task in tasks.list_tasks(self.db_path, room_id=room_id):
            if task['status'] not in {'indeterminate', 'deferred'}:
                continue
            member = task['payload'].get('target_member_id') or task['payload']['target_profile']
            if self._member_is_peer(room_id, member) and task['status'] == 'indeterminate':
                continue
            actions.append({'kind': 'discard' if task['status'] == 'indeterminate' else 'retry',
                            'member_id': member, 'task_id': task['identity'].task_id,
                            'execution_generation': task['execution_generation']})
        return {**result, 'pending_actions': actions}

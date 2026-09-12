"""Remember exact operations in the canonical room, adapted from #98073's rules.

The existing runtime owns pending prompts. These records grant only repeated
one-time decisions for the same owner, member, target and operation identity.
"""
import hashlib
import json
import time
from dataclasses import dataclass

from gateway.session_hosted_service import _OWNER
from hermes_state_runtime import RuntimeStoreError, _epoch
from tools.approval_operation import valid_operation_context, valid_operation_key

MAX_RULES_PER_MEMBER = 32
MAX_RULES_TOTAL = 1024
AUTO_PREFIX = 'approval-rule:'


def operation_metadata(prompt):
    if (prompt.get('allow_permanent') is not True or prompt.get('allow_session') is not True
            or prompt.get('smart_denied', False) is not False or 'edit' in prompt
            or not valid_operation_key(prompt.get('remember_key'))
            or not valid_operation_context(prompt.get('remember_context'))):
        return {}
    return {key: prompt[key] for key in ('remember_key', 'remember_context')}


def _ensure(conn):
    if not conn.in_transaction:
        raise RuntimeStoreError('invalid_params')
    conn.execute('''CREATE TABLE IF NOT EXISTS canonical_group_approval_rules (
        rule_id TEXT PRIMARY KEY, room_id TEXT NOT NULL, owner_subject TEXT NOT NULL,
        member_id TEXT NOT NULL, scope_json TEXT NOT NULL, operation_key TEXT NOT NULL,
        command_text TEXT NOT NULL, context_text TEXT NOT NULL,
        state TEXT NOT NULL CHECK(state IN ('pending','active','revoked')),
        generation INTEGER NOT NULL, grant_command_id TEXT NOT NULL,
        created_at REAL NOT NULL, updated_at REAL NOT NULL)''')


def _scope(service, conn, owner, pending, *, require_task=True):
    _epoch(conn, service.authority.epoch)
    if not valid_operation_key(pending.get('remember_key')) or not valid_operation_context(pending.get('remember_context')):
        raise RuntimeStoreError('invalid_params')
    subject = conn.execute('SELECT value FROM state_meta WHERE key=?', (_OWNER + pending['room_id'],)).fetchone()
    room = conn.execute('SELECT * FROM hosted_rooms WHERE room_id=?', (pending['room_id'],)).fetchone()
    if (subject is None or subject[0] != owner or room is None or room['disbanded_at'] is not None
            or room['authority_gateway_id'] != pending['authority_gateway_id']
            or room['authority_epoch'] != pending['authority_epoch']):
        raise RuntimeStoreError('permission_denied')
    member = next((item for item in json.loads(room['members_json']) if item['member_id'] == pending['member_id']), None)
    if member is None or member['profile'] != pending['profile']:
        raise RuntimeStoreError('permission_denied')
    target = member.get('target') or {}
    target_digest = hashlib.sha256(json.dumps({'profile': member['profile'],
        'target': {key: target.get(key) for key in ('kind', 'installation_id', 'peer_id', 'profile', 'capability_digest')}},
        sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    if require_task:
        task = conn.execute('SELECT * FROM hosted_room_driver_tasks WHERE room_id=? AND task_id=?',
                             (pending['room_id'], pending['task_id'])).fetchone()
        payload = json.loads(task['payload_json']) if task else {}
        lease = conn.execute('SELECT * FROM hosted_room_driver_leases WHERE room_id=?', (pending['room_id'],)).fetchone()
        if (task is None or task['status'] != 'running' or task['cancel_id'] is not None
                or task['execution_generation'] != pending['execution_generation']
                or payload.get('target_member_id', payload.get('target_profile')) != pending['member_id']
                or payload.get('target_profile') != pending['profile']
                or lease is None or lease['released_at'] is not None or lease['expires_at'] <= time.time()
                or lease['process_generation'] != service.runtime.process_generation
                or lease['gateway_id'] != pending['authority_gateway_id'] or lease['authority_epoch'] != pending['authority_epoch']):
            raise RuntimeStoreError('stale_generation')
    scope = {key: pending[key] for key in ('room_id', 'authority_gateway_id', 'authority_epoch', 'member_id', 'profile')}
    scope.update(owner_subject=owner, target_digest=target_digest, operation_key=pending['remember_key'])
    encoded = json.dumps(scope, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(encoded.encode()).hexdigest(), encoded


def stage(service, conn, owner, pending, command_id):
    rule_id, encoded = _scope(service, conn, owner, pending)
    _ensure(conn)
    now = time.time()
    conn.execute("""UPDATE canonical_group_approval_rules SET state='revoked',generation=generation+1,updated_at=?
        WHERE state!='revoked' AND NOT EXISTS (
            SELECT 1 FROM hosted_rooms r JOIN state_meta o ON o.key=? || r.room_id
            WHERE r.room_id=canonical_group_approval_rules.room_id AND r.disbanded_at IS NULL
              AND r.authority_gateway_id=json_extract(canonical_group_approval_rules.scope_json, '$.authority_gateway_id')
              AND r.authority_epoch=json_extract(canonical_group_approval_rules.scope_json, '$.authority_epoch')
              AND o.value=canonical_group_approval_rules.owner_subject)""", (now, _OWNER))
    conn.execute("DELETE FROM canonical_group_approval_rules WHERE state='revoked' AND updated_at<?", (now - 7 * 86400,))
    old = conn.execute('SELECT * FROM canonical_group_approval_rules WHERE rule_id=?', (rule_id,)).fetchone()
    if old is not None:
        if old['state'] == 'revoked' and old['grant_command_id'] == command_id:
            raise RuntimeStoreError('permission_denied')
        if old['state'] == 'active' or old['grant_command_id'] == command_id:
            return dict(old)
    if conn.execute("SELECT COUNT(*) FROM canonical_group_approval_rules WHERE state!='revoked'").fetchone()[0] >= MAX_RULES_TOTAL:
        raise RuntimeStoreError('storage_unavailable')
    if conn.execute("SELECT COUNT(*) FROM canonical_group_approval_rules WHERE room_id=? AND member_id=? AND state!='revoked'",
                    (pending['room_id'], pending['member_id'])).fetchone()[0] >= MAX_RULES_PER_MEMBER:
        raise RuntimeStoreError('storage_unavailable')
    generation = old['generation'] + 1 if old else 1
    conn.execute('''INSERT INTO canonical_group_approval_rules VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(rule_id) DO UPDATE SET state='pending', generation=excluded.generation,
        grant_command_id=excluded.grant_command_id, command_text=excluded.command_text,
        context_text=excluded.context_text, updated_at=excluded.updated_at''',
        (rule_id, pending['room_id'], owner, pending['member_id'], encoded, pending['remember_key'],
         pending['command'], pending['remember_context'], 'pending', generation, command_id, now, now))
    return dict(conn.execute('SELECT * FROM canonical_group_approval_rules WHERE rule_id=?', (rule_id,)).fetchone())


def activate(service, conn, owner, pending, rule, command_id):
    rule_id, encoded = _scope(service, conn, owner, pending, require_task=False)
    if (rule_id, encoded) != (rule['rule_id'], rule['scope_json']):
        raise RuntimeStoreError('permission_denied')
    conn.execute("UPDATE canonical_group_approval_rules SET state='active', updated_at=? WHERE rule_id=? "
                 "AND generation=? AND grant_command_id=? AND state='pending'",
                 (time.time(), rule_id, rule['generation'], command_id))
    current = conn.execute('SELECT state,generation FROM canonical_group_approval_rules WHERE rule_id=?', (rule_id,)).fetchone()
    return current is not None and current['state'] == 'active' and current['generation'] == rule['generation']


def matching(service, conn, owner, pending):
    if conn.execute("SELECT 1 FROM sqlite_master WHERE name='canonical_group_approval_rules'").fetchone() is None:
        return None
    rule_id, encoded = _scope(service, conn, owner, pending)
    row = conn.execute("SELECT * FROM canonical_group_approval_rules WHERE rule_id=? AND state='active'", (rule_id,)).fetchone()
    return dict(row) if row is not None and row['scope_json'] == encoded else None


def list_rules(service, room_id):
    owner = service._owner(room_id)
    service._owned_authority(room_id)
    with service.authority.db._read_ctx() as conn:
        if conn.execute("SELECT 1 FROM sqlite_master WHERE name='canonical_group_approval_rules'").fetchone() is None:
            return []
        result = []
        for row in conn.execute("SELECT * FROM canonical_group_approval_rules WHERE room_id=? AND state!='revoked' ORDER BY created_at LIMIT ?",
                                (room_id, MAX_RULES_TOTAL)):
            scope = json.loads(row['scope_json'])
            pending = {**scope, 'remember_key': row['operation_key'], 'remember_context': row['context_text']}
            try:
                if _scope(service, conn, owner, pending, require_task=False) != (row['rule_id'], row['scope_json']):
                    continue
            except RuntimeStoreError:
                continue
            result.append({key: row[key] for key in ('rule_id', 'member_id', 'command_text', 'context_text', 'generation', 'state')})
        return result


def revoke(service, proof, rule_id, generation):
    if not valid_operation_key(rule_id) or type(generation) is not int or generation < 1:
        raise RuntimeStoreError('invalid_params')
    def write(conn):
        proof.check(conn)
        _ensure(conn)
        return conn.execute("UPDATE canonical_group_approval_rules SET state='revoked',generation=generation+1,updated_at=? "
            "WHERE rule_id=? AND room_id=? AND owner_subject=? AND generation=? AND state!='revoked'",
            (time.time(), rule_id, proof.room_id, proof.owner, generation)).rowcount
    return service.authority.db._execute_write(write)


@dataclass
class _RulePermission:
    authority: object
    room_id: str
    owner: str
    gateway_id: str
    room_epoch: int
    runtime_epoch: int
    pending: dict
    rule: dict
    member_id: None = None

    def check(self, conn):
        _epoch(conn, self.runtime_epoch)
        service = self.authority.hosted_room_service
        current = matching(service, conn, self.owner, self.pending)
        if current is None or (current['rule_id'], current['generation']) != (self.rule['rule_id'], self.rule['generation']):
            raise RuntimeStoreError('permission_denied')
        return True


def apply_remembered(service, room_id, member_id):
    from gateway.session_group_decisions import pending_decisions, _apply
    owner = service._owner(room_id)
    for pending in pending_decisions(service, room_id):
        if pending['member_id'] != member_id or not pending.get('remember_key'):
            continue
        with service.authority.db._read_ctx() as conn:
            rule = matching(service, conn, owner, pending)
        if rule is None:
            continue
        proof = _RulePermission(service.authority, room_id, owner, pending['authority_gateway_id'],
            pending['authority_epoch'], service.authority.epoch, pending, rule)
        decision = {key: pending[key] for key in ('member_id', 'task_id', 'execution_generation', 'request_id')} | {'choice': 'once'}
        command_id = AUTO_PREFIX + hashlib.sha256(json.dumps([rule['rule_id'], rule['generation'], decision], sort_keys=True).encode()).hexdigest()
        _apply(proof, command_id, decision, pending=pending, remembered_rule=rule)

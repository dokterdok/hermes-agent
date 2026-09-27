"""Output-owned source evidence and served-recipient private transfer boundary."""
import base64
import hashlib
import json
from pathlib import Path

from gateway.hosted_room_artifacts import RoomArtifactError
from gateway.session_hosted_output_retry import digest
from hermes_state_runtime import RuntimeStoreError

_CHUNK = 24 * 1024  # private request is capped at 64 KiB


def source_secondary_attestation(service, selector, operation, params):
    """Rebuild a publication from the source owner's live task and canonical event."""
    from gateway.session_hosted_output_secondary import (
        _registration, _completion, _stored, _same, _require_owner_provenance,
        _require_recorded_lifetime, _expired, _events,
    )
    expected = {'publication_id', 'task_id', 'execution_generation', '_target_home'}
    if operation == 'secondary_chunk':
        expected |= {'index', 'offset'}
    if (set(params) != expected or type(params['task_id']) is not str
            or type(params['publication_id']) is not str
            or type(params['execution_generation']) is not int):
        raise RuntimeStoreError('invalid_params')
    key = selector['room_id'], params['task_id'], params['execution_generation']
    with service._output_policy_read():
        pass
    with service.authority.db.live_read_connection() as conn:
        if conn is None:
            raise RuntimeStoreError('runtime_draining')
        service.authority.db._raise_if_db_corrupt()
        service.authority.db._raise_if_db_replaced()
        service._output_owner(conn)
        row = _registration(conn, key[0], params['publication_id'])
        done = _completion(conn, key[0], params['publication_id'])
        if row is None and done is None:
            raise RuntimeStoreError('permission_denied')
        if row is not None and (row['operation'] != 'publish' or row['blocked']
                or row['reason_code'] != 'pending'):
            raise RuntimeStoreError('permission_denied')
        record = row if row is not None else done
        attempt = int(row['attempts'] if row is not None else done['attempt'])
        if attempt < 1:
            raise RuntimeStoreError('permission_denied')
        metadata = service._output_metadata(conn, key)
        stored = _stored(record)
        _require_owner_provenance(service, stored)
        _require_recorded_lifetime(record, stored)
        if (record['task_id'] != key[1] or record['execution_generation'] != key[2]
                or not _same(stored, metadata) or _expired(stored, metadata, service._artifact_clock())
                or stored['publication'] != _events(service, conn, key)
                or stored['member_id'] != selector['member_id']):
            raise RuntimeStoreError('permission_denied')
        task = conn.execute('SELECT result_json FROM hosted_room_driver_tasks WHERE room_id=? AND task_id=?', key[:2]).fetchone()
        result = json.loads(task['result_json'])
        scope = result['artifact_scope']
        if (scope['target_profile'] != selector['profile']
                or result.get('owner_output_receipt') is None
                or service._publication_operation(conn, key) != 'ack'):
            raise RuntimeStoreError('permission_denied')
        stem = key[1].removeprefix('dtask:')
        event_id = 'dmessage:' + stem
        event = conn.execute('SELECT kind,payload_json FROM hosted_room_events WHERE room_id=? AND event_id=?',
                             (key[0], event_id)).fetchone()
        if event is None or event['kind'] != 'message.member':
            raise RuntimeStoreError('permission_denied')
        attachments = json.loads(event['payload_json']).get('attachments', [])
        if not attachments or len(attachments) > 8:
            raise RuntimeStoreError('permission_denied')
        manifests = []
        for item in attachments:
            saved = service.attachments.describe(room_id=key[0], event_id=event_id,
                attachment_id=item['attachment_id'], recipient_member_id=selector['member_id'])
            if any(saved[field] != item[field] for field in ('attachment_id','kind','name','mime','size')):
                raise RuntimeStoreError('permission_denied')
            manifests.append({field: saved[field] for field in ('attachment_id','kind','name','mime','size','sha256')})
        identity = dict(source_home=service.authority.profile_id, owner=service._owner(key[0]),
            target_home=params['_target_home'], room_id=key[0], task_id=key[1],
            execution_generation=key[2], publication_id=params['publication_id'],
            member_id=selector['member_id'], profile=selector['profile'],
            owner_epoch=stored['owner_epoch'], owner_instance=stored['owner_instance'],
            work=stored['work'], route=stored['route'], lineage=stored['lineage'],
            event_digest=stored['publication'], event_id=event_id,
            valid_until=float(record['valid_until']))
        if operation == 'secondary_chunk':
            index, offset = params.get('index'), params.get('offset')
            if (type(index) is not int or not 0 <= index < len(manifests)
                    or type(offset) is not int or not 0 <= offset < manifests[index]['size']):
                raise RuntimeStoreError('invalid_params')
    if operation == 'secondary_chunk':
        saved = service.attachments.read_range(room_id=key[0], event_id=event_id,
            attachment_id=manifests[index]['attachment_id'],
            recipient_member_id=selector['member_id'], offset=offset, length=_CHUNK)
        # Do not reuse the pre-I/O SQLite snapshot. Enter the source's live
        # owner/member/route policy after the real Files byte read completes.
        current = service.attest(selector, 'secondary_receipt',
            {name: value for name, value in params.items() if name not in {'index', 'offset'}})
        if current['identity'] != identity or current['manifests'] != manifests or current['attempt'] != attempt:
            raise RuntimeStoreError('permission_denied')
        return {'identity': identity, 'manifest': manifests[index],
                'data_base64': base64.b64encode(saved.data).decode('ascii')}
    return {'identity': identity, 'manifests': manifests, 'attempt': attempt}


def target_secondary_operation(authority, binding, operation, params, attested):
    from gateway.hosted_room_recipient_files import retain_recipient_bytes, read_recipient_bytes
    identity, manifests = attested['identity'], attested['manifests']
    if (identity['source_home'] != binding['source_home'] or identity['owner'] != binding['owner']
            or identity['target_home'] != authority.profile_id
            or identity['member_id'] != binding['selector']['member_id']
            or identity['profile'] != binding['selector']['profile']):
        raise RuntimeStoreError('permission_denied')
    receipts = []
    for index, item in enumerate(manifests):
        if operation == 'secondary_deliver':
            data = bytearray()
            while len(data) < item['size']:
                part = _attest_chunk(binding, params, index, len(data))
                raw = base64.b64decode(part['data_base64'], validate=True)
                if (part['identity'] != identity or part['manifest'] != item
                        or len(raw) != min(_CHUNK, item['size'] - len(data))):
                    raise RuntimeStoreError('permission_denied')
                data.extend(raw)
            receipts.append(retain_recipient_bytes(authority, identity={**identity, 'index': index},
                                                  manifest=item, data=bytes(data), attempt=attested['attempt']))
        else:
            receipts.append(read_recipient_bytes(authority, identity={**identity, 'index': index},
                                                manifest=item, attempt=attested['attempt']))
    return {'identity': identity, 'receipts': receipts, 'receipt_digest': digest(receipts)}


def _attest_chunk(binding, params, index, offset):
    from gateway.session_hosted_transport import _attest
    return _attest(binding, 'secondary_chunk', {**params, 'index': index, 'offset': offset})


def read_authenticated_recipient(service, task, publication_id):
    """Query current custody at the served target, not a caller-supplied token."""
    from gateway.session_hosted_transport import HostedRoomOwnerRPC
    scope = task['result']['artifact_scope']
    home = service.profile_homes().get(scope['target_profile'])
    if home is None or Path(home) == Path(service.authority.profile_id):
        raise RoomArtifactError('Group Chat secondary recipient is not served')
    rpc = HostedRoomOwnerRPC(home=home, source_home=service.authority.profile_id,
        room_id=scope['room_id'], member_id=scope['member_id'], profile=scope['target_profile'])
    params = dict(publication_id=publication_id, task_id=task['identity'].task_id,
                  execution_generation=task['execution_generation'])
    return rpc._call('secondary_receipt', **params)


def complete_after_recipient_receipt(service, task, publication_id, *, attempt, receipt=None):
    """Output re-reads target custody before committing; this hint is not authority."""
    return service.complete_secondary_publication(task, publication_id, attempt=attempt,
                                                   recipient_receipt=receipt)

"""Read/ACK the exact peer Run through the room's retained, authenticated route."""
from dataclasses import dataclass
import json

from gateway.hosted_room_artifacts import RoomArtifactError
from gateway.hosted_room_output_fence import require_output_task, require_peer_output_receipt
from tui_gateway.hosted_room_peer_artifacts import read_artifact, acknowledge_artifacts
from tui_gateway.hosted_room_peer_http import PeerRunsHTTPClient

_ACK_PREFIX = 'gateway.hosted.peer-output.ack.v1:'


@dataclass
class PeerOutputCustody:
    service: object
    scope: object
    manifest: dict
    result: dict
    cancel_generation: int

    def _local_receipt(self, conn, scope):
        from gateway.session_peer_output import root_output_authority
        from hermes_state_runtime import _epoch
        if scope != self.scope or not root_output_authority(self.service.authority) or scope.target_profile != 'default':
            raise RoomArtifactError('Group Chat output route is unsupported')
        _epoch(conn, self.service.authority.epoch)
        task = require_output_task(conn, scope, self.cancel_generation)
        actual = json.loads(task['result_json'])
        if actual != self.result or actual.get('artifacts') != self.manifest:
            raise RoomArtifactError('Group Chat output result changed')
        return require_peer_output_receipt(conn, scope, self.result)

    def _completed_record(self):
        return {'scope': self.scope.as_mapping(), 'run_id': self.result['peer_run_id'],
                'manifest': self.manifest, 'cancel_generation': self.cancel_generation,
                'message_event_id': 'dmessage:' + self.scope.task_id.removeprefix('dtask:')}

    def _route(self, scope):
        from gateway import hosted_room_links
        with self.service.authority.db._read_ctx() as conn:
            receipt = self._local_receipt(conn, scope)
        link = hosted_room_links.load_room_link(self.service.db_path, room_id=scope.room_id, member_id=scope.member_id)
        if (link is None or link.target_profile != scope.target_profile
                or link.catalog.installation_id != scope.target_install_id):
            raise RoomArtifactError('Group Chat output peer route changed')
        client = PeerRunsHTTPClient(base_url=link.target_url, api_key='', target_profile=link.target_profile)
        return client, link.grant, receipt['run_id']

    def read(self, scope, artifact_id):
        metadata = next((item for item in self.manifest['items'] if item['artifact_id'] == artifact_id), None)
        if metadata is None:
            raise RoomArtifactError('Group Chat output file is unavailable')
        client, grant, run_id = self._route(scope)
        data = read_artifact(client, run_id=run_id, artifact_id=artifact_id, grant=grant)
        return metadata, data

    def retirement_complete(self, scope):
        # Caller has already reverified its canonical publication and exact bytes.
        with self.service.authority.db._read_ctx() as conn:
            self._local_receipt(conn, scope)
            row = conn.execute('SELECT value FROM state_meta WHERE key=?', (_ACK_PREFIX + scope.key,)).fetchone()
            if row is None:
                return False
            if json.loads(row[0]) != self._completed_record():
                raise RoomArtifactError('Group Chat output acknowledgment changed')
            return True

    def acknowledge(self, scope, artifact_ids, *, message_event_id):
        client, grant, run_id = self._route(scope)
        result = acknowledge_artifacts(client, run_id=run_id, artifact_ids=artifact_ids,
            manifest_digest=self.manifest['manifest_digest'], message_event_id=message_event_id, grant=grant)
        if (result.get('acknowledged') is not True or type(result.get('changed')) is not int
                or not 0 <= result['changed'] <= len(artifact_ids)
                or message_event_id != self._completed_record()['message_event_id']):
            raise RoomArtifactError('Group Chat output acknowledgment was not confirmed')
        encoded = json.dumps(self._completed_record(), sort_keys=True, separators=(',', ':'))
        def remember(conn):
            self._local_receipt(conn, scope)
            row = conn.execute('SELECT value FROM state_meta WHERE key=?', (_ACK_PREFIX + scope.key,)).fetchone()
            if row is not None and row[0] != encoded:
                raise RoomArtifactError('Group Chat output acknowledgment changed')
            conn.execute('INSERT OR IGNORE INTO state_meta(key,value) VALUES(?,?)', (_ACK_PREFIX + scope.key, encoded))
        self.service.authority.db._execute_write(remember)
        return result

    def discard_durably(self, scope):
        # No remote discard capability: leave unclaimed bytes to the peer's quota/TTL.
        return 0

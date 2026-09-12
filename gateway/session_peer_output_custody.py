"""Read/ACK the exact peer Run through the room's retained, authenticated route."""
from dataclasses import dataclass

from gateway.hosted_room_artifacts import RoomArtifactError
from gateway.hosted_room_output_fence import require_output_task, require_peer_output_receipt
from tui_gateway.hosted_room_peer_artifacts import read_artifact, acknowledge_artifacts
from tui_gateway.hosted_room_peer_http import PeerRunsHTTPClient


@dataclass
class PeerOutputCustody:
    service: object
    scope: object
    manifest: dict
    result: dict
    cancel_generation: int

    def _route(self, scope):
        from gateway import hosted_room_links
        from gateway.session_peer_output import root_output_authority
        if scope != self.scope or not root_output_authority(self.service.authority) or scope.target_profile != 'default':
            raise RoomArtifactError('Group Chat output route is unsupported')
        with self.service.authority.db._read_ctx() as conn:
            require_output_task(conn, scope, self.cancel_generation)
            receipt = require_peer_output_receipt(conn, scope, self.result)
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
        # The peer owns this fact. Repeat its idempotent ACK only after home proof.
        return False

    def acknowledge(self, scope, artifact_ids, *, message_event_id):
        client, grant, run_id = self._route(scope)
        return acknowledge_artifacts(client, run_id=run_id, artifact_ids=artifact_ids,
            manifest_digest=self.manifest['manifest_digest'], message_event_id=message_event_id, grant=grant)

    def discard_durably(self, scope):
        # No remote discard capability: leave unclaimed bytes to the peer's quota/TTL.
        return 0

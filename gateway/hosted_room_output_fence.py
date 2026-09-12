"""Final Files write/publication checks inside the owning SQLite transaction."""

import json

from gateway.hosted_room_artifacts import RoomArtifactError, RoomArtifactScope


def require_output_task(conn, scope: RoomArtifactScope, cancel_generation, *, status="settled"):
    if type(cancel_generation) is not int or cancel_generation < 0:
        raise RoomArtifactError("Group Chat output cancellation generation is invalid")
    room = conn.execute(
        "SELECT authority_gateway_id, authority_epoch, disbanded_at, members_json FROM hosted_rooms WHERE room_id=?",
        (scope.room_id,),
    ).fetchone()
    if (room is None or room["disbanded_at"] is not None
            or room["authority_gateway_id"] != scope.authority_gateway_id
            or room["authority_epoch"] != scope.authority_epoch):
        raise RoomArtifactError("Group Chat output authority changed")
    if not any(m["member_id"] == scope.member_id and m["profile"] == scope.target_profile
               and m.get("target", {}).get("kind", "local") == "local" for m in json.loads(room["members_json"])):
        raise RoomArtifactError("Group Chat output participant changed")
    from gateway.hosted_room_attachments import HostedRoomAttachmentStore
    HostedRoomAttachmentStore._require_viewer_room(conn, room_id=scope.room_id,
        authority_gateway_id=scope.authority_gateway_id, authority_epoch=scope.authority_epoch)
    task = conn.execute("SELECT * FROM hosted_room_driver_tasks WHERE room_id=? AND task_id=?",
                        (scope.room_id, scope.task_id)).fetchone()
    payload = json.loads(task["payload_json"]) if task else {}
    if (task is None or task["status"] != status or task["execution_generation"] != scope.execution_generation
            or task["cancel_generation"] != cancel_generation
            or payload.get("target_member_id", payload.get("target_profile")) != scope.member_id
            or payload.get("target_profile") != scope.target_profile):
        raise RoomArtifactError("Group Chat output attempt changed")
    return task


def require_output_publication(conn, room_id, expected, *, kind, actor, payload):
    scope = RoomArtifactScope.from_mapping(expected["scope"])
    if scope.room_id != room_id:
        raise RoomArtifactError("Group Chat output room changed")
    task = require_output_task(conn, scope, expected["cancel_generation"])
    if (payload.get("task_id") != scope.task_id or payload.get("member_id") != scope.member_id
            or payload.get("thread_id") != task["thread_id"] or payload.get("turn_id") != task["turn_id"]):
        raise RoomArtifactError("Group Chat output event coordinates changed")
    result = json.loads(task["result_json"])
    if result.get("artifact_scope") != scope.as_mapping() or result.get("artifacts") != expected["manifest"]:
        raise RoomArtifactError("Group Chat output receipt changed")
    if kind == "message.member":
        if actor.get("id") != scope.member_id or actor.get("profile") != scope.target_profile:
            raise RoomArtifactError("Group Chat output event author changed")
        from tui_gateway.hosted_room_artifact_service import _upload_id
        items = expected["manifest"]["items"]
        attachments = payload.get("attachments", [])
        if len(attachments) != len(items):
            raise RoomArtifactError("Group Chat output event manifest changed")
        for item, attachment in zip(items, attachments):
            row = conn.execute("SELECT upload_id FROM hosted_room_attachments WHERE room_id=? AND attachment_id=?",
                               (room_id, attachment["attachment_id"])).fetchone()
            if row is None or row[0] != _upload_id(scope, item):
                raise RoomArtifactError("Group Chat output event source changed")

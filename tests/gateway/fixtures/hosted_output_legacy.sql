-- Persisted Bot-output schema before durable cleanup and exact ACK receipts.
CREATE TABLE hosted_room_output_artifacts (
    artifact_id TEXT PRIMARY KEY,
    scope_key TEXT NOT NULL,
    scope_json TEXT NOT NULL,
    name TEXT NOT NULL,
    kind TEXT NOT NULL,
    mime TEXT NOT NULL,
    size INTEGER NOT NULL,
    sha256 TEXT NOT NULL,
    blob_name TEXT NOT NULL UNIQUE,
    created_at REAL NOT NULL,
    acknowledged_at REAL,
    UNIQUE(scope_key, sha256, name)
);
CREATE TABLE hosted_room_output_generation_fences (
    lineage_identity TEXT PRIMARY KEY,
    lineage_json TEXT NOT NULL,
    max_generation INTEGER NOT NULL,
    retired_generation INTEGER NOT NULL DEFAULT 0,
    updated_at REAL NOT NULL
);
-- Older upgrade attempts could install this trigger without adding its column.
CREATE TRIGGER hosted_room_output_generation_track_terminal
AFTER UPDATE OF acknowledged_at, cleanup_required_at ON hosted_room_output_artifacts
WHEN NEW.acknowledged_at IS NOT NULL OR NEW.cleanup_required_at IS NOT NULL
BEGIN
    UPDATE hosted_room_output_generation_fences
       SET max_generation = MAX(max_generation,
               CAST(json_extract(NEW.scope_json, '$.execution_generation') AS INTEGER)),
           retired_generation = MAX(retired_generation,
               CAST(json_extract(NEW.scope_json, '$.execution_generation') AS INTEGER)),
           updated_at = CAST(strftime('%s', 'now') AS REAL)
     WHERE lineage_identity = json_object(
         'authority_epoch', json_extract(NEW.scope_json, '$.authority_epoch'),
         'authority_gateway_id', json_extract(NEW.scope_json, '$.authority_gateway_id'),
         'home_install_id', json_extract(NEW.scope_json, '$.home_install_id'),
         'member_id', json_extract(NEW.scope_json, '$.member_id'),
         'room_id', json_extract(NEW.scope_json, '$.room_id'),
         'target_install_id', json_extract(NEW.scope_json, '$.target_install_id'),
         'target_profile', json_extract(NEW.scope_json, '$.target_profile'),
         'task_id', json_extract(NEW.scope_json, '$.task_id'));
END;

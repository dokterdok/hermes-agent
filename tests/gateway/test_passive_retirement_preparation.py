"""Pure retirement wire/scope checks; no retirement, Stop, or network operation."""

import copy
import sqlite3
from contextlib import closing

import pytest

from gateway import hosted_room_passive_lineage as lineage
from gateway import hosted_room_passive_protocol as protocol
from gateway import hosted_room_replica_retirement as retirement


def enrollment():
    history = [{"gateway_id": "owner-a", "epoch": 1, "from_seq": 0},
               {"gateway_id": "owner-b", "epoch": 2, "from_seq": 3}]
    _, encoded, digest = lineage.descriptor(history, gateway_id="owner-b", epoch=2)
    row = dict(enrollment_id="enrollment", room_id="room", authority_gateway_id="owner-b",
        authority_epoch=2, target_install_id="target", roster_sha256="a" * 64,
        version=2, lineage_sha256=digest, nonce="fixture-nonce")
    closing_value = retirement._closing_value(b"fixture-key" * 4, row)
    row["commitment"] = retirement._commitment(closing_value, row)
    return row, encoded, closing_value


@pytest.mark.parametrize("field,value", [
    ("room_id", "other"), ("authority_gateway_id", "other"), ("authority_epoch", 3),
    ("target_install_id", "other"), ("enrollment_id", "other"),
    ("roster_sha256", "b" * 64), ("lineage_sha256", "b" * 64), ("version", None),
])
def test_v2_retirement_commitment_binds_every_scope_coordinate(field, value):
    row, _, closing_value = enrollment()
    public = retirement._public(row)
    assert retirement._validate_enrollment(public, "target") == public
    changed = {**row, field: value}
    assert retirement._commitment(closing_value, changed) != row["commitment"]
    assert retirement._closing_value(b"fixture-key" * 4, changed) != closing_value
    # A retirement format never carries an execution permission.
    assert "execution_authorized" not in public


@pytest.mark.parametrize("state,version,digest,expected", [
    ("active", 2, None, True), ("revoked", 2, None, False),
    ("active", None, None, False), ("active", 2, "0" * 64, False),
])
def test_copy_scope_and_verified_prefix_are_distinct(tmp_path, state, version, digest, expected):
    row, encoded, _ = enrollment()
    members = "[]"
    import hashlib
    row["roster_sha256"] = hashlib.sha256(members.encode("utf-8")).hexdigest()
    with closing(sqlite3.connect(tmp_path / "target.db")) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute(f"""CREATE TABLE {lineage.ENROLLMENTS} (
            room_id TEXT, is_current INTEGER, state TEXT, authority_gateway_id TEXT,
            authority_epoch INTEGER, roster_sha256 TEXT, version INTEGER,
            lineage_sha256 TEXT, authority_history_json TEXT)""")
        conn.execute(f"INSERT INTO {lineage.ENROLLMENTS} VALUES ('room',1,?,'owner-b',2,?,2,?,?)",
                     (state, row["roster_sha256"], row["lineage_sha256"], encoded))
        assert retirement.copy_scope_matches_locked(conn, room_id="room", authority_gateway_id="owner-b",
            authority_epoch=2, members_json=members, replica_version=version,
            lineage_sha256=row["lineage_sha256"] if digest is None else digest) is expected
        replica = dict(room_id="room", replica_version=2, lineage_sha256=row["lineage_sha256"],
                       authority_gateway_id="owner-a", authority_epoch=1, last_seq=2, quarantine_reason=None)
        fields = lineage.state_fields_locked(conn, replica)
        assert fields["lineage_status"] == "pending"
        assert fields["source_authority"] == {"gateway_id": "owner-b", "epoch": 2}
        replica["quarantine_reason"] = "invalid_history"
        assert lineage.state_fields_locked(conn, replica) == {}


@pytest.mark.parametrize("field,versions", [
    ("history_versions", [True, 2]), ("retirement_versions", [1, 2.0]),
    ("work_record_versions", [2, 2]), ("history_versions", [1]),
    ("retirement_versions", [3]), ("history_versions", None),
])
def test_capability_negotiation_does_not_silently_downgrade(field, versions):
    proof = {"passive_replication": protocol.passive_capabilities()}
    assert protocol.supports_lineage(proof)
    bad = copy.deepcopy(proof)
    bad["passive_replication"][field] = versions
    assert not protocol.supports_lineage(bad)
    proof["passive_replication"]["work_record_versions"] = [1]
    assert protocol.supports_lineage(proof)  # History v2 need not grant work v2.

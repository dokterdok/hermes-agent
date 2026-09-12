"""Passive retirement formats and scope checks; no enrollment or retirement writer.

Extracted from #104601 for the future canonical consumer. Possessing a valid
commitment or descriptor is neither permission to run nor proof of settlement.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import sqlite3
from dataclasses import dataclass, field
from typing import Any, Mapping

from gateway import hosted_rooms as rooms
from gateway import hosted_room_passive_lineage as lineage
from gateway.hosted_rooms_common import compact_json, table_exists

HOME_TABLE = "hosted_room_replica_retirement_home"
ENROLLMENT_TABLE = "hosted_room_replica_retirement_enrollments"
RETIREMENT_TABLE = "hosted_room_replica_retirements"
_PUBLIC_FIELDS = (
    "enrollment_id",
    "room_id",
    "authority_gateway_id",
    "authority_epoch",
    "target_install_id",
    "roster_sha256",
    "commitment",
)
_SCOPE_FIELDS = _PUBLIC_FIELDS[:-1]
_DOMAIN = b"hermes.group.replica.retirement.v1\0"
_COMMITMENT_DOMAIN = b"hermes.group.replica.retirement.commitment.v1\0"


class RetirementError(rooms.HostedRoomError):
    """Invalid or unavailable copy-retirement operation."""


class RetirementConflictError(RetirementError):
    """An immutable enrollment, room namespace or expected state differs."""


class RetirementAuthorizationError(RetirementError):
    """The closing value does not authorize the exact current enrollment."""


class RetirementCapacityError(RetirementError):
    """Pending cleanup obligations cannot be silently dropped for space."""


class RetirementKeyUnavailable(RetirementError):
    """The original home secret is no longer available."""


@dataclass(frozen=True)
class RetirementNotice:
    enrollment_id: str
    room_id: str
    authority_gateway_id: str
    authority_epoch: int
    target_install_id: str
    endpoint: str
    value: str = field(repr=False)
    version: int | None = None
    lineage_sha256: str | None = None

    def payload(self) -> dict[str, Any]:
        result = {name: getattr(self, name) for name in _SCOPE_FIELDS if name != "roster_sha256"}
        if self.version == 2:
            result.update(version=2, lineage_sha256=self.lineage_sha256)
        return result


def _identifier(value: Any, name: str) -> str:
    return rooms._validate_identifier(value, label=name, max_chars=128)


def _digest(value: Any, name: str) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise RetirementError(f"invalid {name}")
    return value


def roster_digest(members: Any) -> str:
    _, encoded = rooms._validate_members(members)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _public_fields(row):
    return (*_PUBLIC_FIELDS, "version", "lineage_sha256") if lineage.is_v2(row) else _PUBLIC_FIELDS


def _scope_fields(row):
    return (*_SCOPE_FIELDS, "version", "lineage_sha256") if lineage.is_v2(row) else _SCOPE_FIELDS


def _public(row: Mapping[str, Any]) -> dict[str, Any]:
    return {key: row[key] for key in _public_fields(row)}


def _closing_value(secret: bytes, row: Mapping[str, Any]) -> str:
    if not isinstance(secret, bytes) or len(secret) < 32:
        raise RetirementKeyUnavailable("retirement key is unavailable")
    material = {key: row[key] for key in _scope_fields(row)}
    material["nonce"] = row["nonce"]
    raw = hmac.new(
        secret, (_DOMAIN.replace(b".v1\0", b".v2\0") if lineage.is_v2(row) else _DOMAIN)
        + compact_json(material).encode("utf-8"), hashlib.sha256
    ).digest()
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _commitment(value: str, scope: Mapping[str, Any]) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[A-Za-z0-9_-]{43}", value) is None:
        raise RetirementAuthorizationError("invalid retirement capability")
    binding = compact_json({key: scope[key] for key in _scope_fields(scope)}).encode("utf-8")
    domain = _COMMITMENT_DOMAIN.replace(b".v1\0", b".v2\0") if lineage.is_v2(scope) else _COMMITMENT_DOMAIN
    return hashlib.sha256(
        domain + binding + b"\0" + value.encode("ascii")
    ).hexdigest()


def _validate_enrollment(value: Any, target_install_id: str) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != set(_public_fields(value)):
        raise RetirementError("invalid retirement enrollment fields")
    value = dict(value)
    for key in (
        "enrollment_id",
        "room_id",
        "authority_gateway_id",
        "target_install_id",
    ):
        validated = _identifier(value[key], key)
        if lineage.is_v2(value) and validated != value[key]:
            raise RetirementError("v2 enrollment identifiers must be canonical")
        value[key] = validated
    if (type(value["authority_epoch"]) is not int or not 1 <= value["authority_epoch"] < 2**63
            or (not lineage.is_v2(value) and value["authority_epoch"] != 1)):
        raise RetirementConflictError("verified initial authority or v2 lineage is required")
    if lineage.is_v2(value):
        _digest(value["lineage_sha256"], "lineage_sha256")
    if (
        value["target_install_id"] != target_install_id
        or value["authority_gateway_id"] == target_install_id
    ):
        raise RetirementConflictError(
            "retirement enrollment targets another installation"
        )
    _digest(value["roster_sha256"], "roster_sha256")
    _digest(value["commitment"], "commitment")
    return value


def copy_retired_locked(conn: sqlite3.Connection, room_id: str) -> bool:
    return (
        table_exists(conn, RETIREMENT_TABLE)
        and conn.execute(
            f"SELECT 1 FROM {RETIREMENT_TABLE} WHERE room_id=?",
            (room_id,),
        ).fetchone()
        is not None
    )


def copy_scope_matches_locked(
    conn: sqlite3.Connection,
    *,
    room_id: str,
    authority_gateway_id: str,
    authority_epoch: int,
    members_json: str,
    replica_version: int | None = None,
    lineage_sha256: str | None = None,
) -> bool:
    row = lineage.current_locked(conn, room_id)
    if row is None:
        return replica_version is None
    if lineage.is_v2(row):
        if replica_version != 2 or lineage_sha256 != row["lineage_sha256"] or row["state"] != "active":
            return False
    elif replica_version is not None:
        return False
    return (row["authority_gateway_id"], row["authority_epoch"], row["roster_sha256"]) == (
        authority_gateway_id, authority_epoch, hashlib.sha256(members_json.encode("utf-8")).hexdigest())


def _retired_response(row):
    result = dict(row)
    if lineage.is_v2(result):
        _digest(result.get("lineage_sha256"), "lineage_sha256")
        if result.get("lineage_status") not in {"pending", "verified"}:
            raise RetirementConflictError("retired lineage coverage is unavailable")
    else:
        if result["authority_epoch"] != 1 or result.get("version") is not None:
            raise RetirementConflictError("retired lineage format is unavailable")
        for key in ("version", "lineage_sha256", "lineage_status"):
            result.pop(key, None)
    return {"retired": True, **result}

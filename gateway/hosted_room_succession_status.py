"""What a computer knows about its group's host, as the C7 status clients render, and the upkeep behind it.

``status`` answers ``groups.succession.status`` from this computer's own records: the room it
hosts or the copy it keeps, the configuration, its custody view, and the heartbeat record the
upkeep keeps. A backup asks the configured host every minute (the signed succession query); the
host counts as offline only after a bounded window without an answer, never after one missed
poll, and a planned restart the host announced is not loss. Every field is a code or parameter;
times are Unix seconds.

When the host stays offline, the eligible computer best placed to continue the group (ranked as
``actions[continue].targets``) sends the owner one notice per incident: through the owner's
private chats when messaging offers them, otherwise the home channel. Another eligible computer
waits five minutes and sends only while every better-placed one still looks offline from here.
"""

from __future__ import annotations

import asyncio
import threading
import time
from contextlib import closing
from pathlib import Path
from typing import Any, Mapping

from gateway import hosted_room_succession as succession
from gateway import hosted_rooms as rooms
from gateway.hosted_room_succession import SuccessionError

HEARTBEAT_INTERVAL_SECONDS = 60.0
UNREACHABLE_AFTER_SECONDS = 300.0
RESTART_GRACE_SECONDS = 120.0
NOTICE_DELAY_SECONDS = 300.0
UPKEEP_INTERVAL_SECONDS = 15.0
_READINESS_RANK = {"caught_up": 0, "behind": 1, "unknown": 2, "offline": 3, "unsupported": 4}


# --- the room as this computer holds it ------------------------------------------------------------
def _holding(conn, room_id: str) -> dict[str, Any]:
    """The room this computer hosts or keeps a copy of, its membership, and what it set aside."""
    room = conn.execute("""SELECT name, members_json, authority_gateway_id, authority_epoch, next_seq - 1
        FROM hosted_rooms WHERE room_id=? AND disbanded_at IS NULL""", (room_id,)).fetchone()
    if room is not None:
        return {"kind": "room" if room[2] == succession.local_install_id() else "foreign", "name": room[0],
                "members": room[1], "authority": room[2], "epoch": int(room[3]), "latest_seq": int(room[4])}
    copy = conn.execute("""SELECT name, members_json, authority_gateway_id, authority_epoch, latest_seq, last_seq
        FROM hosted_room_replicas WHERE room_id=? AND disbanded_at IS NULL AND quarantine_reason IS NULL""",
                        (room_id,)).fetchone()
    if copy is not None:
        return {"kind": "copy", "name": copy[0], "members": copy[1], "authority": copy[2], "epoch": int(copy[3]),
                "latest_seq": int(copy[4]), "held_seq": int(copy[5])}
    member = conn.execute("SELECT 1 FROM hosted_room_peer_reservations WHERE room_id=? LIMIT 1",
                          (room_id,)).fetchone() if succession.table_exists(conn, "hosted_room_peer_reservations") \
        else None
    if member is not None:
        return {"kind": "member"}
    if any(succession.load_record_locked(conn, room_id, kind) for kind in ("move", "return", "heartbeat")):
        return {"kind": "none"}
    raise SuccessionError("this computer holds no record of that group", reason="room_not_found")


def _readiness(entry: Mapping[str, Any], *, latest_seq: int, now: float, heard: Mapping[str, Any] | None):
    if entry.get("state") == "unsupported":
        return "unsupported", None
    watermark = entry.get("watermark") or (heard or {}).get("watermark")
    seen = entry.get("acknowledged_at") or (heard or {}).get("last_ok")
    if (heard or {}).get("last_attempt") and not (heard or {}).get("ok", True):
        if seen is None or now - float(seen) > UNREACHABLE_AFTER_SECONDS:
            return "offline", None
    if not isinstance(watermark, Mapping):
        return "unknown", None
    behind = max(0, int(latest_seq) - int(watermark["seq"]))
    return ("caught_up" if behind == 0 else "behind"), behind


def backups(configuration: Mapping[str, Any], custody: Mapping[str, Any] | None, *, host_id: str | None,
            latest_seq: int, heartbeat: Mapping[str, Any], now: float) -> list[dict[str, Any]]:
    """One row per computer that keeps a copy, best placed first among those that can continue."""
    entries = {item["install_id"]: item for item in (custody or {}).get("custodians", ())}
    peers = heartbeat.get("peers") or {}
    rows = []
    for order, item in enumerate(configuration.get("custodians") or ()):
        install_id = item["install_id"]
        if install_id == host_id:
            continue
        entry = {**item, **entries.get(install_id, {})}
        readiness, behind = _readiness(entry, latest_seq=latest_seq, now=now, heard=peers.get(install_id))
        allowed = entry.get("allowed")
        designated = entry.get("designated")
        rows.append({
            "install_id": install_id, "name": succession.label(configuration, install_id),
            "successor": bool(item.get("successor")), "readiness": readiness, "behind_by": behind,
            "last_seen": entry.get("acknowledged_at") or (peers.get(install_id) or {}).get("last_ok"),
            "allowed": bool(item.get("successor")) if allowed is None else bool(allowed),
            "designated": bool(item.get("successor")) if designated is None else bool(designated),
            "kind": "backup" if item.get("role") == "custodian_only" else "member",
            "operator_name": succession.label(configuration, install_id, "operator_name"), "_order": order})
    return rows


def ranked_targets(rows: list[Mapping[str, Any]]) -> list[str]:
    """Eligible computers, best placed first: always on, then readiness, then the owner's order."""
    eligible = [row for row in rows if row["successor"] and row["readiness"] != "unsupported"]
    eligible.sort(key=lambda row: (not row.get("always_on", False), _READINESS_RANK[row["readiness"]], row["_order"]))
    return [row["install_id"] for row in eligible]


def _host_view(holding: Mapping[str, Any], configuration: Mapping[str, Any], heartbeat: Mapping[str, Any],
               restarting: float | None, now: float) -> dict[str, Any]:
    me = succession.local_install_id()
    host_id = (succession.host_entry(configuration) or {}).get("install_id") or holding.get("authority")
    host = {"install_id": host_id, "name": succession.label(configuration, host_id), "reachable": True, "since": None}
    if host_id == me:
        return host
    last_ok, first = heartbeat.get("last_ok"), heartbeat.get("first_failure")
    failing = heartbeat.get("failing", False)
    if failing:
        since = last_ok if last_ok is not None else first
        host.update(reachable=False, since=since)
    if restarting is not None:
        host["restarting_until"] = restarting
    return host


def status(ctx, room_id: str) -> dict[str, Any]:
    """``groups.succession.status`` for this computer."""
    from gateway.hosted_room_succession_move import host_bots, is_owner, restarting_until, work_counts
    now, me = time.time(), succession.local_install_id()
    with closing(rooms._read_connection(ctx.db_path)) as conn:
        holding = _holding(conn, room_id)
        configuration = succession.configuration_locked(conn, room_id) if holding["kind"] in {"room", "copy"} \
            else {"configuration_seq": 0, "custodians": [], "owner_name": None}
        records = {kind: succession.load_record_locked(conn, room_id, kind) or {}
                   for kind in ("move", "conflict", "heartbeat", "return")}
        restarting = restarting_until(conn, room_id) if holding["kind"] == "copy" else None
    custody = None
    if holding["kind"] in {"room", "copy"}:
        try:
            custody = succession.custody_status(ctx.db_path, room_id)
        except Exception:
            custody = None
    move, conflict, heartbeat, returned = (records[k] for k in ("move", "conflict", "heartbeat", "return"))
    host = _host_view(holding, configuration, heartbeat, restarting, now)
    latest = holding.get("latest_seq", 0)
    rows = backups(configuration, custody, host_id=host["install_id"], latest_seq=latest, heartbeat=heartbeat,
                   now=now)
    targets = ranked_targets(rows)
    owner = is_owner(ctx, room_id)
    restarting_now = restarting is not None and now < restarting + RESTART_GRACE_SECONDS
    if returned.get("state") == "stepped_down" and holding["kind"] != "room":
        state = "moved_away"
    elif conflict.get("state") == "active":
        state = "continued_on_two"
    elif move.get("state") == "moving" or returned.get("state") == "paused":
        state = "moving"
    elif holding["kind"] == "room":
        state = "ok"
    elif restarting_now:
        state = "host_restarting"
    elif not host["reachable"]:
        state = "host_unreachable"
    else:
        state = "ok"
    role = {"room": "host", "copy": "backup", "member": "member"}.get(holding["kind"], "none")
    at_risk_after = (custody or {}).get("at_risk_after_seq") or 0
    result: dict[str, Any] = {
        "state": state, "host": host,
        "this_install": {"install_id": me, "name": succession.label(configuration, me), "role": role},
        "owner": {"name": succession.owner_label(configuration)},
        "backups": [{key: value for key, value in row.items() if key != "_order"} for row in rows],
        "at_risk": {"count": max(0, int(latest) - int(at_risk_after)) if custody else 0},
        "moving": None, "conflict": None, "moved": None, "work": None, "actions": [],
        "unavailable_reason": None, "previous_host": None, "unavailable_bots": [], "last_attempt": None}
    if state == "moving":
        if move.get("state") == "moving":
            result["moving"] = {"to": {"install_id": me, "name": succession.label(configuration, me)},
                                "step": move.get("step") or "fencing", "started_at": move.get("started_at")}
        else:
            holder = (returned.get("fenced_by") or {}).get("install_id")
            result["moving"] = {"to": {"install_id": holder, "name": succession.label(configuration, holder)},
                                "step": "fencing", "started_at": returned.get("at")}
    if state == "continued_on_two":
        result["conflict"] = {"hosts": [{"install_id": item["install_id"],
                                         "name": succession.label(configuration, item["install_id"]),
                                         "since": item.get("since")} for item in conflict.get("hosts") or ()]}
        if owner:
            result["actions"].append({"action": "keep", "targets": [item["install_id"]
                                                                     for item in conflict.get("hosts") or ()]})
    if state == "moved_away":
        result["moved"] = {"to": {"install_id": returned["successor"],
                                  "name": succession.label(configuration, returned["successor"])},
                           "at": returned.get("at"), "separate_events": returned.get("separate_events", 0),
                           "branch_id": returned.get("branch_id")}
        result["actions"].append({"action": "open_on", "target": returned["successor"]})
    if holding["kind"] == "room" and move.get("state") == "moved":
        reconciliation = move.get("reconciliation") or {}
        result["work"] = reconciliation.get("counts") or work_counts([])
        previous = move.get("previous_host")
        result["previous_host"] = {"install_id": previous, "name": succession.label(configuration, previous),
                                   "offline_since": move.get("offline_since")}
        result["unavailable_bots"] = host_bots(_members(holding), previous)
    if move.get("state") == "failed" and move.get("last_attempt"):
        attempt = move["last_attempt"]
        result["last_attempt"] = {"to": attempt.get("to"), "error": attempt.get("error"), "at": attempt.get("at")}
    if state == "host_unreachable":
        if not owner:
            result["unavailable_reason"] = "not_owner"
        elif not targets:
            result["unavailable_reason"] = "no_successor"
        elif all(row["readiness"] in {"offline", "unsupported"} for row in rows if row["install_id"] in targets):
            result["unavailable_reason"] = "successor_behind_offline"
        else:
            result["actions"].append({"action": "continue", "targets": targets})
    elif state in {"ok", "host_restarting"}:
        result["unavailable_reason"] = "host_reachable"
    if holding["kind"] == "room" and owner and state == "ok":
        designate = [row["install_id"] for row in rows if row["allowed"] and not row["designated"]]
        if designate:
            result["actions"].append({"action": "designate", "targets": designate})
        result["actions"].append({"action": "add_backup"})
        removable = [row["install_id"] for row in rows if row["kind"] == "backup"]
        if removable:
            result["actions"].append({"action": "remove_backup", "targets": removable})
    return result


def _members(holding: Mapping[str, Any]) -> list[dict[str, Any]]:
    import json
    raw = holding.get("members")
    return json.loads(raw) if isinstance(raw, str) else []


# --- heartbeats, the offline window and the owner's notice ----------------------------------------
def heartbeat(ctx, room_id: str, *, now: float | None = None, survey_peers: bool = False) -> dict[str, Any]:
    """Ask the configured host once, and when it stays offline every other custodian too."""
    from gateway.hosted_room_succession_move import RemoteRefusal, _query, survey
    now = time.time() if now is None else float(now)
    with closing(rooms._read_connection(ctx.db_path)) as conn:
        configuration = succession.configuration_locked(conn, room_id)
        record = dict(succession.load_record_locked(conn, room_id, "heartbeat") or {})
    host = succession.host_entry(configuration) or {}
    me = succession.local_install_id()
    if not host or host.get("install_id") == me:
        return record
    if record.get("host") != host["install_id"]:
        record = {"host": host["install_id"], "first_seen_at": now}
    ok = False
    try:
        if host.get("endpoint"):
            answer = _query(ctx, room_id, host["install_id"], str(host["endpoint"]))
            ok = bool(answer.get("hosting")) or isinstance(answer.get("restarting_until"), (int, float))
    except (RemoteRefusal, SuccessionError, OSError, ValueError):
        ok = False
    except Exception:
        ok = False
    record["last_attempt"] = now
    if ok:
        record.update(last_ok=now, failing=False, first_failure=None, notice_waiting_since=None)
    else:
        record.setdefault("first_failure", now)
        record["first_failure"] = record["first_failure"] or now
        reference = record.get("last_ok") or record.get("first_seen_at") or record["first_failure"]
        record["failing"] = now - float(reference) >= UNREACHABLE_AFTER_SECONDS
    if record.get("failing") and survey_peers:
        peers = dict(record.get("peers") or {})
        for install_id, outcome in survey(ctx, room_id, configuration).items():
            if install_id == host["install_id"]:
                continue
            answer = outcome.get("answer")
            peer = dict(peers.get(install_id) or {})
            peer.update(last_attempt=now, ok=answer is not None)
            if answer is not None:
                peer.update(last_ok=now, watermark=answer.get("watermark"))
            peers[install_id] = peer
        record["peers"] = peers
    succession.save_record(ctx.db_path, room_id, "heartbeat", record)
    return record


def notice_due(ctx, room_id: str, *, now: float | None = None) -> dict[str, Any] | None:
    """The incident this computer should tell the owner about now, once; ``None`` otherwise."""
    now = time.time() if now is None else float(now)
    try:
        current = status(ctx, room_id)
    except SuccessionError:
        return None
    me = succession.local_install_id()
    record = succession.load_record(ctx.db_path, room_id, "heartbeat") or {}
    if current["state"] != "host_unreachable" or not succession.local_consent(ctx.db_path, room_id):
        return None
    targets = next((action["targets"] for action in current["actions"] if action["action"] == "continue"), None)
    if targets is None:
        targets = ranked_targets([{**row, "_order": index} for index, row in enumerate(current["backups"])])
    if me not in targets:
        return None
    incident = f"{current['host']['install_id']}:{current['host']['since']}"
    if record.get("notified") == incident:
        return None
    peers = record.get("peers") or {}
    better = targets[:targets.index(me)]
    reachable_better = [install_id for install_id in better if (peers.get(install_id) or {}).get("ok")]
    if better:
        waiting = record.get("notice_waiting_since")
        if waiting is None:
            succession.save_record(ctx.db_path, room_id, "heartbeat", {**record, "notice_waiting_since": now})
            return None
        if reachable_better or now - float(waiting) < NOTICE_DELAY_SECONDS:
            return None
    return {"incident": incident, "status": current}


def notice_text(*, group: str, host: str, minutes: int, here: str, hint: str | None) -> str:
    if hint is not None:
        return (f"“{group}” is paused: {host} has been offline for {minutes} min. "
                f"Reply {hint} to continue it on {here}.")
    return (f"“{group}” is paused: {host} has been offline for {minutes} min. Open Hermes Desktop on a computer "
            f"you own, or run `hermes groups continue {group}` on {here}.")


async def send_notice(runner, *, room_id: str, group: str, host: str | None, minutes: int, here: str) -> bool:
    """One notice: messaging renders it, with its own Continue button, when it offers that; otherwise
    our text goes to the owner's private chats it offers, else the home channel."""
    render = getattr(runner, "_group_chat_notify", None)
    if render is not None:
        return bool(await render(room_id, "host_offline", {"host": host, "minutes": minutes}))
    host = host or "the host"
    refs_for = getattr(runner, "_group_chat_continue_refs", None)
    refs = await refs_for(room_id) if refs_for is not None else []
    sent = False
    for adapter, chat_id, metadata, number in refs or ():
        hint = runner._typed_command_prefix_for(adapter.platform) + f"group {number} continue"
        try:
            await adapter.send(chat_id, notice_text(group=group, host=host, minutes=minutes, here=here, hint=hint),
                               metadata=metadata)
            sent = True
        except Exception:
            continue
    if refs:
        return sent
    for platform, _config, home, transport in runner._home_channel_transports():
        sent = await runner._send_home_channel_message(
            platform, home, transport, notice_text(group=group, host=host, minutes=minutes, here=here, hint=None),
            "Group Chat pause notice to %s:%s failed: %s") or sent
    return sent


def notify(ctx, room_id: str, runner, loop, *, now: float | None = None) -> bool:
    """Send the owner's notice for this incident when this computer is the one to send it."""
    due = notice_due(ctx, room_id, now=now)
    if due is None or runner is None or loop is None:
        return False
    now = time.time() if now is None else float(now)
    current = due["status"]
    with closing(rooms._read_connection(ctx.db_path)) as conn:
        holding = _holding(conn, room_id)
    since = current["host"]["since"] or now
    future = asyncio.run_coroutine_threadsafe(send_notice(
        runner, room_id=room_id, group=holding.get("name") or "this group", host=current["host"]["name"],
        minutes=max(1, int((now - float(since)) // 60)), here=current["this_install"]["name"] or "this computer"),
        loop)
    try:
        sent = bool(future.result(timeout=30))
    except Exception:
        return False
    if sent:
        record = succession.load_record(ctx.db_path, room_id, "heartbeat") or {}
        succession.save_record(ctx.db_path, room_id, "heartbeat", {**record, "notified": due["incident"]})
    return sent


# --- the upkeep thread ------------------------------------------------------------------------------
def held_rooms(db_path: Path) -> dict[str, list[str]]:
    """Rooms this computer hosts and rooms it keeps a copy of, as succession sees them."""
    me = succession.local_install_id()
    with closing(rooms._read_connection(db_path)) as conn:
        hosted = [row[0] for row in conn.execute(
            "SELECT room_id FROM hosted_rooms WHERE authority_gateway_id=? AND disbanded_at IS NULL", (me,))]
        copies = [row[0] for row in conn.execute(
            "SELECT room_id FROM hosted_room_replicas WHERE disbanded_at IS NULL AND quarantine_reason IS NULL")] \
            if succession.table_exists(conn, "hosted_room_replicas") else []
    return {"hosted": hosted, "copies": copies}


def back_from_restart(db_path: Path, room_id: str) -> None:
    """A host running again ends the restart it announced, so backups stop waiting for it."""
    from gateway.hosted_room_succession_move import append_state, restarting_until
    with closing(rooms._read_connection(db_path)) as conn:
        announced = restarting_until(conn, room_id)
    if announced is not None:
        append_state(db_path, room_id, "ok")


class SuccessionUpkeep:
    """Background checks for the groups this computer hosts or keeps a copy of."""

    def __init__(self, context_factory, *, runner=None, loop=None, interval: float = UPKEEP_INTERVAL_SECONDS):
        self._context_factory, self._runner, self._loop = context_factory, runner, loop
        self._interval, self._stop = float(interval), threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_beat: dict[str, float] = {}
        self.suspect: set[str] = set()

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="group-succession-upkeep", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> bool:
        self._stop.set()
        self._wake.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout)
            return not thread.is_alive()
        return True

    def wakeup(self) -> None:
        self._wake.set()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.run_once()
            except Exception:
                pass  # upkeep never decides a room's authority; the next cycle asks again
            self._wake.wait(self._interval)
            self._wake.clear()

    def run_once(self, *, now: float | None = None) -> None:
        from gateway import hosted_room_succession_move as move
        from gateway import hosted_room_succession_return as returning
        ctx = self._context_factory()
        if ctx is None:
            return
        now = time.time() if now is None else float(now)
        held = held_rooms(ctx.db_path)
        for room_id in held["hosted"]:
            back_from_restart(ctx.db_path, room_id)
        move.maintain(ctx, held["hosted"])
        returning.maintain(ctx, held["hosted"], suspect=frozenset(self.suspect))
        self.suspect.clear()
        for room_id in held["copies"]:
            try:
                returning.follow_up(ctx, room_id)
                move.deliver_decision(ctx, room_id)
                if now - self._last_beat.get(room_id, 0.0) >= HEARTBEAT_INTERVAL_SECONDS:
                    self._last_beat[room_id] = now
                    record = heartbeat(ctx, room_id, now=now, survey_peers=True)
                    if record.get("failing"):
                        notify(ctx, room_id, self._runner, self._loop, now=now)
            except Exception:
                continue

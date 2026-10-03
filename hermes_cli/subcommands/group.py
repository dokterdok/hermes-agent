"""``hermes groups`` — a Group Chat whose host went offline: status, continue, keep and backup copies.

Thin wrappers over this computer's gateway (``groups.succession.*`` and ``groups.custody.*``).
``continue`` always runs on the computer the group should continue on: this one. The words are the
product's own: the host, a backup copy, continue the group.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time

_MESSAGES = {
    "not_owner": "Only the group's owner can do that.",
    "host_reachable": "The group's host can be reached, so the group doesn't need to move.",
    "room_authority_promised": "Another computer is already continuing this group.",
    "preview_stale": "Something changed since the summary. Run the command again.",
    "target_not_ready": "This computer can't continue the group: it isn't allowed to, or keeps no usable copy.",
    "target_not_local": "Run this on the computer the group should continue on.",
    "room_not_found": "This computer has no group by that name.",
    "room_authority_conflict": "This group was continued on two computers. Choose one with `hermes groups keep`.",
}


class GroupCommandError(RuntimeError):
    pass


def _call(method: str, **params):
    from hermes_cli.gateway_client import GatewayClientError, connect_gateway

    async def run():
        async with connect_gateway() as client:
            return await client.rpc(method, **params)

    try:
        return asyncio.run(run())
    except GatewayClientError as exc:
        raise GroupCommandError(_MESSAGES.get(str(exc), str(exc))) from exc


def _room_id(name: str) -> str:
    """A group's room id from its id or its name (exact, any case), copies included."""
    rooms = _call("groups.list").get("rooms") or []
    for room in rooms:
        if room.get("room_id") == name:
            return name
    matches = [room["room_id"] for room in rooms if str(room.get("name") or "").casefold() == name.casefold()]
    if len(matches) == 1:
        return matches[0]
    raise GroupCommandError(_MESSAGES["room_not_found"] if not matches else
                            f"More than one group is called “{name}”; use its id.")


def _computers(status: dict) -> dict[str, str]:
    """Display name → install id for every computer the status names."""
    named = {row["install_id"]: row.get("name") or row["install_id"] for row in status.get("backups") or ()}
    for item in (status.get("host") or {}, status.get("this_install") or {}):
        if item.get("install_id"):
            named[item["install_id"]] = item.get("name") or item["install_id"]
    return named


def _computer(status: dict, value: str) -> str:
    if value in {"here", "this"}:
        return status["this_install"]["install_id"]
    for install_id, name in _computers(status).items():
        if value in {install_id, name} or value.casefold() == str(name).casefold():
            return install_id
    raise GroupCommandError(f"This group has no computer called “{value}”.")


def _when(value) -> str:
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(float(value))) if value else "an unknown time"


def status_lines(status: dict) -> list[str]:
    host = status["host"].get("name") or "the host"
    names = _computers(status)
    state, lines = status["state"], []
    if state == "host_unreachable":
        lines.append(f"{host} is offline (last seen {_when(status['host'].get('since'))}). The group is paused.")
    elif state == "host_restarting":
        lines.append(f"{host} is restarting. The group will continue in a moment.")
    elif state == "moving":
        moving = status.get("moving") or {}
        lines.append(f"Continuing on {(moving.get('to') or {}).get('name') or 'another computer'}… "
                     f"({moving.get('step')})")
    elif state == "continued_on_two":
        hosts = [item.get("name") or item["install_id"] for item in (status.get("conflict") or {}).get("hosts", [])]
        lines.append(f"This group was continued on two computers: {' and '.join(hosts)}. "
                     "Choose one with `hermes groups keep <group> <computer>`.")
    elif state == "moved_away":
        moved = status.get("moved") or {}
        lines.append(f"This group moved to {(moved.get('to') or {}).get('name') or 'another computer'}. "
                     f"{moved.get('separate_events', 0)} messages from that time are kept separately.")
    else:
        lines.append(f"Hosted on {host}.")
    for row in status.get("backups") or ():
        name = row.get("name") or row["install_id"]
        readiness = row["readiness"]
        detail = {"caught_up": "full copy, up to date", "behind": f"full copy, {row.get('behind_by')} messages behind",
                  "offline": f"offline since {_when(row.get('last_seen'))}", "unknown": "full copy, not confirmed yet",
                  "unsupported": "needs a newer Hermes to keep a copy of this group"}.get(readiness, readiness)
        lines.append(f"  {name}: {detail}{' (can continue this group)' if row.get('successor') else ''}")
    targets = next((action["targets"] for action in status.get("actions") or () if action["action"] == "continue"),
                   None)
    if targets:
        lines.append("Can continue on: " + ", ".join(names.get(item, item) for item in targets))
    elif state == "ok":
        eligible = [row.get("name") or row["install_id"] for row in status.get("backups") or () if row.get("successor")]
        lines.append(f"If {host} goes offline, you can continue this group on {', '.join(eligible)}." if eligible
                     else f"If {host} goes offline, this group pauses until it's back.")
    return lines


def cmd_status(args: argparse.Namespace) -> int:
    try:
        status = _call("groups.succession.status", room_id=_room_id(args.group))
    except GroupCommandError as exc:
        print(exc, file=sys.stderr)
        return 1
    print("\n".join(status_lines(status)))
    return 0


def _confirm(question: str, assume_yes: bool) -> bool:
    if assume_yes:
        return True
    try:
        return input(f"{question} [y/N] ").strip().lower() in {"y", "yes"}
    except EOFError:
        return False


def summary_lines(group: str, host: str, preview: dict) -> list[str]:
    target = preview["target"].get("name") or "this computer"
    lines = [f"Continue “{group}” on {target}? {target} becomes the group's host. "
             "The conversation, members and history stay the same."]
    bots = [bot.get("name") or bot.get("member_id") for bot in preview.get("unavailable_bots") or ()]
    if bots:
        lines.append(f"{len(bots)} Bots run on {host} and stay unavailable until it's back: {', '.join(bots)}.")
    work = preview.get("work") or {}
    if any(work.values()):
        lines.append(f"Work in progress: {work.get('completed', 0)} finished, {work.get('elsewhere', 0)} still running "
                     f"on other computers, {work.get('unknown', 0)} unknown. Unknown work won't run again "
                     "automatically.")
    if preview.get("behind_by"):
        lines.append(f"{target} is missing {preview['behind_by']} recent messages. They'll appear if {host} comes back.")
    operator, owner = preview["target"].get("operator_name"), (preview.get("owner") or {}).get("name")
    if operator and owner and operator != owner:
        lines.append(f"{operator} will manage this group from {target}.")
    lines.append(f"If {host} comes back, it rejoins as a member. Anything it did while offline is shown separately.")
    for caution in preview.get("cautions") or ():
        if caution.get("code") == "host_may_be_running":
            lines.append(f"Only continue if {host} is really offline. If it's still running somewhere you can't "
                         "reach, both computers may keep working until they reconnect, and you'll be asked to "
                         "choose one.")
        elif caution.get("code") == "participant_not_fenced":
            names = ", ".join(caution.get("names") or []) or f"{caution.get('count')} computers"
            lines.append(f"{names} run an older Hermes and may still accept work from {host} if it is still running.")
    return lines


def cmd_continue(args: argparse.Namespace) -> int:
    try:
        room_id = _room_id(args.group)
        status = _call("groups.succession.status", room_id=room_id)
        here = status["this_install"]["install_id"]
        names = _computers(status)
        if args.on:
            target = _computer(status, args.on)
            if target != here:
                raise GroupCommandError(f"Run this on {names.get(target, target)}: a group continues on the computer "
                                        "that runs the command.")
        targets = next((action["targets"] for action in status.get("actions") or ()
                        if action["action"] == "continue"), [])
        if not args.on and targets and targets[0] != here and not _confirm(
                f"{names.get(targets[0], targets[0])} is better placed to continue this group. Continue here anyway?",
                args.yes):
            return 1
        preview = _call("groups.succession.prepare", room_id=room_id, target_install_id=here)
        host = status["host"].get("name") or "the host"
        print("\n".join(summary_lines(args.group, host, preview)))
        if not _confirm("Continue?", args.yes):
            return 1
        result = _call("groups.succession.promote", room_id=room_id, target_install_id=here,
                       preview_id=preview["preview_id"], confirm=True)
        while result["state"] == "moving":
            time.sleep(1)
            result = _call("groups.succession.status", room_id=room_id)
    except GroupCommandError as exc:
        print(exc, file=sys.stderr)
        return 1
    work = result.get("work") or {}
    print(f"Done. “{args.group}” now continues on {result['this_install'].get('name') or 'this computer'}. "
          f"{work.get('unknown', 0)} task(s) unknown, {work.get('waiting_for_host', 0)} waiting for "
          f"{(result.get('previous_host') or {}).get('name') or 'the old host'}.")
    return 0


def cmd_keep(args: argparse.Namespace) -> int:
    try:
        room_id = _room_id(args.group)
        status = _call("groups.succession.status", room_id=room_id)
        result = _call("groups.succession.keep", room_id=room_id, install_id=_computer(status, args.computer))
    except GroupCommandError as exc:
        print(exc, file=sys.stderr)
        return 1
    print("\n".join(status_lines(result)))
    return 0


def cmd_backups(args: argparse.Namespace) -> int:
    try:
        room_id = _room_id(args.group)
        status = _call("groups.succession.status", room_id=room_id)
        action = getattr(args, "backups_action", None)
        if action in {"allow", "disallow"}:
            install_id, allowed = _computer(status, args.computer), action == "allow"
            if install_id == status["this_install"]["install_id"]:
                _call("groups.custody.allow", room_id=room_id, successor=allowed)
            if status["this_install"]["role"] == "host":
                _call("groups.custody.designate", room_id=room_id, install_id=install_id, successor=allowed)
            status = _call("groups.succession.status", room_id=room_id)
        elif action == "add":
            _add_backup(room_id, args.computer)
            status = _call("groups.succession.status", room_id=room_id)
    except GroupCommandError as exc:
        print(exc, file=sys.stderr)
        return 1
    print("\n".join(status_lines(status)))
    return 0


def _add_backup(room_id: str, peer: str) -> None:
    """A backup computer from ``hermes peer``: a copy-only invitation there, then this host adds it."""
    from hermes_cli.subcommands import peer as peers
    configured = peers._load_peers().get(peer)
    key = peers._peer_secret(peer)
    if not isinstance(configured, dict) or not configured.get("url") or not key:
        raise GroupCommandError(f"Add the computer first with `hermes peer add {peer} <url>`.")
    state = _call("groups.state", room_id=room_id)["room"]
    invitation = peers._request(
        configured["url"].rstrip("/") + "/v1/room-members/invitations", key, method="POST", body={
            "room_id": room_id, "home_install_id": state["authority_gateway_id"],
            "authority_gateway_id": state["authority_gateway_id"], "authority_epoch": state["authority_epoch"],
            "member_id": "custody:installation", "passive_only": True})
    _call("groups.custody.add", room_id=room_id, target_url=configured["url"], catalog=invitation["catalog"],
          grant=invitation["grant"])


def build_groups_parser(subparsers) -> None:
    """Attach ``hermes groups`` to ``subparsers``: a group whose host went offline.

    #111939 adds its messaging subcommands (``allow``, ``chats``, ``revoke``) to the same family;
    whichever lands second builds one ``groups`` parser and calls ``add_succession_commands`` on it.
    """
    parser = subparsers.add_parser(
        "groups", help="A Group Chat whose host went offline: status, continue, keep, backup copies",
        description="See whether a group's host can be reached, continue the group on this computer, "
                    "choose a computer after it was continued on two, and manage backup copies.")
    add_succession_commands(parser.add_subparsers(dest="groups_command", required=True))


def add_succession_commands(commands) -> None:
    """Attach this family's host-loss subcommands to the ``hermes groups`` subparsers ``commands``,
    so one ``groups`` parser can carry them beside #111939's messaging ones."""
    status = commands.add_parser("status", help="Whether the group's host can be reached, and its backup copies")
    status.add_argument("group")
    status.set_defaults(func=cmd_status)
    continuing = commands.add_parser("continue", help="Continue the group on this computer")
    continuing.add_argument("group")
    continuing.add_argument("--on", help="The computer to continue on (it must be this one)")
    continuing.add_argument("--yes", action="store_true", help="Don't ask for confirmation")
    continuing.set_defaults(func=cmd_continue)
    keeping = commands.add_parser("keep", help="Keep one computer after the group was continued on two")
    keeping.add_argument("group")
    keeping.add_argument("computer")
    keeping.set_defaults(func=cmd_keep)
    backups = commands.add_parser("backups", help="The computers keeping a backup copy of the group")
    backups.set_defaults(func=cmd_backups, backups_action=None)
    actions = backups.add_subparsers(dest="backups_action")
    for name, help_text in (("add", "Keep a backup copy on another computer (from `hermes peer`)"),
                            ("allow", "Let a computer continue this group"),
                            ("disallow", "Stop letting a computer continue this group")):
        action = actions.add_parser(name, help=help_text)
        action.add_argument("group")
        action.add_argument("computer")
        action.set_defaults(func=cmd_backups, backups_action=name)
    backups.add_argument("group", nargs="?")

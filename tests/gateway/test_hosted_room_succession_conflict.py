"""Continued on two computers: both pause, the owner keeps one from either side, and nothing is merged."""

import pytest

from gateway import hosted_room_fence as fence
from gateway import hosted_room_succession as succession
from gateway import hosted_room_succession_move as move
from gateway import hosted_room_succession_return as returning
from gateway import hosted_room_succession_status as status_module
from tests.gateway.fixtures.succession import (
    ROOM, context, copy_to, events, head, home_room, make_gateways, message)


@pytest.fixture
def split(tmp_path):
    """The host is lost; s and t were both continued while each reached only its own backup."""
    gateways = make_gateways(tmp_path, "h", "s", "t", "p1", "p2")
    home_room(gateways, "h", successors=("s", "t"))
    for name in ("s", "t", "p1", "p2"):
        copy_to(gateways["h"], gateways[name])
    s, t = gateways["s"], gateways["t"]
    for gateway, cut in ((s, ("h", "t", "p2")), (t, ("h", "s", "p1"))):
        with gateway.acting():
            ctx = context(gateway, gateways, down=cut)
            preview = move.preview(ctx, ROOM, gateway.install_id)
            move.continue_here(ctx, ROOM, gateway.install_id, preview_id=preview["preview_id"], confirm=True)
    copy_to(s, gateways["p1"])
    copy_to(t, gateways["p2"])
    with s.acting():
        message(s.db, "user:on-s", gateway=s.install_id, epoch=2)
    with t.acting():
        message(t.db, "user:on-t", gateway=t.install_id, epoch=2)
    yield gateways
    for gateway in gateways.values():
        gateway.close()


def state(gateway, gateways, **options):
    with gateway.acting():
        return status_module.status(context(gateway, gateways, **options), ROOM)


def meet(gateways):
    """The partition heals: s's announcement reaches t, which already continued the group itself."""
    s = gateways["s"]
    with s.acting():
        move.announce(context(s, gateways, down=("h",)), ROOM)


def test_the_first_contact_pauses_both_and_offers_the_owner_the_choice(split):
    s, t = split["s"], split["t"]
    meet(split)
    for gateway in (s, t):
        current = state(gateway, split, down=("h",))
        assert current["state"] == "continued_on_two"
        assert {host["install_id"] for host in current["conflict"]["hosts"]} == {s.install_id, t.install_id}
        assert {"action": "keep", "targets": [host["install_id"] for host in current["conflict"]["hosts"]]} in \
            current["actions"]
        assert succession.paused_reason(gateway.db, ROOM) == "room_authority_conflict"
    assert state(t, split, down=("h",), subject="uid:999")["actions"] == []


def test_keeping_on_the_kept_computer_continues_it_at_a_fresh_epoch_everyone_follows(split):
    h, s, t, p1, p2 = (split[name] for name in ("h", "s", "t", "p1", "p2"))
    meet(split)
    with s.acting():
        kept = move.keep(context(s, split, down=("h",)), ROOM, s.install_id)
    assert kept["state"] == "ok" and head(s)["authority_epoch"] == 3
    transition = events(s)[-2]
    assert transition["kind"] == "authority.transition"
    assert transition["payload"]["proof"]["statement"] == succession.KEEP_TEXT
    # t stepped aside on contact: its own continuation and message are set aside, shown separately.
    assert head(t)["authoritative"] is False
    with t.acting():
        returned = succession.load_record(t.db, ROOM, "return")
        branch = returning.branch_log(t.db, ROOM, returned["branch_id"])
    assert [event["kind"] for event in branch["events"]][0] == "authority.transition"
    assert "user:on-t" in [event["payload"].get("text") for event in branch["events"]]
    assert state(t, split, down=("h",))["state"] == "moved_away"
    # Every copy, whichever host it followed, catches up and follows the kept host's fresh epoch.
    for gateway in (t, p1, p2):
        with gateway.acting():
            returning.follow_up(context(gateway, split, down=("h",)), ROOM)
        copy_to(s, gateway)
        assert head(gateway)["authority_gateway_id"] == s.install_id and head(gateway)["authority_epoch"] == 3
    with s.acting():
        move.announce(context(s, split, down=("h",)), ROOM)
    for gateway in (t, p1, p2):
        assert fence.room_fence_state(gateway.runs.path, ROOM)["authority"] == {"epoch": 3, "install_id": s.install_id}
    texts = [event["payload"].get("text") for event in events(s)]
    assert "user:on-s" in texts and "user:on-t" not in texts
    assert h.install_id  # the old host stays offline throughout


def test_keeping_from_the_other_computer_steps_it_aside_at_once_and_reaches_the_kept_one_later(split):
    s, t = split["s"], split["t"]
    meet(split)
    with t.acting():
        result = move.keep(context(t, split, down=("h", "s")), ROOM, s.install_id)
    assert result["state"] == "moved_away" and head(t)["authoritative"] is False
    assert state(s, split, down=("h",))["state"] == "continued_on_two"
    # The owner's choice, signed on t, reaches s on first contact; s then continues at a fresh epoch.
    with t.acting():
        move.deliver_decision(context(t, split, down=("h",)), ROOM)
    with s.acting():
        move.maintain(context(s, split, down=("h",)), [ROOM])
    assert state(s, split, down=("h",))["state"] == "ok" and head(s)["authority_epoch"] == 3


def test_a_choice_signed_by_neither_computer_changes_nothing(split):
    s, t, p1 = split["s"], split["t"], split["p1"]
    meet(split)
    record = succession.load_record(s.db, ROOM, "conflict")
    with p1.acting():
        forged = move._sign_decision(context(p1, split), ROOM, {**record, "hosts": record["hosts"]}, s.install_id)
    with t.acting():
        with pytest.raises(succession.SuccessionError):
            from gateway import hosted_room_succession_backup as backup
            backup.answer_decision(t.backup_context(), forged)
    assert head(t)["authoritative"] and state(t, split, down=("h",))["state"] == "continued_on_two"

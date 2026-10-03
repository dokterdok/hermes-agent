"""A voter's lease to a Group Chat's host: atomic with fence-and-promise, durable, measured through sleep."""

import multiprocessing
import threading
import time
from contextlib import closing

import pytest

from gateway import hosted_room_clock as clock
from gateway import hosted_room_fence as fence
from gateway.platforms.api_server_run_idempotency import RunIdempotencyStore

ROOM = "room-one"
HOST, OTHER = "install:host", "install:other"


@pytest.fixture
def path(tmp_path):
    value = tmp_path / "runs.db"
    with closing(RunIdempotencyStore(str(value))):
        pass
    return value


@pytest.fixture
def ticking(monkeypatch):
    """A controllable sleep-counting clock (and boot) for this process."""
    state = {"now": 1000.0, "awake": 1000.0}
    monkeypatch.setattr(clock, "_NOW", lambda: state["now"])
    monkeypatch.setattr(clock, "_AWAKE", lambda: state["awake"])
    monkeypatch.setattr(clock, "EXACT", True)
    monkeypatch.setattr(clock, "boot_id", lambda: 42)
    return state


def promise(path, candidate, epoch=2):
    return fence.fence_and_promise(path, room_id=ROOM, fence_epoch=epoch - 1, promise_epoch=epoch,
                                   candidate_install_id=candidate)


def lease(path, epoch=1, authority=HOST, duration=20.0, **options):
    return fence.grant_lease(path, room_id=ROOM, epoch=epoch, authority_install_id=authority, duration_s=duration,
                             **options)


def test_no_promise_while_the_host_holds_this_voters_lease(path, ticking):
    granted = lease(path)
    assert granted == {"room_id": ROOM, "epoch": 1, "authority_install_id": HOST, "duration_s": 20.0}
    with pytest.raises(fence.RoomLeaseActive) as refused:
        promise(path, OTHER)
    assert (refused.value.code, refused.value.status) == ("room_lease_active", 409)
    assert fence.room_fence_state(path, ROOM)["promise"] is None
    ticking["now"] += 20.5
    ticking["awake"] += 20.5
    assert fence.room_lease_state(path, ROOM) is None
    assert promise(path, OTHER)["promise"]["candidate_install_id"] == OTHER


def test_leases_go_only_to_the_host_of_an_unfenced_epoch(path, ticking):
    promise(path, OTHER, 2)
    for epoch, authority, error in ((1, HOST, fence.RoomAuthorityFenced), (2, HOST, fence.RoomAuthorityPromised),
                                    (3, HOST, None)):
        if error is None:
            assert lease(path, epoch, authority)["epoch"] == 3
        else:
            with pytest.raises(error):
                lease(path, epoch, authority)
    fence.learn_authority(path, room_id=ROOM, epoch=4, install_id=OTHER)
    with pytest.raises(fence.RoomAuthorityFenced):
        lease(path, 3, HOST)
    assert lease(path, 4, OTHER)["authority_install_id"] == OTHER


def test_a_renewal_never_shortens_and_a_restart_extension_is_capped(path, ticking):
    lease(path, duration=20.0)
    assert lease(path, duration=5.0)["duration_s"] == 20.0
    extended = lease(path, duration=20.0, until=time.time() + 3600, now=time.time())
    assert extended["duration_s"] == fence.MAX_RESTART_EXTENSION_SECONDS
    with pytest.raises(ValueError):
        lease(path, duration=fence.MAX_LEASE_SECONDS + 1)
    with pytest.raises(fence.RoomAuthorityConflict):
        lease(path, authority=OTHER)


def test_a_lease_counts_the_time_the_voter_slept(path, ticking):
    detector = clock.SuspendDetector()
    lease(path, duration=20.0)
    # Asleep for 30 s: the clock that counts sleep moved on, the awake one did not.
    ticking["now"] += 30.0
    assert detector.check() is True
    assert fence.room_lease_state(path, ROOM) is None
    assert promise(path, OTHER)["fenced_epoch"] == 1
    ticking["now"] += 1.0
    ticking["awake"] += 1.0
    assert detector.check() is False


def test_after_a_reboot_the_wall_clock_bounds_the_lease(path, ticking, monkeypatch):
    lease(path, duration=20.0, now=5000.0)
    monkeypatch.setattr(clock, "boot_id", lambda: 43)
    ticking["now"] = 1.0  # the new boot's clock starts again
    with pytest.raises(fence.RoomLeaseActive):
        fence.fence_and_promise(path, room_id=ROOM, fence_epoch=1, promise_epoch=2, candidate_install_id=OTHER,
                                now=5010.0)
    assert fence.fence_and_promise(path, room_id=ROOM, fence_epoch=1, promise_epoch=2, candidate_install_id=OTHER,
                                   now=5021.0)["promise"]["candidate_install_id"] == OTHER


def _process_promise(path, start, channel):
    start.wait(10)
    try:
        channel.send(promise(path, OTHER)["promise"]["candidate_install_id"])
    except fence.RoomFenceError as exc:
        channel.send(exc.code)
    finally:
        channel.close()


def test_a_lease_survives_a_restart_and_binds_another_process(path):
    lease(path, duration=30.0)
    with closing(RunIdempotencyStore(str(path))):
        pass
    context = multiprocessing.get_context("spawn")
    start = context.Event()
    parent, child = context.Pipe(duplex=False)
    process = context.Process(target=_process_promise, args=(str(path), start, child))
    process.start()
    child.close()
    start.set()
    try:
        assert parent.poll(15), "worker did not report"
        assert parent.recv() == "room_lease_active"
    finally:
        process.join(5)
        if process.is_alive():
            process.terminate()
        parent.close()


def test_grants_and_promises_never_overlap_under_concurrency(path):
    events, done = [], threading.Event()

    def host():
        """Renews for a while, goes quiet as if it died, then tries again late."""
        began = clock.now()
        while clock.now() - began < 0.2:
            before = clock.now()
            lease(path, duration=0.05)
            events.append(("lease", before, clock.now()))
        time.sleep(0.3)
        while not done.is_set():
            before = clock.now()
            try:
                lease(path, duration=0.05)
                events.append(("lease", before, clock.now()))
            except fence.RoomFenceError:
                events.append(("refused", before, clock.now()))
                return
            time.sleep(0.01)

    def candidate():
        while not done.is_set():
            before = clock.now()
            try:
                promise(path, OTHER)
                events.append(("promise", before, clock.now()))
                return
            except fence.RoomLeaseActive:
                time.sleep(0.005)

    threads = [threading.Thread(target=host), threading.Thread(target=candidate)]
    for thread in threads:
        thread.start()
    threads[1].join(10)
    done.set()
    threads[0].join(10)
    _, _, promised = next(event for event in events if event[0] == "promise")
    # Every lease had run out before the promise was made, and none was granted after it.
    for kind, began, returned in events:
        if kind == "lease":
            assert began + 0.05 <= promised
    assert events[-1][0] in {"refused", "promise"}
    with pytest.raises(fence.RoomAuthorityFenced):
        lease(path, duration=0.05)


def test_only_the_host_itself_gives_its_lease_back(path, ticking):
    lease(path)
    assert fence.release_lease(path, room_id=ROOM, epoch=1, authority_install_id=OTHER) is False
    assert fence.release_lease(path, room_id=ROOM, epoch=2, authority_install_id=HOST) is False
    with pytest.raises(fence.RoomLeaseActive):
        promise(path, OTHER)
    assert fence.release_lease(path, room_id=ROOM, epoch=1, authority_install_id=HOST) is True
    assert promise(path, OTHER)["promise"]["candidate_install_id"] == OTHER

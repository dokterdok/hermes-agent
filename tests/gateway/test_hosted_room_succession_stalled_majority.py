"""A stalled promise cannot turn majority recovery into an all-successor requirement."""
from contextlib import closing

import pytest

from gateway import hosted_room_custody as custody
from gateway import hosted_room_fence as fence
from gateway import hosted_rooms as rooms
from gateway import hosted_room_succession as succession
from gateway import hosted_room_succession_backup as backup
from gateway import hosted_room_succession_move as move
from gateway import hosted_room_succession_return as returning
from tests.gateway.fixtures.succession import ROOM
from tests.gateway.fixtures.succession_world import World
from tests.gateway.test_hosted_room_succession_stalled import owner_continues, last_transition


def _abandoned_majority_step(tmp_path, monkeypatch, *, names=('h', 'c', 'd')):
    world = World(tmp_path, monkeypatch, names, voters=names)
    world.advance(10)
    assert world.status('h')['automatic']['mode'] == 'majority'
    world.network.stopped.add('h')
    # No coordinator runs during this interval; real voter leases expire on the virtual clock.
    world.t += 65
    def abort_after_promises(*_):
        raise succession.SuccessionError('candidate stopped before transition', reason='target_not_ready')
    with monkeypatch.context() as stopped:
        stopped.setattr(move, '_adopt', abort_after_promises)
        with pytest.raises(succession.SuccessionError, match='candidate stopped'):
            owner_continues(world, 'c')
    assert not world.head('c')['authoritative']
    world.network.stopped.discard('h')
    world.network.stopped.update(set(names) - {'h', 'c'})
    return world


def _past_stall(world):
    world.tick('h')
    assert not world.head('h')['serving']
    world.t += move.STALLED_PROMISE_SECONDS + 1


def _check(world):
    host = world.gateways['h']
    with host.acting():
        returning.check(world.context(host), ROOM)


def _verify_proof(world, proof):
    host = world.gateways['h']
    with host.acting(), closing(rooms._read_connection(host.db)) as conn:
        configuration = next({'configuration_seq': item['seq'], **{k: v for k, v in item.items() if k != 'seq'}}
                             for item in custody.configurations_locked(conn, ROOM)
                             if item['seq'] == proof['configuration_seq'])
        succession.verify_proof_locked(conn, ROOM, proof=proof, proof_kind='attested', from_epoch=1,
                                       to_epoch=3, successor=host.install_id, configuration=configuration)


def test_majority_recovers_stalled_promise_with_its_holder_and_one_voter_offline(tmp_path, monkeypatch):
    world = _abandoned_majority_step(tmp_path, monkeypatch)
    try:
        # C's voter endpoint answers while its aborted coordinator stays idle.
        for _ in range(int(move.STALLED_PROMISE_SECONDS) + 120):
            world.t += 1
            if world.t % 5 == 0:
                world.push_all('h', everyone=True)
            world.tick('h')
            world.observe()
            if world.head('h')['authority_epoch'] > 1 and world.head('h')['serving']:
                break
        assert world.head('h')['authority_epoch'] == 3, world.status('h')
        assert world.admitting() == {('h', 3)}
        proof = last_transition(world, 'h')['proof']
        assert proof['statement'] == succession.RECOVER_TEXT
        assert {world.by_id[r['custodian_install_id']] for r in proof['receipts']} == {'h', 'c'}
        _verify_proof(world, proof)
        # Producer success does not bypass the independent verifier's holder/quorum requirements.
        forged = {**proof, 'receipts': [r for r in proof['receipts']
                                       if r['custodian_install_id'] != world.gateways['c'].install_id]}
        with world.gateways['h'].acting():
            forged['signature'] = succession.sign(succession.ATTESTATION, succession._unsigned(forged))
        with pytest.raises(succession.ProofInvalid):
            _verify_proof(world, forged)
    finally:
        world.close()


@pytest.mark.parametrize('other_epoch', [3, 4])
def test_old_copy_with_conflicting_promise_cannot_witness_recovery(tmp_path, monkeypatch, other_epoch):
    world = _abandoned_majority_step(tmp_path, monkeypatch)
    try:
        _past_stall(world)
        candidate = world.gateways['c']
        with candidate.acting():
            fence.fence_and_promise(candidate.runs.path, room_id=ROOM, fence_epoch=other_epoch - 1,
                                    promise_epoch=other_epoch, candidate_install_id=world.gateways['d'].install_id)
        assert world.head('c')['authority_epoch'] == 1  # No transition is needed to withhold recovery.
        _check(world)
        assert world.head('h')['authority_epoch'] == 1 and not world.head('h')['serving']
        with world.gateways['h'].acting():
            assert succession.load_record(world.gateways['h'].db, ROOM, 'return')['unconfirmed'] == [candidate.install_id]
    finally:
        world.close()


def test_fresh_recovery_waits_for_the_abandoned_candidates_live_lease(tmp_path, monkeypatch):
    world = _abandoned_majority_step(tmp_path, monkeypatch)
    try:
        _past_stall(world)
        candidate = world.gateways['c']
        with candidate.acting():
            fence.grant_lease(candidate.runs.path, room_id=ROOM, epoch=2,
                              authority_install_id=candidate.install_id, duration_s=60)
        _check(world)
        assert world.head('h')['authority_epoch'] == 1
        world.t += 61
        _check(world)
        assert world.head('h')['authority_epoch'] > 1
    finally:
        world.close()


@pytest.mark.parametrize('obstacle', ['no_quorum', 'holder_offline', 'forged_holder'])
def test_missing_quorum_or_authenticated_holder_never_recovers(tmp_path, monkeypatch, obstacle):
    names = ('h', 'c', 'd', 'e', 'f') if obstacle == 'no_quorum' else ('h', 'c', 'd')
    world = _abandoned_majority_step(tmp_path, monkeypatch, names=names)
    try:
        _past_stall(world)
        if obstacle == 'holder_offline':
            world.network.stopped.add('c')
        elif obstacle == 'forged_holder':
            answer = backup.answer_query
            def unsigned_holder(context, request):
                result = answer(context, request)
                if result['responder_install_id'] == world.gateways['c'].install_id:
                    result['signature'] = 'forged'
                return result
            monkeypatch.setattr(backup, 'answer_query', unsigned_holder)
        _check(world)
        assert world.head('h')['authority_epoch'] == 1 and not world.head('h')['serving']
    finally:
        world.close()


def test_completed_candidate_transition_prevents_fresh_recovery(tmp_path, monkeypatch):
    world = _abandoned_majority_step(tmp_path, monkeypatch)
    try:
        _past_stall(world)
        owner_continues(world, 'c')
        assert world.head('c')['authority_epoch'] == 2
        host = world.gateways['h']
        with host.acting():
            heard = move.survey(world.context(host), ROOM, world.configuration('h'), from_epoch=1)
        assert heard[world.gateways['c'].install_id]['answer']['transition'] is not None
        _check(world)
        assert world.head('h')['authority_epoch'] == 1 and not world.head('h')['serving']
    finally:
        world.close()

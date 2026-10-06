"""Policy changes retain the previous authority guard until voters store the new choice."""
from contextlib import closing

import pytest

from gateway import hosted_room_custody as custody
from gateway import hosted_room_succession as succession
from gateway import hosted_rooms as rooms
from tests.gateway.fixtures.succession import ROOM
from tests.gateway.fixtures.succession_world import World


def test_disabling_automatic_during_partition_does_not_create_two_hosts(tmp_path, monkeypatch):
    world = World(tmp_path, monkeypatch, ('h', 'c', 'd'), voters=('h', 'c', 'd'))
    try:
        world.advance(10)
        assert world.admitting() == {('h', 1)}
        world.network.isolate('h')
        host = world.gateways['h']
        with host.acting():
            custody.set_automatic(host.db, room_id=ROOM, enabled=False)
        world.configure('h')
        assert world.configuration('h')['automatic'] is False
        with host.acting(), closing(rooms._read_connection(host.db)) as conn:
            assert custody.admission_mode_locked(conn, ROOM) == 'majority'
        world.advance(180)
        assert world.admitting() == {('c', 2)}
    finally:
        world.close()


def test_acknowledged_disable_releases_the_old_lease_requirement(tmp_path, monkeypatch):
    world = World(tmp_path, monkeypatch, ('h', 'c', 'd'), voters=('h', 'c', 'd'))
    try:
        world.advance(10)
        host = world.gateways['h']
        with host.acting():
            custody.set_automatic(host.db, room_id=ROOM, enabled=False)
        world.settle('h')
        with host.acting(), closing(rooms._read_connection(host.db)) as conn:
            assert custody.admission_mode_locked(conn, ROOM) == 'ask'
        world.network.isolate('h')
        world.advance(180)
        assert world.admitting() == {('h', 1)}
    finally:
        world.close()


def test_two_voters_ask_until_the_owner_explicitly_accepts_the_risk(tmp_path, monkeypatch):
    world = World(tmp_path, monkeypatch, ('h', 'c'), voters=('h', 'c'))
    try:
        assert world.status('h')['automatic']['mode'] == 'ask'
        world.network.stopped.add('h')
        world.advance(240)
        assert not world.head('c')['authoritative']
    finally:
        world.close()


def test_explicit_risk_consent_survives_takeover_and_switching_off_withdraws_it(tmp_path, monkeypatch):
    world = World(tmp_path, monkeypatch, ('h', 'c'), voters=('h', 'c'))
    try:
        host = world.gateways['h']
        view = world.status('h')['automatic']
        assert view['enabled'] is True and view['careful_opt_in'] is False
        assert view['reason'] == 'careful_confirmation_required'
        with host.acting():
            with pytest.raises(custody.CarefulConfirmationRequired):
                custody.set_automatic(host.db, room_id=ROOM, enabled=True)
            custody.set_automatic(host.db, room_id=ROOM, enabled=True, accept_two_host_risk=True)
        world.settle('h')
        assert world.status('c')['automatic']['mode'] == 'careful'
        world.network.stopped.add('h')
        world.until(lambda: world.head('c')['authoritative'], limit=300)
        candidate = world.gateways['c']
        with candidate.acting(), closing(rooms._read_connection(candidate.db)) as conn:
            assert custody.automatic_locked(conn, ROOM) and custody.careful_opt_in_locked(conn, ROOM)
        with candidate.acting():
            custody.set_automatic(candidate.db, room_id=ROOM, enabled=False)
            with pytest.raises(custody.CarefulConfirmationRequired):
                custody.set_automatic(candidate.db, room_id=ROOM, enabled=True)
    finally:
        world.close()


def test_an_ordinary_majority_preference_never_consents_to_a_two_voter_downgrade(tmp_path, monkeypatch):
    world = World(tmp_path, monkeypatch, ('h', 'c', 'd'), voters=('h', 'c', 'd'))
    try:
        world.advance(10)
        host = world.gateways['h']
        with host.acting():
            custody.set_automatic(host.db, room_id=ROOM, enabled=True)
            custody.designate_successor(host.db, room_id=ROOM, install_id=world.gateways['d'].install_id,
                                        successor=False)
        world.settle('h')
        view = world.status('h')['automatic']
        assert len(view['voters']) == 2 and view['mode'] == 'ask' and view['enabled'] is True
        assert view['careful_opt_in'] is False
        world.network.stopped.add('h')
        world.advance(240)
        assert not world.head('c')['authoritative']
    finally:
        world.close()


def _legacy_two_voter_world(tmp_path, monkeypatch, *, names=('h', 'c')):
    parse = custody.parse_configuration
    with monkeypatch.context() as old_writer:
        # Produce the exact legacy four-field wire payload; all stores, hashes, keys and replication
        # remain real. No historical signed bytes are edited after they have been written.
        old_writer.setattr(custody, 'parse_configuration', lambda payload: parse(
            {key: value for key, value in payload.items() if key != 'careful_opt_in'}))
        world = World(tmp_path, monkeypatch, names, voters=names)
    return world


def test_legacy_true_migrates_without_consent_or_rewriting_signed_history(tmp_path, monkeypatch):
    world = _legacy_two_voter_world(tmp_path, monkeypatch)
    try:
        host = world.gateways['h']
        with host.acting(), rooms._transaction(host.db, immediate=True) as conn:
            conn.execute(f'INSERT INTO {custody.SETTINGS_TABLE}(room_id, automatic, updated_at) VALUES (?,?,?)',
                         (ROOM, 1, world.wall0 + world.t))
            prefix = list(conn.execute('SELECT seq, payload_json FROM hosted_room_events WHERE room_id=? ORDER BY seq',
                                       (ROOM,)))
            last_seq = prefix[-1][0]
            digest = custody.chain_hash_locked(conn, ROOM, last_seq, store=False)
        assert world.configuration('h').get('careful_opt_in') is None
        world.settle('h')
        assert world.status('c')['automatic']['mode'] == 'ask'
        with host.acting(), closing(rooms._read_connection(host.db)) as conn:
            assert list(conn.execute('SELECT seq, payload_json FROM hosted_room_events WHERE room_id=? AND seq<=? '
                                     'ORDER BY seq', (ROOM, last_seq))) == prefix
            assert custody.chain_hash_locked(conn, ROOM, last_seq, store=False) == digest
            assert custody.automatic_locked(conn, ROOM) and not custody.careful_opt_in_locked(conn, ROOM)
    finally:
        world.close()


@pytest.mark.parametrize('names', [('h', 'c'), ('h', 'c', 'd')], ids=['prior-careful', 'prior-majority'])
def test_old_reader_rejecting_new_policy_cannot_promote_beside_the_upgraded_host(tmp_path, monkeypatch, names):
    world = _legacy_two_voter_world(tmp_path, monkeypatch, names=names)
    try:
        parse, mode, protection = custody.parse_configuration, custody.mode_of, custody.protection_locked
        def legacy_mode(configuration):
            if not configuration.get('automatic', True) or len(configuration.get('voters') or ()) < 2:
                return 'ask'
            return 'majority' if len(configuration['voters']) >= 3 else 'careful'
        def old_peer_reader(payload):
            if world.acting_name() != 'h' and 'careful_opt_in' in payload:
                host = next(item['install_id'] for item in payload['custodians'] if item['role'] == 'authority')
                if host == world.gateways[world.acting_name()].install_id:
                    # The old successor also writes the old four-field shape after its takeover.
                    payload = {key: value for key, value in payload.items() if key != 'careful_opt_in'}
                else:
                    raise custody.CustodyError('legacy configuration fields are invalid')
            return parse(payload)
        def mixed_protection(conn, room_id, host):
            result = protection(conn, room_id, host)
            if world.acting_name() != 'h':
                result['admission_mode'] = legacy_mode(result['configuration'])
            return result
        monkeypatch.setattr(custody, 'parse_configuration', old_peer_reader)
        monkeypatch.setattr(custody, 'mode_of', lambda value: mode(value) if world.acting_name() == 'h' else legacy_mode(value))
        monkeypatch.setattr(custody, 'protection_locked', mixed_protection)
        if len(names) == 3:
            with world.gateways['h'].acting():
                custody.designate_successor(world.gateways['h'].db, room_id=ROOM,
                    install_id=world.gateways['d'].install_id, successor=False)
        world.configure('h')
        assert world.configuration('h')['careful_opt_in'] is False
        assert not world.push('h', 'c')  # no durable ack and no fresh lease for the rejected page
        world.network.isolate('h')
        world.until(lambda: world.head('c')['authoritative'] and world.head('c')['serving'], limit=300, observe=True)
        assert world.admitting() == {('c', 2)}  # the old coordinator really promoted under its old rule
        assert not world.head('h')['serving'] and not world.send('h', 'must stay paused')
        assert world.configuration('c')['automatic'] is True  # the old risk-bearing policy is retained
        assert 'careful_opt_in' not in world.configuration('c')
    finally:
        world.close()


def test_ordinary_majority_configuration_keeps_the_legacy_wire_shape(tmp_path, monkeypatch):
    world = _legacy_two_voter_world(tmp_path, monkeypatch, names=('h', 'c', 'd'))
    try:
        world.advance(10)
        before = world.configuration('h')
        assert before['automatic'] is True and 'careful_opt_in' not in before
        assert world.configure('h') is None
        assert world.configuration('h') == before
        assert world.status('h')['automatic']['mode'] == 'majority'
        assert world.status('h')['automatic']['pending'] is None
    finally:
        world.close()


def _careful_proof(world):
    candidate = world.gateways['c']
    with candidate.acting(), closing(rooms._read_connection(candidate.db)) as conn:
        statement = succession.evidence_statement(conn, ROOM, from_epoch=1, to_epoch=2,
            successor=candidate.install_id, silent_since=world.wall0 + world.t - 180, silent_for_s=180)
        return {'statement': statement, 'signature': succession.sign(succession.EVIDENCE, statement)}


def test_historical_careful_proof_stays_readable_but_a_new_unconsented_proof_is_refused(tmp_path, monkeypatch):
    world = _legacy_two_voter_world(tmp_path, monkeypatch)
    try:
        candidate = world.gateways['c']
        historical, old_configuration = _careful_proof(world), world.configuration('c')
        world.settle('h')
        unconsented = _careful_proof(world)
        with candidate.acting(), closing(rooms._read_connection(candidate.db)) as conn:
            succession.verify_proof_locked(conn, ROOM, proof_kind='evidence', proof=historical,
                from_epoch=1, to_epoch=2, successor=candidate.install_id,
                fork_seq=historical['statement']['last_seq'], configuration=old_configuration)
            with pytest.raises(succession.ProofInvalid):
                succession.verify_proof_locked(conn, ROOM, proof_kind='evidence', proof=unconsented,
                    from_epoch=1, to_epoch=2, successor=candidate.install_id,
                    fork_seq=unconsented['statement']['last_seq'])
    finally:
        world.close()

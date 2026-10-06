"""Authentic signatures do not make malformed silence evidence valid."""
from contextlib import closing

import pytest

from gateway import hosted_rooms as rooms
from gateway import hosted_room_succession as succession
from tests.gateway.fixtures.succession import ROOM
from tests.gateway.test_hosted_room_succession_careful import world as world


@pytest.mark.parametrize('field,value', [
    ('silent_for_s', float('nan')), ('silent_for_s', float('inf')),
    ('silent_since', float('nan')), ('silent_since', float('inf')), ('silent_since', float('-inf')),
])
def test_nonfinite_silence_cannot_authorize_a_careful_transition(world, field, value):
    standby = world.gateways['s']
    with standby.acting(), closing(rooms._read_connection(standby.db)) as conn:
        statement = succession.evidence_statement(conn, ROOM, from_epoch=1, to_epoch=2,
            successor=standby.install_id, silent_since=world.wall0 + world.t - 180, silent_for_s=180)
        statement[field] = value
        proof = {'statement': statement, 'signature': succession.sign(succession.EVIDENCE, statement)}
        with pytest.raises(succession.ProofInvalid):
            succession.verify_proof_locked(conn, ROOM, proof_kind='evidence', proof=proof,
                from_epoch=1, to_epoch=2, successor=standby.install_id, fork_seq=statement['last_seq'])

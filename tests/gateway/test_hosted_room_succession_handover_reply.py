"""A lost handover reply never proves that the signed history stayed on the old host."""
import urllib.error

import pytest

from gateway import hosted_room_succession as succession
from gateway import hosted_room_succession_handover as handover
from gateway import hosted_room_succession_move as move
from tests.gateway.fixtures.succession import ROOM, context, copy_to, head, home_room, make_gateways


@pytest.mark.parametrize('failure', [
    ConnectionResetError('reply connection reset'),
    urllib.error.URLError('reply connection reset'),
    move.RemoteRefusal('http_500', None),
], ids=['reset', 'url-error', 'server-error'])
def test_handover_reply_failure_keeps_the_old_host_paused_until_signed_readback(tmp_path, failure):
    gateways = make_gateways(tmp_path, 'h', 's', 'p')
    h, s = gateways['h'], gateways['s']
    try:
        home_room(gateways, 'h', messages=3, successors=('s',))
        for name in ('s', 'p'):
            copy_to(h, gateways[name])
        # The standby can take the signed handover but cannot announce back to the old host.
        ctx = context(h, gateways, down=('h',))
        post = ctx.post
        def lost_reply(endpoint, path, body, timeout):
            result = post(endpoint, path, body, timeout)
            if path.endswith('/handover'):
                raise failure
            return result
        ctx.post = lost_reply
        with h.acting(), pytest.raises(succession.SuccessionError):
            handover.hand_over(ctx, ROOM, s.install_id)
        assert head(s)['authoritative'] and head(s)['authority_epoch'] == 2
        with h.acting():
            assert succession.paused_reason(h.db, ROOM) == 'room_authority_promised'
            assert not head(h)['serving']
            assert handover.recover(context(h, gateways), ROOM) is False
        assert not head(h)['authoritative']
        assert head(h)['authority_gateway_id'] == s.install_id
    finally:
        for gateway in gateways.values():
            gateway.close()


def test_a_delayed_signed_handover_cannot_revive_the_old_host_after_an_empty_query(tmp_path):
    gateways = make_gateways(tmp_path, 'h', 's', 'p')
    h, s = gateways['h'], gateways['s']
    delayed = []
    try:
        home_room(gateways, 'h', messages=3, successors=('s', 'p'))
        for name in ('s', 'p'):
            copy_to(h, gateways[name])
        ctx = context(h, gateways, down=('h',))
        post = ctx.post
        def retain_request(endpoint, path, body, timeout):
            if path.endswith('/handover'):
                delayed.append((endpoint, path, body, timeout))
                raise TimeoutError('request outcome unknown')
            return post(endpoint, path, body, timeout)
        ctx.post = retain_request
        with h.acting(), pytest.raises(succession.SuccessionError):
            handover.hand_over(ctx, ROOM, s.install_id)
        assert not head(s)['authoritative']
        with h.acting():
            with pytest.raises(succession.SuccessionError, match='previous signed handover'):
                handover.hand_over(ctx, ROOM, gateways['p'].install_id)
            assert len(delayed) == 1
            # The target's real signed query has no transition or promise yet. Recovery must not
            # resume the old epoch while this earlier signed request remains deliverable.
            assert handover.recover(context(h, gateways), ROOM) is False
            assert not head(h)['serving']
            assert handover.recover(context(h, gateways), ROOM) is False
            before = head(s)
            with pytest.raises(move.RemoteRefusal):
                post(*delayed[0])
        assert head(s) == before
        assert head(h)['authority_gateway_id'] == s.install_id
    finally:
        for gateway in gateways.values():
            gateway.close()

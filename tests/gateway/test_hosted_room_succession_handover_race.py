"""Signing and resuming an interrupted handover make opposing decisions in the same writer."""
import pytest

from gateway import hosted_room_succession as succession
from gateway import hosted_room_succession_handover as handover
from tests.gateway.fixtures.succession import ROOM, context, copy_to, head, home_room, make_gateways


@pytest.mark.parametrize('winner', ['sign', 'resume'])
def test_stale_recovery_and_completion_cannot_both_resume_and_send(tmp_path, monkeypatch, winner):
    gateways = make_gateways(tmp_path, 'h', 's', 'p')
    h, s = gateways['h'], gateways['s']
    try:
        home_room(gateways, 'h', messages=3, successors=('s',))
        for name in ('s', 'p'):
            copy_to(h, gateways[name])
        ctx = context(h, gateways, down=('h',))
        post, sent = ctx.post, []
        def lost_reply(endpoint, path, body, timeout):
            result = post(endpoint, path, body, timeout)
            if path.endswith('/handover'):
                sent.append(body)
                raise ConnectionResetError('lost reply after target committed')
            return result
        ctx.post = lost_reply
        with h.acting():
            current, target = handover._target(ctx, ROOM, s.install_id)
            record = handover._begin_handover(ctx, ROOM, s.install_id, current['head']['authority_epoch'], step='fencing')
            resume = handover._resume
            if winner == 'sign':
                def complete_before_resume(context, room_id, exc, *, expected):
                    with pytest.raises(succession.SuccessionError):
                        handover._complete(ctx, ROOM, s.install_id, target, record, 0)
                    assert head(s)['authoritative'] and head(s)['authority_epoch'] == 2
                    return resume(context, room_id, exc, expected=expected)
                monkeypatch.setattr(handover, '_resume', complete_before_resume)
                assert handover.recover(ctx, ROOM) is False
                assert not head(h)['serving']
                assert succession.load_record(h.db, ROOM, 'move')['state'] == 'handing_over'
                assert len(sent) == 1
            else:
                signed_over = handover.signed_over
                def resume_before_signature(context, conn, room_id, **params):
                    assert handover.recover(ctx, ROOM) is True
                    return signed_over(context, conn, room_id, **params)
                monkeypatch.setattr(handover, 'signed_over', resume_before_signature)
                # recover normally skips the in-process active set; model a recovery worker that
                # already read the unsigned record before completion marked itself active.
                def resume_unsigned(context, room_id):
                    return resume(context, room_id, succession.SuccessionError('interrupted'),
                                  expected=succession.load_record(h.db, ROOM, 'move'))
                monkeypatch.setattr(handover, 'recover', resume_unsigned)
                with pytest.raises(succession.SuccessionError):
                    handover._complete(ctx, ROOM, s.install_id, target, record, 0)
                assert sent == [] and not head(s)['authoritative']
                assert head(h)['serving']
    finally:
        for gateway in gateways.values():
            gateway.close()

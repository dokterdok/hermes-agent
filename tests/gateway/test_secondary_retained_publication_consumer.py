"""A3 consumer of the Output secondary retained-publication contract.

The invitation→NEW-run RPC calls the contract. Without those methods the
consumer fails closed. With them, registration, publish, retry, and completion
keep the owner's provenance, and a blocked authorization failure stays blocked.
Send-consent is not publication authority.
"""
import json
import time

import pytest

from gateway.hosted_room_artifacts import RoomArtifactError


def _fixtures():
    from tests.gateway.test_secondary_retained_publication import (
        _primary, _secondary_counts, _settled)
    return _primary, _secondary_counts, _settled


def _rpc(service):
    from gateway.session_hosted_rpc import HostedRoomAuthorityRPC
    found = [rpc for rpc in service.member_rpcs.values() if type(rpc) is HostedRoomAuthorityRPC]
    assert len(found) == 1
    return found[0]


def _consume(service, task, **kwargs):
    return _rpc(service).publish_secondary_retained(task, **kwargs)


@pytest.mark.asyncio
async def test_consumer_publish_retry_and_completion_keep_provenance(tmp_path, monkeypatch):
    _primary, _secondary_counts, _settled = _fixtures()
    async for authority, service, runner, task in _settled(tmp_path, monkeypatch):
        before = _primary(authority.db)
        pointer = runner.session_authority
        clock = {'now': time.time()}
        service._artifact_clock = lambda: clock['now']
        published = _consume(service, task)
        assert published['accepted'] is True and published['published'] is True
        assert published['completed'] is False and published['attempt'] == 1
        assert published['provenance']['owner_epoch'] == service._output_epoch
        assert published['provenance']['owner_instance'] == service._output_instance
        assert published['provenance']['publication']
        assert published['valid_until'] == published['provenance']['valid_until']
        publication_id = published['publication_id']
        again = _consume(service, task)
        assert again['publication_id'] == publication_id
        assert again['attempt'] == 1 and again['valid_until'] == published['valid_until']
        failed = _consume(
            service, task, publication_id=publication_id, transport_error=ConnectionError('reset'))
        assert failed['accepted'] is False and failed['blocked'] is False
        assert failed['reason_code'] == 'transient'
        assert failed['provenance'] == published['provenance']
        assert 'reset' not in json.dumps(failed['provenance'])
        early = _consume(service, task, publication_id=publication_id)
        assert early['accepted'] is False and early['attempt'] == 1
        clock['now'] = failed['next_attempt_at'] + 1
        retried = _consume(service, task, publication_id=publication_id)
        assert retried['accepted'] is True and retried['published'] is True
        assert retried['attempt'] == 2
        assert retried['provenance']['publication'] == published['provenance']['publication']
        assert retried['valid_until'] == published['valid_until']
        completed = _consume(service, task, publication_id=publication_id, confirm=True)
        assert completed['completed'] is True
        assert completed['event_digest'] == published['provenance']['publication']
        assert completed['valid_until'] == published['valid_until']
        assert completed['provenance']['work'] == published['provenance']['work']
        assert completed['provenance']['route'] == published['provenance']['route']
        assert completed['provenance']['member_id'] == published['provenance']['member_id']
        assert _secondary_counts(authority.db) == (0, 1)
        reopened = _consume(service, task)
        assert reopened['completed'] is True and _secondary_counts(authority.db) == (0, 1)
        assert _primary(authority.db) == before
        assert runner.session_authority is pointer is authority
        assert authority.hosted_room_service is service


@pytest.mark.asyncio
async def test_consumer_rejects_consent_and_forged_routes(tmp_path, monkeypatch):
    _primary, _secondary_counts, _settled = _fixtures()
    async for authority, service, runner, task in _settled(tmp_path, monkeypatch):
        before = _primary(authority.db)
        changes = authority.db._conn.total_changes
        with pytest.raises(RoomArtifactError, match='send consent is not publication authority'):
            _consume(service, task, consent={'permissions': ['publish'], 'expires_at': 10 ** 12})
        assert authority.db._conn.total_changes == changes
        with pytest.raises(RoomArtifactError, match='route is unauthorized'):
            _consume(service, task, route='forged-route')
        assert _secondary_counts(authority.db) == (0, 0)
        assert _primary(authority.db) == before
        assert runner.session_authority is authority
        assert authority.hosted_room_service is service


@pytest.mark.asyncio
async def test_consumer_expiry_does_not_extend_the_grant(tmp_path, monkeypatch):
    _primary, _secondary_counts, _settled = _fixtures()
    async for authority, service, runner, task in _settled(tmp_path, monkeypatch):
        before = _primary(authority.db)
        clock = {'now': time.time()}
        service._artifact_clock = lambda: clock['now']
        published = _consume(service, task)
        horizon = published['valid_until']
        publication_id = published['publication_id']
        clock['now'] = horizon + 1
        expired = _consume(service, task, publication_id=publication_id)
        assert expired['accepted'] is False and expired['published'] is False
        assert expired['blocked'] is True and expired['reason_code'] == 'expired_grant'
        assert expired['valid_until'] == horizon and expired['attempt'] == 1
        with pytest.raises(RoomArtifactError, match='completion refused'):
            _consume(service, task, publication_id=publication_id, confirm=True)
        with pytest.raises(RoomArtifactError, match='lifetime expired'):
            _consume(service, task)
        assert _secondary_counts(authority.db) == (1, 0)
        assert _primary(authority.db) == before
        assert runner.session_authority is authority


@pytest.mark.asyncio
async def test_consumer_blocked_authorization_stays_blocked(tmp_path, monkeypatch):
    _primary, _secondary_counts, _settled = _fixtures()
    async for authority, service, runner, task in _settled(tmp_path, monkeypatch):
        before = _primary(authority.db)
        clock = {'now': time.time()}
        service._artifact_clock = lambda: clock['now']
        published = _consume(service, task)
        publication_id = published['publication_id']
        blocked = _consume(
            service, task, publication_id=publication_id,
            transport_error=RoomArtifactError('denied'))
        assert blocked['blocked'] is True
        assert blocked['reason_code'] == 'authorization_or_verification'
        later = _consume(
            service, task, publication_id=publication_id,
            transport_error=ConnectionError('reset'))
        assert later['blocked'] is True
        assert later['reason_code'] == 'authorization_or_verification'
        clock['now'] = blocked['next_attempt_at'] + 1
        still = _consume(service, task, publication_id=publication_id)
        assert still['accepted'] is False and still['blocked'] is True
        assert still['reason_code'] == 'authorization_or_verification'
        with pytest.raises(RoomArtifactError, match='completion refused'):
            _consume(service, task, publication_id=publication_id, confirm=True)
        assert _secondary_counts(authority.db) == (1, 0)
        assert _primary(authority.db) == before
        assert runner.session_authority is authority
        assert authority.hosted_room_service is service


def test_consumer_fails_closed_when_the_contract_is_missing():
    from gateway.session_hosted_output_secondary_consumer import (
        consume_secondary_retained_publication)

    class Bare:
        pass

    service = Bare()
    with pytest.raises(RoomArtifactError, match='not registered'):
        consume_secondary_retained_publication(service, {'payload': {}})
    seen = []

    def refuse(task, consent):
        seen.append(consent)
        raise RoomArtifactError('Group Chat send consent is not publication authority')

    service.publish_secondary_from_consent = refuse
    with pytest.raises(RoomArtifactError, match='send consent is not publication authority'):
        consume_secondary_retained_publication(service, {'payload': {}}, consent={'ok': True})
    assert seen == [{'ok': True}]

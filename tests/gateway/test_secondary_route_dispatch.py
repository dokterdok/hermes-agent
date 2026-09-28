"""Private secondary-recipient verbs retain the hosted route's source and target guards."""

import pytest

from tests.gateway.test_hosted_mux_runtime import mux  # noqa: F401; registered owners and real socket


@pytest.mark.live_system_guard_bypass
def test_secondary_verbs_refuse_unbound_source_and_forged_target_before_recipient_write(mux):
    from gateway.session_authorities import owner_scope
    from gateway.session_hosted_service import ensure_hosted_service
    from gateway.session_hosted_transport import owner_request
    from hermes_state_runtime import RuntimeStoreError

    runner, homes, _, call = mux
    call(ensure_hosted_service(runner))
    source = runner.session_authorities.require(homes['default'])
    target = runner.session_authorities.require(homes['beta'])
    with owner_scope(source):
        service = source.hosted_room_service
        service.authorize_room('alice', 'route-recipient', create=True)
        service.create_room(room_id='route-recipient', name='Route recipient', members=[
            {'member_id': 'host', 'profile': 'default', 'handle': 'host'},
            {'member_id': 'helper', 'profile': 'beta', 'handle': 'helper'}])

    selector = dict(room_id='route-recipient', member_id='helper', profile='beta')
    params = dict(publication_id='not-registered', task_id='not-settled', execution_generation=1)
    def target_call(*, source_home=homes['default'], operation='secondary_receipt',
                    selected=selector, target_home=homes['beta'], payload=params):
        return owner_request(target_home, 'hosted-producer', dict(
            source_home=str(source_home), selector=selected,
            operation=operation, params=payload))

    def recipient_rows():
        with target.db._read_ctx() as conn:
            if conn.execute("SELECT 1 FROM sqlite_master WHERE name='hosted_room_recipient_receipts'").fetchone():
                return conn.execute('SELECT COUNT(*) FROM hosted_room_recipient_receipts').fetchone()[0]
            return 0
    before = recipient_rows()
    for operation in ('secondary_deliver', 'secondary_receipt'):
        with pytest.raises(RuntimeStoreError, match='permission_denied'):
            target_call(operation=operation)
    # A direct private attest still refuses a chunk without a current publication.
    with pytest.raises(RuntimeStoreError, match='permission_denied'):
        owner_request(homes['default'], 'hosted-attest', dict(
            selector=selector, operation='secondary_chunk',
            params={**params, 'index': 0, 'offset': 0, '_target_home': str(homes['beta'])}))
    with pytest.raises(RuntimeStoreError, match='profile_mismatch'):
        target_call(target_home=homes['default'])
    with pytest.raises(RuntimeStoreError, match='permission_denied'):
        target_call(source_home=homes['alpha'])
    with pytest.raises(RuntimeStoreError, match='permission_denied'):
        target_call(selected={**selector, 'room_id': 'another-room'})
    with pytest.raises(RuntimeStoreError, match='permission_denied'):
        target_call(selected={**selector, 'member_id': 'host'})
    with pytest.raises(RuntimeStoreError, match='invalid_params'):
        target_call(operation='secondary_chunk')
    with pytest.raises(RuntimeStoreError, match='invalid_params'):
        target_call(operation='secondary_unknown')
    assert recipient_rows() == before

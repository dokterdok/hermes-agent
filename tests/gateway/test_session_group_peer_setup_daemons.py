"""Desktop's one-time setup of a Bot from another gateway (create on the home, invite on the Bot's
gateway, register on the home), stopped between its steps, on two real gateways.

The setup holds nothing durable on the client: the room and the route live on the home, the grant's
scope on the member's gateway. A setup that stops after the invitation leaves a room whose member
has not joined, says so, and is seated by the same steps run again."""
import asyncio
from pathlib import Path
import socket

from tests.gateway.fixtures.local_recovery_probe import daemon, rpc, websocket
from tests.gateway.test_session_group_peer_daemons import (
    _events, _gateway, _grant_status, _last_user_text, _model, _room_link, _send)

# What Desktop asks for: the longest dispatch lifetime and renewal horizon a member gateway grants.
_LIFETIMES = {'ttl_seconds': 86400, 'status_ttl_seconds': 30 * 86400}


async def _invite(target_ws, room):
    invited = await rpc(target_ws, 'groups.peer.invite', room_id=room['room_id'], member_id='reviewer',
                        home_install_id=room['authority_gateway_id'],
                        authority_gateway_id=room['authority_gateway_id'],
                        authority_epoch=room['authority_epoch'], **_LIFETIMES)
    assert invited['result']['grant'], invited
    return invited['result']


def test_a_setup_stopped_between_its_steps_leaves_a_visible_member_that_running_it_again_seats(tmp_path):
    root = Path(__file__).resolve().parents[2]
    home_model, target_model = _model('HOME_REPLY'), _model('PEER_REPLY')
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        api_port = sock.getsockname()[1]
    home, home_env = _gateway(tmp_path, 'home', home_model, root)
    target, target_env = _gateway(tmp_path, 'target', target_model, root, api_port=api_port)
    setup = {}

    def register(home_ws, grant):
        return rpc(home_ws, 'groups.peer.register', room_id='linked', member_id='reviewer',
                   target_url=setup['url'], target_profile='default', grant=grant, catalog=setup['catalog'])

    async def routes(home_ws):
        return (await rpc(home_ws, 'groups.state', room_id='linked'))['result']['driver_status']['peer_routes']

    async def create_then_invite(home_desc, target_desc):
        async with websocket(home, home_desc) as home_ws, websocket(target, target_desc) as target_ws:
            link = await _room_link(target_ws)
            offered = (await rpc(target_ws, 'groups.capabilities'))['result']
            assert 'groups.peer.invite' in offered['methods'] and 'peer_renewal_horizon' in offered['features']
            assert 'groups.peer.register' in (await rpc(home_ws, 'groups.capabilities'))['result']['methods']
            catalog = link['catalog']
            pinned = {'kind': 'peer', 'peer_id': catalog['installation_id'], 'installation_id': catalog['installation_id'],
                      'profile': 'default', 'capability_digest': catalog['catalog_digest']}
            created = await rpc(home_ws, 'groups.create', room_id='linked', name='Linked', members=[
                {'member_id': 'host', 'profile': 'default', 'handle': 'host'},
                {'member_id': 'reviewer', 'profile': 'default', 'handle': 'reviewer', 'target': pinned}])
            setup.update(room=created['result']['room'], catalog=catalog, url=link['endpoint']['url'])
            # Desktop quits with the grant in hand: nothing registers it, and nothing else holds it.
            setup['orphan'] = (await _invite(target_ws, setup['room']))['grant']

    async def run_again_with_the_member_gateway_away(home_desc, target_desc, target_proc):
        async with websocket(home, home_desc) as home_ws:
            # The home restarted too. The room is there, its member has not joined, and a turn for
            # it fails visibly instead of waiting on a member that never comes.
            assert await routes(home_ws) == []
            await _send(home_ws, 'early', '@reviewer EARLY')
            failed, = await _events(home_ws, 'turn.failed')
            assert failed['payload']['error'] == 'peer room route is unavailable', failed
            # The orphaned grant does not block inviting the same member of the same room again.
            async with websocket(target, target_desc) as target_ws:
                setup['grant'] = (await _invite(target_ws, setup['room']))['grant']
            assert setup['grant'] != setup['orphan']
            # The member's gateway goes away before registration: the home cannot reach it, refuses
            # without echoing the grant, and the member stays not joined.
            target_proc.kill()
            await asyncio.to_thread(target_proc.wait, 10)
            refused = await register(home_ws, setup['grant'])
            assert refused['error']['message'] == 'peer_unreachable', refused
            assert setup['grant'] not in str(refused)
            assert await routes(home_ws) == []

    async def register_after_the_member_gateway_restarts(home_desc):
        async with websocket(home, home_desc) as home_ws:
            # The grant outlives its gateway's restart: registering it again seats the member.
            registered = await register(home_ws, setup['grant'])
            assert registered['result']['registered'], registered
            assert await routes(home_ws) == [{'room_id': 'linked', 'member_id': 'reviewer', 'status': 'ready'}]
            await _send(home_ws, 'ask', '@reviewer PEER_PROOF')
            reply, = await _events(home_ws, 'message.member')
            assert (reply['payload']['member_id'], reply['payload']['text']) == ('reviewer', 'PEER_REPLY'), reply
            assert reply['actor']['connection_id'] == setup['catalog']['installation_id'], reply
            assert [_last_user_text(r) for r in target_model.requests if 'EARLY' in _last_user_text(r)] == []
            assert sum('PEER_PROOF' in _last_user_text(r) for r in target_model.requests) == 1
            # The orphan stays valid until its lifetime ends or its scope is revoked; Disband revokes
            # the member's whole scope on its gateway, the orphan with the registered grant.
            assert (await asyncio.to_thread(_grant_status, setup['url'], setup['orphan']))[0] == 200
            disbanded = await rpc(home_ws, 'groups.disband', room_id='linked')
            assert 'tombstone' in disbanded['result'], disbanded
            for grant in (setup['orphan'], setup['grant']):
                status, body = await asyncio.to_thread(_grant_status, setup['url'], grant)
                assert (status, body['error']['code']) == (403, 'room_reauthorization_required'), body

    try:
        with daemon(root, target, target_env, barrier=False) as (target_proc, target_desc):
            with daemon(root, home, home_env, barrier=False) as (home_proc, home_desc):
                asyncio.run(create_then_invite(home_desc, target_desc))
                home_proc.kill()
            with daemon(root, home, home_env, barrier=False) as (_, home_desc):
                asyncio.run(run_again_with_the_member_gateway_away(home_desc, target_desc, target_proc))
                with daemon(root, target, target_env, barrier=False):
                    asyncio.run(register_after_the_member_gateway_restarts(home_desc))
    finally:
        for model in (home_model, target_model):
            model.shutdown()
            model.server_close()

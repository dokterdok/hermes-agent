"""Review probe (claude) for PR #140972 RT-38: the authority's profiles.list on a
secondary profile's own gateway names every profile once, the authority's row first."""
import asyncio
from types import SimpleNamespace

import pytest

from gateway.session_controls import AuthorityConnection
from hermes_state import SessionDB


@pytest.mark.parametrize('process_home', ['root', 'writer'])
def test_writer_gateway_roster_names_each_profile_once(tmp_path, monkeypatch, process_home):
    root = tmp_path / 'state'
    for name in ('helper', 'writer'):
        (root / 'profiles' / name).mkdir(parents=True)
        (root / 'profiles' / name / 'config.yaml').write_text('model:\n  default: m\n')
    (root / 'config.yaml').write_text('model:\n  default: fixture-model\n')
    writer = root / 'profiles' / 'writer'
    # A per-profile launchd gateway runs with HERMES_HOME=<root>/profiles/writer; a
    # multiplexer serving writer as a secondary runs with HERMES_HOME=<root>.
    monkeypatch.setenv('HERMES_HOME', str(root if process_home == 'root' else writer))
    monkeypatch.setenv('HOME', str(tmp_path / 'user'))
    with SessionDB(writer / 'state.db') as db:
        authority = SimpleNamespace(profile_id=str(writer), instance_id='owner', db=db, events={}, sessions={})

        async def probe():
            connection = AuthorityConnection(authority, object(), {'user_id': 'reader'})
            return await connection.dispatch({'id': 'r', 'method': 'profiles.list', 'params': {}})
        result = asyncio.run(probe())
    assert 'result' in result, result
    names = [row['name'] for row in result['result']['profiles']]
    assert names == ['writer', 'default', 'helper'], names
    assert result['result']['install_id']

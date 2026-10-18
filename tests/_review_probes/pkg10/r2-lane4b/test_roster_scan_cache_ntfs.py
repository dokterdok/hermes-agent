"""Probe: the profiles.list disk-scan cache (c2846b54bbe) keys on os.scandir entry mtimes.
On Windows those come from the parent directory index, which NTFS does not update when a
child of the subdirectory is created, so a new identity marker or tombstone is not seen."""
import asyncio
from types import SimpleNamespace

from gateway.session_controls import AuthorityConnection
from hermes_constants import mark_named_profile_deleted
from hermes_state import SessionDB


def _home(tmp_path, monkeypatch):
    home = tmp_path / 'state'
    (home / 'profiles' / 'helper').mkdir(parents=True)
    (home / 'profiles' / 'helper' / 'config.yaml').write_text('model:\n  default: m\n')
    (home / 'profiles' / '.deleted').mkdir()          # an earlier delete left the tombstone dir
    (home / 'profiles' / 'scout' / 'logs').mkdir(parents=True)  # marker-less ghost shell
    monkeypatch.setenv('HERMES_HOME', str(home))
    monkeypatch.setenv('HOME', str(tmp_path / 'user'))
    (home / 'config.yaml').write_text('model:\n  default: fixture-model\n')
    return home


def _names(home):
    with SessionDB(home / 'state.db') as db:
        authority = SimpleNamespace(profile_id=str(home), instance_id='owner', db=db, events={}, sessions={})

        async def probe():
            connection = AuthorityConnection(authority, object(), {'user_id': 'reader'})
            return await connection.dispatch({'id': 'r', 'method': 'profiles.list', 'params': {}})
        return [row['name'] for row in asyncio.run(probe())['result']['profiles']]


def test_probe_identity_marker_in_existing_ghost_dir_is_listed(tmp_path, monkeypatch):
    home = _home(tmp_path, monkeypatch)
    assert 'scout' not in _names(home)
    # `hermes profile create scout` into the ghost shell: the marker appears inside it.
    (home / 'profiles' / 'scout' / 'config.yaml').write_text('model:\n  default: m\n')
    assert 'scout' in _names(home)


def test_probe_tombstoned_profile_leaves_the_roster(tmp_path, monkeypatch):
    home = _home(tmp_path, monkeypatch)
    assert 'helper' in _names(home)
    # delete_profile tombstones first; on Windows rmtree can fail (WinError 32) and leave the dir.
    mark_named_profile_deleted(home / 'profiles' / 'helper')
    assert 'helper' not in _names(home)

"""Proof-only inert guard regression on the preserved newer CLI dispatcher."""
from types import SimpleNamespace

from hermes_cli import sessions_cmd
from hermes_state import SessionDB


def test_held_store_prune_refuses_before_handler_on_temporary_store(tmp_path, monkeypatch, capsys):
    import hermes_state_holders

    home = tmp_path / 'home'
    home.mkdir()
    monkeypatch.setenv('HERMES_HOME', str(home))
    with SessionDB(home / 'state.db') as db:
        db.create_session('retained', source='cli')
    seen = []

    def held(path, *, command):
        seen.append((path, command))
        return 'fixture: held store'

    def must_not_mutate(*args, **kwargs):
        raise AssertionError('prune handler reached a held store')

    monkeypatch.setattr(hermes_state_holders, 'held_store_refusal', held)
    monkeypatch.setitem(sessions_cmd._DB_HANDLERS, 'prune', must_not_mutate)
    assert sessions_cmd.cmd_sessions(SimpleNamespace(sessions_action='prune', dry_run=False,
                                                    force=False)) == 1
    assert seen == [(home / 'state.db', 'prune')]
    assert 'fixture: held store' in capsys.readouterr().out
    with SessionDB(home / 'state.db', read_only=True) as db:
        assert db.get_session('retained') is not None

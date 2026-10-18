"""A secondary profile's /bg must not reuse the default profile's cached workspace env."""
from types import SimpleNamespace

from tests.gateway.test_docker_session_workspace import home, _policy, _turn, _plan


def test_secondary_side_agent_does_not_reuse_default_profiles_workspace(home, tmp_path, monkeypatch):
    from gateway.session_tui_side import _side_scope
    from tools import terminal_tool
    from tools.terminal_tool_lifecycle import get_active_env

    workspace = tmp_path / 'shared-project'
    workspace.mkdir()
    beta_home = home / 'profiles' / 'beta'
    beta_home.mkdir(parents=True)
    default_policy = _policy(home, workspace, persistent=True)
    beta_policy = _policy(beta_home, workspace, persistent=True)
    monkeypatch.setattr(terminal_tool, '_active_environments', {})
    monkeypatch.setattr(terminal_tool, '_last_activity', {})
    with _turn(default_policy, 'default-session'):
        default_key = _plan('default-session')[0]
    default_environment = SimpleNamespace(profile_owner='default', cwd='/workspace', env_type='docker',
                                          host_cwd=str(workspace))
    terminal_tool._active_environments[default_key] = default_environment
    authority = SimpleNamespace(profile_id=str(beta_home),
                                runner=SimpleNamespace(config=SimpleNamespace(multiplex_profiles=True)))
    with _side_scope(authority, beta_policy, 'bg_beta'):
        actual_key = _plan('bg_beta')[0]
        selected_environment = get_active_env('bg_beta')
    assert selected_environment is not default_environment, (
        'secondary profile selected default profile environment', actual_key, default_key)

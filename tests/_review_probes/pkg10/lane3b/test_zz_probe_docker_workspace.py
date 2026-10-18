"""Review probes: real policy, planner, cache and delegation; sandbox creation is a recorder.

On main the absent turn-binding hook is a no-op, matching main's turn runner.
No Docker/SSH daemon or provider is contacted.
"""
import json
from contextlib import contextmanager
from types import SimpleNamespace

import pytest


@pytest.fixture
def terminal(monkeypatch):
    from tools import terminal_tool as tt
    from tools import terminal_tool_lifecycle as lifecycle
    for name in ('_task_env_overrides', '_session_cwd', '_container_aliases',
                 '_active_environments', '_last_activity', '_creation_locks'):
        monkeypatch.setattr(tt, name, {})
    monkeypatch.setattr(tt, '_start_cleanup_thread', lambda: None)
    created = []

    def factory(config, env_type, **kw):
        env = SimpleNamespace(env_type=env_type, **kw)
        created.append(env)
        return env

    monkeypatch.setattr(tt, '_create_configured_env', factory)
    monkeypatch.setattr(lifecycle, '_create_configured_env', factory)
    return tt, lifecycle, created


def policy(home, cwd, **terminal):
    from gateway.session_policy import build_policy
    (home / 'config.yaml').write_text(json.dumps({'terminal': terminal}), encoding='utf-8')
    return build_policy({'source': 'cli', 'cwd': str(cwd), 'model': 'm'}, {})


@contextmanager
def turn(policy, sid):
    from gateway import session_policy as sp
    from gateway.session_context import set_session_vars, clear_session_vars
    with sp.policy_scope(policy):
        tokens = set_session_vars(session_key=f'agent:main:local:dm:{sid}')
        try:
            if hasattr(sp, 'bind_turn_workspace'):
                sp.bind_turn_workspace(policy, sid)
            yield
        finally:
            clear_session_vars(tokens)


def plan(tt, sid):
    return tt._plan_execution('pwd', task_id=sid, timeout=None, background=False, _host_local=False)


def test_delegate_reuses_created_parent_workspace_container(terminal, tmp_path):
    from hermes_constants import get_hermes_home
    from tools.delegate_tool_child_run import _ChildRun
    tt, lifecycle, created = terminal
    p = policy(get_hermes_home(), tmp_path, backend='docker',
               docker_mount_cwd_to_workspace=True, container_persistent=True)
    with turn(p, 'parent'):
        parent = tt._acquire_env(plan(tt, 'parent'), 'parent')
        child_run = _ChildRun(child=SimpleNamespace(), parent_agent=SimpleNamespace(_current_task_id='parent'),
                              task_index=0, goal='inspect project', subagent_id='child', child_progress_cb=None)
        child_run.seed_workspace()
        child = tt._acquire_env(plan(tt, 'child'), 'child')
        assert child is parent, [(env.task_id, env.host_cwd) for env in created]


def test_lazy_workspace_setup_remains_findable_with_profile_container_present(terminal, tmp_path):
    from hermes_constants import get_hermes_home
    tt, lifecycle, created = terminal
    p = policy(get_hermes_home(), tmp_path, backend='docker',
               docker_mount_cwd_to_workspace=True, container_persistent=True)
    with turn(p, 'workspace-session'):
        # A messaging task has no local-session workspace binding.
        tt._acquire_env(plan(tt, 'messaging-task'), 'messaging-task')
        returned = lifecycle.ensure_task_env('workspace-session')
        assert lifecycle.get_active_env('workspace-session') is returned, (
            returned.task_id, [env.task_id for env in created])


@pytest.mark.asyncio
async def test_resuming_docker_as_ssh_uses_new_remote_cwd(terminal, tmp_path, monkeypatch):
    from gateway.config import GatewayConfig
    from gateway.session import SessionStore
    from gateway.session_authority import initialize_session_authority
    from gateway.session_controls import AuthorityConnection
    from tests.gateway.test_session_resume_config_refresh import _home, _runner, _call, _policy
    tt, lifecycle, created = terminal
    home = _home(monkeypatch)
    model = {'model': {'default': 'm1', 'provider': 'custom', 'base_url': 'http://127.0.0.1:9/v1'}}
    (home / 'config.yaml').write_text(json.dumps({**model, 'terminal': {
        'backend': 'docker', 'docker_mount_cwd_to_workspace': True, 'container_persistent': True}}))
    store = SessionStore(home / 'sessions', GatewayConfig())
    authority = await initialize_session_authority(_runner(store), profile_id=str(home), instance_id='review')
    owner = AuthorityConnection(authority, object(), {'user_id': 'human'})
    viewer = AuthorityConnection(authority, object(), {'user_id': 'human'})
    try:
        sid = (await _call(owner, 'session.create', request_id='create', source='cli', cwd=str(tmp_path)))['session_id']
        with turn(_policy(authority, sid), sid):
            plan(tt, sid)
        (home / 'config.yaml').write_text(json.dumps({**model, 'terminal': {
            'backend': 'ssh', 'cwd': '/srv/project', 'ssh_host': 'example.invalid'}}))
        await _call(viewer, 'session.resume', session_id=sid)
        with turn(_policy(authority, sid), sid):
            execution = plan(tt, sid)
            assert execution.env_type == 'ssh'
            assert execution.cwd == '/srv/project', (execution.cwd, tt.resolve_task_overrides(sid))
    finally:
        await viewer.close()
        await owner.close()
        store.close_all_db_handles()

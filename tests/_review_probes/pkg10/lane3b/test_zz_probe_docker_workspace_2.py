"""Review probes (lane 3b, CLI-39): per-(profile, workspace) persistent Docker keys.

Same harness as tests/gateway/test_docker_session_workspace.py: a real frozen policy built from a
temp HERMES_HOME config.yaml, the turn's real ``policy_scope`` + session vars, and the turn's real
``bind_turn_workspace``. On a tree without ``bind_turn_workspace`` (main) the binding is a no-op,
which is exactly what a gateway turn does there.
"""
import json
from dataclasses import asdict
from types import SimpleNamespace

import pytest


@pytest.fixture
def home(tmp_path, monkeypatch):
    from tools import terminal_tool as tt
    home = tmp_path / 'home'
    home.mkdir()
    monkeypatch.setenv('HERMES_HOME', str(home))
    for name in ('TERMINAL_ENV', 'TERMINAL_CWD', 'TERMINAL_CONTAINER_PERSISTENT',
                 'TERMINAL_DOCKER_MOUNT_CWD_TO_WORKSPACE'):
        monkeypatch.delenv(name, raising=False)
    tables = (tt._task_env_overrides, tt._session_cwd, tt._container_aliases, tt._active_environments,
              tt._last_activity, tt._creation_locks)
    saved = [dict(t) for t in tables]
    monkeypatch.setattr(tt, '_start_cleanup_thread', lambda: None)
    yield home
    for table, snapshot in zip(tables, saved):
        table.clear()
        table.update(snapshot)


def _policy(home, cwd, terminal):
    from gateway.session_policy import build_policy, restore_policy
    (home / 'config.yaml').write_text(json.dumps({'terminal': terminal}), encoding='utf-8')
    return restore_policy(asdict(build_policy({'source': 'cli', 'cwd': str(cwd), 'model': 'm'}, {})))


def _docker(home, cwd, persistent=True):
    return _policy(home, cwd, {'backend': 'docker', 'docker_mount_cwd_to_workspace': True,
                               'container_persistent': persistent})


def _turn(policy, session_id):
    from contextlib import ExitStack
    from gateway import session_policy
    from gateway.session_context import clear_session_vars, set_session_vars
    stack = ExitStack()
    stack.enter_context(session_policy.policy_scope(policy))
    tokens = set_session_vars(session_key=f'agent:main:local:dm:{session_id}')
    stack.callback(clear_session_vars, tokens)
    bind = getattr(session_policy, 'bind_turn_workspace', None)
    if bind is not None:
        bind(policy, session_id)
    return stack


def _spawn_child(parent_task_id, subagent_id):
    """delegate_task's real spawn-site seeding (cwd record + container alias)."""
    from tools.delegate_tool_child_run import _ChildRun
    run = _ChildRun(child=SimpleNamespace(), parent_agent=SimpleNamespace(_current_task_id=parent_task_id),
                    task_index=0, goal='g', subagent_id=subagent_id, child_progress_cb=None)
    run.seed_workspace()
    return run.child_task_id


# G2 ---------------------------------------------------------------------------------------------

def test_g2_delegate_child_shares_its_parents_persistent_container(home, tmp_path):
    from tools.terminal_tool import _resolve_container_task_id
    ws1, ws2 = tmp_path / 'ws1', tmp_path / 'ws2'
    ws1.mkdir(), ws2.mkdir()
    keys = {}
    for sid, ws in (('local-a', ws1), ('local-b', ws2)):
        with _turn(_docker(home, ws), sid):
            child = _spawn_child(sid, f'sub-{sid}')
            keys[sid] = (_resolve_container_task_id(sid), _resolve_container_task_id(child))
    # A delegate shares its parent's container (the #16177 contract; the alias exists for this).
    assert keys['local-a'][1] == keys['local-a'][0], keys
    assert keys['local-b'][1] == keys['local-b'][0], keys
    # ... so children of sessions in different workspaces never share one container.
    assert keys['local-a'][1] != keys['local-b'][1] or keys['local-a'][0] == keys['local-b'][0], keys


# G3 ---------------------------------------------------------------------------------------------

def test_g3_vision_lazy_env_finds_the_sessions_own_container(home, tmp_path, monkeypatch):
    """image_source._resolve_container_fallback: _ensure_container_env(task) then _get_active_env(task)."""
    from tools import image_source, terminal_tool as tt, terminal_tool_lifecycle as lifecycle
    ws = tmp_path / 'ws'
    ws.mkdir()
    created = []

    def fake_create(config, env_type, **kw):
        env = SimpleNamespace(kind='created', task_id=kw['task_id'], host_cwd=kw.get('host_cwd'))
        created.append(env)
        return env

    monkeypatch.setattr(lifecycle, '_create_configured_env', fake_create)
    # The profile container another session of this profile already runs (a messaging chat, cron,
    # or a delegate child: every turn with no registered workspace keys "default").
    profile_env = SimpleNamespace(kind='profile-container', host_cwd=str(tmp_path / 'other'))
    tt._active_environments['default'] = profile_env
    with _turn(_docker(home, ws), 'local-a'):
        image_source._ensure_container_env('local-a')
        seen = image_source._get_active_env('local-a')
        terminal_key = tt._resolve_container_task_id('local-a')
        terminal_env = tt._active_environments.get(terminal_key)
    # Vision finds a sandbox, and it is the one the session's own terminal commands use.
    assert seen is not None, ('no active sandbox for vision', terminal_key, created)
    assert seen is terminal_env, (seen, terminal_env, terminal_key)


# G4 ---------------------------------------------------------------------------------------------

def test_g4_resumed_session_moved_off_docker_drops_the_old_workspace_override(home, tmp_path):
    from tools import terminal_tool as tt
    project = tmp_path / 'project'
    project.mkdir()
    with _turn(_docker(home, project), 'local-a'):
        pass
    # Resume after `terminal.backend: ssh` + `terminal.cwd: /srv/project`: the refresh commits the new
    # policy and drops the session's terminal state (_drop_terminal: clear_session_cwd + cleanup_vm).
    ssh = _policy(home, project, {'backend': 'ssh', 'cwd': '/srv/project', 'ssh_host': 'example.invalid',
                                  'ssh_user': 'u'})
    tt.clear_session_cwd('local-a')
    with _turn(ssh, 'local-a'):
        plan = tt._plan_execution('pwd', task_id='local-a', timeout=None, background=False, _host_local=False)
        overrides = tt.resolve_task_overrides('local-a')
    assert plan.env_type == 'ssh'
    assert plan.cwd == '/srv/project', (plan.cwd, overrides)


# Sibling door: /bg (prompt.background) and preview.restart side agents --------------------------

def _side_plan(policy, task_id):
    """The side agent's real scope (gateway/session_tui_side.py::_side_scope) on a single-profile owner."""
    from gateway.session_tui_side import _side_scope
    from tools.terminal_tool import _get_env_config, _resolve_container_task_id, _resolve_task_host_cwd
    owner = SimpleNamespace(runner=None, profile_id='single-profile-owner')
    with _side_scope(owner, policy, task_id):
        return _resolve_container_task_id(task_id), _resolve_task_host_cwd(_get_env_config(), task_id)


def test_bg_side_agent_mounts_the_sessions_workspace_nonpersistent(home, tmp_path):
    ws = tmp_path / 'ws'
    ws.mkdir()
    policy = _docker(home, ws, persistent=False)
    with _turn(policy, 'local-a'):
        from tools.terminal_tool import _get_env_config, _resolve_task_host_cwd
        assert _resolve_task_host_cwd(_get_env_config(), 'local-a') == (str(ws) if hasattr(
            __import__('gateway.session_policy', fromlist=['x']), 'bind_turn_workspace') else None)
    key, mount = _side_plan(policy, 'bg_abc123')
    # The session's own /bg agent runs in the session's workspace like its turns do.
    assert mount == str(ws), (key, mount)


def test_bg_side_agent_shares_the_sessions_persistent_container(home, tmp_path):
    from tools.terminal_tool import _resolve_container_task_id
    ws = tmp_path / 'ws'
    ws.mkdir()
    policy = _docker(home, ws, persistent=True)
    with _turn(policy, 'local-a'):
        session_key = _resolve_container_task_id('local-a')
    side_key, _mount = _side_plan(policy, 'bg_abc123')
    assert side_key == session_key, (side_key, session_key)


def test_g2_children_of_sessions_in_two_workspaces_each_run_in_their_own_workspace(home, tmp_path, monkeypatch):
    """The terminal tool's real plan + acquire path for each session's delegate child."""
    from tools import terminal_tool as tt
    monkeypatch.setattr(tt, '_create_configured_env', lambda config, env_type, **kw: SimpleNamespace(
        host_cwd=kw.get('host_cwd'), task_id=kw['task_id'], cwd='/workspace'))
    ws1, ws2 = tmp_path / 'ws1', tmp_path / 'ws2'
    ws1.mkdir(), ws2.mkdir()
    mounted = {}
    for sid, ws in (('local-a', ws1), ('local-b', ws2)):
        with _turn(_docker(home, ws), sid):
            child = _spawn_child(sid, f'sub-{sid}')
            plan = tt._plan_execution('ls', task_id=child, timeout=None, background=False, _host_local=False)
            env = tt._acquire_env(plan, child)
            mounted[sid] = (plan.effective_task_id, env.host_cwd)
    assert {sid: m[1] for sid, m in mounted.items()} == {'local-a': str(ws1), 'local-b': str(ws2)}, mounted

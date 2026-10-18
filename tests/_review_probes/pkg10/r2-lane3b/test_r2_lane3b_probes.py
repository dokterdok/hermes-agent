"""Round-2 lane3b review probes (claude). Real reaper/cache/policy logic; only the docker CLI
(subprocess.run / _docker_query) and the container constructor are faked (no Docker daemon here)."""
import json
import subprocess
import threading
import time
from dataclasses import asdict
from types import SimpleNamespace

import pytest

CID = "c0ffee00c0ffee00c0ffee00"
WS_KEY = "default:ws:0123456789abcdef"


@pytest.fixture
def home(tmp_path, monkeypatch):
    from tools import terminal_tool
    home = tmp_path / 'home'
    home.mkdir()
    monkeypatch.setenv('HERMES_HOME', str(home))
    for var in ('TERMINAL_ENV', 'TERMINAL_CWD', 'TERMINAL_CONTAINER_PERSISTENT',
                'TERMINAL_DOCKER_MOUNT_CWD_TO_WORKSPACE'):
        monkeypatch.delenv(var, raising=False)
    saved = dict(terminal_tool._task_env_overrides), dict(terminal_tool._session_cwd)
    yield home
    terminal_tool._task_env_overrides.clear()
    terminal_tool._task_env_overrides.update(saved[0])
    terminal_tool._session_cwd.clear()
    terminal_tool._session_cwd.update(saved[1])


def _docker_env_shell(*, persist_across_processes):
    """A real DockerEnvironment (real cleanup/stop_idle code) without the constructor's docker run."""
    from tools.environments.docker import DockerEnvironment
    env = object.__new__(DockerEnvironment)
    env._container_id = CID
    env._docker_exe = "docker"
    env._persistent = True
    env._persist_across_processes = persist_across_processes
    env._cleanup_thread = None
    env._workspace_dir = None
    env._home_dir = None
    return env


@pytest.fixture
def docker_cli(monkeypatch):
    from tools.environments import docker as docker_mod
    calls = []
    real_run = subprocess.run

    def fake_run(argv, *a, **kw):
        if argv and argv[0] == "docker":
            calls.append(list(argv[1:]))
            return subprocess.CompletedProcess(argv, 0, "", "")
        return real_run(argv, *a, **kw)

    monkeypatch.setattr(docker_mod.subprocess, "run", fake_run)
    return calls


def _reap_stale(monkeypatch, key, env, lifetime=300):
    from tools import terminal_tool, terminal_tool_lifecycle
    from tools.environments.docker import DockerEnvironment
    monkeypatch.setattr(terminal_tool, '_active_environments', {key: env})
    monkeypatch.setattr(terminal_tool, '_last_activity', {key: time.time() - lifetime - 30})
    terminal_tool_lifecycle._cleanup_inactive_envs(lifetime_seconds=lifetime)
    DockerEnvironment.wait_for_all_teardowns(timeout=5)


# --- F1: docker_persist_across_processes: false -> idle-stopped workspace containers are never removed
def test_idle_reaper_removes_workspace_container_when_not_persisting_across_processes(monkeypatch, docker_cli):
    env = _docker_env_shell(persist_across_processes=False)
    _reap_stale(monkeypatch, WS_KEY, env)
    # Contract of persist_across_processes=False (DockerEnvironment.cleanup): stop + rm -f, since no
    # later DockerEnvironment will ever re-attach (docker.py: reused = persist_across_processes and ...).
    assert ["rm", "-f", CID] in docker_cli, docker_cli


def test_next_process_orphan_reaper_spares_that_stopped_container_forever(monkeypatch):
    # The next process's sweep then skips it as an "idle workspace container" (hermes-workspace label),
    # although with persist_across_processes=False nothing can ever re-attach it.
    from tools.environments import docker as docker_mod
    import datetime
    old = (datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=3)).strftime("%Y-%m-%dT%H:%M:%S.000000000Z")
    removed = []

    def fake_query(argv, **kw):
        if argv[1] == "ps":
            return subprocess.CompletedProcess(argv, 0, CID + "\n", "")
        if argv[1] == "inspect" and "FinishedAt" in argv[3]:
            return subprocess.CompletedProcess(argv, 0, old + "\n", "")
        if argv[1] == "inspect":
            label = "hermes-workspace" if "hermes-workspace" in argv[3] else "hermes-task-id"
            return subprocess.CompletedProcess(argv, 0, ("0123456789abcdef" if label == "hermes-workspace"
                                                         else "default_ws_0123456789abcdef") + "\n", "")
        if argv[1] == "rm":
            removed.append(argv[2])
            return subprocess.CompletedProcess(argv, 0, "", "")
        raise AssertionError(argv)

    monkeypatch.setattr(docker_mod, "_docker_query", fake_query)
    monkeypatch.setattr(docker_mod, "_container_finished_at",
                        lambda exe, cid: datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=3))
    docker_mod.reap_orphan_containers(max_age_seconds=600, docker_exe="docker")
    assert removed == [CID]


# --- F2: a foreground command still running in the workspace container gets its container stopped
def test_idle_reaper_does_not_stop_a_workspace_container_with_a_foreground_command_in_flight(monkeypatch, docker_cli):
    from tools.environments import base
    env = _docker_env_shell(persist_across_processes=True)
    # A foreground `docker exec` started (acquire refreshed _last_activity), still running past
    # lifetime_seconds (FOREGROUND_MAX_TIMEOUT is 600 > default lifetime 300).
    handle = SimpleNamespace(pid=4242)
    with base._live_foreground_cond:
        base._live_foreground[id(handle)] = (env, handle)
    try:
        _reap_stale(monkeypatch, WS_KEY, env)
    finally:
        with base._live_foreground_cond:
            base._live_foreground.pop(id(handle), None)
    assert not any(c[:1] == ["stop"] for c in docker_cli), docker_cli


# --- F3: preview restart overwrites the side-scope workspace binding -> profile container
def _policy(home, cwd, persistent):
    from gateway.session_policy import build_policy, restore_policy
    (home / 'config.yaml').write_text(json.dumps({'terminal': {
        'backend': 'docker', 'docker_mount_cwd_to_workspace': True,
        'container_persistent': persistent}}), encoding='utf-8')
    return restore_policy(asdict(build_policy({'source': 'cli', 'cwd': str(cwd), 'model': 'm'}, {})))


def _plan(task_id):
    from tools.terminal_tool import _get_env_config, _resolve_container_task_id, _resolve_task_host_cwd
    return _resolve_container_task_id(task_id), _resolve_task_host_cwd(_get_env_config(), task_id)


def test_preview_restart_side_agent_keeps_the_sessions_workspace_container(home, tmp_path):
    from contextlib import ExitStack
    from gateway.session_context import clear_session_vars, set_session_vars
    from gateway.session_policy import bind_turn_workspace, policy_scope
    from gateway.session_tui_side import _side_scope
    from tools.terminal_tool import register_task_env_overrides
    ws1 = tmp_path / 'ws1'
    ws1.mkdir()
    policy = _policy(home, ws1, persistent=True)
    with ExitStack() as stack:
        stack.enter_context(policy_scope(policy))
        tokens = set_session_vars(session_key='agent:main:local:dm:local-a')
        stack.callback(clear_session_vars, tokens)
        bind_turn_workspace(policy, 'local-a')
        session_key = _plan('local-a')[0]
    assert ':ws:' in session_key
    authority = SimpleNamespace(runner=SimpleNamespace(), profile_id='fixture')
    with _side_scope(authority, policy, 'preview_abc123'):
        # session_tui_side.preview_restart body(): the validated preview cwd (the project folder).
        register_task_env_overrides('preview_abc123', {'cwd': str(ws1)})
        key, _mount = _plan('preview_abc123')
    assert key == session_key, (key, session_key)

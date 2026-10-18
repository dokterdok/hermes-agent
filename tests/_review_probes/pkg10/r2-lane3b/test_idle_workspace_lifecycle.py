"""Adversarial lifecycle contracts; Docker subprocess boundary is simulated, no daemon needed."""
import subprocess
import threading
import time
from types import SimpleNamespace

import pytest


@pytest.fixture
def sandbox(monkeypatch):
    from tools import terminal_tool as terminal
    from tools import terminal_tool_lifecycle as lifecycle
    from tools.environments.docker import DockerEnvironment

    key = 'default:ws:0123456789abcdef'
    state = {'running': True}
    monkeypatch.setattr(terminal, '_active_environments', {})
    monkeypatch.setattr(terminal, '_last_activity', {})
    monkeypatch.setattr(terminal, '_creation_locks', {})
    monkeypatch.setattr(terminal, '_start_cleanup_thread', lambda: None)
    monkeypatch.setattr(lifecycle, '_clear_file_ops_cache', lambda key: None)

    def make_env():
        env = DockerEnvironment.__new__(DockerEnvironment)
        env._container_id = 'review-workspace-container'
        env._docker_exe = 'review-fake-docker'
        env._persist_across_processes = True
        env._persistent = True
        env._image = 'review-image'
        env._image_pinned = False
        env.cwd = '/workspace'
        env.host_cwd = None
        env._find_reusable_container = lambda *args: (
            'review-workspace-container', 'running' if state['running'] else 'exited')
        env._container_image = lambda cid: 'review-image'
        env._start_container = lambda cid: state.update(running=True)
        return env

    def create(*args, **kwargs):
        env = make_env()
        assert env._attach_existing_container('task', 'default', 'off', True)
        return env

    monkeypatch.setattr(terminal, '_create_configured_env', create)
    plan = SimpleNamespace(env_type='docker', effective_task_id=key, config={}, image='review-image',
                           cwd='/workspace', effective_timeout=600, host_cwd=None)
    env = make_env()
    terminal._active_environments[key] = env
    terminal._last_activity[key] = time.time() - 301
    return terminal, lifecycle, key, state, plan, env


def test_reattached_workspace_must_not_be_stopped_by_previous_idle_reaper(sandbox, monkeypatch):
    from tools.environments import docker
    terminal, lifecycle, key, state, plan, env = sandbox
    stop_entered, allow_stop = threading.Event(), threading.Event()

    def docker_run(cmd, **kwargs):
        assert cmd[1] == 'stop'
        stop_entered.set()
        assert allow_stop.wait(10)
        state['running'] = False
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(docker.subprocess, 'run', docker_run)
    try:
        lifecycle._cleanup_inactive_envs(lifetime_seconds=300)
        worker = getattr(env, '_cleanup_thread', None)
        if worker is not None:
            assert stop_entered.wait(10)
        replacement = terminal._acquire_env(plan, 'local-session')
        assert replacement._container_id == 'review-workspace-container'
        assert state['running']
    finally:
        allow_stop.set()
        assert env.wait_for_cleanup(timeout=10)
    assert state['running'], 'Idle stop killed the workspace after a new caller reattached'


def test_foreground_command_longer_than_idle_lifetime_must_not_be_stopped(sandbox, monkeypatch):
    from tools.environments import docker
    terminal, lifecycle, key, state, plan, env = sandbox

    def docker_run(cmd, **kwargs):
        assert cmd[1] == 'stop'
        state['running'] = False
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(docker.subprocess, 'run', docker_run)

    def execute(command, **kwargs):
        # The call is still inside its allowed 600-second foreground timeout when
        # the cleanup tick observes 301 seconds since acquisition. No wall-clock sleep.
        terminal._last_activity[key] = time.time() - 301
        lifecycle._cleanup_inactive_envs(lifetime_seconds=300)
        assert env.wait_for_cleanup(timeout=10)
        return {'output': 'build finished' if state['running'] else 'terminated',
                'returncode': 0 if state['running'] else 137}

    env.execute = execute
    terminal._acquire_env(plan, 'local-session')
    monkeypatch.setattr(terminal, 'finalize_foreground_result', lambda **kw: kw['result'])
    result = terminal._run_foreground('build-project', env, plan, task_id='local-session',
                                      session_id='local-session', session_key='local-session',
                                      workdir=None, approval_note=None, clear_interrupt=False, metered=False)
    assert result['returncode'] == 0, 'Idle reaper stopped a foreground command before its timeout'

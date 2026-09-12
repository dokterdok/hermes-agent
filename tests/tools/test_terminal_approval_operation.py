"""Real terminal guard/notification plumbing retains exact operation and policy scope."""

import json
from types import SimpleNamespace

import pytest

from tools import approval, approval_context
from tools import terminal_tool as terminal
from tools.approval_operation import approval_operation_key
from tools.environments.local import LocalEnvironment
from tools.registry import registry


@pytest.fixture
def terminal_context(tmp_path, monkeypatch):
    config = {"mode": "manual", "timeout": 2}
    monkeypatch.setattr(approval_context, "_get_approval_config", lambda: config)
    monkeypatch.setenv("HERMES_GATEWAY_SESSION", "1")
    monkeypatch.setenv("HERMES_SESSION_KEY", "remember-test")
    for variable in ("HERMES_INTERACTIVE", "HERMES_CRON_SESSION", "HERMES_EXEC_ASK"):
        monkeypatch.delenv(variable, raising=False)
    monkeypatch.setattr(approval, "_gateway_queues", {})
    monkeypatch.setattr(approval, "_gateway_notify_cbs", {})
    from gateway.session_events import SessionEvents
    from gateway.session_pending_controls import PendingControls
    controls = PendingControls(SessionEvents())
    executed, notices, projected = [], [], []
    env = object.__new__(LocalEnvironment)
    env.env = {}
    env.execute = lambda command, **kwargs: (
        executed.append((command, kwargs)) or {"output": "ok", "returncode": 0})
    monkeypatch.setattr(terminal, "_active_environments", {"remember-test": env})
    monkeypatch.setattr(terminal, "_last_activity", {})
    monkeypatch.setattr(terminal, "_session_cwd", {})
    monkeypatch.setattr(terminal, "_task_env_overrides", {})
    monkeypatch.setattr(terminal, "_start_cleanup_thread", lambda: None)
    monkeypatch.setattr(terminal, "_get_env_config", lambda: {
        "env_type": "local", "cwd": str(tmp_path), "timeout": 30, "lifetime_seconds": 3600,
    })
    return SimpleNamespace(config=config, notices=notices, projected=projected, controls=controls,
                           env=env, executed=executed, cwd=str(tmp_path))


@pytest.mark.parametrize("outcome", ["once", "deny", "changed-cwd", "policy-deny", "smart-deny"])
def test_terminal_uses_real_guard_and_does_not_persist_global_permission(terminal_context, monkeypatch, outcome):
    state = terminal_context
    command = "rm -rf ./build # ghp_" + "A" * 36
    if outcome == "policy-deny":
        state.config["deny"] = ["rm -rf *"]
    if outcome == "smart-deny":
        state.config["mode"] = "smart"
        monkeypatch.setattr("tools.approval_smart._smart_approve", lambda *args, **kwargs: "deny")

    def notify(data):
        state.notices.append(data)
        state.controls.register('owned-session', 'remember-test', 1, data)
        state.projected.extend(state.controls.snapshot('owned-session', 1))
        if outcome == "changed-cwd":
            terminal.record_session_cwd("remember-test", state.cwd + "/changed")
        approval.resolve_gateway_approval("remember-test", "deny" if outcome == "deny" else "once")

    approval.register_gateway_notify("remember-test", notify)
    monkeypatch.setattr(approval, "approve_permanent", lambda *args: pytest.fail("profile permission changed"))
    result = json.loads(registry.get_entry("terminal").handler({"command": command}, task_id="remember-test"))
    if outcome == "policy-deny":
        assert not state.notices and not state.executed
        assert result["status"] == "blocked"
    else:
        assert len(state.notices) == 1
        notice = state.notices[0]
        assert "A" * 36 not in notice["command"]
        assert bool(notice.get("remember_key")) is (outcome != "smart-deny")
        projected, = state.projected
        assert projected.get('remember_key') == notice.get('remember_key')
        assert projected.get('remember_context') == notice.get('remember_context')
        assert projected['allow_permanent'] is (outcome != 'smart-deny')
        assert projected['allow_session'] is (outcome != 'smart-deny')
        assert projected.get('smart_denied', False) is (outcome == 'smart-deny')
        assert projected['choices'] == (['once', 'deny'] if outcome == 'smart-deny'
                                        else ['once', 'deny', 'session', 'always'])
        if outcome in {"deny", "changed-cwd"}:
            assert not state.executed and result["status"] == "blocked"
        else:
            assert state.executed[0][0] == command
            assert state.executed[0][1]["cwd"] == state.cwd
            assert result["exit_code"] == 0
    assert approval_operation_key(command, ["anything"]) == ""


def inert_ssh(state, host):
    from tools.environments.ssh import SSHEnvironment
    env = object.__new__(SSHEnvironment)
    env.host, env.user, env.port, env.key_path = host, 'worker', 22, ''
    env.env = {}
    env.execute = lambda command, **kwargs: (
        state.executed.append((env.host, command, kwargs['cwd'])) or {'output': 'inert SSH', 'returncode': 0})
    return env


@pytest.mark.parametrize('change', [None, 'host', 'user', 'port', 'key_path', 'cached-local'])
def test_acquired_ssh_target_owns_metadata_and_is_rechecked(terminal_context, monkeypatch, change):
    state = terminal_context
    monkeypatch.setattr(terminal, '_get_env_config', lambda: {
        'env_type': 'ssh', 'cwd': state.cwd, 'timeout': 30, 'lifetime_seconds': 3600,
        'ssh_host': 'new.invalid', 'ssh_user': 'worker', 'ssh_port': 22})
    env = inert_ssh(state, 'acquired.invalid')
    if change != 'cached-local':
        terminal._active_environments['remember-test'] = env

    def notify(data):
        state.notices.append(data)
        if change not in (None, 'cached-local'):
            setattr(env, change, 2222 if change == 'port' else 'changed')
        approval.resolve_gateway_approval('remember-test', 'once', request_id=data['request_id'])

    approval.register_gateway_notify('remember-test', notify)
    result = json.loads(registry.get_entry('terminal').handler({'command': 'rm -rf ./build'}, task_id='remember-test'))
    first, = state.notices
    if change == 'cached-local':
        assert 'remember_key' not in first and result['exit_code'] == 0
    elif change is not None:
        assert result['status'] == 'blocked' and not state.executed
    else:
        assert result['exit_code'] == 0 and state.executed[0][0] == 'acquired.invalid'
        assert 'acquired.invalid' in first['remember_context'] and 'new.invalid' not in first['remember_context']
        terminal._active_environments['remember-test'] = inert_ssh(state, 'new.invalid')
        repeated = json.loads(registry.get_entry('terminal').handler({'command': 'rm -rf ./build'}, task_id='remember-test'))
        assert repeated['exit_code'] == 0 and state.executed[-1][0] == 'new.invalid'
        assert state.notices[-1]['remember_key'] != first['remember_key']


@pytest.mark.parametrize('mode', ['background', 'promoted'])
def test_background_operations_do_not_offer_foreground_metadata(terminal_context, monkeypatch, mode):
    state = terminal_context
    def notify(data):
        state.notices.append(data)
        approval.resolve_gateway_approval('remember-test', 'once', request_id=data['request_id'])
    approval.register_gateway_notify('remember-test', notify)
    monkeypatch.setattr(terminal, 'spawn_background_process', lambda **kwargs: json.dumps({'status': 'background'}))
    options = {'background': True} if mode == 'background' else {'timeout': terminal.FOREGROUND_MAX_TIMEOUT + 1}
    result = json.loads(registry.get_entry('terminal').handler({'command': 'rm -rf ./build', **options}, task_id='remember-test'))
    assert result['status'] == 'background' and len(state.notices) == 1
    assert 'remember_key' not in state.notices[0] and 'remember_context' not in state.notices[0]
    assert not state.executed

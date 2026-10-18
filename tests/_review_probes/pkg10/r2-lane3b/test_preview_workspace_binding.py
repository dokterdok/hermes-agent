"""Exercise preview.restart's body, including its client-cwd override after _side_scope."""
import asyncio
from types import SimpleNamespace

from tests.gateway.test_docker_session_workspace import home, _policy, _turn, _plan


def test_preview_restart_with_client_cwd_reuses_parents_workspace(home, tmp_path, monkeypatch):
    from gateway import session_tui_side as side
    from gateway import session_local_recovery

    workspace = tmp_path / 'project'
    workspace.mkdir()
    policy = _policy(home, workspace, persistent=True)
    with _turn(policy, 'parent-session'):
        parent_key = _plan('parent-session')[0]

    task_id = 'preview_abcdef'
    authority = SimpleNamespace(runner=SimpleNamespace(), profile_id='fixture')
    connection = SimpleNamespace(authority=authority)
    ref = SimpleNamespace(session_id='parent-session')
    seen = {}

    class Agent:
        def run_conversation(self, **kwargs):
            seen['key'], seen['mount'] = _plan(kwargs['task_id'])
            return {'final_response': 'done'}

    monkeypatch.setattr(side, '_admit', lambda *args, **kwargs: (
        SimpleNamespace(), policy, 'http://localhost:3000', task_id))
    monkeypatch.setattr(side, '_side_agent', lambda *args, **kwargs: Agent())
    monkeypatch.setattr(session_local_recovery, 'transcript_target', lambda *args: None)
    monkeypatch.setattr(side, '_spawn', lambda authority, ref, tid, event, body: body())
    authority.sessions = {}
    asyncio.run(side.preview_restart(connection, ref, {'cwd': str(workspace)}))
    assert seen['key'] == parent_key, (seen, parent_key)

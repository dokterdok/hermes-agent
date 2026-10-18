"""Self-contained copy of the lane's CLI<->gateway harness (tests/hermes_cli/test_cli_gateway_model_route.py and
test_cli_gateway_model_scope.py at bee90c2e12) so the same probe runs on main, where those files do not exist."""
import queue
from types import SimpleNamespace

from hermes_cli.gateway_client import GatewayClientError
from hermes_cli.model_switch import ModelSwitchResult


class RecordingConnection:
    def __init__(self, info):
        from hermes_cli.gateway_connection import SessionState
        self.events = queue.Queue()
        self.calls = []
        self.closed = False
        # The session store is truthy until the connection is final.
        self.final = False
        self._state = SessionState("s")
        self._state.info = dict(info)

    def state(self, session_id):
        return self._state

    def reply(self, *_a, **_k):
        return True

    def call(self, method, *, timeout=None, **params):
        self.calls.append((method, params))
        if method == "session.resume":
            return {"revision": 7, "execution_generation": 3, "info": dict(self._state.info), "messages": [],
                    "stored_session_id": "s", "session_id": "s", "prompts": [], "pending": []}
        if method == "session.mutate":
            if params.get("operation") == "model":
                self._state.info.update(model=params["payload"]["model"],
                                        provider=params["payload"].get("provider") or self._state.info.get("provider"))
            return {}
        if method == "prompt.submit":
            self.events.put({"method": "event", "params": {
                "type": "message.complete", "session_id": "s", "admission_id": "a1", "execution_generation": 3,
                "payload": {"text": "ok", "status": "complete", "admission_id": "a1"}}})
            return {"admission_id": "a1", "status": "queued"}
        if method == "prompt.receipt":
            return {"status": "terminal", "outcome": "completed", "result": {"final_response": "ok"}}
        if method == "config.set":
            return {}
        raise GatewayClientError("unsupported_method")


def _agent(conn, **kw):
    from hermes_cli.gateway_agent import GatewayAgent
    return GatewayAgent(connection=conn, session_id="s", **kw)


def _model_routes(conn):
    """Every model the CLI asked the gateway to run: session switches and one-turn overrides."""
    mutates = [p["payload"] for m, p in conn.calls if m == "session.mutate" and p.get("operation") == "model"]
    overrides = [p["model_override"] for m, p in conn.calls if m == "prompt.submit" and "model_override" in p]
    return mutates, overrides



def _cli(monkeypatch, conn):
    import cli as cli_mod
    from hermes_cli.gateway_session_store import GatewaySessionStore
    from tests.hermes_cli.test_cli_model_once import _StubCLI
    stub = _StubCLI()
    stub.agent = _agent(conn, model="old/model", provider="openrouter")
    stub._snapshot_model_runtime = cli_mod.HermesCLI._snapshot_model_runtime.__get__(stub)
    stub._session_db = GatewaySessionStore(conn)
    stub.session_id = "s"
    printed = []
    monkeypatch.setattr(cli_mod, "_cprint", lambda s, *a, **k: printed.append(str(s)))
    monkeypatch.setattr(cli_mod, "save_config_value", lambda *a, **k: None)
    monkeypatch.setattr("hermes_cli.inventory.load_picker_context", lambda: SimpleNamespace(
        user_providers=None, custom_providers=None,
        with_overrides=lambda **_: SimpleNamespace(user_providers=None, custom_providers=None)))
    _pick(monkeypatch, "claude-sonnet-4.6", "anthropic", "https://api.anthropic.com", "anthropic_messages")
    monkeypatch.setattr("hermes_cli.model_switch.resolve_display_context_length", lambda *a, **k: None)
    return cli_mod, stub, printed


def _pick(monkeypatch, model, provider, base_url, api_mode):
    monkeypatch.setattr("hermes_cli.model_switch.switch_model", lambda **_: ModelSwitchResult(
        success=True, new_model=model, target_provider=provider, api_key=f"sk-{provider}",
        base_url=base_url, api_mode=api_mode, provider_label=provider.title()))


def _submits(conn):
    return [p for m, p in conn.calls if m == "prompt.submit"]


def _prepare_for_chat(monkeypatch, cli_mod, stub):
    """Enough of HermesCLI for the real ``chat``: its retire, ``_init_agent``, the agent thread
    and the one-turn restore all run. Only the terminal UI around the turn is stubbed."""
    for name in ("chat", "_chat_run_agent", "_chat_release_turn_audio", "_init_agent",
                 "_resolve_turn_agent_config", "_restore_model_runtime_snapshot"):
        setattr(stub, name, getattr(cli_mod.HermesCLI, name).__get__(stub))
    monkeypatch.setattr(cli_mod, "ChatConsole", lambda *a, **k: SimpleNamespace(print=lambda *a, **k: None))
    stub._ensure_runtime_credentials = lambda: True
    stub.finalize_preloaded_skills = lambda: None
    stub._install_tool_callbacks = lambda: None
    stub._ensure_gateway_session = lambda: None
    stub._sync_fallback_chain_with_config = lambda agent: None
    stub._chat_route_images = lambda message, images: message

    def _stage(agent, message):
        stub.conversation_history.append({"role": "user", "content": message})
        agent._pending_cli_user_message = {"role": "user", "content": message}

    stub._chat_stage_user_message = _stage
    stub._reset_stream_state = lambda: None
    stub._chat_setup_turn_audio = lambda turn, message, voice_input: None
    stub._chat_monitor_agent_thread = lambda turn, thread: thread.join()
    stub._chat_settle_turn = lambda turn: None
    stub._chat_render_turn = lambda turn, thread, interrupt: (turn.result or {}).get("final_response")
    stub._flush_credit_notices = lambda: None
    for callback in ("_secret_capture_callback", "_sudo_password_callback", "_approval_callback",
                     "_vault_unlock_callback", "_vault_save_login_callback", "_vault_code_callback"):
        setattr(stub, callback, None)
    stub._pending_moa_config = None
    stub._resumed = False
    stub.conversation_history = []
    stub._active_agent_route_signature = None
    stub.acp_command = None
    stub.acp_args = []
    stub.max_turns = 8
    stub.enabled_toolsets = None
    stub.disabled_toolsets = None
    stub.verbose = False
    stub.tool_progress_mode = "off"
    stub.system_prompt = ""
    stub.prefill_messages = None
    stub.reasoning_config = None
    stub.service_tier = None
    stub._reasoning_user_set = False
    stub._providers_only = None
    stub._providers_ignore = None
    stub._providers_order = None
    stub._provider_sort = None
    stub._provider_require_params = None
    stub._provider_data_collection = None
    stub._openrouter_min_coding_score = None
    stub._single_query_mode = False
    stub._clarify_callback = None
    stub._connection_callback = None
    stub._confirm_owner_model_switch = lambda refusal: False
    stub._withdraw_gateway_modal = lambda method: None
    stub._current_reasoning_callback = lambda: None
    stub._fallback_model = None
    stub._on_thinking = None
    stub.checkpoints_enabled = False
    stub.checkpoint_max_snapshots = 0
    stub.checkpoint_max_total_size_mb = 0
    stub.checkpoint_max_file_size_mb = 0
    stub.pass_session_id = False
    stub.ignore_rules = False
    stub._inline_diffs_enabled = False
    stub.streaming_enabled = False
    stub._on_tool_progress = None
    stub._on_tool_start = None
    stub._on_tool_complete = None
    stub._stream_delta = None
    stub._on_tool_gen_start = None
    stub._on_notice = None
    stub._on_notice_clear = None
    stub._on_reaction = None
    stub._agent_status_print = lambda *_a, **_k: None
    stub._pending_title = None
    stub._auto_load_skills_result = None


def _chat_cli(monkeypatch, conn):
    cli_mod, stub, printed = _cli(monkeypatch, conn)
    _prepare_for_chat(monkeypatch, cli_mod, stub)
    return cli_mod, stub, printed



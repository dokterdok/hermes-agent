"""Files support does not depend on unrelated optional provider SDKs."""
import sys
from types import SimpleNamespace

from openai import OpenAI

from gateway.session_selected_route import supports_files_agent


def test_missing_optional_anthropic_does_not_break_other_transports(monkeypatch):
    monkeypatch.setitem(sys.modules, 'anthropic', None)
    with OpenAI(api_key='fixture-inert-key') as client:
        assert supports_files_agent(SimpleNamespace(api_mode='chat_completions', client=client))
        assert not supports_files_agent(SimpleNamespace(api_mode='codex_app_server'))
        assert not supports_files_agent(SimpleNamespace(api_mode='anthropic_messages', _anthropic_client=object()))

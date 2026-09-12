"""Direct Slack/Matrix fallback markers share the parent's task-local guard."""

from importlib import import_module

import pytest



@pytest.mark.parametrize(
    "module_name,class_name",
    [
        ("plugins.platforms.telegram.adapter", "TelegramAdapter"),
        ("plugins.platforms.discord.adapter", "DiscordAdapter"),
        ("plugins.platforms.matrix.adapter", "MatrixAdapter"),
        ("plugins.platforms.slack.adapter", "SlackAdapter"),
        ("plugins.platforms.whatsapp.adapter", "WhatsAppAdapter"),
        ("gateway.platforms.signal", "SignalAdapter"),
        ("gateway.platforms.whatsapp_cloud", "WhatsAppCloudAdapter"),
    ],
)
def test_supported_adapter_advertises_native_document_contract(module_name, class_name):
    adapter = getattr(import_module(module_name), class_name)
    assert adapter.send_document.strict_native_document_guard is True

"""Round-2 regression: constructing the real gateway listener preserves file logs."""
import logging
from pathlib import Path

import hermes_logging
from hermes_constants import reset_hermes_home_override, set_hermes_home_override


def test_gateway_listener_start_keeps_profile_file_logging(tmp_path):
    from hermes_cli.web_server_lifecycle import build_listener_server

    root = tmp_path / 'root'
    beta = root / 'profiles' / 'beta'
    beta.mkdir(parents=True)
    (beta / 'config.yaml').write_text('{}', encoding='utf-8')
    hermes_logging._reset_queued_handlers()
    original_level = logging.getLogger().level
    try:
        hermes_logging.setup_logging(hermes_home=root, mode='gateway', force=True)
        assert hermes_logging.enable_profile_log_routing([root, beta])
        logger = logging.getLogger('gateway.review_listener_logging')

        def emit(marker):
            for home in (root, beta):
                token = set_hermes_home_override(str(home))
                try:
                    logger.warning(marker)
                finally:
                    reset_hermes_home_override(token)
            hermes_logging.flush_log_queue()

        emit('before-listener')
        for home in (root, beta):
            assert 'before-listener' in (home / 'logs' / 'agent.log').read_text(encoding='utf-8-sig')
        # This is the production gateway-session listener factory. No bind/start or network access.
        build_listener_server(lambda scope, receive, send: None, '127.0.0.1', 0, auth_required=False)
        emit('after-listener')
        # setup_logging is also unable to repair routers closed by uvicorn's dictConfig.
        hermes_logging.setup_logging(hermes_home=root, mode='gateway', force=True)
        emit('after-setup-again')
        missing = [str(home) for home in (root, beta)
                   if 'after-listener' not in (home / 'logs' / 'agent.log').read_text(encoding='utf-8-sig')
                   or 'after-setup-again' not in (home / 'logs' / 'agent.log').read_text(encoding='utf-8-sig')]
        assert not missing, f'gateway listener permanently silenced file logging for {missing}'
    finally:
        hermes_logging._reset_queued_handlers()
        logging.getLogger().setLevel(original_level)

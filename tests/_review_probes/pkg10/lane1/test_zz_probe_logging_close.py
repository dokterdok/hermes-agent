"""T23: a routed write racing dictConfig's shutdown must not reopen a forgotten child."""
import logging
import threading
import weakref
from logging.handlers import RotatingFileHandler

import hermes_logging
import pytest


@pytest.mark.platforms('windows')
def test_shutdown_during_routed_record_releases_file_lock(tmp_path, monkeypatch):
    default = tmp_path / 'default'
    profile = tmp_path / 'profile'
    for home in (default, profile):
        (home / 'logs').mkdir(parents=True)
    monkeypatch.setenv('HERMES_HOME', str(default))
    existing = RotatingFileHandler(default / 'logs' / 'agent.log', encoding='utf-8')
    router = hermes_logging._ProfileRoutingFileHandler(existing, [profile])
    existing.close()
    selected, release, close_started, close_done = (threading.Event() for _ in range(4))
    record = logging.LogRecord('close-probe', logging.ERROR, __file__, 1, 'record', (), None)
    record.hermes_home = str(profile)
    router.handle(record)
    child = router._profile_handlers[profile.resolve()]
    real_get = router._handler_for_home

    def paused_get(home):
        handler = real_get(home)
        selected.set()
        assert release.wait(10), 'probe did not release in-flight record'
        return handler

    monkeypatch.setattr(router, '_handler_for_home', paused_get)

    def close():
        close_started.set()
        # Exactly dictConfig's module-lock -> shutdown -> child/router close sequence.
        # Restrict the handler list to our pair so pytest's logging stays untouched.
        with logging._lock:
            logging.shutdown([weakref.ref(router), weakref.ref(child)])
        close_done.set()

    writer = threading.Thread(target=router.handle, args=(record,), daemon=True)
    closer = threading.Thread(target=close, daemon=True)
    try:
        writer.start()
        assert selected.wait(5)
        closer.start()
        assert close_started.wait(5)
        # Main waits on the router's handle lock; head returns while a write holds
        # a child reference. Both are legal only if teardown releases the log file.
        close_done.wait(2)
        release.set()
        writer.join(5)
        closer.join(5)
        assert not writer.is_alive() and not closer.is_alive()
        (profile / 'logs' / '.__agent.lock').unlink()
    finally:
        release.set()
        writer.join(5)
        if closer.ident is not None:
            closer.join(5)
        router.close()
        child.close()

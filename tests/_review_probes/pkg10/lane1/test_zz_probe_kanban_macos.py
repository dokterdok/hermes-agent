"""G2: native macOS fault injection; never pretend this Windows host is macOS."""
import subprocess
import sys

import pytest


@pytest.mark.platforms('macos')
def test_submitter_fingerprint_survives_old_psutil_boot_reference(monkeypatch):
    import psutil
    from psutil import _psosx
    from hermes_cli.kanban_db_dispatch import _process_fingerprint, _worker_alive
    process = subprocess.Popen([sys.executable, '-c', 'import sys; sys.stdin.read()'], stdin=subprocess.PIPE)
    try:
        # The gateway imported psutil before a five-second correction. Its newly spawned
        # submitter is still alive when the fresh managed worker checks the saved identity.
        boot = _psosx.boot_time()
        monkeypatch.setattr(_psosx, 'INIT_BOOT_TIME', boot + 5)
        recorded = _process_fingerprint(process.pid)
        monkeypatch.setattr(_psosx, 'INIT_BOOT_TIME', boot)
        assert psutil.Process(process.pid).is_running()
        assert _worker_alive(process.pid, recorded), 'live submitter rejected by fresh worker'
    finally:
        process.stdin.close()
        process.wait(timeout=5)

"""The crash fixture owns the authenticated process and its real wait result."""
import json
import signal
import subprocess
import sys

import psutil
import pytest

from tests.gateway.fixtures.local_recovery_probe import DaemonProcess, child_env


@pytest.fixture
def owned_process(tmp_path):
    code = "import os,time; print(os.getpid(),flush=True); time.sleep(60)"
    with DaemonProcess([sys.executable, "-c", code], cwd=tmp_path, env=child_env(),
                       stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, text=True) as proc:
        proc.identify({"pid": json.loads(proc.stdout.readline())})
        try:
            yield proc
        finally:
            proc.kill()
            proc.wait(timeout=10)


def test_crash_kills_identified_daemon_and_repeated_cleanup_preserves_result(owned_process):
    proc = owned_process
    actual = proc.gateway
    assert actual.is_running()
    proc.kill()
    result = proc.wait(timeout=10)
    assert not actual.is_running()
    assert result is not None
    proc.kill()
    assert proc.wait(timeout=10) == result


@pytest.mark.platforms("posix")
def test_direct_child_crash_preserves_sigkill_wait_status(owned_process):
    proc = owned_process
    assert proc.gateway.pid == proc.pid
    proc.kill()
    assert proc.wait(timeout=10) == -signal.SIGKILL


def test_identification_rejects_an_unrelated_live_process(owned_process):
    proc = owned_process
    previous = proc.gateway
    with pytest.raises(AssertionError):
        proc.identify({"pid": psutil.Process().pid})
    assert proc.gateway == previous and previous.is_running()

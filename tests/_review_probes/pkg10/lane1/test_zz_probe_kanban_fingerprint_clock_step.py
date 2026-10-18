"""G2 probe: after a macOS clock step, the Kanban submitter fingerprint the long-lived gateway's
embedded dispatcher recorded (``_set_worker_pid`` -> ``_process_fingerprint``) must still match
what a freshly spawned managed worker reads in ``_watch_submitter`` (``_worker_alive``).

Same psutil emulation as the PR's own KB-6 test
(``tests/gateway/test_worker_admission_reservation.py::test_reservation_survives_a_clock_step_under_emulated_macos_psutil``):
psutil adds ``abs(INIT_BOOT_TIME - boot_time())`` with a reference frozen at import, so a stale
(long-lived) process reads ``kernel + |drift|`` and a fresh one reads ``kernel``. Real behaviour is
macOS-only; this host is Windows, where ``get_process_start_time`` also falls back to psutil.
"""
import subprocess
import sys

import psutil
import pytest


@pytest.fixture
def emulated_macos_psutil(monkeypatch):
    kernel = psutil._psplatform.Process.create_time
    view = {'stale': 0.0}

    def create_time(self, monotonic=False, **kwargs):
        start = kernel(self, **kwargs)  # an epoch start, as macOS kinfo_proc records it
        return start if monotonic else start + abs(view['stale'])

    monkeypatch.setattr(psutil, 'MACOS', True)
    monkeypatch.setattr(psutil._psplatform.Process, 'create_time', create_time)
    return view


@pytest.mark.parametrize('drift', [5.0, -5.0])
def test_submitter_fingerprint_survives_a_clock_step_across_processes(emulated_macos_psutil, drift):
    from hermes_cli.kanban_db_dispatch import _process_fingerprint, _worker_alive
    submitter = subprocess.Popen([sys.executable, '-c', 'import sys; sys.stdin.read()'], stdin=subprocess.PIPE,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        emulated_macos_psutil['stale'] = drift  # the gateway's embedded dispatcher (imported before the step)
        recorded = _process_fingerprint(submitter.pid)
        emulated_macos_psutil['stale'] = 0.0  # the managed worker spawned after the step
        assert recorded is not None
        assert _worker_alive(submitter.pid, recorded), (
            f'the live submitter reads as dead/recycled across the step: recorded {recorded!r}, '
            f'worker reads {_process_fingerprint(submitter.pid)!r} -> _watch_submitter os._exit(1)')
    finally:
        submitter.stdin.close()
        submitter.wait(timeout=5)


@pytest.mark.parametrize('drift', [5.0, -5.0])
def test_control_process_birth_is_stable_across_the_same_step(emulated_macos_psutil, drift):
    """Control: the KB-6 birth reader (head only) is stable under the same emulation."""
    process_identity = pytest.importorskip('hermes_cli.process_identity')
    if not hasattr(process_identity, 'process_birth'):
        pytest.skip('process_birth is new in this PR')
    submitter = subprocess.Popen([sys.executable, '-c', 'import sys; sys.stdin.read()'], stdin=subprocess.PIPE,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        emulated_macos_psutil['stale'] = drift
        owner = process_identity.process_birth(psutil.Process(submitter.pid))
        emulated_macos_psutil['stale'] = 0.0
        assert process_identity.process_birth(psutil.Process(submitter.pid)) == owner
    finally:
        submitter.stdin.close()
        submitter.wait(timeout=5)

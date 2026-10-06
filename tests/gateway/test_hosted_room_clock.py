"""Native clock reads must not share mutable result storage across gateway worker threads."""
import ctypes
import threading
from types import SimpleNamespace

from gateway import hosted_room_clock as clock


def test_windows_clock_reads_keep_each_calls_own_native_sample(monkeypatch):
    first_written, second_written = threading.Event(), threading.Event()
    samples = {}
    def query(pointer):
        if threading.current_thread().name == 'clock-first':
            pointer._obj.value = 10_000_000
            first_written.set()
            assert second_written.wait(5)
        else:
            assert first_written.wait(5)
            pointer._obj.value = 20_000_000
            second_written.set()
    monkeypatch.setattr(ctypes, 'windll', SimpleNamespace(kernelbase=SimpleNamespace(
        QueryInterruptTime=query, QueryUnbiasedInterruptTime=query)), raising=False)
    read, _ = clock._windows_clocks()
    threads = [threading.Thread(name=name, target=lambda name=name: samples.update({name: read()}))
               for name in ('clock-first', 'clock-second')]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10)
    assert samples == {'clock-first': 1.0, 'clock-second': 2.0}

"""A clock that keeps counting while the computer sleeps, for Group Chat host leases.

A lease is a promise about elapsed time: "I won't follow anyone else for the next 20 seconds".
Python's ``time.monotonic()`` stops while a Mac (and some PCs) sleeps, so a lease measured with
it would seem to last longer than it really did. ``now()`` keeps counting through sleep:

- Linux: ``CLOCK_BOOTTIME``;
- macOS: ``CLOCK_MONOTONIC``, which, unlike ``time.monotonic()``, advances during sleep;
- Windows: ``QueryInterruptTime``, which includes sleep and hibernation;
- anything else: the wall clock, whose jumps ``SuspendDetector`` treats as sleep.

``boot_id()`` names the current boot, so a value saved before the computer restarted is never
compared with this clock; after a reboot callers fall back to wall-clock time, conservatively.
``SuspendDetector`` compares ``now()`` with a clock that stops during sleep: when ``now()`` ran
further ahead than the tolerance, the computer slept in between.
"""

from __future__ import annotations

import os
import sys
import time

# How much further now() may run than the awake clock before the gap counts as sleep.
SUSPEND_TOLERANCE_SECONDS = 2.0


def _windows_clocks():
    try:
        import ctypes
        kernel = ctypes.windll.kernelbase  # type: ignore[attr-defined]
        with_sleep, awake = kernel.QueryInterruptTime, kernel.QueryUnbiasedInterruptTime
    except (AttributeError, OSError, ImportError):
        return None

    def reader(query):
        value = ctypes.c_ulonglong()

        def read() -> float:
            query(ctypes.byref(value))
            return value.value / 10_000_000
        return read
    return reader(with_sleep), reader(awake)


def _pick():
    """``(clock counting sleep, clock stopping during sleep, whether the first one is exact)``."""
    if sys.platform.startswith("linux") and hasattr(time, "CLOCK_BOOTTIME"):
        return (lambda: time.clock_gettime(time.CLOCK_BOOTTIME)), time.monotonic, True
    if sys.platform == "darwin" and hasattr(time, "CLOCK_MONOTONIC"):
        return (lambda: time.clock_gettime(time.CLOCK_MONOTONIC)), time.monotonic, True
    if os.name == "nt":
        clocks = _windows_clocks()
        if clocks is not None:
            return clocks[0], clocks[1], True
    return time.time, time.monotonic, False


_NOW, _AWAKE, EXACT = _pick()


def now() -> float:
    """Seconds on a clock that keeps counting while the computer sleeps."""
    return float(_NOW())


def awake() -> float:
    """Seconds on a clock that stops while the computer sleeps."""
    return float(_AWAKE())


def boot_id() -> int:
    """This boot's identity: the wall-clock time ``now()`` started from, to the nearest minute."""
    return int(round((time.time() - now()) / 60.0)) if EXACT else 0


class SuspendDetector:
    """Reports whether the computer slept since the last ``check()``."""

    def __init__(self):
        self._last = (now(), awake())

    def check(self) -> bool:
        current = (now(), awake())
        (previous_now, previous_awake), self._last = self._last, current
        return (current[0] - previous_now) - (current[1] - previous_awake) > SUSPEND_TOLERANCE_SECONDS

"""A trimmed host-terminal replay restores the modes its full-screen program set (vim, htop)."""
import pytest

from hermes_cli.pty_terminal_modes import TerminalModes
from hermes_cli.web_host_terminal_sessions import HostTerminalRegistry
from tests.hermes_cli.test_pty_session import FakeBridge, FakeWS
from tests.waiting import async_wait_until

SETUP = b"\x1b[?1049h\x1b[?1002h\x1b[?1006h\x1b[?1h\x1b=\x1b[?2004h\x1b[?25l"
CHECKPOINT = b"\x1b[?1049h\x1b[?1002h\x1b[?1006h\x1b[?1h\x1b[?66h\x1b[?2004h\x1b[?25l"


def test_modes_follow_xterm_resets_across_split_chunks():
    modes = TerminalModes()
    for byte in SETUP:  # every sequence arrives split
        modes.feed(bytes([byte]))
    assert modes.checkpoint() == CHECKPOINT
    modes.feed(b"\x1b[!p")  # soft reset keeps the screen buffer and mouse reporting
    assert modes.checkpoint() == b"\x1b[?1049h\x1b[?1002h\x1b[?1006h"
    modes.feed(b"\x1b[?1000l\x1b[?1049l")
    assert modes.checkpoint() == b"\x1b[?1006h"
    modes.feed(b"\x1bc")
    assert modes.checkpoint() == b""


@pytest.mark.asyncio
async def test_trimmed_host_replay_puts_the_modes_back_before_the_tail():
    registry = HostTerminalRegistry()
    tail = b"x" * (1024 * 1024 + 1)
    try:
        session, _ = await registry.attach_or_spawn("t", spawn=lambda: FakeBridge([SETUP, tail]))
        await async_wait_until(lambda: session.buffer.truncated)
        ws = FakeWS()
        assert await session.attach(ws, initial_text="meta")
        # The setup sequences and one byte fell out of the 1 MiB buffer.
        assert ws.sent[1] == ("bytes", CHECKPOINT + tail[1:])
    finally:
        await registry.close_all()

"""Terminal modes a full-screen program set, so a trimmed reattach replay can restore them.

A host shell's reattach replays the last 1 MiB of output. Once a full-screen program
(vim, htop, ``hermes --tui``) has written more than that, the tail no longer holds the
sequences that switched on the alternate screen, mouse reporting, application cursor
keys or bracketed paste. ``TerminalModes`` follows those DEC private modes the way the
browser's xterm.js applies them, so the replay can re-emit them ahead of the tail.
"""

from __future__ import annotations

import re

# DECSET/DECRST (ESC [ ? Pm h|l), DECSTR (ESC [ ! p), RIS (ESC c), keypad (ESC = / ESC >).
_SEQUENCE = re.compile(rb"\x1b(?:\[\?([0-9;]*)([hl])|(\[!p)|(c)|([=>]))")
_ALT_SCREEN = frozenset({47, 1047, 1049})
_MOUSE_PROTOCOL = frozenset({9, 1000, 1002, 1003})
_MOUSE_ENCODING = frozenset({1006, 1016})
# On/off modes a soft reset clears. 66 (DECNKM) is the flag ESC = / ESC > also set.
_FLAGS = frozenset({1, 66, 1004, 2004})
# Longest unfinished sequence carried into the next chunk.
_CARRY_LIMIT = 64


class TerminalModes:
    def __init__(self) -> None:
        self._carry = b""
        self._reset()

    def _reset(self) -> None:
        self.alt: int | None = None
        self.mouse: int | None = None
        self.encoding: int | None = None
        self.flags: set[int] = set()
        self.cursor_hidden = False
        self.wrap_off = False

    def feed(self, data: bytes) -> None:
        data = self._carry + data
        end = 0
        for match in _SEQUENCE.finditer(data):
            end = match.end()
            params, action, soft_reset, full_reset, keypad = match.groups()
            if action is not None:
                for value in params.split(b";"):
                    if value.isdigit():
                        self._apply(int(value), action == b"h")
            elif keypad is not None:
                self._apply(66, keypad == b"=")
            elif full_reset is not None:
                self._reset()
            elif soft_reset is not None:
                # xterm.js keeps the screen buffer and mouse reporting on a soft reset.
                self.flags.clear()
                self.cursor_hidden = self.wrap_off = False
        tail = data.rfind(b"\x1b", end)
        self._carry = data[tail:] if tail != -1 and len(data) - tail <= _CARRY_LIMIT else b""

    def _apply(self, mode: int, on: bool) -> None:
        if mode in _ALT_SCREEN:
            self.alt = mode if on else None
        elif mode in _MOUSE_PROTOCOL:
            self.mouse = mode if on else None
        elif mode in _MOUSE_ENCODING:
            self.encoding = mode if on else None
        elif mode in _FLAGS:
            (self.flags.add if on else self.flags.discard)(mode)
        elif mode == 25:
            self.cursor_hidden = not on
        elif mode == 7:
            self.wrap_off = not on

    def checkpoint(self) -> bytes:
        """What puts a freshly reset terminal back into the current modes (screen buffer first)."""
        modes = [mode for mode in (self.alt, self.mouse, self.encoding) if mode is not None]
        out = b"".join(b"\x1b[?%dh" % mode for mode in [*modes, *sorted(self.flags)])
        if self.cursor_hidden:
            out += b"\x1b[?25l"
        if self.wrap_off:
            out += b"\x1b[?7l"
        return out

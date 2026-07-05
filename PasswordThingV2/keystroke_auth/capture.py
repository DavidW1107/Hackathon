"""Live keyboard capture via pynput.

pynput taps OS-level key events, so we get the full feature set — dwell, flight,
rollover, and which physical Shift was used — even from a plain terminal. On
macOS the first run needs Input Monitoring permission for the terminal/host app.

Everything here is import-lazy: the engine, simulator, tests, and the ``demo``
command all run without pynput installed. Only :func:`capture_attempt` needs it.
"""

from __future__ import annotations

import sys
import threading
import time
from dataclasses import dataclass
from typing import Any

from .models import Attempt, KeyEvent
from .render import Ink, render_event_line


@dataclass(frozen=True)
class KeyInfo:
    key_id: str
    label: str
    char: str | None = None
    is_shift: bool = False
    shift_side: str | None = None
    is_backspace: bool = False
    is_enter: bool = False
    is_escape: bool = False


class TerminalEchoGuard:
    """Suppress terminal echo so the typed password never hits the screen."""

    def __init__(self) -> None:
        self._fd: int | None = None
        self._settings: list[Any] | None = None

    def __enter__(self) -> "TerminalEchoGuard":
        try:
            import termios
        except ImportError:
            return self
        if sys.stdin.isatty():
            self._fd = sys.stdin.fileno()
            self._settings = termios.tcgetattr(self._fd)
            new_settings = termios.tcgetattr(self._fd)
            new_settings[3] = new_settings[3] & ~termios.ECHO
            termios.tcsetattr(self._fd, termios.TCSADRAIN, new_settings)
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        if self._fd is not None and self._settings is not None:
            import termios

            termios.tcsetattr(self._fd, termios.TCSADRAIN, self._settings)


def capture_attempt(prompt: str, ink: Ink, *, realtime: bool = True) -> Attempt:
    """Capture one password attempt from the live keyboard.

    Enter finishes, Escape cancels, Backspace discards the attempt (the clean
    "retype instead of correcting" policy that keeps the timing vector honest).
    """
    try:
        from pynput import keyboard
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise RuntimeError(
            "pynput is not installed. Run `python3 -m pip install -e .` "
            "(or use `demo`, which needs no keyboard)."
        ) from exc

    events: list[KeyEvent] = []
    text_chars: list[str] = []
    pressed_keys: set[str] = set()
    active_shifts: set[str] = set()
    corrected = False
    aborted = False
    done = threading.Event()
    start = time.perf_counter()

    print(prompt)
    print(ink("Enter finishes · Escape cancels · Backspace discards this attempt.", "grey"))

    def emit(t: float, kind: str, info: KeyInfo) -> None:
        if not realtime:
            return
        print(
            render_event_line(
                t * 1000.0,
                kind,
                info.label,
                info.char,
                active_shifts,
                "".join(text_chars),
                ink,
            )
        )

    def on_press(key: Any) -> bool | None:
        nonlocal corrected, aborted
        t = time.perf_counter() - start
        info = key_info(key, keyboard)
        repeated = info.key_id in pressed_keys and not info.is_shift
        pressed_keys.add(info.key_id)
        if info.is_shift and info.shift_side:
            active_shifts.add(info.shift_side)

        char = info.char if info.char is not None and not repeated else None
        events.append(
            KeyEvent(t=t, kind="down", key=info.key_id, char=char,
                     is_shift=info.is_shift, shift_side=info.shift_side)
        )

        if info.is_backspace:
            corrected = True
            emit(t, "DOWN", info)
            print(ink("Backspace detected; this attempt will be discarded.", "yellow"))
            done.set()
            return False
        if info.is_escape:
            aborted = True
            emit(t, "DOWN", info)
            done.set()
            return False
        if info.is_enter:
            emit(t, "DOWN", info)
            done.set()
            return False
        if char is not None:
            text_chars.append(char)
        emit(t, "DOWN", info)
        return None

    def on_release(key: Any) -> bool | None:
        t = time.perf_counter() - start
        info = key_info(key, keyboard)
        events.append(
            KeyEvent(t=t, kind="up", key=info.key_id, char=None,
                     is_shift=info.is_shift, shift_side=info.shift_side)
        )
        pressed_keys.discard(info.key_id)
        emit(t, "UP", info)
        if info.is_shift and info.shift_side:
            active_shifts.discard(info.shift_side)
        return None

    with TerminalEchoGuard():
        with keyboard.Listener(on_press=on_press, on_release=on_release) as listener:
            done.wait()
            listener.stop()

    print()
    return Attempt(text="".join(text_chars), events=events, corrected=corrected, aborted=aborted)


def capture_attempt_stdin(prompt: str, ink: Ink, *, realtime: bool = True) -> Attempt:
    """Capture by reading this terminal's own input stream (no global tap).

    This path is immune to macOS Secure Keyboard Entry and needs no Input
    Monitoring permission, so you can type directly in the focused terminal. The
    trade-off: a TTY only delivers key *presses*, not releases — so there is **no
    dwell time and no Shift-side detection**, only flight timing between keys and
    total duration. Enroll and verify must both use this mode to stay compatible.
    """
    import termios
    import tty

    if not sys.stdin.isatty():
        raise RuntimeError("stdin capture needs an interactive terminal (a TTY).")

    print(prompt)
    print(ink("Type in THIS terminal. Enter finishes · Esc cancels · Backspace discards.", "grey"))
    print(ink("(stdin mode: flight timing only — no dwell/shift — but no permission needed "
              "and it works with the terminal focused.)", "grey"))

    events: list[KeyEvent] = []
    text_chars: list[str] = []
    corrected = False
    aborted = False

    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)
    start = time.perf_counter()
    try:
        tty.setcbreak(fd)  # char-at-a-time
        new_settings = termios.tcgetattr(fd)
        new_settings[3] = new_settings[3] & ~termios.ECHO  # never echo the password
        termios.tcsetattr(fd, termios.TCSADRAIN, new_settings)

        while True:
            ch = sys.stdin.read(1)
            if not ch:
                break
            t = time.perf_counter() - start
            code = ord(ch)

            if ch in ("\r", "\n"):
                if realtime:
                    print(render_event_line(t * 1000, "DOWN", "enter", None, set(),
                                            "".join(text_chars), ink))
                break
            if code == 27:  # Esc
                aborted = True
                if realtime:
                    print(render_event_line(t * 1000, "DOWN", "escape", None, set(),
                                            "".join(text_chars), ink))
                break
            if code in (8, 127):  # Backspace / Delete
                corrected = True
                if realtime:
                    print(render_event_line(t * 1000, "DOWN", "backspace", None, set(),
                                            "".join(text_chars), ink))
                print(ink("Backspace detected; this attempt will be discarded.", "yellow"))
                break
            if code < 32:  # other control byte (e.g. arrow-key escape tail) — skip
                continue

            key = f"char:{ch.lower()}"
            events.append(KeyEvent(t=t, kind="down", key=key, char=ch))
            text_chars.append(ch)
            if realtime:
                print(render_event_line(t * 1000, "DOWN", _display_char(ch), ch, set(),
                                        "".join(text_chars), ink))
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)

    print()
    return Attempt(text="".join(text_chars), events=events, corrected=corrected, aborted=aborted)


def key_info(key: Any, keyboard: Any) -> KeyInfo:
    if key == keyboard.Key.shift_l:
        return KeyInfo("shift:left", "shift_l", is_shift=True, shift_side="left")
    if key == keyboard.Key.shift_r:
        return KeyInfo("shift:right", "shift_r", is_shift=True, shift_side="right")
    if key == keyboard.Key.shift:
        return KeyInfo("shift:left", "shift", is_shift=True, shift_side="left")
    if key == keyboard.Key.backspace:
        return KeyInfo("backspace", "backspace", is_backspace=True)
    if key == keyboard.Key.enter:
        return KeyInfo("enter", "enter", is_enter=True)
    if key == keyboard.Key.esc:
        return KeyInfo("escape", "escape", is_escape=True)
    if key == keyboard.Key.space:
        return KeyInfo("space", "space", char=" ")

    char = getattr(key, "char", None)
    vk = getattr(key, "vk", None)
    if vk is not None:
        key_id = f"vk:{vk}"
    elif char is not None:
        key_id = f"char:{char.lower()}"
    else:
        key_id = str(key)

    if char is not None and len(char) == 1 and char >= " ":
        return KeyInfo(key_id=key_id, label=_display_char(char), char=char)
    return KeyInfo(key_id=key_id, label=str(key))


def _display_char(char: str) -> str:
    if char == " ":
        return "space"
    if char == "\t":
        return "tab"
    return repr(char)

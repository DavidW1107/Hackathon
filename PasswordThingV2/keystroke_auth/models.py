"""Core value types shared across capture, simulation, and the engine.

These are deliberately plain: a :class:`KeyEvent` is one low-level keyboard
event (from pynput *or* the simulator), and an :class:`Attempt` is the raw
event stream for a single typed password plus the text it produced. Everything
downstream — feature extraction, scoring, the eventual website port — consumes
these and nothing else.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class KeyEvent:
    """A single keyboard transition.

    Attributes:
        t: Seconds since the attempt started (monotonic).
        kind: ``"down"`` for a press, ``"up"`` for a release.
        key: Stable identifier for the physical key (used to pair down/up).
        char: The character produced, if any (``None`` for shift/enter/etc.).
        is_shift: Whether this event is a Shift key.
        shift_side: ``"left"`` or ``"right"`` for Shift events, else ``None``.
    """

    t: float
    kind: str
    key: str
    char: str | None = None
    is_shift: bool = False
    shift_side: str | None = None


@dataclass
class Attempt:
    """One typed password attempt and the events that produced it."""

    text: str
    events: list[KeyEvent] = field(default_factory=list)
    corrected: bool = False
    aborted: bool = False


@dataclass(frozen=True)
class Stroke:
    """A paired keydown/keyup for a single password character.

    ``index`` is the position of the character in the password (0-based),
    ``down_t``/``up_t`` are the press and release times in seconds.
    """

    index: int
    char: str
    key: str
    down_t: float
    up_t: float

    @property
    def dwell(self) -> float:
        """Hold time in seconds (keydown -> keyup)."""
        return self.up_t - self.down_t

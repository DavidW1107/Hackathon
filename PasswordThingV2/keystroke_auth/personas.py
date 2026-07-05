"""Synthetic typists, so the whole system demos with zero permissions.

Live capture needs macOS Input Monitoring and a human at the keyboard. That is
great for the real thing but useless in a script, a CI run, or a 60-second
hackathon demo. A :class:`TypingPersona` fabricates a realistic ``Attempt`` —
the same :class:`~keystroke_auth.models.KeyEvent` stream pynput would emit, fed
through the *exact same* feature extractor — so nothing about the engine is
faked or shortcut.

Each persona has a deterministic *fingerprint*: a stable per-key dwell and
per-digraph flight offset derived by hashing ``(seed, key, position)``. Repeated
attempts by one persona cluster tightly (small per-attempt jitter); different
personas sit far apart (different seeds and speed classes). That is exactly the
low-within / high-between structure keystroke dynamics relies on.

The cast:
    genuine       the enrolled user (built per demo from a seed)
    stranger      knows the password, types at a normal speed, own fingerprint
                  — the realistic threat, and the one that proves the concept
    hunt_and_peck slow and erratic
    speed_demon   very fast; flights shorter than dwell -> keys roll over
    wrong_shift   genuine-ish timing but capitalises with the other Shift
"""

from __future__ import annotations

import hashlib
import random
from dataclasses import dataclass

from .models import Attempt, KeyEvent

# US-keyboard characters that require Shift (besides A-Z).
_SHIFT_SYMBOLS = set('~!@#$%^&*()_+{}|:"<>?')

# Minimum physically-plausible dwell / flight, in seconds.
_MIN_DWELL_S = 0.015
_MIN_FLIGHT_S = 0.008
_SAME_KEY_GAP_S = 0.006  # a key must be released before it can be pressed again


def needs_shift(char: str) -> bool:
    return char.isupper() or char in _SHIFT_SYMBOLS


@dataclass(frozen=True)
class TypingPersona:
    """A reproducible synthetic typist.

    ``*_base`` values set the persona's speed class, ``*_spread`` sets how much
    the deterministic fingerprint varies key-to-key, and ``*_jitter`` sets the
    per-attempt noise (small = a consistent typist). All values are milliseconds.
    """

    name: str
    seed: int
    dwell_base: float = 95.0
    dwell_spread: float = 30.0
    dwell_jitter: float = 7.0
    flight_base: float = 115.0
    flight_spread: float = 45.0
    flight_jitter: float = 12.0
    shift_side: str = "left"
    shift_lead: float = 45.0
    shift_lag: float = 30.0
    shift_spread: float = 15.0
    shift_jitter: float = 6.0

    # -- deterministic fingerprint -------------------------------------------

    def _unit(self, *parts: object) -> float:
        """A stable pseudo-random value in [0, 1) keyed by this persona + parts."""
        payload = "|".join(str(p) for p in (self.seed, *parts)).encode("utf-8")
        digest = hashlib.sha256(payload).digest()
        return int.from_bytes(digest[:8], "big") / 2**64

    def _centered(self, *parts: object) -> float:
        """Fingerprint offset in [-1, 1)."""
        return (self._unit(*parts) - 0.5) * 2.0

    def sample_dwell(self, char: str, index: int, rng: random.Random) -> float:
        base = self.dwell_base + self._centered("dwell", char, index) * self.dwell_spread
        return base + rng.gauss(0.0, self.dwell_jitter)

    def sample_flight(self, a: str, b: str, index: int, rng: random.Random) -> float:
        base = self.flight_base + self._centered("flight", a, b, index) * self.flight_spread
        return base + rng.gauss(0.0, self.flight_jitter)

    def sample_shift_lead(self, char: str, index: int, rng: random.Random) -> float:
        base = self.shift_lead + self._centered("lead", char, index) * self.shift_spread
        return base + rng.gauss(0.0, self.shift_jitter)

    def sample_shift_lag(self, char: str, index: int, rng: random.Random) -> float:
        base = self.shift_lag + self._centered("lag", char, index) * self.shift_spread
        return base + rng.gauss(0.0, self.shift_jitter)


def synthesize_attempt(text: str, persona: TypingPersona, rng: random.Random) -> Attempt:
    """Fabricate one realistic ``Attempt`` for ``persona`` typing ``text``.

    Builds down/up events (plus Shift wrap events for capitals/symbols) on a
    timeline, then normalises so the earliest event is at t=0. Rollover — the
    next key going down before the previous is released — emerges naturally
    whenever a persona's flight is shorter than its dwell, no special case.
    """
    raw: list[tuple[float, str, str, str | None, bool, str | None]] = []
    prev_down = 0.0
    prev_up = 0.0
    prev_key: str | None = None
    prev_char = ""
    down = 0.0

    for i, char in enumerate(text):
        key = _key_id(char)
        dwell_s = max(_MIN_DWELL_S, persona.sample_dwell(char, i, rng) / 1000.0)

        if i == 0:
            down = 0.0
        else:
            flight_s = max(_MIN_FLIGHT_S, persona.sample_flight(prev_char, char, i - 1, rng) / 1000.0)
            down = prev_down + flight_s
            if key == prev_key:
                # Same physical key: it must be released before re-pressed.
                down = max(down, prev_up + _SAME_KEY_GAP_S)

        up = down + dwell_s

        if needs_shift(char):
            side = persona.shift_side
            skey = f"shift:{side}"
            lead_s = max(0.0, persona.sample_shift_lead(char, i, rng) / 1000.0)
            lag_s = max(0.0, persona.sample_shift_lag(char, i, rng) / 1000.0)
            raw.append((down - lead_s, "down", skey, None, True, side))
            raw.append((up + lag_s, "up", skey, None, True, side))

        raw.append((down, "down", key, char, False, None))
        raw.append((up, "up", key, None, False, None))

        prev_down, prev_up, prev_key, prev_char = down, up, key, char

    # Normalise so the first event (possibly a leading Shift press) starts at 0.
    offset = min(t for t, *_ in raw) if raw else 0.0
    raw.sort(key=lambda item: item[0])
    events = [
        KeyEvent(t=t - offset, kind=kind, key=key, char=char, is_shift=is_shift, shift_side=side)
        for (t, kind, key, char, is_shift, side) in raw
    ]
    return Attempt(text=text, events=events)


def synthesize_attempts(
    text: str, persona: TypingPersona, count: int, *, seed: int = 0
) -> list[Attempt]:
    """Generate ``count`` reproducible attempts for a persona."""
    rng = random.Random((persona.seed << 8) ^ seed)
    return [synthesize_attempt(text, persona, rng) for _ in range(count)]


# --------------------------------------------------------------------------- #
# The demo cast
# --------------------------------------------------------------------------- #


def make_genuine(seed: int, *, shift_side: str = "left") -> TypingPersona:
    """The enrolled user: moderate speed, consistent, low jitter."""
    return TypingPersona(
        name="genuine",
        seed=seed,
        dwell_base=95.0,
        dwell_spread=30.0,
        dwell_jitter=6.0,
        flight_base=115.0,
        flight_spread=45.0,
        flight_jitter=10.0,
        shift_side=shift_side,
    )


def impostor_cast(genuine_seed: int) -> dict[str, TypingPersona]:
    """The stock impostors, seeded to differ from the genuine user."""
    return {
        "stranger": TypingPersona(
            name="stranger",
            seed=genuine_seed + 101,
            dwell_base=105.0,
            dwell_spread=35.0,
            dwell_jitter=9.0,
            flight_base=125.0,
            flight_spread=55.0,
            flight_jitter=15.0,
            shift_side="left",
        ),
        "hunt_and_peck": TypingPersona(
            name="hunt_and_peck",
            seed=genuine_seed + 202,
            dwell_base=150.0,
            dwell_spread=60.0,
            dwell_jitter=25.0,
            flight_base=280.0,
            flight_spread=150.0,
            flight_jitter=55.0,
            shift_side="left",
            shift_lead=120.0,
            shift_lag=80.0,
        ),
        "speed_demon": TypingPersona(
            name="speed_demon",
            seed=genuine_seed + 303,
            dwell_base=55.0,
            dwell_spread=15.0,
            dwell_jitter=6.0,
            flight_base=50.0,
            flight_spread=18.0,
            flight_jitter=8.0,
            shift_side="left",
            shift_lead=25.0,
            shift_lag=15.0,
        ),
        "wrong_shift": TypingPersona(
            name="wrong_shift",
            seed=genuine_seed + 404,
            dwell_base=98.0,
            dwell_spread=32.0,
            dwell_jitter=8.0,
            flight_base=118.0,
            flight_spread=48.0,
            flight_jitter=12.0,
            shift_side="right",  # capitalises with the other hand
        ),
    }


def _key_id(char: str) -> str:
    return f"key:{char.lower()}"

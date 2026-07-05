from __future__ import annotations

import unittest

from keystroke_auth.features import extract_features
from keystroke_auth.models import Attempt, KeyEvent


def make_attempt(
    text: str, *, dwell_ms: float = 80.0, flight_dd_ms: float = 130.0
) -> Attempt:
    """A simple, evenly-typed attempt with fixed dwell and keydown->keydown gap."""
    events: list[KeyEvent] = []
    down = 0.0
    for char in text:
        up = down + dwell_ms / 1000.0
        key = f"key:{char}"
        events.append(KeyEvent(t=down, kind="down", key=key, char=char))
        events.append(KeyEvent(t=up, kind="up", key=key))
        down += flight_dd_ms / 1000.0
    return Attempt(text=text, events=events)


class FeatureExtractionTest(unittest.TestCase):
    def test_dwell_flight_and_negative_rollover(self) -> None:
        # dwell 100ms, keydown->keydown 70ms -> keyup->keydown is 70-100 = -30ms.
        features = extract_features(make_attempt("hello1", dwell_ms=100.0, flight_dd_ms=70.0))
        self.assertAlmostEqual(features["dwell.0"], 100.0)
        self.assertAlmostEqual(features["flight_dd.0"], 70.0)
        self.assertAlmostEqual(features["flight_ud.0"], -30.0)

    def test_total_duration_spans_first_down_to_last_up(self) -> None:
        features = extract_features(make_attempt("abcdef", dwell_ms=50.0, flight_dd_ms=100.0))
        # 5 gaps * 100ms + final dwell 50ms = 550ms.
        self.assertAlmostEqual(features["total_duration"], 550.0)

    def test_lowercase_password_has_no_shift_features(self) -> None:
        features = extract_features(make_attempt("hunter"))
        self.assertFalse(any(name.startswith("shift.") for name in features))

    def test_stroke_count_mismatch_raises(self) -> None:
        # Two chars of text but only one timed stroke.
        attempt = Attempt(
            text="ab",
            events=[KeyEvent(t=0.0, kind="down", key="key:a", char="a"),
                    KeyEvent(t=0.08, kind="up", key="key:a")],
        )
        with self.assertRaises(ValueError):
            extract_features(attempt)

    def test_left_shift_capital_is_captured(self) -> None:
        events = [
            KeyEvent(t=0.000, kind="down", key="shift:left", is_shift=True, shift_side="left"),
            KeyEvent(t=0.050, kind="down", key="key:h", char="H"),
            KeyEvent(t=0.130, kind="up", key="key:h"),
            KeyEvent(t=0.160, kind="up", key="shift:left", is_shift=True, shift_side="left"),
            KeyEvent(t=0.200, kind="down", key="key:i", char="i"),
            KeyEvent(t=0.280, kind="up", key="key:i"),
        ]
        features = extract_features(Attempt(text="Hi", events=events))
        self.assertEqual(features["shift.left.active.0"], 1.0)
        self.assertEqual(features["shift.right.active.0"], 0.0)
        self.assertAlmostEqual(features["shift.left.lead.0"], 50.0)  # shift down 50ms before H
        self.assertAlmostEqual(features["shift.left.lag.0"], 30.0)   # shift up 30ms after H up


if __name__ == "__main__":
    unittest.main()

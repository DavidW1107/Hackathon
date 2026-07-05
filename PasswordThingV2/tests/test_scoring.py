from __future__ import annotations

import os
import tempfile
import unittest

from keystroke_auth.profile import (
    build_profile,
    load_profile,
    save_profile,
    update_profile_with_attempt,
)
from keystroke_auth.scoring import compare_attempt, score_to_confidence

from tests.test_features import make_attempt


def enrollment(dwell_ms: float = 80.0, flight_dd_ms: float = 120.0):
    # Six near-identical attempts with tiny variation so stdev is non-zero.
    return [
        make_attempt("hello1", dwell_ms=dwell_ms + j, flight_dd_ms=flight_dd_ms + j)
        for j in (-2.0, -1.0, 0.0, 1.0, 2.0, 0.5)
    ]


class ScoringTest(unittest.TestCase):
    def test_genuine_passes_slow_impostor_fails(self) -> None:
        profile = build_profile(enrollment(), discard=1, threshold=2.2)

        good = compare_attempt(profile, make_attempt("hello1", dwell_ms=81.0, flight_dd_ms=121.0))
        impostor = compare_attempt(profile, make_attempt("hello1", dwell_ms=190.0, flight_dd_ms=310.0))

        self.assertTrue(good.passed)
        self.assertLess(good.anomaly_score, profile.threshold)
        self.assertFalse(impostor.passed)
        self.assertGreater(impostor.anomaly_score, profile.threshold)

    def test_wrong_text_rejected_before_scoring(self) -> None:
        profile = build_profile(enrollment(), discard=1, threshold=2.2)
        result = compare_attempt(profile, make_attempt("hellox"))
        self.assertFalse(result.passed)
        self.assertFalse(result.text_ok)
        self.assertEqual(result.reason, "password text does not match")
        self.assertEqual(result.anomaly_score, float("inf"))

    def test_wrong_length_rejected(self) -> None:
        profile = build_profile(enrollment(), discard=1, threshold=2.2)
        result = compare_attempt(profile, make_attempt("hello12"))
        self.assertFalse(result.passed)
        self.assertFalse(result.text_ok)

    def test_threshold_override_flips_verdict(self) -> None:
        profile = build_profile(enrollment(), discard=1, threshold=2.2)
        # A moderately-off attempt: passes when loose, fails when strict.
        attempt = make_attempt("hello1", dwell_ms=110.0, flight_dd_ms=150.0)
        loose = compare_attempt(profile, attempt, threshold=6.0)
        strict = compare_attempt(profile, attempt, threshold=0.5)
        self.assertTrue(loose.passed)
        self.assertFalse(strict.passed)
        # Same underlying score, different dial.
        self.assertAlmostEqual(loose.anomaly_score, strict.anomaly_score)

    def test_corrected_attempt_rejected(self) -> None:
        profile = build_profile(enrollment(), discard=1, threshold=2.2)
        attempt = make_attempt("hello1")
        attempt.corrected = True
        result = compare_attempt(profile, attempt)
        self.assertFalse(result.passed)
        self.assertIn("backspace", result.reason.lower())

    def test_adaptive_update_increments_counts(self) -> None:
        profile = build_profile(enrollment(), discard=1, threshold=2.2)
        before = profile.stats["dwell.0"].n
        update_profile_with_attempt(profile, make_attempt("hello1", dwell_ms=83.0))
        self.assertEqual(profile.stats["dwell.0"].n, before + 1)
        self.assertEqual(profile.adaptive_updates, 1)

    def test_build_rejects_mismatched_passwords(self) -> None:
        attempts = enrollment()
        attempts[-1] = make_attempt("world1")
        with self.assertRaises(ValueError):
            build_profile(attempts, discard=1)

    def test_confidence_is_fifty_percent_at_threshold(self) -> None:
        self.assertAlmostEqual(score_to_confidence(2.2, 2.2), 50.0)
        self.assertEqual(score_to_confidence(float("inf"), 2.2), 0.0)
        self.assertGreater(score_to_confidence(0.5, 2.2), 90.0)


class AdaptiveWindowTest(unittest.TestCase):
    def test_cumulative_mode_keeps_full_history(self) -> None:
        profile = build_profile(enrollment(dwell_ms=80.0), discard=1, threshold=2.2)
        self.assertEqual(profile.window_size, 0)
        self.assertEqual(profile.samples, [])
        for _ in range(3):
            update_profile_with_attempt(profile, make_attempt("hello1", dwell_ms=200.0))
        # The mean is pulled toward 200 but the old ~80ms history holds it back.
        self.assertTrue(80.0 < profile.stats["dwell.0"].mean < 150.0)
        self.assertEqual(profile.adaptive_updates, 3)

    def test_window_is_seeded_and_capped(self) -> None:
        profile = build_profile(enrollment(), discard=1, threshold=2.2, window_size=3)
        self.assertEqual(profile.window_size, 3)
        self.assertEqual(len(profile.samples), 3)  # seeded from recent enrollment
        for _ in range(5):
            update_profile_with_attempt(profile, make_attempt("hello1", dwell_ms=120.0))
        self.assertEqual(len(profile.samples), 3)  # never exceeds the window

    def test_window_forgets_old_attempts(self) -> None:
        profile = build_profile(enrollment(dwell_ms=80.0), discard=1, threshold=2.2, window_size=3)
        # After window_size new attempts, the enrollment samples are fully gone.
        for _ in range(3):
            update_profile_with_attempt(profile, make_attempt("hello1", dwell_ms=200.0))
        self.assertAlmostEqual(profile.stats["dwell.0"].mean, 200.0, delta=1.0)

    def test_window_survives_save_and_load(self) -> None:
        profile = build_profile(enrollment(), discard=1, threshold=2.2, window_size=4)
        update_profile_with_attempt(profile, make_attempt("hello1", dwell_ms=120.0))
        fd, path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        try:
            save_profile(profile, path)
            loaded = load_profile(path)
        finally:
            os.unlink(path)
        self.assertEqual(loaded.window_size, 4)
        self.assertEqual(len(loaded.samples), len(profile.samples))
        self.assertAlmostEqual(
            loaded.stats["dwell.0"].mean, profile.stats["dwell.0"].mean, places=6
        )

    def test_negative_window_rejected(self) -> None:
        with self.assertRaises(ValueError):
            build_profile(enrollment(), discard=1, window_size=-1)

    def test_capture_mode_defaults_and_round_trips(self) -> None:
        full = build_profile(enrollment(), discard=1)
        self.assertEqual(full.capture_mode, "full")
        flight = build_profile(enrollment(), discard=1, capture_mode="flight_only")
        self.assertEqual(flight.capture_mode, "flight_only")
        fd, path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        try:
            save_profile(flight, path)
            loaded = load_profile(path)
        finally:
            os.unlink(path)
        self.assertEqual(loaded.capture_mode, "flight_only")

    def test_legacy_profile_defaults_to_full_mode(self) -> None:
        # A profile written before capture_mode existed must load as "full".
        profile = build_profile(enrollment(), discard=1)
        data = profile.to_json()
        del data["capture_mode"]
        from keystroke_auth.profile import Profile

        self.assertEqual(Profile.from_json(data).capture_mode, "full")


if __name__ == "__main__":
    unittest.main()

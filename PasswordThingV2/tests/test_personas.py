from __future__ import annotations

import unittest

from keystroke_auth.features import extract_features
from keystroke_auth.personas import (
    impostor_cast,
    make_genuine,
    needs_shift,
    synthesize_attempt,
    synthesize_attempts,
)
from keystroke_auth.profile import build_profile
from keystroke_auth.scoring import compare_attempt


class PersonaTest(unittest.TestCase):
    def test_synthesized_attempt_round_trips_through_extractor(self) -> None:
        # A synthesized attempt must produce exactly one stroke per character.
        attempts = synthesize_attempts("Hello123", make_genuine(7), 1, seed=1)
        features = extract_features(attempts[0])
        for i in range(8):
            self.assertIn(f"dwell.{i}", features)
        self.assertIn("shift.left.active.0", features)  # the capital H

    def test_reproducible_for_a_fixed_seed(self) -> None:
        a = synthesize_attempts("hello1", make_genuine(3), 4, seed=11)
        b = synthesize_attempts("hello1", make_genuine(3), 4, seed=11)
        self.assertEqual([e.t for e in a[0].events], [e.t for e in b[0].events])
        self.assertEqual([e.t for e in a[3].events], [e.t for e in b[3].events])

    def test_events_start_at_zero(self) -> None:
        attempt = synthesize_attempts("Hello123", make_genuine(9), 1, seed=2)[0]
        self.assertAlmostEqual(min(e.t for e in attempt.events), 0.0)

    def test_speed_demon_produces_rollover(self) -> None:
        # A fast typist's flights are shorter than dwell, so keyup->keydown goes
        # negative somewhere (the next key goes down before the last is released).
        speed = impostor_cast(7)["speed_demon"]
        attempt = synthesize_attempts("hello1", speed, 1, seed=5)[0]
        features = extract_features(attempt)
        ud = [features[k] for k in features if k.startswith("flight_ud.")]
        self.assertTrue(any(v < 0 for v in ud), "expected at least one rollover")

    def test_genuine_passes_and_impostors_are_separable(self) -> None:
        pw, seed = "Hello123", 7
        genuine = make_genuine(seed)
        profile = build_profile(
            synthesize_attempts(pw, genuine, 8, seed=1), discard=2, threshold=2.2
        )

        genuine_scores = [
            compare_attempt(profile, a).anomaly_score
            for a in synthesize_attempts(pw, genuine, 5, seed=500)
        ]
        impostor_scores = [
            compare_attempt(profile, synthesize_attempts(pw, p, 1, seed=500)[0]).anomaly_score
            for p in impostor_cast(seed).values()
        ]

        # The genuine user always scores well under the closest impostor: there
        # exists a threshold that accepts every genuine attempt and rejects all.
        self.assertLess(max(genuine_scores), min(impostor_scores))
        self.assertLess(max(genuine_scores), 1.0)

    def test_wrong_shift_only_matters_with_a_capital(self) -> None:
        self.assertTrue(needs_shift("H"))
        self.assertTrue(needs_shift("!"))
        self.assertFalse(needs_shift("h"))
        self.assertFalse(needs_shift("1"))


if __name__ == "__main__":
    unittest.main()

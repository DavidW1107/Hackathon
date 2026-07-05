"""Score a fresh attempt against a profile.

The anomaly score is the **scaled Manhattan distance**: for each feature we
compute a z-score ``z = (value - mean) / max(stdev, floor)``, then average the
absolute z-scores. This is the metric that won the CMU keystroke benchmark
(Killourhy & Maxion, 2009) and it is fully explainable — every feature's
contribution is right there in the returned rows, so you can point at the exact
gap that betrayed an impostor.

A lower score means "types like the enrolled user". The threshold is the dial
between false accepts (too loose) and false rejects (too strict).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .features import extract_features, feature_floor
from .models import Attempt
from .profile import Profile, password_matches


@dataclass(frozen=True)
class ScoreRow:
    """One feature's contribution to the anomaly score."""

    name: str
    mean: float
    stdev: float
    floor: float
    value: float
    z: float

    @property
    def abs_z(self) -> float:
        return abs(self.z)


@dataclass
class VerificationResult:
    passed: bool
    text_ok: bool
    anomaly_score: float
    threshold: float
    confidence: float
    rows: list[ScoreRow]
    reason: str

    @property
    def suspicious_rows(self) -> list[ScoreRow]:
        """Rows ordered by how far they deviate from the profile."""
        return sorted(self.rows, key=lambda row: row.abs_z, reverse=True)


def _reject(reason: str, threshold: float) -> VerificationResult:
    return VerificationResult(
        passed=False,
        text_ok=False,
        anomaly_score=float("inf"),
        threshold=threshold,
        confidence=0.0,
        rows=[],
        reason=reason,
    )


def compare_attempt(
    profile: Profile,
    attempt: Attempt,
    *,
    threshold: float | None = None,
) -> VerificationResult:
    """Verify ``attempt`` against ``profile``.

    The password text is checked first (constant-time hash compare); only a
    correct password reaches the rhythm scoring. ``threshold`` overrides the
    profile's stored threshold without mutating it — handy for demoing the
    strict/loose trade-off.
    """
    active_threshold = profile.threshold if threshold is None else threshold

    if attempt.corrected:
        return _reject("backspace was used; policy requires a clean retype", active_threshold)
    if len(attempt.text) != profile.password_length:
        return _reject("password length does not match the enrolled password", active_threshold)
    if not password_matches(profile, attempt.text):
        return _reject("password text does not match", active_threshold)

    vector = extract_features(attempt)
    rows: list[ScoreRow] = []
    for name in profile.feature_order:
        stat = profile.stats[name]
        floor = feature_floor(name)
        sigma = max(stat.stdev, floor)
        value = vector.get(name, 0.0)
        z = (value - stat.mean) / sigma
        rows.append(ScoreRow(name=name, mean=stat.mean, stdev=stat.stdev, floor=floor, value=value, z=z))

    anomaly_score = sum(row.abs_z for row in rows) / len(rows) if rows else float("inf")
    confidence = score_to_confidence(anomaly_score, active_threshold)
    passed = anomaly_score <= active_threshold
    reason = "within threshold" if passed else "typing rhythm is outside threshold"
    return VerificationResult(
        passed=passed,
        text_ok=True,
        anomaly_score=anomaly_score,
        threshold=active_threshold,
        confidence=confidence,
        rows=rows,
        reason=reason,
    )


def score_to_confidence(score: float, threshold: float) -> float:
    """Map an anomaly score to a 0-100 confidence that this is the real user.

    Uses ``100 / (1 + (score/threshold)^2)`` so confidence is exactly 50% at
    the threshold, high when the rhythm matches, and decays toward 0 as the
    attempt drifts further away.
    """
    if not math.isfinite(score) or threshold <= 0:
        return 0.0
    return max(0.0, min(100.0, 100.0 / (1.0 + (score / threshold) ** 2)))

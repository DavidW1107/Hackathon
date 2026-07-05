"""Keystroke Dynamics Password System (v2).

Verifies *how* you type your password, not just *what* it is. The public API
below is the pure, portable engine — feature extraction, profiling and scoring
have no I/O or keyboard dependency and lift straight into a website port.
"""

from __future__ import annotations

from .features import (
    extract_features,
    feature_floor,
    feature_label,
    feature_unit,
    timing_feature_order,
)
from .models import Attempt, KeyEvent, Stroke
from .profile import (
    DEFAULT_THRESHOLD,
    MIN_PASSWORD_LENGTH,
    PROFILE_VERSION,
    FeatureStat,
    Profile,
    build_profile,
    load_profile,
    save_profile,
    update_profile_with_attempt,
)
from .scoring import ScoreRow, VerificationResult, compare_attempt, score_to_confidence

__version__ = "2.0.0"

__all__ = [
    "Attempt",
    "KeyEvent",
    "Stroke",
    "Profile",
    "FeatureStat",
    "ScoreRow",
    "VerificationResult",
    "build_profile",
    "compare_attempt",
    "update_profile_with_attempt",
    "extract_features",
    "timing_feature_order",
    "feature_label",
    "feature_unit",
    "feature_floor",
    "score_to_confidence",
    "save_profile",
    "load_profile",
    "DEFAULT_THRESHOLD",
    "MIN_PASSWORD_LENGTH",
    "PROFILE_VERSION",
    "__version__",
]

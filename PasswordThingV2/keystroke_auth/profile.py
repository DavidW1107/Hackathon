"""The per-user profile: password hash + per-feature timing statistics.

A profile never stores the plaintext password — only a salted PBKDF2 hash used
to gate scoring — and one running mean/variance per feature. Enrollment builds
it from several attempts; adaptive updates fold successful logins back in to
track natural drift.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import math
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .features import extract_features, feature_sort_key, timing_feature_order
from .models import Attempt

PROFILE_VERSION = 2
DEFAULT_THRESHOLD = 1.6
MIN_PASSWORD_LENGTH = 6
_PBKDF2_ROUNDS = 210_000


@dataclass
class FeatureStat:
    """Streaming mean/variance for one feature via Welford's algorithm.

    Stores ``m2`` (sum of squared deltas) so new observations can be folded in
    for adaptive updates without keeping the raw samples around.
    """

    n: int
    mean: float
    m2: float

    @property
    def stdev(self) -> float:
        if self.n < 2:
            return 0.0
        return math.sqrt(max(self.m2, 0.0) / (self.n - 1))

    @classmethod
    def from_values(cls, values: list[float]) -> "FeatureStat":
        stat = cls(n=0, mean=0.0, m2=0.0)
        for value in values:
            stat.observe(value)
        return stat

    def observe(self, value: float) -> None:
        self.n += 1
        delta = value - self.mean
        self.mean += delta / self.n
        self.m2 += delta * (value - self.mean)

    def to_json(self) -> dict[str, float | int]:
        return {"n": self.n, "mean": self.mean, "m2": self.m2, "stdev": self.stdev}

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "FeatureStat":
        return cls(n=int(data["n"]), mean=float(data["mean"]), m2=float(data["m2"]))


@dataclass
class Profile:
    version: int
    created_at: str
    password_salt: str
    password_hash: str
    password_length: int
    threshold: float
    feature_order: list[str]
    stats: dict[str, FeatureStat]
    enrollment_attempts: int
    discarded_attempts: int
    adaptive_updates: int = 0
    label: str = "user"
    # How attempts were captured. "full" = OS-level tap (dwell + flight + shift);
    # "flight_only" = TTY/stdin (flight timing only, no key-up so no dwell/shift).
    # Enroll and verify MUST use the same mode or every dwell/shift reads zero.
    capture_mode: str = "full"
    # Adaptive mode. window_size == 0 -> cumulative (every accepted login counts
    # forever, via the FeatureStat counters). window_size > 0 -> sliding window:
    # stats are recomputed over the most recent `window_size` feature vectors,
    # which are kept in `samples` so old attempts can be forgotten.
    window_size: int = 0
    samples: list[dict[str, float]] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "created_at": self.created_at,
            "label": self.label,
            "capture_mode": self.capture_mode,
            "password_salt": self.password_salt,
            "password_hash": self.password_hash,
            "password_length": self.password_length,
            "threshold": self.threshold,
            "feature_order": self.feature_order,
            "stats": {name: stat.to_json() for name, stat in self.stats.items()},
            "enrollment_attempts": self.enrollment_attempts,
            "discarded_attempts": self.discarded_attempts,
            "adaptive_updates": self.adaptive_updates,
            "window_size": self.window_size,
            "samples": self.samples,
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "Profile":
        return cls(
            version=int(data["version"]),
            created_at=str(data["created_at"]),
            label=str(data.get("label", "user")),
            capture_mode=str(data.get("capture_mode", "full")),
            password_salt=str(data["password_salt"]),
            password_hash=str(data["password_hash"]),
            password_length=int(data["password_length"]),
            threshold=float(data["threshold"]),
            feature_order=list(data["feature_order"]),
            stats={
                str(name): FeatureStat.from_json(stat_data)
                for name, stat_data in data["stats"].items()
            },
            enrollment_attempts=int(data["enrollment_attempts"]),
            discarded_attempts=int(data["discarded_attempts"]),
            adaptive_updates=int(data.get("adaptive_updates", 0)),
            window_size=int(data.get("window_size", 0)),
            samples=[
                {str(k): float(v) for k, v in sample.items()}
                for sample in data.get("samples", [])
            ],
        )


def build_profile(
    attempts: list[Attempt],
    *,
    discard: int = 1,
    threshold: float = DEFAULT_THRESHOLD,
    min_length: int = MIN_PASSWORD_LENGTH,
    window_size: int = 0,
    label: str = "user",
    capture_mode: str = "full",
    now: str | None = None,
) -> Profile:
    """Build a profile from enrollment attempts.

    The first ``discard`` attempts are treated as warmup and excluded from the
    statistics (the "practice effect"). All kept attempts must type the same
    password with no corrections.

    ``window_size`` selects the adaptive mode. ``0`` (default) is cumulative:
    every accepted login is weighted equally forever. A positive value keeps a
    **sliding window** of the last ``window_size`` attempts — the window is
    seeded here with the most recent enrollment attempts, and later logins push
    old ones out so the profile keeps tracking your current rhythm.
    """
    if discard < 0:
        raise ValueError("discard must be zero or greater")
    if len(attempts) <= discard:
        raise ValueError("not enough attempts after warmup discard")
    if window_size < 0:
        raise ValueError("window_size must be zero or greater")
    if any(a.corrected for a in attempts):
        raise ValueError("corrected attempts must be discarded before enrollment")

    password = attempts[0].text
    if len(password) < min_length:
        raise ValueError(f"password must contain at least {min_length} keystrokes")
    if any(a.text != password for a in attempts):
        raise ValueError("all enrollment attempts must type the same password")

    included = attempts[discard:]
    vectors = [extract_features(a) for a in included]

    base_order = timing_feature_order(len(password))
    extras = sorted(
        {name for vector in vectors for name in vector if name not in base_order},
        key=feature_sort_key,
    )
    feature_order = base_order + extras

    if window_size > 0:
        # Seed the window with the most recent enrollment attempts.
        samples = [_project(vector, feature_order) for vector in vectors[-window_size:]]
        stats = _stats_from_samples(feature_order, samples)
    else:
        samples = []
        stats = _stats_from_samples(feature_order, vectors)

    salt = base64.urlsafe_b64encode(os.urandom(18)).decode("ascii")
    return Profile(
        version=PROFILE_VERSION,
        created_at=now or datetime.now(timezone.utc).isoformat(),
        label=label,
        capture_mode=capture_mode,
        password_salt=salt,
        password_hash=hash_password(password, salt),
        password_length=len(password),
        threshold=threshold,
        feature_order=feature_order,
        stats=stats,
        enrollment_attempts=len(included),
        discarded_attempts=discard,
        window_size=window_size,
        samples=samples,
    )


def update_profile_with_attempt(profile: Profile, attempt: Attempt) -> None:
    """Fold a (successful) attempt into the profile statistics in place.

    Cumulative mode nudges each running mean/variance by the new sample.
    Sliding-window mode appends the sample, drops anything older than the last
    ``window_size`` attempts, and recomputes the stats over what remains.
    """
    vector = extract_features(attempt)
    if profile.window_size > 0:
        profile.samples.append(_project(vector, profile.feature_order))
        if len(profile.samples) > profile.window_size:
            del profile.samples[: len(profile.samples) - profile.window_size]
        profile.stats = _stats_from_samples(profile.feature_order, profile.samples)
    else:
        for name in profile.feature_order:
            profile.stats[name].observe(vector.get(name, 0.0))
    profile.adaptive_updates += 1


def _project(vector: dict[str, float], feature_order: list[str]) -> dict[str, float]:
    """Keep only the profile's features, filling missing ones with 0.0."""
    return {name: vector.get(name, 0.0) for name in feature_order}


def _stats_from_samples(
    feature_order: list[str], samples: list[dict[str, float]]
) -> dict[str, FeatureStat]:
    return {
        name: FeatureStat.from_values([sample.get(name, 0.0) for sample in samples])
        for name in feature_order
    }


# --------------------------------------------------------------------------- #
# Password hashing & persistence
# --------------------------------------------------------------------------- #


def hash_password(password: str, salt: str) -> str:
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt.encode("ascii"), _PBKDF2_ROUNDS
    )
    return base64.urlsafe_b64encode(digest).decode("ascii")


def password_matches(profile: Profile, password: str) -> bool:
    candidate = hash_password(password, profile.password_salt)
    return hmac.compare_digest(candidate, profile.password_hash)


def save_profile(profile: Profile, path: str | Path) -> None:
    profile_path = Path(path)
    profile_path.parent.mkdir(parents=True, exist_ok=True)
    profile_path.write_text(json.dumps(profile.to_json(), indent=2) + "\n", encoding="utf-8")


def load_profile(path: str | Path) -> Profile:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    profile = Profile.from_json(data)
    if profile.version != PROFILE_VERSION:
        raise ValueError(f"profile version {profile.version} is not supported by this build")
    return profile

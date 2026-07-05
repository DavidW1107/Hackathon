#!/usr/bin/env python3
"""Acoustic sonar DSP primitives — chirp generation + matched-filter ranging.

Shared by emit_test.py and the real-time motion/human sensor. One laptop
speaker + mic gives range only per channel (no bearing from a single mic).
"""
import numpy as np

FS = 48000
C = 343.0
F0, F1 = 17000, 21000   # near-inaudible band (validated flat to ~22kHz on-device)
CHIRP_MS = 6
FRAME_MS = 40           # pulse period -> max unambiguous range ~6.8 m
MIN_RANGE = 0.15        # blind zone (m): gate out speaker->mic crosstalk
MAX_RANGE = 6.0
NOISE_FLOOR = 0.15


def make_chirp():
    n = int(FS * CHIRP_MS / 1000)
    t = np.arange(n) / FS
    k = (F1 - F0) / (CHIRP_MS / 1000)          # linear FM sweep rate
    sig = np.sin(2 * np.pi * (F0 * t + 0.5 * k * t * t))
    sig *= np.hanning(n)                        # taper -> less spectral splatter
    return sig.astype(np.float32)


def estimate_range(frame, chirp):
    """Range (m) of the first echo after the direct path, or nan."""
    corr = np.abs(np.correlate(frame, chirp, mode="valid"))
    t0 = int(np.argmax(corr))                   # direct path = strongest = t0
    peak0 = corr[t0]
    lo = t0 + int(2 * MIN_RANGE / C * FS)
    hi = min(t0 + int(2 * MAX_RANGE / C * FS), len(corr))
    if lo >= hi:
        return np.nan
    seg = corr[lo:hi]
    j = int(np.argmax(seg))
    if seg[j] < NOISE_FLOOR * peak0:
        return np.nan
    return ((lo + j) - t0) / FS * C / 2


def build_emit(seconds):
    chirp = make_chirp()
    flen = int(FS * FRAME_MS / 1000)
    frame = np.zeros(flen, dtype=np.float32)
    frame[:len(chirp)] = chirp
    nframes = int(seconds * 1000 / FRAME_MS)
    return np.tile(frame, nframes), nframes, chirp, flen


def selftest():
    chirp = make_chirp()
    flen = int(FS * FRAME_MS / 1000)
    rng = np.random.default_rng(0)
    for true_r in (0.3, 0.5, 1.0, 2.5, 4.0):
        frame = np.zeros(flen, dtype=np.float32)
        frame[:len(chirp)] = chirp
        lag = int(2 * true_r / C * FS)
        frame[lag:lag + len(chirp)] += 0.3 * chirp
        frame += rng.normal(0, 0.02, flen).astype(np.float32)
        est = estimate_range(frame, chirp)
        assert abs(est - true_r) < 0.03, f"range {true_r}m -> {est}"
        print(f"ok  range {true_r}m -> {est:.3f}m")
    print("selftest pass")


if __name__ == "__main__":
    selftest()

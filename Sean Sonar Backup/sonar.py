#!/usr/bin/env python3
"""
sonar.py -- near-ultrasonic sonar using the laptop's speakers and mic.

Continuously emits short 18-20 kHz chirps (inaudible to most adults),
records with the built-in mic, and matched-filters the recording.
The mic hears each chirp twice: once via the direct speaker->mic path,
and again after reflecting off your hand. Distance is computed from the
delay between those two arrivals, so audio I/O latency cancels out.

A ~2 s calibration at startup learns the static echoes (screen, desk,
walls) as a complex correlation profile; each ping is coherently
subtracted against it so only a moving/new reflector -- your hand --
stands out.

Usage:
    python sonar.py [--debug]

Keep your hands away from the laptop during calibration.
"""

import argparse
import queue
import sys
import time
from collections import deque

import numpy as np
import sounddevice as sd

# ---------------------------- configuration ---------------------------------
FS          = 48_000          # sample rate (Hz)
F0, F1      = 18_000, 20_000  # chirp band (Hz) -- above most adults' hearing
CHIRP_DUR   = 0.006           # chirp length (s)
PING_PERIOD = 0.100           # one ping every 100 ms -> 10 readings/s
VOLUME      = 0.6             # output amplitude (0..1)
C           = 343.0           # speed of sound (m/s)
MIN_D       = 0.07            # closest detectable hand (m)
MAX_D       = 1.00            # farthest detectable hand (m)
CAL_PINGS   = 20              # pings used to learn the static background
SNR_THRESH  = 6.0             # detection threshold (x noise sigma)
ABS_FLOOR   = 0.002           # min echo strength relative to direct path
BG_ADAPT    = 0.02            # slow background adaptation rate
BG_FAST     = 0.10            # fast absorption of stale (phase-frozen) residue
REF_ADAPT   = 0.05            # direct-path reference tracking rate
EMA_A       = 0.20            # temporal-coherence smoothing per ping
COH_STALE   = 0.95            # residue coherence above this = static scene
                              # change, not a live target (living things
                              # wobble in echo phase; even breathing is
                              # >1 rad at these frequencies)
# -----------------------------------------------------------------------------

PING_N  = int(FS * PING_PERIOD)
CHIRP_N = int(FS * CHIRP_DUR)
WIN_N   = 2 * PING_N                      # analysis window: two ping periods
NFFT    = 1 << (WIN_N + CHIRP_N).bit_length()

# echo lag window (samples after the direct-path arrival)
MIN_LAG = int(round(2 * MIN_D / C * FS))
MAX_LAG = int(round(2 * MAX_D / C * FS))

# Complex (analytic) chirp: correlating against it yields a complex output
# whose magnitude is the envelope -- and whose phase lets the static
# background be subtracted coherently.
_t = np.arange(CHIRP_N) / FS
_phase = 2 * np.pi * (F0 * _t + (F1 - F0) / (2 * CHIRP_DUR) * _t**2)
CHIRP_C = np.exp(1j * _phase) * np.hanning(CHIRP_N)
CHIRP_FFT_CONJ = np.conj(np.fft.fft(CHIRP_C, NFFT))

# transmit buffer: one chirp then silence, repeated forever by the callback
TX = np.zeros(PING_N, dtype=np.float32)
TX[:CHIRP_N] = (VOLUME * np.sin(_phase) * np.hanning(CHIRP_N)).astype(np.float32)


def matched_filter(x: np.ndarray) -> np.ndarray:
    """Complex cross-correlation of x with the chirp (magnitude = envelope)."""
    X = np.fft.fft(x, NFFT)
    corr = np.fft.ifft(X * CHIRP_FFT_CONJ)
    return corr[: len(x) - CHIRP_N + 1]


def parabolic_peak(y: np.ndarray, i: int) -> float:
    """Sub-sample peak position via parabolic interpolation around index i."""
    if 0 < i < len(y) - 1:
        denom = y[i - 1] - 2 * y[i] + y[i + 1]
        if abs(denom) > 1e-12:
            return i + 0.5 * (y[i - 1] - y[i + 1]) / denom
    return float(i)


class Sonar:
    """Turns successive WIN_N-sample mic windows into distance readings.

    Windows must advance by exactly PING_N samples so the direct-path
    arrival stays at a fixed index (playback and capture share a clock).
    """

    def __init__(self) -> None:
        self.direct: int | None = None      # locked direct-path index
        self.ref: complex = 0j              # slow-tracked direct-path value
        self.cal: list[np.ndarray] = []
        self.cal_ref: list[complex] = []
        self.bg: np.ndarray | None = None   # complex background profile
        self.med = 0.0                      # noise floor from calibration
        self.sigma = 1e-9
        self.ema_c: np.ndarray | None = None  # complex residual average
        self.ema_m: np.ndarray | None = None  # residual magnitude average
        self.recent: deque[float] = deque(maxlen=3)

    def process(self, window: np.ndarray) -> dict:
        """Returns {'state': 'silent'|'calibrating'|'no_echo'|'echo', ...}."""
        corr = matched_filter(window)
        mag = np.abs(corr[:PING_N])
        p = int(np.argmax(mag))
        # a genuine direct-path arrival towers over the correlation floor;
        # anything else means the mic hears no chirp (muted / no permission)
        if mag[p] < 1e-6 or mag[p] < 20 * np.median(mag):
            return {"state": "silent"}

        # lock onto the direct-path arrival; re-lock only if it truly moved
        # (e.g. audio device change), not on one-sample noise jitter
        if self.direct is None or mag[p] > 2 * mag[self.direct]:
            relocked = self.direct is not None
            self.direct = p
            self.cal.clear()
            self.cal_ref.clear()
            self.bg = None
            self.recent.clear()
            if relocked:
                return {"state": "relock"}

        d = self.direct

        if self.bg is None:
            self.cal.append(corr[d + MIN_LAG: d + MAX_LAG].copy())
            self.cal_ref.append(complex(corr[d]))
            if len(self.cal) >= CAL_PINGS:
                self.ref = np.mean(self.cal_ref)
                self.bg = np.mean(self.cal, axis=0) / self.ref
                # noise floor from the (echo-free) calibration residuals;
                # per-frame estimates would be inflated by the very echoes
                # we want to detect -- a person's many weak reflections
                # would raise the floor until they mask themselves
                pool = np.concatenate(
                    [np.abs(c / self.ref - self.bg) for c in self.cal])
                self.med = float(np.median(pool))
                self.sigma = float(
                    1.4826 * np.median(np.abs(pool - self.med))) + 1e-9
                self.ema_c = np.zeros(len(self.bg), dtype=complex)
                self.ema_m = np.zeros(len(self.bg))
                self.cal.clear()
                self.cal_ref.clear()
            return {"state": "calibrating", "n": len(self.cal)}

        # Echo profile referenced to the slow-tracked direct-path value.
        # The instantaneous corr[d] must not be used here: a hand close to
        # the laptop overlaps the direct chirp and perturbs corr[d] by a few
        # percent, which multiplied by the large direct-path tail would
        # swamp the true echo.
        seg = corr[d + MIN_LAG: d + MAX_LAG] / self.ref
        self.ref = (1 - REF_ADAPT) * self.ref + REF_ADAPT * corr[d]

        # coherent subtraction: static reflectors cancel, a target remains
        cres = seg - self.bg
        resid = np.abs(cres)

        # Temporal coherence per bin: |mean(residual)| / mean(|residual|).
        # A live target wanders in phase ping to ping (even breathing moves
        # the echo by >1 rad), so its coherence stays low; leftover static
        # residue (scene changed, or a target we partially absorbed) is
        # phase-frozen with coherence near 1.
        self.ema_c = (1 - EMA_A) * self.ema_c + EMA_A * cres
        self.ema_m = (1 - EMA_A) * self.ema_m + EMA_A * resid
        coh = np.abs(self.ema_c) / (self.ema_m + 1e-12)

        loud = resid > self.med + 4 * self.sigma
        # the ema_m gate means "stale" needs ~6 frames of sustained,
        # phase-frozen amplitude -- a freshly appeared target (whose EMAs
        # are dominated by one frame, faking coherence 1) is never absorbed
        stale = loud & (coh > COH_STALE) & (self.ema_m > 0.7 * resid)

        # strongest live (non-stale) bin is the candidate target
        live = np.where(stale, 0.0, resid)
        i = int(np.argmax(live))
        snr = float((resid[i] - self.med) / self.sigma)
        detected = snr >= SNR_THRESH and resid[i] >= ABS_FLOOR

        # background update rates by bin class: fast absorption of
        # phase-frozen residue, normal drift far from any activity, and
        # NEVER absorb a live target or its surrounding correlation skirt
        # -- a person standing still must stay visible indefinitely, and
        # nothing of them may leak into the background to resurface as a
        # phantom when they leave
        protect = np.convolve((loud & ~stale).astype(float),
                              np.ones(91), "same") > 0
        rate = np.where(stale, BG_FAST,
                        np.where(protect | loud, 0.0, BG_ADAPT))
        self.bg = self.bg + rate * (seg - self.bg)

        dbg = {"direct_amp": float(mag[d]), "peak": float(resid[i]),
               "sigma": float(self.sigma)}

        if not detected:
            self.recent.clear()
            # the noise floor may drift only while nothing is detected,
            # so a present target can never ratchet it up over itself
            q = resid[~loud]
            if len(q) > 40:
                m = np.median(q)
                s = 1.4826 * np.median(np.abs(q - m)) + 1e-9
                self.med += 0.1 * (m - self.med)
                self.sigma += 0.1 * (s - self.sigma)
            return {"state": "no_echo", "snr": snr, **dbg}

        lag = MIN_LAG + parabolic_peak(resid, i)
        self.recent.append(lag / FS * C / 2)
        return {"state": "echo", "dist": float(np.median(self.recent)),
                "snr": snr, **dbg}


def bar(dist_m: float, width: int = 30) -> str:
    filled = int(round(np.clip(dist_m / MAX_D, 0, 1) * width))
    return "#" * filled + "." * (width - filled)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--debug", action="store_true",
                    help="print raw peak/noise levels for tuning")
    args = ap.parse_args()

    audio_q: queue.Queue[np.ndarray] = queue.Queue()
    tx_pos = 0

    def callback(indata, outdata, frames, time_info, status):
        nonlocal tx_pos
        if status:
            print(f"[audio] {status}", file=sys.stderr)
        idx = (tx_pos + np.arange(frames)) % PING_N
        outdata[:, 0] = TX[idx]
        tx_pos = (tx_pos + frames) % PING_N
        audio_q.put(indata[:, 0].copy())

    print(f"sonar: {F0/1000:.1f}-{F1/1000:.1f} kHz chirps, "
          f"{1/PING_PERIOD:.0f} pings/s, range {MIN_D*100:.0f}-{MAX_D*100:.0f} cm")
    print("make sure system volume is up (~60-80%) -- the chirp is inaudible")
    print(f"calibrating for {CAL_PINGS * PING_PERIOD:.1f} s, "
          "keep hands away from the laptop...")

    sonar = Sonar()
    buf = np.zeros(0, dtype=np.float32)
    silent_count = 0
    was_calibrating = True

    with sd.Stream(samplerate=FS, channels=1, dtype="float32",
                   callback=callback):
        while True:
            buf = np.concatenate([buf, audio_q.get()])
            while len(buf) >= WIN_N:
                window = buf[:WIN_N].astype(np.float64)
                buf = buf[PING_N:]           # advance exactly one ping
                r = sonar.process(window)
                stamp = time.strftime("%H:%M:%S")

                if r["state"] == "silent":
                    silent_count += 1
                    if silent_count % 10 == 1:
                        print(f"{stamp} | mic is silent -- check macOS mic "
                              "permission and system volume")
                    continue
                silent_count = 0

                if r["state"] == "calibrating":
                    continue
                if r["state"] == "relock":
                    print(f"{stamp} | audio path changed -- recalibrating, "
                          "keep hands away...")
                    was_calibrating = True
                    continue
                if was_calibrating:
                    was_calibrating = False
                    print("calibration done -- move your hand "
                          "in front of the laptop\n")

                if r["state"] == "echo":
                    d = r["dist"]
                    line = (f"{stamp} | {d * 100:6.1f} cm |{bar(d)}| "
                            f"snr {r['snr']:5.1f}")
                else:
                    line = f"{stamp} |    --- cm |{'.' * 30}| no echo"

                if args.debug:
                    line += (f" | direct {r['direct_amp']:8.1f} "
                             f"peak {r['peak']:.4f} sigma {r['sigma']:.5f}")
                print(line, flush=True)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nstopped")

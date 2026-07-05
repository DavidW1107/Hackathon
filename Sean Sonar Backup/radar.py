#!/usr/bin/env python3
"""
radar.py -- 2-channel near-ultrasonic radar with a live top-down view.

The left speaker transmits an up-chirp and the right speaker a
down-chirp half a ping later, both sweeping the full 17.6-20.2 kHz
near-ultrasonic band. Time offset plus opposite sweep directions let
the single mic recording be split into per-speaker echo profiles by
matched-filtering against each chirp. Every reflector then yields two
bistatic ranges (speaker->object->mic path lengths), i.e. two ellipses
whose intersection localizes it in 2D: lateral position x and distance z.

Up to 3 reflectors are tracked and drawn as blobs on an ASCII radar,
top-down. Elevation is ambiguous (a hand above the keyboard and one
straight ahead at equal path lengths look identical) -- this is a planar
projection, not full 3D.

Usage:
    python radar.py            live radar view
    python radar.py --log      scrolling text log instead of the radar
    python radar.py --debug    extra per-channel numbers (implies --log)

Keep your hands away from the laptop during the ~2 s calibration.
"""

import argparse
import queue
import sys
import time
from collections import deque

import numpy as np
import sounddevice as sd

# ---------------------------- configuration ---------------------------------
FS          = 48_000            # sample rate (Hz)
BAND        = (17_600, 20_200)  # chirp band (Hz), shared by both speakers:
                                # left sweeps up, right sweeps down, offset
                                # by half a ping period in time
CHIRP_DUR   = 0.010             # chirp length (s)
PING_PERIOD = 0.100             # one ping every 100 ms -> 10 frames/s
VOLUME      = 0.6               # per-channel output amplitude (0..1)
C           = 343.0             # speed of sound (m/s)
CAL_PINGS   = 20                # pings used to learn the static background
SNR_THRESH  = 6.0               # detection threshold (x noise sigma)
ABS_FLOOR   = 0.002             # min echo strength relative to direct path
BG_ADAPT    = 0.02              # slow background adaptation rate
BG_FAST     = 0.10              # fast absorption of stale (phase-frozen) residue
REF_ADAPT   = 0.05              # direct-path reference tracking rate
EMA_A       = 0.20              # temporal-coherence smoothing per frame
COH_STALE   = 0.95              # residue coherence above this = static scene
                                # change, not a live target
SIDE_REJ    = 0.10              # backstop: secondary peaks below this fraction
                                # of the strongest are leftover fit error
MAX_OBJ     = 3                 # max simultaneous reflectors
SUB         = 32                # sub-sample resolution of the echo templates

# geometry (metres, in the keyboard plane; tweak for your machine)
SPK_L_X     = -0.12             # left speaker x
SPK_R_X     = +0.12             # right speaker x
MIC_X       =  0.00             # mic x (MacBooks: often left of centre)

MIN_E       = 0.04              # min bistatic extra path (m)
MAX_E       = 2.30              # max bistatic extra path (m)
# -----------------------------------------------------------------------------

PING_N  = int(FS * PING_PERIOD)
CHIRP_N = int(FS * CHIRP_DUR)
WIN_N   = 2 * PING_N
CORR_N  = WIN_N - CHIRP_N + 1
NFFT    = 1 << (WIN_N + CHIRP_N).bit_length()
MIN_LAG = int(round(MIN_E / C * FS))
MAX_LAG = int(round(MAX_E / C * FS))


def make_chirp(f0: float, f1: float) -> tuple[np.ndarray, np.ndarray]:
    """Real transmit chirp and the FFT-conjugate of its analytic version."""
    t = np.arange(CHIRP_N) / FS
    phase = 2 * np.pi * (f0 * t + (f1 - f0) / (2 * CHIRP_DUR) * t**2)
    win = np.hanning(CHIRP_N)
    tx = (VOLUME * np.sin(phase) * win).astype(np.float32)
    analytic = np.exp(1j * phase) * win
    return tx, np.conj(np.fft.fft(analytic, NFFT))


# column 0 = left speaker (up-chirp at t=0), column 1 = right speaker
# (down-chirp half a ping later). Full band for both -> best range
# resolution; time offset + opposite sweeps keep the channels separable.
TX = np.zeros((PING_N, 2), dtype=np.float32)
TX[:CHIRP_N, 0], H_L = make_chirp(BAND[0], BAND[1])
_half = PING_N // 2
TX[_half:_half + CHIRP_N, 1], H_R = make_chirp(BAND[1], BAND[0])


def make_templates(chirp: np.ndarray, h_conj: np.ndarray) -> np.ndarray:
    """Complex matched-filter response of a unit echo, tabulated at SUB
    sub-sample shifts. Row s, column CHIRP_N + u = response at integer lag u
    for an echo delayed by (s - SUB/2)/SUB of a sample. Used to CLEAN each
    detected echo's full correlation lobe (skirts, image-term ripple and all)
    out of the residual before searching for the next object."""
    chirp_f = np.fft.fft(chirp.astype(np.float64), NFFT)
    freqs = np.fft.fftfreq(NFFT)
    tbl = np.empty((SUB, 2 * CHIRP_N + 1), dtype=complex)
    for s in range(SUB):
        delta = (s - SUB // 2) / SUB
        resp = np.fft.ifft(chirp_f * h_conj *
                           np.exp(-2j * np.pi * freqs * delta))
        tbl[s] = np.concatenate([resp[-CHIRP_N:], resp[:CHIRP_N + 1]])
    return tbl


TMPL_L = make_templates(TX[:CHIRP_N, 0], H_L)
TMPL_R = make_templates(TX[_half:_half + CHIRP_N, 1], H_R)


class Channel:
    """One speaker's echo profile: coherent background subtraction + peaks.

    Same pipeline as sonar.py's 1D processor, generalized to return every
    detected echo as a bistatic extra-path length in metres.
    """

    def __init__(self, name: str, h_conj: np.ndarray,
                 tmpl: np.ndarray) -> None:
        self.name = name
        self.h_conj = h_conj
        self.tmpl = tmpl
        self.direct: int | None = None
        self.ref: complex = 0j
        self.cal: list[np.ndarray] = []
        self.cal_ref: list[complex] = []
        self.bg: np.ndarray | None = None
        self.med = 0.0                  # noise floor, learned at calibration
        self.sigma = 1e-9
        self.ema_c: np.ndarray | None = None  # complex residual average
        self.ema_m: np.ndarray | None = None  # residual magnitude average

    def process(self, X: np.ndarray) -> dict:
        corr = np.fft.ifft(X * self.h_conj)[:CORR_N]
        mag = np.abs(corr[:PING_N])
        p = int(np.argmax(mag))
        if mag[p] < 1e-6 or mag[p] < 20 * np.median(mag):
            return {"state": "silent"}

        if self.direct is None or mag[p] > 2 * mag[self.direct]:
            self.direct = p
            self.cal.clear()
            self.cal_ref.clear()
            self.bg = None

        d = self.direct
        if self.bg is None:
            self.cal.append(corr[d + MIN_LAG: d + MAX_LAG].copy())
            self.cal_ref.append(complex(corr[d]))
            if len(self.cal) >= CAL_PINGS:
                self.ref = np.mean(self.cal_ref)
                self.bg = np.mean(self.cal, axis=0) / self.ref
                # noise floor from the (echo-free) calibration residuals --
                # a per-frame estimate would be inflated by the echoes we
                # are trying to detect
                pool = np.concatenate(
                    [np.abs(c / self.ref - self.bg) for c in self.cal])
                self.med = float(np.median(pool))
                self.sigma = float(
                    1.4826 * np.median(np.abs(pool - self.med))) + 1e-9
                self.ema_c = np.zeros(len(self.bg), dtype=complex)
                self.ema_m = np.zeros(len(self.bg))
                self.cal.clear()
                self.cal_ref.clear()
            return {"state": "calibrating"}

        # slow-tracked reference: a close hand perturbs the instantaneous
        # direct-path value, which must not rescale the whole profile
        seg = corr[d + MIN_LAG: d + MAX_LAG] / self.ref
        self.ref = (1 - REF_ADAPT) * self.ref + REF_ADAPT * corr[d]

        cres = seg - self.bg             # complex residual
        resid = np.abs(cres)
        resid0 = resid                   # pre-CLEAN copy, for adaptation
        n = len(cres)

        # Temporal coherence per bin: |mean(residual)| / mean(|residual|).
        # Live targets wander in phase ping to ping (breathing alone is
        # >1 rad here); leftover static residue is phase-frozen (coh ~ 1).
        self.ema_c = (1 - EMA_A) * self.ema_c + EMA_A * cres
        self.ema_m = (1 - EMA_A) * self.ema_m + EMA_A * resid
        coh = np.abs(self.ema_c) / (self.ema_m + 1e-12)

        loud = resid0 > self.med + 4 * self.sigma
        # the ema_m gate means "stale" needs ~6 frames of sustained,
        # phase-frozen amplitude -- a freshly appeared target (whose EMAs
        # are dominated by one frame, faking coherence 1) is never absorbed
        stale = loud & (coh > COH_STALE) & (self.ema_m > 0.7 * resid0)

        # CLEAN: detect the strongest live echo, fit its exact complex lobe
        # (sub-sample position + amplitude), subtract it, repeat
        peaks = []                       # (extra path m, snr, amp)
        killed = np.zeros(n, dtype=bool)
        for _ in range(MAX_OBJ):
            i = int(np.argmax(np.where(killed | stale, 0.0, resid)))
            amp = float(resid[i])
            snr = float((amp - self.med) / self.sigma)
            if snr < SNR_THRESH or amp < ABS_FLOOR:
                break
            if peaks and amp < SIDE_REJ * peaks[0][2]:
                break
            best = None
            for s in range(SUB):         # least-squares over sub-sample shift
                T = self.tmpl[s][CHIRP_N - i: CHIRP_N - i + n] / self.ref
                lo, hi = max(i - 45, 0), min(i + 46, n)
                tn, cn = T[lo:hi], cres[lo:hi]
                a = np.vdot(tn, cn) / (float(np.vdot(tn, tn).real) + 1e-18)
                err = float(np.sum(np.abs(cn - a * tn) ** 2))
                if best is None or err < best[0]:
                    best = (err, s, a, T)
            _, s, a, T = best
            lag = MIN_LAG + i + (s - SUB // 2) / SUB
            peaks.append((lag / FS * C, snr, amp))
            cres = cres - a * T          # remove this echo's entire lobe
            resid = np.abs(cres)
            killed[max(i - 6, 0): i + 7] = True

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

        # the noise floor may drift only while nothing is detected, so a
        # present target can never ratchet it up over itself
        if not peaks:
            q = resid0[~loud]
            if len(q) > 40:
                m = np.median(q)
                s = 1.4826 * np.median(np.abs(q - m)) + 1e-9
                self.med += 0.1 * (m - self.med)
                self.sigma += 0.1 * (s - self.sigma)

        return {"state": "ok", "peaks": [(e, s) for e, s, _ in peaks],
                "med": self.med, "sigma": self.sigma}


# ------------------------- 2D localization ----------------------------------
# Precomputed bistatic extra-path fields over the viewing area: for every
# candidate point, the speaker->point->mic path minus the direct path.
XS = np.arange(-0.60, 0.601, 0.01)
ZS = np.arange(0.03, 1.051, 0.01)
_XX, _ZZ = np.meshgrid(XS, ZS)


def _field(spk_x: float) -> np.ndarray:
    return (np.hypot(_XX - spk_x, _ZZ) + np.hypot(_XX - MIC_X, _ZZ)
            - abs(spk_x - MIC_X))


E_L, E_R = _field(SPK_L_X), _field(SPK_R_X)


def localize(e_l: float, e_r: float) -> tuple[float, float, float]:
    """Intersect the two ellipses; returns (x, z, residual)."""
    cost = (E_L - e_l) ** 2 + (E_R - e_r) ** 2
    r, c = np.unravel_index(np.argmin(cost), cost.shape)
    res = max(abs(E_L[r, c] - e_l), abs(E_R[r, c] - e_r))
    return float(XS[c]), float(ZS[r]), float(res)


class Radar:
    """Fuses both channels into tracked 2D objects."""

    def __init__(self) -> None:
        self.ch_l = Channel("L", H_L, TMPL_L)
        self.ch_r = Channel("R", H_R, TMPL_R)
        self.tracks: list[dict] = []

    def process(self, window: np.ndarray) -> dict:
        X = np.fft.fft(window, NFFT)
        rl, rr = self.ch_l.process(X), self.ch_r.process(X)
        states = {rl["state"], rr["state"]}
        if "silent" in states:
            return {"state": "silent"}
        if "calibrating" in states:
            return {"state": "calibrating"}

        objs = self._fuse(rl["peaks"], rr["peaks"])
        self._track(objs)
        live = [t for t in self.tracks if t["miss"] == 0]
        return {"state": "ok", "objects": live, "raw": (rl, rr)}

    def _fuse(self, peaks_l: list, peaks_r: list) -> list[dict]:
        """Pair per-speaker echoes by path similarity, intersect ellipses."""
        objs = []
        used_r: set[int] = set()
        for e_l, snr_l in sorted(peaks_l, key=lambda p: -p[1]):
            best, best_diff = None, 0.35    # max plausible L/R path spread
            for j, (e_r, snr_r) in enumerate(peaks_r):
                if j not in used_r and abs(e_l - e_r) < best_diff:
                    best, best_diff = j, abs(e_l - e_r)
            if best is None:
                continue
            e_r, snr_r = peaks_r[best]
            x, z, res = localize(e_l, e_r)
            if res < 0.07:                  # ellipses must actually intersect
                                            # (loosely: an extended body's L/R
                                            # bright spots aren't one point)
                used_r.add(best)
                objs.append({"x": x, "z": z, "snr": min(snr_l, snr_r)})
        return objs

    def _track(self, objs: list[dict]) -> None:
        """Nearest-neighbour tracking with light smoothing."""
        for t in self.tracks:
            t["miss"] += 1
        for o in objs:
            near = min(self.tracks,
                       key=lambda t: np.hypot(t["x"] - o["x"], t["z"] - o["z"]),
                       default=None)
            if near is not None and near["miss"] > 0 and \
                    np.hypot(near["x"] - o["x"], near["z"] - o["z"]) < 0.15:
                near["x"] = 0.5 * near["x"] + 0.5 * o["x"]
                near["z"] = 0.5 * near["z"] + 0.5 * o["z"]
                near["snr"], near["miss"] = o["snr"], 0
            else:
                self.tracks.append({**o, "miss": 0})
        self.tracks = [t for t in self.tracks if t["miss"] <= 1]


# ------------------------------ display -------------------------------------
GRID_W, GRID_H = 61, 20
X_SPAN, Z_MAX = 0.45, 1.0


def render(status: str, objects: list[dict], trail: deque) -> str:
    grid = [[" "] * GRID_W for _ in range(GRID_H)]

    def cell(x: float, z: float) -> tuple[int, int]:
        c = int(round((x + X_SPAN) / (2 * X_SPAN) * (GRID_W - 1)))
        r = int(round((1 - z / Z_MAX) * (GRID_H - 1)))
        return r, c

    for x, z in trail:
        r, c = cell(x, z)
        if 0 <= r < GRID_H and 0 <= c < GRID_W and grid[r][c] == " ":
            grid[r][c] = "."

    for o in objects:
        r0, c0 = cell(o["x"], o["z"])
        rad = 4 if o["snr"] >= 15 else 2      # blob size ~ echo strength
        for dr in range(-2, 3):
            for dc in range(-4, 5):
                m = 2 * abs(dr) + abs(dc)     # cells are ~2x taller than wide
                if m > rad:
                    continue
                r, c = r0 + dr, c0 + dc
                if 0 <= r < GRID_H and 0 <= c < GRID_W:
                    grid[r][c] = "@" if m == 0 else ("#" if m <= rad - 2
                                                     else "+")

    lines = [" acoustic radar -- top-down view (elevation ambiguous)",
             "      +" + "-" * GRID_W + "+"]
    for r in range(GRID_H):
        z = Z_MAX * (1 - r / (GRID_H - 1))
        label = f"{z:4.1f}m" if r % 5 == 0 or r == GRID_H - 1 else "     "
        lines.append(f"{label} |" + "".join(grid[r]) + "|")
    lines.append("      +" + "-" * GRID_W + "+")
    lines.append("      -45cm" + " " * 21 + "0" + " " * 21 + "+45cm")
    lines.append(" " * 18 + "[L spk]====(mic)====[R spk]")
    lines.append(f" status: {status:<50}")
    for k in range(MAX_OBJ):
        if k < len(objects):
            o = objects[k]
            lines.append(f"   obj {k + 1}: x {o['x'] * 100:+5.0f} cm   "
                         f"z {o['z'] * 100:4.0f} cm   snr {o['snr']:5.1f}")
        else:
            lines.append("")
    return "\x1b[H" + "\n".join(line + "\x1b[K" for line in lines)


# -------------------------------- main ---------------------------------------
def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--log", action="store_true",
                    help="scrolling text output instead of the radar view")
    ap.add_argument("--debug", action="store_true",
                    help="per-channel diagnostics (implies --log)")
    args = ap.parse_args()
    log_mode = args.log or args.debug

    audio_q: queue.Queue[np.ndarray] = queue.Queue()
    tx_pos = 0

    def callback(indata, outdata, frames, time_info, status):
        nonlocal tx_pos
        if status:
            print(f"[audio] {status}", file=sys.stderr)
        idx = (tx_pos + np.arange(frames)) % PING_N
        outdata[:] = TX[idx]
        tx_pos = (tx_pos + frames) % PING_N
        audio_q.put(indata[:, 0].copy())

    print(f"radar: {BAND[0] / 1000:.1f}-{BAND[1] / 1000:.1f} kHz chirps "
          f"(L up-sweep, R down-sweep), {1 / PING_PERIOD:.0f} frames/s")
    print("make sure system volume is up (~60-80%) -- the chirps are inaudible")
    print(f"calibrating for {CAL_PINGS * PING_PERIOD:.1f} s, "
          "keep hands away from the laptop...")

    radar = Radar()
    trail: deque = deque(maxlen=30)
    buf = np.zeros(0, dtype=np.float32)
    calibrated = False

    if not log_mode:
        sys.stdout.write("\x1b[2J\x1b[?25l")     # clear screen, hide cursor
    try:
        with sd.Stream(samplerate=FS, channels=(1, 2), dtype="float32",
                       callback=callback):
            while True:
                buf = np.concatenate([buf, audio_q.get()])
                while len(buf) >= WIN_N:
                    window = buf[:WIN_N].astype(np.float64)
                    buf = buf[PING_N:]
                    r = radar.process(window)
                    stamp = time.strftime("%H:%M:%S")

                    if r["state"] == "ok" and not calibrated:
                        calibrated = True
                        if log_mode:
                            dl, dr = radar.ch_l.direct, radar.ch_r.direct
                            off = (dl - dr) / FS * C / 2 * 100
                            print(f"calibration done (direct-path skew "
                                  f"suggests mic at x = {off:+.1f} cm; edit "
                                  "MIC_X if far off). tracking...\n")

                    if log_mode:
                        if r["state"] == "silent":
                            print(f"{stamp} | mic is silent -- check mic "
                                  "permission / volume")
                        elif r["state"] == "ok":
                            objs = " | ".join(
                                f"x{o['x'] * 100:+5.0f}cm z{o['z'] * 100:4.0f}cm "
                                f"snr{o['snr']:5.1f}" for o in r["objects"])
                            line = f"{stamp} | {objs or 'no objects'}"
                            if args.debug:
                                rl, rr = r["raw"]
                                line += (f" || L med {rl['med']:.4f} "
                                         f"sd {rl['sigma']:.5f} "
                                         f"pk {len(rl['peaks'])}"
                                         f" | R med {rr['med']:.4f} "
                                         f"sd {rr['sigma']:.5f} "
                                         f"pk {len(rr['peaks'])}")
                            print(line, flush=True)
                        continue

                    if r["state"] == "silent":
                        status = "NO SIGNAL -- check mic permission and volume"
                        objects = []
                    elif r["state"] == "calibrating":
                        status = "CALIBRATING -- keep hands away"
                        objects = []
                    else:
                        objects = r["objects"]
                        for o in objects:
                            trail.append((o["x"], o["z"]))
                        status = (f"tracking {len(objects)} object(s)"
                                  if objects else "scanning...")
                    sys.stdout.write(render(status, objects, trail))
                    sys.stdout.flush()
    finally:
        if not log_mode:
            sys.stdout.write("\x1b[?25h\n")      # restore cursor


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nstopped")

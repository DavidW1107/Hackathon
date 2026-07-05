#!/usr/bin/env python3
"""
sonar.py -- near-ultrasonic sonar using the laptop's speakers and mic.

Continuously emits short 17-21 kHz chirps (near-inaudible),
records with the built-in mic, and matched-filters the recording.
The mic hears each chirp twice: once via the direct speaker->mic path,
and again after reflecting off your hand. Coarse distance comes from the
delay between those two arrivals, so audio I/O latency cancels out. On
top of that, each echo's carrier phase is tracked ping to ping: it turns
2*pi per ~9 mm of range change, resolving sub-millimetre motion.

A ~2 s calibration at startup learns the static echoes (screen, desk,
walls); each ping is coherently subtracted against them so only moving /
new reflectors stand out. Up to MAX_OBJ echoes are CLEANed out per ping
and tracked independently (two hands work), each with a small Kalman
filter fusing coarse range and carrier-phase velocity -- the predicted
velocity picks the phase branch, so fast motion keeps the right sign.
A target that stays for ~30 s gets its breathing rate estimated from
the mm-scale wobble of its echo.

Usage:
    python sonar.py [--debug] [--waterfall] [--json] [--udp HOST:PORT]
                    [--lock SECS] [--simulate]

Keep your hands away from the laptop during calibration.
"""

import argparse
import json
import queue
import socket
import subprocess
import sys
import time
from collections import deque

import numpy as np

import echo_core as ec

# ---------------------------- configuration ---------------------------------
FS          = 48_000          # sample rate (Hz)
F0, F1      = 17_000, 21_000  # chirp band (Hz) -- near-inaudible; 4 kHz of
                              # bandwidth -> c/2B ~ 4.3 cm range resolution
CHIRP_DUR   = 0.006           # chirp length (s)
PING_PERIOD = 0.050           # one ping every 50 ms -> 20 readings/s
VOLUME      = 0.6             # output amplitude (0..1)
C           = 343.0           # speed of sound (m/s)
MIN_D       = 0.07            # closest detectable hand (m)
MAX_D       = 1.00            # farthest detectable hand (m)
CAL_PINGS   = 40              # pings used to learn the static background (~2 s)
SNR_ENTER   = 6.0             # a NEW target must clear this (x noise sigma)
SNR_EXIT    = 4.0             # a tracked target survives down to this --
                              # hysteresis stops flicker at the range limit
ABS_FLOOR   = 0.002           # min echo strength relative to direct path
BG_ADAPT    = 0.01            # slow background adaptation rate
BG_FAST     = 0.30            # absorption rate of confirmed-stale residue
REF_ADAPT   = 0.025           # direct-path reference tracking rate
EMA_A       = 0.10            # temporal-coherence smoothing per ping
COH_STALE   = 0.95            # residue coherence above this = static scene
STALE_RUN   = 40              # frames coherence must stay high before
                              # absorbing -- longer than the still moment
                              # at the extremes of a breath (~2 s)
MAX_OBJ     = 3               # simultaneous targets (CLEAN passes per ping)
SIDE_REJ    = 0.10            # weaker peaks below this fraction of the
                              # strongest are leftover fit error
FC          = (F0 + F1) / 2   # carrier (Hz) for phase-based fine ranging
# per-target Kalman filter: coarse range anchors, carrier phase refines
KF_ACCEL    = 3.0             # expected hand acceleration scale (m/s^2)
R_COARSE    = 0.004           # coarse range measurement sigma (m)
R_VEL       = 0.005           # phase-step velocity measurement sigma (m/s)
MISS_MAX    = 4               # pings a track may coast before it is dropped
BREATH_N    = 600             # fine-range history for breathing (30 s)
# -----------------------------------------------------------------------------

CFG = ec.EchoConfig(
    fs=FS, f0=F0, f1=F1, chirp_dur=CHIRP_DUR, ping_period=PING_PERIOD,
    volume=VOLUME,
    min_lag=int(round(2 * MIN_D / C * FS)),
    max_lag=int(round(2 * MAX_D / C * FS)),
    cal_pings=CAL_PINGS, snr_enter=SNR_ENTER, snr_exit=SNR_EXIT,
    abs_floor=ABS_FLOOR, bg_adapt=BG_ADAPT, bg_fast=BG_FAST,
    ref_adapt=REF_ADAPT, ema_a=EMA_A, coh_stale=COH_STALE,
    stale_run=STALE_RUN, max_obj=MAX_OBJ, side_rej=SIDE_REJ)

# transmit buffer: one chirp then silence, repeated forever by the callback
TX = np.zeros(CFG.ping_n, dtype=np.float32)
_burst, H = ec.make_chirp(CFG, F0, F1)
TX[:CFG.chirp_n] = _burst
TMPL0 = ec.subsample_bank(CFG, ec.ideal_lobe(CFG, _burst, H))


# ------------------------------ tracking -------------------------------------
class Track:
    """One reflector: Kalman range/velocity + carrier-phase memory."""
    _next_id = 1

    def __init__(self, r: float, snr: float, ping: int) -> None:
        self.id = Track._next_id
        Track._next_id += 1
        self.kf = ec.RangeKalman(r, sigma_r=R_COARSE, accel=KF_ACCEL)
        self.snr = snr
        self.miss = 0
        self.born = ping
        self.prev_z: complex | None = None
        self.prev_ping = ping
        self.hist: deque[float] = deque(maxlen=BREATH_N)
        self.breath: float | None = None

    @property
    def r(self) -> float:
        return self.kf.r

    @property
    def v(self) -> float:
        return self.kf.v


def estimate_breath(hist: np.ndarray) -> float | None:
    """Breaths/min from 30 s of fine range, or None if not breathing-like."""
    x = hist * 1000.0                              # mm
    k = 201                                        # ~10 s moving-average
    ma = np.convolve(np.pad(x, k // 2, mode="edge"),
                     np.ones(k) / k, "valid")      # detrend: keep 0.1-0.7 Hz
    hp = x - ma
    if not 0.03 < float(np.std(hp)) < 5.0:         # chest motion is mm-scale
        return None
    spec = np.abs(np.fft.rfft(hp * np.hanning(len(hp)), 4096)) ** 2
    f = np.fft.rfftfreq(4096, PING_PERIOD)
    band = (f >= 0.08) & (f <= 0.7)                # 5-42 breaths/min
    pk = int(np.argmax(np.where(band, spec, 0.0)))
    if spec[pk] < 8 * np.median(spec[band]):       # must be clearly periodic
        return None
    return float(60.0 * f[pk])


class SonarPipeline:
    """Windows in, tracked targets out. Pure DSP -- no I/O, testable."""

    def __init__(self) -> None:
        self.ch = ec.Channel(CFG, H, TMPL0)
        self.tracks: list[Track] = []
        self.ping = 0

    def process(self, window: np.ndarray) -> dict:
        r = self.ch.process(np.fft.fft(window, CFG.nfft))
        self.ping += 1
        if r["state"] == "relock":
            self.tracks.clear()
        elif r["state"] in ("ok", "blip"):
            self._track(r.get("peaks", []))
            r["tracks"] = self.tracks
        return r

    def _track(self, dets: list[dict]) -> None:
        dt = PING_PERIOD
        for t in self.tracks:
            t.kf.predict(dt)

        claimed: set[int] = set()
        for det in sorted(dets, key=lambda p: -p["snr"]):
            r_meas = det["lag"] / FS * C / 2
            best = None
            for t in self.tracks:
                if id(t) in claimed:
                    continue
                gate = max(0.04, 3 * float(np.sqrt(t.kf.P[0, 0])) + 0.01)
                err = abs(t.kf.r - r_meas)
                if err < gate and (best is None or err < best[1]):
                    best = (t, err)
            if best is not None:
                t = best[0]
                claimed.add(id(t))
                t.kf.update_range(r_meas, R_COARSE**2)
                # carrier phase: the predicted velocity picks the 2*pi
                # branch, so motion beyond the lambda/4-per-ping alias
                # limit still reads with the right sign
                if t.prev_z is not None and self.ping - t.prev_ping == 1:
                    dphi = float(np.angle(det["z"] * np.conjugate(t.prev_z)))
                    dphi_pred = -4 * np.pi * FC * t.kf.v * dt / C
                    dphi = dphi_pred + ec.wrap_pi(dphi - dphi_pred)
                    v_meas = -dphi * C / (4 * np.pi * FC) / dt
                    t.kf.update_velocity(v_meas, R_VEL**2, gate=3.0)
                t.prev_z, t.prev_ping = det["z"], self.ping
                t.miss, t.snr = 0, det["snr"]
            elif det["snr"] >= SNR_ENTER and all(
                    abs(t.kf.r - r_meas) > 0.06 for t in self.tracks):
                nt = Track(r_meas, det["snr"], self.ping)
                self.tracks.append(nt)
                claimed.add(id(nt))

        for t in self.tracks:
            if id(t) not in claimed:
                t.miss += 1
                t.prev_z = None          # phase continuity broken
        self.tracks = [t for t in self.tracks
                       if t.miss <= MISS_MAX and t.kf.P[0, 0] < 0.08**2]

        for t in self.tracks:
            if t.miss > 2:
                t.hist.clear()           # gap too long for breathing analysis
            else:
                t.hist.append(t.kf.r)
            if len(t.hist) == BREATH_N and self.ping % 40 == 0:
                t.breath = estimate_breath(np.asarray(t.hist))
            elif len(t.hist) < BREATH_N // 2:
                t.breath = None

    def live(self) -> list[Track]:
        return sorted((t for t in self.tracks if t.miss == 0),
                      key=lambda t: t.id)


# ------------------------------ output ---------------------------------------
def bar(dist_m: float, width: int = 30) -> str:
    filled = int(round(np.clip(dist_m / MAX_D, 0, 1) * width))
    return "#" * filled + "." * (width - filled)


RAMP = " .:-=+*#%@"


def waterfall_row(profile: np.ndarray, med: float, sigma: float,
                  tracks: list[Track], width: int = 64) -> str:
    n = len(profile)
    edges = np.arange(width) * n // width
    pooled = np.maximum.reduceat(profile, edges)
    db = 20 * np.log10(pooled / (med + 4 * sigma + 1e-12) + 1e-12)
    idx = np.clip((db / 3).astype(int), 0, len(RAMP) - 1)
    row = [RAMP[i] for i in idx]
    for k, t in enumerate(tracks[:9]):
        c = int((t.r - MIN_D) / (MAX_D - MIN_D) * (width - 1))
        if 0 <= c < width:
            row[c] = str(k + 1)
    return "".join(row)


class SonarApp:
    """Wires the pipeline to audio blocks, the terminal, JSON/UDP, --lock."""

    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.pipe = SonarPipeline()
        self.pipe.ch.keep_profile = args.waterfall
        self.buf = np.zeros(0, dtype=np.float32)
        self.silent_count = 0
        self.was_calibrating = True
        self.last_present = time.monotonic()
        self.locked = False
        self.sock = self.dest = None
        if args.udp:
            host, port = args.udp.rsplit(":", 1)
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.dest = (host, int(port))

    def feed(self, block: np.ndarray, xrun: bool) -> None:
        if xrun:
            self.pipe.ch.note_xrun()
        self.buf = np.concatenate([self.buf, block])
        # shed lag in whole pings if we fall behind (stalled terminal):
        # ping-multiple cuts keep the direct-path alignment intact
        if len(self.buf) > 8 * CFG.win_n:
            cut = (len(self.buf) - 2 * CFG.win_n) // CFG.ping_n * CFG.ping_n
            self.buf = self.buf[cut:]
            print(f"[sonar] behind by {cut / FS:.1f} s -- skipped ahead",
                  file=sys.stderr)
        while len(self.buf) >= CFG.win_n:
            window = self.buf[:CFG.win_n].astype(np.float64)
            self.buf = self.buf[CFG.ping_n:]     # advance exactly one ping
            self.emit(self.pipe.process(window))

    # -- per-ping output -------------------------------------------------------
    def emit(self, r: dict) -> None:
        stamp = time.strftime("%H:%M:%S")
        state = r["state"]

        if state == "silent":
            self.silent_count += 1
            if self.silent_count % 10 == 1:
                print(f"{stamp} | mic is silent -- check macOS mic permission,"
                      " system volume, and that output is the built-in"
                      " speakers", file=sys.stderr)
            return
        self.silent_count = 0

        if state == "calibrating":
            return
        if state == "relock":
            print(f"{stamp} | audio path changed -- recalibrating, "
                  "keep hands away...", file=sys.stderr)
            self.was_calibrating = True
            return
        if self.was_calibrating:
            self.was_calibrating = False
            self.last_present = time.monotonic()
            print(f"calibration done -- {self.pipe.ch.health()}")
            if not self.args.json:
                print("move your hand in front of the laptop\n")
        if r.get("note"):
            print(f"{stamp} | [{r['note']}]", file=sys.stderr)

        live = self.pipe.live()
        self.presence(bool(live))
        if self.args.json or self.sock:
            self.emit_json(state, live)
            if self.args.json:
                return
        if self.args.waterfall:
            self.emit_waterfall(r, stamp, live)
            return
        self.emit_line(stamp, live, r)

    def emit_line(self, stamp: str, live: list[Track], r: dict) -> None:
        if live:
            t = live[0]
            line = (f"{stamp} | {t.r * 100:7.2f} cm "
                    f"| {t.v * 1000:+7.1f} mm/s |{bar(t.r)}| "
                    f"snr {t.snr:5.1f}")
            if t.breath:
                line += f" | breath {t.breath:4.1f}/min"
            for x in live[1:]:
                line += f" | t{x.id} {x.r * 100:5.1f} cm"
        else:
            line = (f"{stamp} |     --- cm |     --- mm/s "
                    f"|{'.' * 30}| no echo")
        if self.args.debug:
            line += (f" | direct {r.get('direct_amp', 0.0):8.1f} "
                     f"sigma {r.get('sigma', 0.0):.5f} "
                     f"peaks {len(r.get('peaks', []))}")
        print(line, flush=True)

    def emit_waterfall(self, r: dict, stamp: str, live: list[Track]) -> None:
        prof = r.get("profile")
        if prof is None:
            return
        row = waterfall_row(prof, r.get("med", 0.0), r.get("sigma", 1e-9),
                            live)
        info = f" {live[0].r * 100:6.1f} cm" if live else " " * 10
        print(f"{stamp} |{row}|{info}", flush=True)

    def emit_json(self, state: str, live: list[Track]) -> None:
        msg = {"t": round(time.time(), 3), "state": state,
               "presence": bool(live),
               "targets": [{"id": t.id, "r": round(t.r, 4),
                            "v": round(t.v, 4), "snr": round(t.snr, 1),
                            "breath": round(t.breath, 1) if t.breath else None}
                           for t in live]}
        line = json.dumps(msg, separators=(",", ":"))
        if self.args.json:
            print(line, flush=True)
        if self.sock:
            self.sock.sendto(line.encode(), self.dest)

    def presence(self, present: bool) -> None:
        now = time.monotonic()
        if present:
            self.last_present = now
            self.locked = False
        elif (self.args.lock is not None and not self.locked
              and now - self.last_present > self.args.lock):
            self.locked = True
            print(f"[sonar] nobody here for {self.args.lock:.0f} s -- "
                  "locking screen", file=sys.stderr)
            subprocess.run(["pmset", "displaysleepnow"], check=False)


# -------------------------------- main ---------------------------------------
def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--debug", action="store_true",
                    help="append raw levels to each line, for tuning")
    ap.add_argument("--waterfall", action="store_true",
                    help="range-time echo intensity view instead of numbers")
    ap.add_argument("--json", action="store_true",
                    help="one JSON object per ping on stdout")
    ap.add_argument("--udp", metavar="HOST:PORT",
                    help="also stream the JSON messages over UDP")
    ap.add_argument("--lock", type=float, metavar="SECS",
                    help="lock the screen after SECS with nobody in front "
                         "(needs 'require password after sleep' enabled)")
    ap.add_argument("--simulate", action="store_true",
                    help="run on synthetic audio -- no hardware, no chirps")
    args = ap.parse_args()

    app = SonarApp(args)
    if not args.json:
        print(f"sonar: {F0 / 1000:.1f}-{F1 / 1000:.1f} kHz chirps, "
              f"{1 / PING_PERIOD:.0f} pings/s, "
              f"range {MIN_D * 100:.0f}-{MAX_D * 100:.0f} cm, "
              f"up to {MAX_OBJ} targets")

    if args.simulate:
        import simulate as sim
        print("SIMULATION -- synthetic scene, no audio hardware in use")
        scene = sim.SonarScene(
            CFG,
            statics=[(0.32, 0.04), (0.57, 0.03)],
            targets=[(lambda t: 0.40 + 0.15 * np.sin(2 * np.pi * 0.12 * t),
                      0.02),
                     (lambda t: 0.85 + 0.0015 * np.sin(2 * np.pi * 0.23 * t),
                      0.03)],
            noise=3e-4)
        for block, xrun in scene.blocks():
            app.feed(block, xrun)
        return

    import sounddevice as sd
    audio_q: queue.Queue[tuple[np.ndarray, bool]] = queue.Queue()
    tx_pos = 0

    def callback(indata, outdata, frames, time_info, status):
        nonlocal tx_pos
        if status:
            print(f"[audio] {status}", file=sys.stderr)
        idx = (tx_pos + np.arange(frames)) % CFG.ping_n
        outdata[:, 0] = TX[idx]
        tx_pos = (tx_pos + frames) % CFG.ping_n
        audio_q.put((indata[:, 0].copy(), bool(status)))

    if not args.json:
        print("make sure system volume is up (~60-80%) -- "
              "the chirp is inaudible")
        print(f"calibrating for {CAL_PINGS * PING_PERIOD:.1f} s, "
              "keep hands away from the laptop...")

    with sd.Stream(samplerate=FS, channels=1, dtype="float32",
                   callback=callback):
        while True:
            app.feed(*audio_q.get())


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nstopped")

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
    python radar.py                live radar view
    python radar.py --log          scrolling text log instead of the radar
    python radar.py --debug        extra per-channel numbers (implies --log)
    python radar.py --json         one JSON object per frame on stdout
    python radar.py --udp H:P      also stream the JSON over UDP
    python radar.py --simulate     synthetic scene, no audio hardware

Keep your hands away from the laptop during the ~2 s calibration.
"""

import argparse
import json
import queue
import socket
import sys
import time
from collections import deque

import numpy as np

import echo_core as ec

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
SNR_ENTER   = 6.0               # a NEW object must clear this (x noise sigma)
SNR_EXIT    = 4.0               # a tracked object survives down to this
ABS_FLOOR   = 0.002             # min echo strength relative to direct path
BG_ADAPT    = 0.02              # slow background adaptation rate
BG_FAST     = 0.30              # absorption rate of confirmed-stale residue
REF_ADAPT   = 0.05              # direct-path reference tracking rate
EMA_A       = 0.20              # temporal-coherence smoothing per frame
COH_STALE   = 0.95              # residue coherence above this = static scene
STALE_RUN   = 20                # frames coherence must stay high before
                                # absorbing -- longer than the still moment
                                # at the extremes of a breath (~1 s)
SIDE_REJ    = 0.10              # secondary peaks below this fraction of the
                                # strongest are leftover fit error
MAX_OBJ     = 3                 # max simultaneous reflectors

# geometry (metres, in the keyboard plane; tweak for your machine)
SPK_L_X     = -0.12             # left speaker x
SPK_R_X     = +0.12             # right speaker x
MIC_X       =  0.00             # mic x (MacBooks: often left of centre)

MIN_E       = 0.04              # min bistatic extra path (m)
MAX_E       = 2.30              # max bistatic extra path (m)
# -----------------------------------------------------------------------------

CFG = ec.EchoConfig(
    fs=FS, f0=BAND[0], f1=BAND[1], chirp_dur=CHIRP_DUR,
    ping_period=PING_PERIOD, volume=VOLUME,
    min_lag=int(round(MIN_E / C * FS)),
    max_lag=int(round(MAX_E / C * FS)),
    cal_pings=CAL_PINGS, snr_enter=SNR_ENTER, snr_exit=SNR_EXIT,
    abs_floor=ABS_FLOOR, bg_adapt=BG_ADAPT, bg_fast=BG_FAST,
    ref_adapt=REF_ADAPT, ema_a=EMA_A, coh_stale=COH_STALE,
    stale_run=STALE_RUN, max_obj=MAX_OBJ, side_rej=SIDE_REJ)

# column 0 = left speaker (up-chirp at t=0), column 1 = right speaker
# (down-chirp half a ping later). Full band for both -> best range
# resolution; time offset + opposite sweeps keep the channels separable.
TX = np.zeros((CFG.ping_n, 2), dtype=np.float32)
_burst_l, H_L = ec.make_chirp(CFG, BAND[0], BAND[1])
_burst_r, H_R = ec.make_chirp(CFG, BAND[1], BAND[0])
_half = CFG.ping_n // 2
TX[:CFG.chirp_n, 0] = _burst_l
TX[_half:_half + CFG.chirp_n, 1] = _burst_r
TMPL_L0 = ec.subsample_bank(CFG, ec.ideal_lobe(CFG, _burst_l, H_L))
TMPL_R0 = ec.subsample_bank(CFG, ec.ideal_lobe(CFG, _burst_r, H_R))


def mic_skew_cm(dl: int, dr: int) -> float:
    """Mic x implied by the two direct-path arrivals. The right chirp is
    transmitted half a ping late, so that offset must come out of the
    raw index difference before it means anything geometric."""
    raw = (dl - dr + _half) % CFG.ping_n
    if raw >= CFG.ping_n // 2:
        raw -= CFG.ping_n
    return raw / FS * C / 2 * 100


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
        self.ch_l = ec.Channel(CFG, H_L, TMPL_L0, "L")
        self.ch_r = ec.Channel(CFG, H_R, TMPL_R0, "R")
        self.tracks: list[dict] = []

    def note_xrun(self) -> None:
        self.ch_l.note_xrun()
        self.ch_r.note_xrun()

    def process(self, window: np.ndarray) -> dict:
        X = np.fft.fft(window, CFG.nfft)
        rl, rr = self.ch_l.process(X), self.ch_r.process(X)
        states = {rl["state"], rr["state"]}
        if "silent" in states:
            return {"state": "silent"}
        if "relock" in states:
            self.tracks.clear()
            return {"state": "calibrating"}
        if "calibrating" in states:
            return {"state": "calibrating"}

        # a 'blip' channel (glitch / level change settling) contributes no
        # peaks this frame; existing tracks coast through on their miss budget
        peaks_l = [(d["lag"] / FS * C, d["snr"]) for d in rl.get("peaks", [])]
        peaks_r = [(d["lag"] / FS * C, d["snr"]) for d in rr.get("peaks", [])]
        objs = self._fuse(peaks_l, peaks_r)
        self._track(objs)
        # confirmation: one-frame wonders (ghost L/R pairings) never show
        live = [t for t in self.tracks if t["miss"] <= 1 and t["hits"] >= 2]
        return {"state": "ok", "objects": live, "raw": (rl, rr)}

    def _fuse(self, peaks_l: list, peaks_r: list) -> list[dict]:
        """Pair per-speaker echoes by path similarity, intersect ellipses."""
        objs = []
        used_r: set[int] = set()
        order = sorted(range(len(peaks_l)), key=lambda i: -peaks_l[i][1])
        for i in order:
            e_l, snr_l = peaks_l[i]
            best, best_diff = None, 0.35    # max plausible L/R path spread
            for j, (e_r, snr_r) in enumerate(peaks_r):
                if j not in used_r and abs(e_l - e_r) < best_diff:
                    best, best_diff = j, abs(e_l - e_r)
            if best is None:
                continue
            e_r, snr_r = peaks_r[best]
            # mutuality: if that right-channel echo sits closer to some
            # other left-channel echo, pairing it with this one would
            # stitch two different objects into a ghost
            if any(k != i and abs(peaks_l[k][0] - e_r) < best_diff
                   for k in range(len(peaks_l))):
                continue
            x, z, res = localize(e_l, e_r)
            if res < 0.07:                  # ellipses must actually intersect
                                            # (loosely: an extended body's L/R
                                            # bright spots aren't one point)
                used_r.add(best)
                objs.append({"x": x, "z": z, "snr": min(snr_l, snr_r)})
        return objs

    def _track(self, objs: list[dict]) -> None:
        """Nearest-neighbour tracking with light smoothing and hysteresis:
        known objects survive down to SNR_EXIT, new ones need SNR_ENTER."""
        for t in self.tracks:
            t["miss"] += 1
        claimed: set[int] = set()
        for o in sorted(objs, key=lambda o: -o["snr"]):
            best = None
            for k, t in enumerate(self.tracks):
                if k in claimed:
                    continue
                d = float(np.hypot(t["x"] - o["x"], t["z"] - o["z"]))
                if d < 0.15 and (best is None or d < best[1]):
                    best = (k, d)
            if best is not None:
                k = best[0]
                claimed.add(k)
                t = self.tracks[k]
                t["x"] = 0.5 * t["x"] + 0.5 * o["x"]
                t["z"] = 0.5 * t["z"] + 0.5 * o["z"]
                t["snr"], t["miss"] = o["snr"], 0
                t["hits"] += 1
            elif o["snr"] >= SNR_ENTER:
                self.tracks.append({**o, "miss": 0, "hits": 1})
                claimed.add(len(self.tracks) - 1)
        self.tracks = [t for t in self.tracks if t["miss"] <= 3]


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
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--log", action="store_true",
                    help="scrolling text output instead of the radar view")
    ap.add_argument("--debug", action="store_true",
                    help="per-channel diagnostics (implies --log)")
    ap.add_argument("--json", action="store_true",
                    help="one JSON object per frame on stdout (implies --log)")
    ap.add_argument("--udp", metavar="HOST:PORT",
                    help="also stream the JSON messages over UDP")
    ap.add_argument("--simulate", action="store_true",
                    help="run on a synthetic scene -- no audio hardware")
    args = ap.parse_args()
    log_mode = args.log or args.debug or args.json

    sock = dest = None
    if args.udp:
        host, port = args.udp.rsplit(":", 1)
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        dest = (host, int(port))

    radar = Radar()
    trail: deque = deque(maxlen=30)
    buf = np.zeros(0, dtype=np.float32)
    calibrated = False

    if not args.json:
        print(f"radar: {BAND[0] / 1000:.1f}-{BAND[1] / 1000:.1f} kHz chirps "
              f"(L up-sweep, R down-sweep), {1 / PING_PERIOD:.0f} frames/s")
        print("make sure system volume is up (~60-80%) -- "
              "the chirps are inaudible")
        print(f"calibrating for {CAL_PINGS * PING_PERIOD:.1f} s, "
              "keep hands away from the laptop...")

    def handle(window: np.ndarray) -> None:
        nonlocal calibrated
        r = radar.process(window)
        stamp = time.strftime("%H:%M:%S")

        if r["state"] == "ok" and not calibrated:
            calibrated = True
            if log_mode and not args.json:
                off = mic_skew_cm(radar.ch_l.direct, radar.ch_r.direct)
                print(f"calibration done (direct-path skew suggests mic at "
                      f"x = {off:+.1f} cm; edit MIC_X if far off)")
                print(f"  L: {radar.ch_l.health()}")
                print(f"  R: {radar.ch_r.health()}\ntracking...\n")

        if args.json or sock:
            msg = {"t": round(time.time(), 3), "state": r["state"],
                   "objects": [{"x": round(o["x"], 3), "z": round(o["z"], 3),
                                "snr": round(o["snr"], 1)}
                               for o in r.get("objects", [])]}
            line = json.dumps(msg, separators=(",", ":"))
            if args.json:
                print(line, flush=True)
            if sock:
                sock.sendto(line.encode(), dest)
            if args.json:
                return

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
                    line += (f" || L med {rl.get('med', 0):.4f} "
                             f"sd {rl.get('sigma', 0):.5f} "
                             f"pk {len(rl.get('peaks', []))}"
                             f" | R med {rr.get('med', 0):.4f} "
                             f"sd {rr.get('sigma', 0):.5f} "
                             f"pk {len(rr.get('peaks', []))}")
                print(line, flush=True)
            return

        if r["state"] == "silent":
            status, objects = "NO SIGNAL -- check mic permission and volume", []
        elif r["state"] == "calibrating":
            status, objects = "CALIBRATING -- keep hands away", []
        else:
            objects = r["objects"]
            for o in objects:
                trail.append((o["x"], o["z"]))
            status = (f"tracking {len(objects)} object(s)"
                      if objects else "scanning...")
        sys.stdout.write(render(status, objects, trail))
        sys.stdout.flush()

    def consume(block: np.ndarray, xrun: bool) -> None:
        nonlocal buf
        if xrun:
            radar.note_xrun()
        buf = np.concatenate([buf, block])
        if len(buf) > 8 * CFG.win_n:
            cut = (len(buf) - 2 * CFG.win_n) // CFG.ping_n * CFG.ping_n
            buf = buf[cut:]
            print(f"[radar] behind by {cut / FS:.1f} s -- skipped ahead",
                  file=sys.stderr)
        while len(buf) >= CFG.win_n:
            window = buf[:CFG.win_n].astype(np.float64)
            buf = buf[CFG.ping_n:]
            handle(window)

    if args.simulate:
        import simulate as sim
        print("SIMULATION -- synthetic scene, no audio hardware in use")
        scene = sim.RadarScene(
            CFG, spk_x=(SPK_L_X, SPK_R_X), mic_x=MIC_X,
            statics=[((0.0, 0.33), 0.04), ((-0.22, 0.62), 0.03)],
            targets=[(lambda t: (0.22 * np.sin(2 * np.pi * 0.07 * t),
                                 0.50 + 0.18 * np.sin(2 * np.pi * 0.045 * t)),
                      0.025),
                     (lambda t: (-0.10 + 0.002 * np.sin(2 * np.pi * 1.1 * t),
                                 0.75 + 0.002 * np.sin(2 * np.pi * 0.9 * t)),
                      0.02)],
            noise=3e-4)
        if not log_mode:
            sys.stdout.write("\x1b[2J\x1b[?25l")
        try:
            for block, xrun in scene.blocks():
                consume(block, xrun)
        finally:
            if not log_mode:
                sys.stdout.write("\x1b[?25h\n")
        return

    import sounddevice as sd
    audio_q: queue.Queue[tuple[np.ndarray, bool]] = queue.Queue()
    tx_pos = 0

    def callback(indata, outdata, frames, time_info, status):
        nonlocal tx_pos
        if status:
            print(f"[audio] {status}", file=sys.stderr)
        idx = (tx_pos + np.arange(frames)) % CFG.ping_n
        outdata[:] = TX[idx]
        tx_pos = (tx_pos + frames) % CFG.ping_n
        audio_q.put((indata[:, 0].copy(), bool(status)))

    if not log_mode:
        sys.stdout.write("\x1b[2J\x1b[?25l")     # clear screen, hide cursor
    try:
        with sd.Stream(samplerate=FS, channels=(1, 2), dtype="float32",
                       callback=callback):
            while True:
                consume(*audio_q.get())
    finally:
        if not log_mode:
            sys.stdout.write("\x1b[?25h\n")      # restore cursor


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nstopped")

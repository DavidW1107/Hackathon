#!/usr/bin/env python3
"""Real-time acoustic motion sensor.

Emits chirps, detects moving targets, classifies them (human/soft vs hard/rigid
vs falling), estimates coarse azimuth from the 2-mic array, and serves detection
frames as JSON over HTTP for the three.js viewer to render.

  python sensor.py                              # live sensing on device 4
  python sensor.py --sim                         # synthetic targets, no hardware
  python sensor.py --sim --record web/sample.json --seconds 15   # make demo data

Frame contract (also what the viewer expects):
  {
    "t": <float seconds>,
    "fov": <cone half-angle deg>, "max_range": <m>,
    "clutter": [ {"range": m, "strength": 0..1} ],          # static (grey arcs)
    "targets": [ {"id","range":m,"az":deg,"vel":m/s,
                  "strength":0..1,"spread":0..1,"class":"human|hard|falling"} ]
  }

DSP is deliberately simple for v1: per window of N pulses, matched-filter each
into a range profile; static reflectors are the cross-pulse MEAN (clutter),
motion is the cross-pulse STD (a moving echo fluctuates). Classification uses
reflectivity + range-spread + speed. Doppler/micro-Doppler is the upgrade.
"""
import argparse
import json
import math
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import numpy as np
from sonar import FS, C, F0, F1, make_chirp, MIN_RANGE, MAX_RANGE

FRAME_MS = 40
PULSES_PER_WIN = 8            # ~0.32 s per detection frame
DEVICE = 4                    # ALC285 analog, 2-ch
MIC_BASELINE = 0.10           # m; assumed 2-mic spacing. ponytail: calibrate per laptop.
PORT = 8765
FOV = 50                      # forward cone half-angle (deg) we trust azimuth within

# detection thresholds (tune live) — ponytail: hand-tuned heuristics, swap for a
# trained classifier if it misfires.
CLUTTER_THRESH = 0.45         # fraction of peak = a static reflector
MOTION_THRESH = 0.12          # cross-pulse std (normalised) = movement
FALL_SPEED = 2.0              # m/s and tight -> falling/thrown
SPREAD_HUMAN = 0.30           # range-spread (m) or weak echo -> soft/human
SENSOR_MIN = 0.5              # m; skip near-field crosstalk zone (false motion)

_latest = {"t": 0, "fov": FOV, "max_range": MAX_RANGE, "clutter": [], "targets": []}
_lock = threading.Lock()


# ---------- DSP ----------

def _clusters(mask):
    """Contiguous True runs in a boolean array -> list of (start,end) inclusive."""
    out, s = [], None
    for i, v in enumerate(mask):
        if v and s is None:
            s = i
        elif not v and s is not None:
            out.append((s, i - 1)); s = None
    if s is not None:
        out.append((s, len(mask) - 1))
    return out


def _parabolic(y, i):
    """Sub-sample peak offset around index i (for finer azimuth)."""
    if 0 < i < len(y) - 1:
        denom = (y[i - 1] - 2 * y[i] + y[i + 1])
        if abs(denom) > 1e-12:
            return i + 0.5 * (y[i - 1] - y[i + 1]) / denom
    return float(i)


def _azimuth(seg0, seg1):
    """Inter-mic lag -> azimuth (deg). Coarse; front cone only."""
    a = seg0 - seg0.mean(); b = seg1 - seg1.mean()
    if a.std() < 1e-6 or b.std() < 1e-6:
        return 0.0
    xc = np.correlate(a, b, "full")
    i = int(np.argmax(np.abs(xc)))
    lag = _parabolic(np.abs(xc), i) - (len(b) - 1)
    sin_th = np.clip(lag / FS * C / MIC_BASELINE, -1, 1)
    return math.degrees(math.asin(sin_th))


def _az_at(rec2, flen, t0, minlag, bin_, P):
    """Azimuth (deg) of the reflector in range-bin `bin_`, median over pulses."""
    g = t0 + minlag + bin_
    return float(np.clip(np.median(
        [_azimuth(rec2[p * flen:(p + 1) * flen, 0][max(g - 200, 0):g + 200],
                  rec2[p * flen:(p + 1) * flen, 1][max(g - 200, 0):g + 200]) for p in range(P)]),
        -FOV, FOV))


def process_window(rec2, chirp, flen):
    """rec2: (PULSES*flen, 2) float32. -> (clutter list, targets list)."""
    minlag = int(2 * MIN_RANGE / C * FS)
    maxlag = int(2 * MAX_RANGE / C * FS)
    P = rec2.shape[0] // flen
    corrs = [np.abs(np.correlate(rec2[p * flen:(p + 1) * flen, 0], chirp, "valid"))
             for p in range(P)]
    L = min(len(c) for c in corrs)
    # ONE common t0 for all pulses: speaker->mic latency is constant within a
    # window, so per-pulse argmax jitter would misalign profiles and fake motion.
    t0 = int(np.argmax(np.sum([c[:L] for c in corrs], 0)))
    nb = min(maxlag, L - t0) - minlag
    if nb <= 0:
        return [], []
    prof = np.vstack([c[t0 + minlag:t0 + minlag + nb] for c in corrs])
    rng = (minlag + np.arange(nb)) / FS * C / 2
    mean_p = prof.mean(0)
    norm = mean_p.max() + 1e-9
    mean_n = mean_p / norm
    motion_n = prof.std(0) / norm
    gate = rng >= SENSOR_MIN                        # drop near-field crosstalk

    clutter = [{"range": round(float(rng[i]), 2),
                "az": round(_az_at(rec2, flen, t0, minlag, i, P), 1),
                "strength": round(float(mean_n[i]), 2)}
               for i in _peaks(mean_n * gate, rng, CLUTTER_THRESH)]

    targets = []
    for a, b in _clusters((motion_n > MOTION_THRESH) & gate):
        spread = float(rng[b] - rng[a])
        if spread > 1.5:                            # spans half the room = misalign/noise
            continue
        peak = a + int(np.argmax(motion_n[a:b + 1]))
        strength = float(mean_n[peak])
        pk_bins = prof[:, max(a - 1, 0):b + 2].argmax(1) + max(a - 1, 0)
        rt = (minlag + pk_bins) / FS * C / 2
        speed = float(np.clip(abs(rt[-1] - rt[0]) / (P * FRAME_MS / 1000), 0, 5))
        az = _az_at(rec2, flen, t0, minlag, peak, P)
        targets.append({"id": 0, "range": round(float(rng[peak]), 2),
                        "az": round(az, 1), "vel": round(speed, 2),
                        "strength": round(strength, 2), "spread": round(min(spread, 1.0), 2),
                        "class": classify(strength, spread, speed), "_m": float(motion_n[peak])})
    targets.sort(key=lambda t: -t["_m"])            # strongest movers first, cap 5
    targets = targets[:5]
    for i, t in enumerate(targets):
        t["id"] = i; t.pop("_m")
    return clutter, targets


def _peaks(y, rng, thresh, min_sep=0.25, cap=14):
    """Prominent local maxima above thresh, at least min_sep apart, strongest first."""
    idx = [i for i in range(1, len(y) - 1) if y[i] > thresh and y[i] >= y[i - 1] and y[i] >= y[i + 1]]
    idx.sort(key=lambda i: -y[i])
    chosen = []
    for i in idx:
        if all(abs(rng[i] - rng[j]) > min_sep for j in chosen):
            chosen.append(i)
            if len(chosen) >= cap:
                break
    return sorted(chosen)


def classify(strength, spread, speed):
    # ponytail: rule-based v1. Real system: micro-Doppler spread + gait periodicity.
    if speed > FALL_SPEED and spread < 0.2:
        return "falling"          # fast + compact = rigid body in flight
    if spread > SPREAD_HUMAN or strength < 0.25:
        return "human"            # range-smeared or weakly reflective = soft/articulated
    return "hard"                 # tight + strongly reflective = rigid surface (door)


# ---------- capture loops ----------

def live_loop():
    """Continuous full-duplex stream: emit a looping chirp, ring-buffer the mic,
    process the most-recent window every ~100 ms (sliding, no dead time)."""
    import sounddevice as sd
    chirp = make_chirp()
    flen = int(FS * FRAME_MS / 1000)
    emit_frame = np.zeros(flen, dtype=np.float32)
    emit_frame[:len(chirp)] = chirp * 0.8
    win = PULSES_PER_WIN * flen
    ring = np.zeros((win * 2, 2), dtype=np.float32)
    st = {"w": 0, "op": 0, "filled": 0}

    def cb(indata, outdata, frames, tinfo, status):
        idx = (st["op"] + np.arange(frames)) % flen        # loop the chirp forever
        outdata[:] = emit_frame[idx][:, None]
        st["op"] = (st["op"] + frames) % flen
        w, n = st["w"], frames                              # ring-buffer the capture
        if w + n <= len(ring):
            ring[w:w + n] = indata
        else:
            k = len(ring) - w
            ring[w:] = indata[:k]; ring[:n - k] = indata[k:]
        st["w"] = (w + n) % len(ring)
        st["filled"] = min(st["filled"] + n, len(ring))

    t_start = time.monotonic()
    with sd.Stream(samplerate=FS, dtype="float32", channels=(2, 2),
                   device=(DEVICE, DEVICE), callback=cb):
        print(f"streaming on device {DEVICE}, ~10 Hz detection")
        prev = []
        while True:
            time.sleep(0.1)
            if st["filled"] < win:
                continue
            w = st["w"]
            # arbitrary window start is fine: chirps are periodic at flen, so
            # process_window's summed-correlation t0 lands consistently.
            seg = (ring[w - win:w] if w >= win
                   else np.vstack([ring[len(ring) - (win - w):], ring[:w]])).copy()
            try:
                clutter, targets = process_window(seg, chirp, flen)
            except Exception as e:
                print("process error:", e); continue
            # 2-window persistence: a real mover corroborates across frames within
            # ~0.4 m; isolated noise bursts don't. ponytail: range-only match.
            matched = [t for t in targets if any(abs(t["range"] - r) < 0.4 for r in prev)]
            prev = [t["range"] for t in targets]
            for i, t in enumerate(matched):
                t["id"] = i
            _publish(time.monotonic() - t_start, clutter, matched)


def sim_loop():
    """Synthetic scene: a human pacing across the cone + an occasional hard mover."""
    t_start = time.monotonic()
    i = 0
    while True:
        t = time.monotonic() - t_start
        az = FOV * math.sin(t * 0.6)                     # human paces left<->right
        rng = 2.4 + 0.5 * math.sin(t * 0.9)
        targets = [{"id": 0, "range": round(rng, 2), "az": round(az, 1),
                    "vel": round(0.6 + 0.3 * abs(math.cos(t * 0.6)), 2),
                    "strength": 0.18, "spread": 0.45, "class": "human"}]
        if int(t) % 7 == 3:                              # a door swings now and then
            targets.append({"id": 1, "range": 1.3, "az": -38, "vel": 0.9,
                            "strength": 0.7, "spread": 0.1, "class": "hard"})
        clutter = [{"range": 3.6, "az": 6, "strength": 0.9},      # back wall (hard)
                   {"range": 1.7, "az": -34, "strength": 0.4},    # couch (soft)
                   {"range": 4.6, "az": 40, "strength": 0.75}]    # side wall / door (hard)
        _publish(t, clutter, targets)
        i += 1
        time.sleep(FRAME_MS * PULSES_PER_WIN / 1000)


def _publish(t, clutter, targets):
    with _lock:
        _latest.update(t=round(t, 2), clutter=clutter, targets=targets)


# ---------- HTTP ----------

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        with _lock:
            body = json.dumps(_latest).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")  # viewer on another origin
        self.end_headers()
        self.wfile.write(body)


def serve():
    HTTPServer(("0.0.0.0", PORT), Handler).serve_forever()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--sim", action="store_true", help="synthetic data, no hardware")
    ap.add_argument("--record", metavar="FILE", help="dump frames to FILE for replay")
    ap.add_argument("--seconds", type=float, default=0, help="stop after N s (with --record)")
    a = ap.parse_args()

    loop = sim_loop if a.sim else live_loop

    if a.record:
        # run the loop in a thread, snapshot frames, write a JSON array
        threading.Thread(target=loop, daemon=True).start()
        frames, t0 = [], time.monotonic()
        while time.monotonic() - t0 < (a.seconds or 15):
            time.sleep(FRAME_MS * PULSES_PER_WIN / 1000)
            with _lock:
                frames.append(json.loads(json.dumps(_latest)))
        json.dump(frames, open(a.record, "w"))
        print(f"wrote {len(frames)} frames -> {a.record}")
    else:
        threading.Thread(target=loop, daemon=True).start()
        print(f"sensor serving http://localhost:{PORT}/  ({'sim' if a.sim else 'live'})")
        serve()

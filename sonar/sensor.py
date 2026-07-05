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
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import numpy as np
import sonar
from sonar import FS, C, F0, F1, make_chirp, MIN_RANGE, MAX_RANGE

FRAME_MS = 90                 # long gap so room reverb decays before the next chirp
PULSES_PER_WIN = 3            # average 3 chirps/window (~0.27 s, ~3.7 Hz) for SNR
DEVICE = 4                    # ALC285 analog, 2-ch
MIC_BASELINE = 0.10           # m; assumed 2-mic spacing. ponytail: calibrate per laptop.
PORT = 8765
FOV = 50                      # forward cone half-angle (deg) we trust azimuth within
MAX_RANGE = 4.0               # room size: range-gate + max mapped distance (overrides sonar)

# detection (tune live via /config?motion= or --motion)
CLUTTER_THRESH = 0.45         # fraction of background peak = a static reflector
MOTION_THRESH = 0.08          # residual margin ABOVE the change-floor (adaptive MTI)
FALL_SPEED = 2.0              # m/s + compact -> falling
SPREAD_HUMAN = 0.30           # range-spread (m) or weak echo -> human/soft
SENSOR_MIN = 0.5              # m; skip near-field crosstalk
BG_ALPHA = 0.05               # background EMA rate (how fast the static scene is learned)
WARMUP = 10                   # windows to learn the background before detecting

# azimuth calibration (baseline in m + sign), overridden by calib.json if present
CALIB_FILE = os.path.join(os.path.dirname(__file__), "calib.json")
_calib = {"baseline": MIC_BASELINE, "sign": 1.0}
if os.path.exists(CALIB_FILE):
    try:
        _calib.update(json.load(open(CALIB_FILE)))
    except Exception:
        pass

_cfg = {"motion": MOTION_THRESH}   # live-tunable via GET /config?motion=<val>
_bg = {"prof": None, "warm": 0}    # MTI static-scene background + warmup counter
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


def _lag(seg0, seg1):
    """Sub-sample inter-mic delay (samples) via cross-correlation."""
    a = seg0 - seg0.mean(); b = seg1 - seg1.mean()
    if a.std() < 1e-6 or b.std() < 1e-6:
        return 0.0
    xc = np.abs(np.correlate(a, b, "full"))
    return _parabolic(xc, int(np.argmax(xc))) - (len(b) - 1)


def _azimuth(seg0, seg1):
    """Inter-mic lag -> azimuth (deg), using calibrated baseline + sign. Front cone only."""
    sin_th = np.clip(_calib["sign"] * _lag(seg0, seg1) / FS * C / _calib["baseline"], -1, 1)
    return math.degrees(math.asin(sin_th))


def _az_at(rec2, flen, t0, minlag, bin_, P):
    """Azimuth (deg) of the reflector in range-bin `bin_`, median over pulses."""
    g = t0 + minlag + bin_
    return float(np.clip(np.median(
        [_azimuth(rec2[p * flen:(p + 1) * flen, 0][max(g - 200, 0):g + 200],
                  rec2[p * flen:(p + 1) * flen, 1][max(g - 200, 0):g + 200]) for p in range(P)]),
        -FOV, FOV))


def process_window(rec2, chirp, flen):
    """Average this window's chirps, subtract the learned static background (MTI).
    -> (clutter from background, moving targets from residual, debug dict)."""
    minlag = int(2 * MIN_RANGE / C * FS)
    maxlag = int(2 * MAX_RANGE / C * FS)
    P = rec2.shape[0] // flen
    corrs = [np.abs(np.correlate(rec2[p * flen:(p + 1) * flen, 0], chirp, "valid"))
             for p in range(P)]
    L = min(len(c) for c in corrs)
    t0 = int(np.argmax(np.sum([c[:L] for c in corrs], 0)))    # common direct-path ref
    nb = min(maxlag, L - t0) - minlag
    if nb <= 0:
        return [], [], {"peak": 0.0, "floor": 0.0, "thr": 0.0, "raw": 0, "warm": True}
    prof = np.vstack([c[t0 + minlag:t0 + minlag + nb] for c in corrs])
    rng = (minlag + np.arange(nb)) / FS * C / 2
    cur = prof.mean(0)                                        # average window -> SNR

    B = _bg["prof"]
    if B is None or len(B) != nb:                            # (re)initialise background
        _bg["prof"] = cur.copy(); _bg["warm"] = 0
        B = cur
    else:                                                    # align cur to background:
        x = np.correlate(cur - cur.mean(), B - B.mean(), "full")   # remove inter-window
        shift = int(np.argmax(x)) - (nb - 1)                       # latency jitter
        if 0 < abs(shift) <= 30:
            cur = np.roll(cur, -shift)
    warming = _bg["warm"] < WARMUP
    resid = np.abs(cur - B)                                   # MTI: change from static
    _bg["prof"] = (1 - BG_ALPHA) * B + BG_ALPHA * cur         # slowly follow the room
    _bg["warm"] += 1

    norm = B.max() + 1e-9
    Bn, curn, residn = B / norm, cur / norm, resid / norm
    gate = rng >= SENSOR_MIN

    # static scene (background) -> clutter to render the room
    clutter = [{"range": round(float(rng[i]), 2),
                "az": round(_az_at(rec2, flen, t0, minlag, i, P), 1),
                "strength": round(float(Bn[i]), 2)}
               for i in _peaks(Bn * gate, rng, CLUTTER_THRESH, min_sep=0.5, cap=6)]

    rg = residn[gate]
    floor = float(np.median(rg)) if rg.size else 0.0
    eff = _cfg["motion"] + floor                             # adaptive on the change-floor
    dbg = {"peak": round(float(rg.max()), 3) if rg.size else 0.0,
           "floor": round(floor, 3), "thr": round(eff, 3), "raw": 0, "warm": warming}
    if warming:
        return clutter, [], dbg

    targets = []
    clusters = _clusters((residn > eff) & gate)
    dbg["raw"] = len(clusters)
    for a, b in clusters:
        spread = float(rng[b] - rng[a])
        if spread > 1.5:
            continue
        peak = a + int(np.argmax(residn[a:b + 1]))
        strength = float(curn[peak])
        az = _az_at(rec2, flen, t0, minlag, peak, P)
        targets.append({"id": 0, "range": round(float(rng[peak]), 2), "az": round(az, 1),
                        "vel": 0.0, "strength": round(strength, 2),
                        "spread": round(min(spread, 1.0), 2),
                        "class": classify(strength, spread, 0.0), "_m": float(residn[peak])})
    targets.sort(key=lambda t: -t["_m"])
    targets = targets[:3]
    for t in targets:
        t.pop("_m")
    return clutter, targets, dbg


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
    """Blocking playrec loop: emit PULSES_PER_WIN chirps, capture 2 ch, detect.
    ponytail: ~3 Hz and rock-solid. A callback stream is faster but segfaulted on
    this ALSA setup; the viewer lerp-smooths to 60 fps so 3 Hz reads fine.
    Knob: lower PULSES_PER_WIN for snappier (noisier) updates."""
    import sounddevice as sd
    sd.default.device = DEVICE
    chirp = make_chirp()
    flen = int(FS * FRAME_MS / 1000)
    fr = np.zeros(flen, dtype=np.float32); fr[:len(chirp)] = chirp
    emit = np.tile(fr, PULSES_PER_WIN) * 0.8
    t_start = time.monotonic()
    prev = []
    dt = PULSES_PER_WIN * FRAME_MS / 1000
    print(f"live sensing on device {DEVICE}  (band={sonar.F0 // 1000}-{sonar.F1 // 1000}kHz, "
          f"range<={MAX_RANGE:.0f}m, margin={_cfg['motion']:.3f})")
    print(f"tune live:  curl 'http://localhost:{PORT}/config?motion=0.12'\n")
    while True:
        rec = sd.playrec(emit, samplerate=FS, channels=2, dtype="float32"); sd.wait()
        try:
            clutter, targets, dbg = process_window(rec, chirp, flen)
        except Exception as e:
            print("process error:", e); continue
        # persistence + velocity: keep only targets matching one from the previous
        # frame (within 0.6 m); velocity = its range change / frame time.
        shown = []
        for t in targets:
            near = min(prev, key=lambda p: abs(p["range"] - t["range"]), default=None)
            if near and abs(near["range"] - t["range"]) < 0.6:
                t["vel"] = round(abs(t["range"] - near["range"]) / dt, 2)
                if t["vel"] > FALL_SPEED and t["spread"] < 0.2:
                    t["class"] = "falling"
                shown.append(t)
        for i, t in enumerate(shown):
            t["id"] = i
        prev = targets
        ts = time.monotonic() - t_start
        _publish(ts, clutter, shown)
        _log(ts, dbg, shown)


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
        clutter = [{"range": 3.8, "az": 4, "strength": 0.85},     # back wall (strong)
                   {"range": 1.8, "az": -32, "strength": 0.4},    # couch (soft)
                   {"range": 3.0, "az": 38, "strength": 0.6}]     # doorway (medium)
        _publish(t, clutter, targets)
        i += 1
        time.sleep(FRAME_MS * PULSES_PER_WIN / 1000)


def _publish(t, clutter, targets):
    with _lock:
        _latest.update(t=round(t, 2), clutter=clutter, targets=targets)


def _log(ts, dbg, targets):
    """Readable per-window line; expands to one row per moving target."""
    if dbg.get("warm"):
        print(f"[{ts:6.1f}s]  learning background…", flush=True)
        return
    head = (f"[{ts:6.1f}s]  change floor={dbg['floor']:.3f}  peak={dbg['peak']:.3f}  "
            f"thr={dbg['thr']:.3f}  moving={len(targets)}")
    if not targets:
        print(head + "   · still", flush=True)
        return
    print(head, flush=True)
    for t in targets:
        bar = "█" * min(int(t["strength"] * 20), 20)
        print(f"      {t['class']:<7} {t['range']:4.2f}m  az {t['az']:+6.1f}°  "
              f"{t['vel']:4.2f} m/s  refl {bar}", flush=True)


def calibrate(angle_deg):
    """Put a strong flat reflector (or stand) at a KNOWN azimuth (right = +, e.g.
    1 m to your right at 2 m deep ~= +27 deg). Measures the inter-mic lag on the
    dominant reflector and solves the mic baseline + sign. Writes calib.json.
    ponytail: single-point solve; assumes boresight (straight-ahead) = 0 deg."""
    import sounddevice as sd
    sd.default.device = DEVICE
    chirp = make_chirp()
    flen = int(FS * FRAME_MS / 1000)
    fr = np.zeros(flen, dtype=np.float32); fr[:len(chirp)] = chirp * 0.8
    emit = np.tile(fr, PULSES_PER_WIN)
    lags = []
    print(f"calibrating: reflector at {angle_deg:+.0f} deg — hold still...")
    for _ in range(12):
        rec = sd.playrec(emit, samplerate=FS, channels=2, dtype="float32"); sd.wait()
        c0 = np.abs(np.correlate(rec[:flen, 0], chirp, "valid"))
        t0 = int(np.argmax(c0))
        lo = t0 + int(2 * SENSOR_MIN / C * FS)
        seg = c0[lo:min(t0 + int(2 * MAX_RANGE / C * FS), len(c0))]
        if not len(seg):
            continue
        g = lo + int(np.argmax(seg))                 # dominant reflector past near field
        lags.append(_lag(rec[:flen, 0][max(g - 200, 0):g + 200],
                         rec[:flen, 1][max(g - 200, 0):g + 200]))
    lag = float(np.median(lags)) if lags else 0.0
    th = math.radians(angle_deg)
    if abs(math.sin(th)) < 0.05 or abs(lag) < 1e-3:
        print("calibration failed: use ~20-45 deg and a strong flat reflector.")
        return
    baseline = abs(lag) / FS * C / abs(math.sin(th))
    sign = math.copysign(1, lag) * math.copysign(1, angle_deg)
    json.dump({"baseline": round(baseline, 4), "sign": sign}, open(CALIB_FILE, "w"))
    print(f"calibrated: lag={lag:.2f} samp -> baseline={baseline * 100:.1f} cm, "
          f"sign={sign:+.0f}. Saved {CALIB_FILE}")


# ---------- HTTP ----------

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path.startswith("/config"):                 # live-tune detection knobs
            from urllib.parse import urlparse, parse_qs
            q = parse_qs(urlparse(self.path).query)
            if "motion" in q:
                try:
                    _cfg["motion"] = max(0.02, min(1.0, float(q["motion"][0])))
                except ValueError:
                    pass
            body = json.dumps(_cfg).encode()
        else:
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
    ap.add_argument("--calibrate", type=float, metavar="DEG",
                    help="calibrate azimuth: reflector at this known angle (right +)")
    ap.add_argument("--motion", type=float, metavar="THR",
                    help="initial motion threshold (default from MOTION_THRESH); tune live via /config")
    ap.add_argument("--band", choices=["high", "low"], default="high",
                    help="high=17-21kHz near-inaudible; low=8-12kHz audible but far more coherent")
    a = ap.parse_args()

    if a.band == "low":                    # longer wavelength -> far less phase-noise
        sonar.F0, sonar.F1 = 8000, 12000
    if a.motion is not None:
        _cfg["motion"] = a.motion

    if a.calibrate is not None:
        calibrate(a.calibrate)
        sys.exit()

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

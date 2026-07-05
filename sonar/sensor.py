#!/usr/bin/env python3
"""Real-time acoustic motion sensor (coherent).

Emits near-ultrasonic chirps, matched-filters the echo COHERENTLY (complex),
references each echo to the direct speaker->mic path to cancel per-ping gain/
phase drift, subtracts a learned static background, and reports moving reflectors
with a CFAR (median/MAD) detector. Coarse azimuth from the 2-mic array. Serves
detection frames over HTTP for the viewer and/or draws a terminal radar.

Why coherent: incoherent |profile| differencing turns the laptop's own gain/
phase drift into phantom "motion" (it fires even in a soundproof booth). Complex
subtraction referenced to the direct path cancels a truly static scene to ~0.
Technique adapted from "Sean Sonar".

  python sensor.py --map                 # live top-down ASCII radar
  python sensor.py --band low --map      # 8-12kHz audible, most coherent
  python sensor.py --selftest            # synthetic motion check, no hardware

Frame: {"t","fov","max_range","clutter":[{range,az,strength}],
        "targets":[{id,range,az,vel,strength,spread,class:"motion",snr}]}
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
from sonar import FS, C, make_chirp

FRAME_MS = 90                 # long gap so room reverb decays before the next chirp
PULSES_PER_WIN = 3            # average 3 chirps/window (~0.27 s, ~3.7 Hz) for SNR
DEVICE = 4                    # ALC285 analog, 2-ch
MIC_BASELINE = 0.10           # m; assumed 2-mic spacing. ponytail: calibrate per laptop.
PORT = 8765
FOV = 50                      # forward cone half-angle (deg) we trust azimuth within
NEAR = 0.10                   # closest detectable range (m) — coherent ref handles near field
MAX_RANGE = 2.0               # farthest mapped range (m); overrides sonar default

CLUTTER_THRESH = 0.45         # fraction of background peak = a static reflector
SNR_THRESH = 3.0              # detect when residual peak > median + SNR_THRESH*sigma
ABS_FLOOR = 0.002             # min residual (echo/direct ratio) to count
WARMUP = 15                   # windows to learn the static background before detecting
CAL_RATE = 1.0 / 12           # background learning rate during warmup
BG_ADAPT = 0.02               # slow background adaptation in quiet bins
GUARD = 40                    # bins around the peak excluded from the CFAR noise estimate

_cfg = {"snr": SNR_THRESH}    # live-tunable via GET /config?snr=<val>
_bg = {"prof": None, "warm": 0}    # complex static background + warmup counter
_mf = {"conj": None, "cn": 0, "nfft": 0}   # complex matched-filter kernel (set per band)
_view = {"map": False}
_latest = {"t": 0, "fov": FOV, "max_range": MAX_RANGE, "clutter": [], "targets": []}
_lock = threading.Lock()


# ---------- matched filter (complex / coherent) ----------

def build_mf(flen):
    """Build the analytic (complex) chirp + conj-FFT kernel. Call after band set."""
    n = int(FS * sonar.CHIRP_MS / 1000)
    t = np.arange(n) / FS
    k = (sonar.F1 - sonar.F0) / (sonar.CHIRP_MS / 1000)
    cchirp = np.exp(1j * 2 * np.pi * (sonar.F0 * t + 0.5 * k * t * t)) * np.hanning(n)
    nfft = 1 << (flen + n).bit_length()
    _mf.update(conj=np.conj(np.fft.fft(cchirp, nfft)), cn=n, nfft=nfft)


def _cmf(x):
    """Complex matched filter: correlation of x against the analytic chirp."""
    corr = np.fft.ifft(np.fft.fft(x, _mf["nfft"]) * _mf["conj"])
    return corr[:len(x) - _mf["cn"] + 1]


# ---------- helpers ----------

def _clusters(mask):
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
    if 0 < i < len(y) - 1:
        d = (y[i - 1] - 2 * y[i] + y[i + 1])
        if abs(d) > 1e-12:
            return i + 0.5 * (y[i - 1] - y[i + 1]) / d
    return float(i)


def _peaks(y, rng, thresh, min_sep=0.3, cap=6):
    idx = [i for i in range(1, len(y) - 1) if y[i] > thresh and y[i] >= y[i - 1] and y[i] >= y[i + 1]]
    idx.sort(key=lambda i: -y[i])
    chosen = []
    for i in idx:
        if all(abs(rng[i] - rng[j]) > min_sep for j in chosen):
            chosen.append(i)
            if len(chosen) >= cap:
                break
    return sorted(chosen)


def _az_at(rec2, flen, t0, minlag, bin_, P):
    """Azimuth (deg) from the 2-mic time delay at range-bin `bin_`. Coarse, front cone."""
    g = t0 + minlag + bin_
    lags = []
    for p in range(P):
        a = rec2[p * flen:(p + 1) * flen, 0][max(g - 200, 0):g + 200]
        b = rec2[p * flen:(p + 1) * flen, 1][max(g - 200, 0):g + 200]
        if a.std() < 1e-6 or b.std() < 1e-6:
            continue
        xc = np.abs(np.correlate(a - a.mean(), b - b.mean(), "full"))
        lags.append(_parabolic(xc, int(np.argmax(xc))) - (len(b) - 1))
    if not lags:
        return 0.0
    sin_th = np.clip(np.median(lags) / FS * C / MIC_BASELINE, -1, 1)
    return float(np.clip(math.degrees(math.asin(sin_th)), -FOV, FOV))


# ARCHIVED 2026-07-05: human/hard/falling split disabled until motion detection is
# solid — detections are all "motion". Re-enable by calling classify(). Kept, not deleted.
def classify(strength, spread, speed):
    if speed > 2.0 and spread < 0.2:
        return "falling"
    if spread > 0.30 or strength < 0.25:
        return "human"
    return "hard"


# ---------- coherent detection ----------

def _clutter(Bmag, rng, rec2, flen, t0, minlag, P):
    norm = Bmag.max() + 1e-9
    return [{"range": round(float(rng[i]), 2),
             "az": round(_az_at(rec2, flen, t0, minlag, i, P), 1),
             "strength": round(float(Bmag[i] / norm), 2)}
            for i in _peaks(Bmag / norm, rng, CLUTTER_THRESH)]


def _dbg(noise=0.0, peak=0.0, thr=0.0, raw=0, warm=False):
    return {"noise": round(noise, 4), "peak": round(peak, 4),
            "thr": round(thr, 4), "raw": raw, "warm": warm}


def process_window(rec2, flen):
    """Coherent MTI. -> (clutter, moving targets, debug). Uses module _mf + _bg."""
    minlag = int(2 * NEAR / C * FS)
    maxlag = int(2 * MAX_RANGE / C * FS)
    P = rec2.shape[0] // flen
    segs, ds = [], []
    for p in range(P):
        c0 = _cmf(rec2[p * flen:(p + 1) * flen, 0].astype(np.float64))
        d = int(np.argmax(np.abs(c0)))
        if d + maxlag > len(c0) or abs(c0[d]) < 1e-9:
            continue
        segs.append(c0[d + minlag:d + maxlag] / c0[d])   # per-ping ref cancels the phase
        ds.append(d)                                     # drift a slow ref can't track
    if not segs:
        return [], [], _dbg()
    nb = min(len(s) for s in segs)
    cur = np.mean([s[:nb] for s in segs], axis=0)        # complex average -> SNR
    rng = (minlag + np.arange(nb)) / FS * C / 2
    t0 = int(np.median(ds))

    B = _bg["prof"]
    if B is None or len(B) != nb:                        # (re)initialise background
        _bg["prof"] = cur.copy(); _bg["warm"] = 0
        return [], [], _dbg(warm=True)
    if _bg["warm"] < WARMUP:                             # learn the static scene
        _bg["prof"] = (1 - CAL_RATE) * B + CAL_RATE * cur
        _bg["warm"] += 1
        return _clutter(np.abs(B), rng, rec2, flen, t0, minlag, P), [], _dbg(warm=True)

    resid = np.abs(cur - B)                              # coherent subtraction (static ~0)
    i = int(np.argmax(resid))                            # CFAR: exclude the peak's own lobe
    noise = np.concatenate([resid[:max(i - GUARD, 0)], resid[i + GUARD:]])  # from the noise est
    med = float(np.median(noise))
    sigma = 1.4826 * float(np.median(np.abs(noise - med))) + 1e-9
    thr = med + _cfg["snr"] * sigma
    B[resid < med + 3 * sigma] = ((1 - BG_ADAPT) * B + BG_ADAPT * cur)[resid < med + 3 * sigma]

    clutter = _clutter(np.abs(B), rng, rec2, flen, t0, minlag, P)
    dbg = _dbg(sigma, float(resid.max()), thr, 0, False)
    targets = []
    clusters = _clusters(resid > thr)
    dbg["raw"] = len(clusters)
    for a, b in clusters:
        if rng[b] - rng[a] > 1.5:
            continue
        peak = a + int(np.argmax(resid[a:b + 1]))
        if resid[peak] < ABS_FLOOR:
            continue
        targets.append({"id": 0, "range": round(float(rng[peak]), 2),
                        "az": round(_az_at(rec2, flen, t0, minlag, peak, P), 1),
                        "vel": 0.0, "strength": round(float(min(abs(cur[peak]), 1.0)), 2),
                        "spread": round(float(min(rng[b] - rng[a], 1.0)), 2),
                        "class": "motion", "snr": round(float((resid[peak] - med) / sigma), 1),
                        "_m": float(resid[peak])})
    targets.sort(key=lambda t: -t["_m"])
    targets = targets[:3]
    for t in targets:
        t.pop("_m")
    return clutter, targets, dbg


# ---------- capture ----------

def live_loop():
    """Continuous full-duplex stream (shared clock -> stable phase, so coherent
    subtraction actually cancels). Emits a looping chirp; a queue feeds windows to
    the detector, advancing one ping at a time. Blocking playrec cannot do this."""
    import queue
    import sounddevice as sd
    flen = int(FS * FRAME_MS / 1000)
    build_mf(flen)
    chirp = make_chirp()
    tx = np.zeros(flen, dtype=np.float32); tx[:len(chirp)] = chirp * 0.8
    win = PULSES_PER_WIN * flen
    q: "queue.Queue[np.ndarray]" = queue.Queue()
    st = {"tx": 0}

    def cb(indata, outdata, frames, tinfo, status):
        idx = (st["tx"] + np.arange(frames)) % flen        # loop the chirp forever
        outdata[:] = tx[idx][:, None]
        st["tx"] = (st["tx"] + frames) % flen
        q.put(indata.copy())                                # 2-ch capture -> main loop

    prev = []
    dt = flen / FS                                          # windows advance one ping
    t_start = time.monotonic()
    print(f"live sensing on device {DEVICE}  (band={sonar.F0 // 1000}-{sonar.F1 // 1000}kHz, "
          f"range {NEAR:.2f}-{MAX_RANGE:.0f}m, snr>{_cfg['snr']:.0f})")
    print(f"tune live:  curl 'http://localhost:{PORT}/config?snr=8'\n")
    buf = np.zeros((0, 2), dtype=np.float32)
    with sd.Stream(samplerate=FS, channels=2, dtype="float32", device=DEVICE, callback=cb):
        while True:
            buf = np.concatenate([buf, q.get()])
            while len(buf) >= win:
                window = buf[:win]
                buf = buf[flen:]                            # advance exactly one ping
                try:
                    clutter, targets, dbg = process_window(window, flen)
                except Exception as e:
                    print("process error:", e); continue
                shown = []
                for t in targets:                           # persistence + velocity
                    near = min(prev, key=lambda p: abs(p["range"] - t["range"]), default=None)
                    if near and abs(near["range"] - t["range"]) < 0.6:
                        t["vel"] = round(abs(t["range"] - near["range"]) / dt, 2)
                        shown.append(t)
                for i, t in enumerate(shown):
                    t["id"] = i
                prev = targets
                ts = time.monotonic() - t_start
                _publish(ts, clutter, shown)
                (_draw_map if _view["map"] else _log)(ts, dbg, clutter, shown)


def sim_loop():
    """Synthetic motion for viewer/no-hardware dev (not real data)."""
    t_start = time.monotonic()
    while True:
        t = time.monotonic() - t_start
        az = FOV * math.sin(t * 0.6)
        rng = 1.4 + 0.4 * math.sin(t * 0.9)
        targets = [{"id": 0, "range": round(rng, 2), "az": round(az, 1),
                    "vel": round(0.6 + 0.3 * abs(math.cos(t * 0.6)), 2),
                    "strength": 0.3, "spread": 0.4, "class": "motion", "snr": 12.0}]
        clutter = [{"range": 1.9, "az": 4, "strength": 0.85}, {"range": 1.0, "az": -30, "strength": 0.5}]
        _publish(t, clutter, targets)
        time.sleep(FRAME_MS * PULSES_PER_WIN / 1000)


def _publish(t, clutter, targets):
    with _lock:
        _latest.update(t=round(t, 2), clutter=clutter, targets=targets)


def _log(ts, dbg, clutter, targets):
    if dbg.get("warm"):
        print(f"[{ts:6.1f}s]  learning background…", flush=True)
        return
    head = (f"[{ts:6.1f}s]  noise={dbg['noise']:.4f}  peak={dbg['peak']:.4f}  "
            f"thr={dbg['thr']:.4f}  moving={len(targets)}")
    if not targets:
        print(head + "   · still", flush=True)
        return
    print(head, flush=True)
    for t in targets:
        print(f"      motion  {t['range']:4.2f}m  az {t['az']:+6.1f}°  "
              f"{t['vel']:4.2f} m/s  snr {t.get('snr', 0):4.1f}", flush=True)


def _draw_map(ts, dbg, clutter, targets):
    """Live top-down ASCII radar: sensor at bottom-centre, depth up, azimuth across."""
    W, H = 61, 20
    cx, cy = W // 2, H - 1
    grid = [[" "] * W for _ in range(H)]

    def put(r, az, ch):
        a = math.radians(az)
        col = cx + int(round(r * math.sin(a) / MAX_RANGE * cx))
        row = cy - int(round(r * math.cos(a) / MAX_RANGE * (H - 1)))
        if 0 <= row < H and 0 <= col < W:
            grid[row][col] = ch

    for c in clutter:
        put(c["range"], c["az"], "·")
    for t in targets:
        put(t["range"], t["az"], "M")
    grid[cy][cx] = "^"

    status = ("learning background…" if dbg.get("warm") else
              f"noise={dbg['noise']:.4f} peak={dbg['peak']:.4f} thr={dbg['thr']:.4f} moving={len(targets)}")
    lines = ["\033[H\033[2J",
             f" t={ts:6.1f}s   {status}",
             " legend: M=motion  ·=static  ^=sensor",
             " +" + "-" * W + "+"]
    lines += [" |" + "".join(r) + "|" for r in grid]
    lines.append(" +" + "-" * W + f"+  depth {NEAR:.1f}..{MAX_RANGE:.0f}m up, width +/-{FOV}deg")
    for t in targets:
        lines.append(f"   motion {t['range']:.2f}m  az {t['az']:+.0f}deg  {t['vel']:.2f}m/s  snr {t.get('snr',0):.1f}")
    print("\n".join(lines), flush=True)


def motion_selftest():
    """No hardware: synthetic static scene + a mover that appears. Asserts the
    coherent MTI stays quiet on static and fires at the mover's range."""
    flen = int(FS * FRAME_MS / 1000)
    build_mf(flen)
    chirp = make_chirp()
    rng = np.random.default_rng(0)

    def win(mover=None):
        w = np.zeros((PULSES_PER_WIN * flen, 2), dtype=np.float32)
        for p in range(PULSES_PER_WIN):
            sig = np.zeros(flen, dtype=np.float32)
            sig[:len(chirp)] += chirp
            for r, amp in ((0.8, 0.3), (1.6, 0.35)):          # static reflectors
                lag = int(2 * r / C * FS); sig[lag:lag + len(chirp)] += amp * chirp
            if mover:
                lag = int(2 * mover / C * FS); sig[lag:lag + len(chirp)] += 0.2 * chirp
            sig += rng.normal(0, 0.003, flen).astype(np.float32)
            w[p * flen:(p + 1) * flen, 0] = sig
            w[p * flen:(p + 1) * flen, 1] = sig
        return w

    _bg["prof"] = None; _bg["warm"] = 0
    for _ in range(WARMUP + 3):
        process_window(win(), flen)
    _, quiet, _ = process_window(win(), flen)
    assert not quiet, f"static should be quiet, got {quiet}"
    _, moved, _ = process_window(win(1.2), flen)
    assert any(abs(t["range"] - 1.2) < 0.3 for t in moved), f"missed mover: {moved}"
    print(f"motion selftest PASS: static quiet; mover detected at {[t['range'] for t in moved]} m")


def calibrate(angle_deg):
    """Reflector at a KNOWN azimuth (right +) -> solve mic baseline + sign."""
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
        lo = t0 + int(2 * NEAR / C * FS)
        seg = c0[lo:min(t0 + int(2 * MAX_RANGE / C * FS), len(c0))]
        if not len(seg):
            continue
        g = lo + int(np.argmax(seg))
        a = rec[:flen, 0][max(g - 200, 0):g + 200]; b = rec[:flen, 1][max(g - 200, 0):g + 200]
        xc = np.abs(np.correlate(a - a.mean(), b - b.mean(), "full"))
        lags.append(_parabolic(xc, int(np.argmax(xc))) - (len(b) - 1))
    lag = float(np.median(lags)) if lags else 0.0
    th = math.radians(angle_deg)
    if abs(math.sin(th)) < 0.05 or abs(lag) < 1e-3:
        print("calibration failed: use ~20-45 deg and a strong flat reflector.")
        return
    baseline = abs(lag) / FS * C / abs(math.sin(th))
    sign = math.copysign(1, lag) * math.copysign(1, angle_deg)
    cf = os.path.join(os.path.dirname(__file__), "calib.json")
    json.dump({"baseline": round(baseline, 4), "sign": sign}, open(cf, "w"))
    print(f"calibrated: baseline={baseline * 100:.1f} cm, sign={sign:+.0f}. Saved {cf}")


# ---------- HTTP ----------

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path.startswith("/config"):
            from urllib.parse import urlparse, parse_qs
            q = parse_qs(urlparse(self.path).query)
            if "snr" in q:
                try:
                    _cfg["snr"] = max(0.5, min(30.0, float(q["snr"][0])))
                except ValueError:
                    pass
            body = json.dumps(_cfg).encode()
        else:
            with _lock:
                body = json.dumps(_latest).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)


def serve():
    HTTPServer(("0.0.0.0", PORT), Handler).serve_forever()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--sim", action="store_true", help="synthetic data, no hardware")
    ap.add_argument("--record", metavar="FILE", help="dump frames to FILE")
    ap.add_argument("--seconds", type=float, default=0, help="stop after N s (with --record)")
    ap.add_argument("--calibrate", type=float, metavar="DEG",
                    help="calibrate azimuth: reflector at this known angle (right +)")
    ap.add_argument("--snr", type=float, metavar="K", help="detection threshold in sigma (default 6)")
    ap.add_argument("--band", choices=["high", "low"], default="high",
                    help="high=17-21kHz near-inaudible; low=8-12kHz audible but far more coherent")
    ap.add_argument("--map", action="store_true", help="live top-down ASCII radar in the terminal")
    ap.add_argument("--selftest", action="store_true", help="synthetic motion-detection check (no hardware)")
    a = ap.parse_args()

    if a.band == "low":
        sonar.F0, sonar.F1 = 8000, 12000
    if a.snr is not None:
        _cfg["snr"] = a.snr
    if a.selftest:
        motion_selftest()
        sys.exit()
    if a.calibrate is not None:
        calibrate(a.calibrate)
        sys.exit()
    if a.map:
        _view["map"] = True

    loop = sim_loop if a.sim else live_loop
    if a.record:
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

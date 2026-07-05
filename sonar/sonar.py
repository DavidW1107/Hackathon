#!/usr/bin/env python3
"""Hand-sweep acoustic sonar for a laptop — engine + CLI.

Spin the laptop through ~360 degrees at a steady speed; it emits chirps, times
the echoes for range, and builds a crude room outline + 3D wall extrusion.

Heading source (auto-detected at runtime):
  * gyroscope via Linux IIO (/sys/bus/iio/devices/.../in_anglvel_z_raw) if present
    -> true integrated yaw, decent map.
  * else constant-rotation-rate assumption -> rough map, skews if you spin unevenly.

Physics reality: one speaker + one mic gives RANGE only, never bearing. That is
why we need a heading source and a physical sweep at all. Hard walls + quiet
room + volume up = best; soft surfaces reflect almost nothing and leave gaps.

Run:
    pip install -r requirements.txt        # + libportaudio2 on Ubuntu
    python sonar.py --selftest             # no hardware, checks the math
    python sonar.py --seconds 12 --height 2.5
    python ui.py                           # graphical version
"""
import argparse
import glob
import os
import sys
import time
import numpy as np

FS = 48000            # sample rate (Hz)
C = 343.0             # speed of sound (m/s)
F0, F1 = 5000, 20000  # chirp band (Hz) — inside laptop speaker/mic range
CHIRP_MS = 6          # chirp length
FRAME_MS = 40         # emit period per chirp -> max range ~6.8 m
MIN_RANGE = 0.15      # blind zone (m): gate out speaker->mic crosstalk
MAX_RANGE = 6.0       # ignore echoes past this (room reverb takes over)
NOISE_FLOOR = 0.15    # echo peak must beat this fraction of the direct path


# ---------- signal ----------

def make_chirp():
    n = int(FS * CHIRP_MS / 1000)
    t = np.arange(n) / FS
    k = (F1 - F0) / (CHIRP_MS / 1000)          # linear FM sweep rate
    sig = np.sin(2 * np.pi * (F0 * t + 0.5 * k * t * t))
    sig *= np.hanning(n)                        # taper edges -> less spectral splatter
    return sig.astype(np.float32)


def estimate_range(frame, chirp):
    """Range (m) of the first echo after the direct path, or nan if none."""
    corr = np.abs(np.correlate(frame, chirp, mode="valid"))
    # ponytail: direct path (speaker->mic) is the strongest peak -> our t0.
    # Holds unless a very close hard wall out-reflects it; fine for a room.
    t0 = int(np.argmax(corr))
    peak0 = corr[t0]
    lo = t0 + int(2 * MIN_RANGE / C * FS)
    hi = min(t0 + int(2 * MAX_RANGE / C * FS), len(corr))
    if lo >= hi:
        return np.nan
    seg = corr[lo:hi]
    j = int(np.argmax(seg))
    if seg[j] < NOISE_FLOOR * peak0:
        return np.nan
    lag = (lo + j) - t0                         # round-trip in samples
    return lag / FS * C / 2


def build_emit(seconds):
    chirp = make_chirp()
    flen = int(FS * FRAME_MS / 1000)
    frame = np.zeros(flen, dtype=np.float32)
    frame[:len(chirp)] = chirp
    nframes = int(seconds * 1000 / FRAME_MS)
    return np.tile(frame, nframes), nframes, chirp, flen


# ---------- gyro heading (auto-detected) ----------

def find_gyro():
    """Path of an IIO device exposing a z-axis gyro, or None."""
    hits = glob.glob("/sys/bus/iio/devices/iio:device*/in_anglvel_z_raw")
    return os.path.dirname(hits[0]) if hits else None


def _gyro_scale(gdir):
    for fn in ("in_anglvel_z_scale", "in_anglvel_scale"):
        p = os.path.join(gdir, fn)
        if os.path.exists(p):
            return float(open(p).read())
    return 1.0  # raw already rad/s on some devices


def _sample_gyro(gdir, scale, stop, out):
    """Poll omega_z (rad/s) with a monotonic timestamp until stop is set."""
    raw = os.path.join(gdir, "in_anglvel_z_raw")
    while not stop.is_set():
        try:
            out.append((time.monotonic(), int(open(raw).read()) * scale))
        except OSError:
            pass
        time.sleep(0.005)  # ~200 Hz


def integrate_yaw(samples, t0):
    """(t,omega_z) samples -> (times, yaw_radians) via trapezoid integration."""
    if len(samples) < 2:
        return None
    ts = np.array([s[0] - t0 for s in samples])
    w = np.array([s[1] for s in samples])
    yaw = np.concatenate([[0.0], np.cumsum(0.5 * (w[1:] + w[:-1]) * np.diff(ts))])
    return ts, yaw


# ---------- scan ----------

def scan(seconds, on_status=print):
    import threading
    import sounddevice as sd

    emit, nframes, chirp, flen = build_emit(seconds)
    emit = emit * 0.8

    gdir = find_gyro()
    samples, stop = [], threading.Event()
    if gdir:
        th = threading.Thread(
            target=_sample_gyro, args=(gdir, _gyro_scale(gdir), stop, samples), daemon=True)

    src = "gyro" if gdir else "constant-rate"
    on_status(f"Spin ~360 deg STEADILY over {seconds:.0f}s (heading: {src}). Go!")

    t0 = time.monotonic()
    if gdir:
        th.start()
    rec = sd.playrec(emit, samplerate=FS, channels=1, dtype="float32")
    sd.wait()
    if gdir:
        stop.set()
        th.join(timeout=1)
    rec = rec[:, 0]

    # playrec has a constant input+output latency; find the first direct-path
    # arrival so frame slicing lines up regardless of the device's offset.
    probe = np.abs(np.correlate(rec[:3 * flen], chirp, mode="valid"))
    offset = int(np.argmax(probe))

    yaw = integrate_yaw(samples, t0) if gdir else None
    frame_dt = FRAME_MS / 1000
    angles, ranges = [], []
    for i in range(nframes):
        frame = rec[offset + i * flen: offset + (i + 1) * flen]
        if len(frame) < len(chirp):
            break
        r = estimate_range(frame, chirp)
        if np.isnan(r):
            continue
        if yaw is not None:
            # ponytail: ignores playrec output latency (ms) vs a ~12s spin. Rough by design.
            ang = float(np.interp(i * frame_dt, yaw[0], yaw[1]))
        else:
            # ponytail: constant-rate heading. Uneven spin -> skew. Gyro path above fixes it.
            ang = 2 * np.pi * i / nframes
        angles.append(ang)
        ranges.append(r)

    mode = "gyro" if yaw is not None else "constant-rate"
    return np.array(angles), np.array(ranges), mode


# ---------- render (headless CLI path) ----------

def render(angles, ranges, height, mode="?", show=True):
    import matplotlib.pyplot as plt
    from mpl_toolkits.mplot3d.art3d import Poly3DCollection
    x = ranges * np.cos(angles)
    y = ranges * np.sin(angles)

    fig = plt.figure(figsize=(12, 5))
    fig.suptitle(f"laptop sonar — {len(ranges)} echoes, heading: {mode}")
    ax1 = fig.add_subplot(1, 2, 1)
    ax1.plot(x, y, ".-", ms=3, lw=0.6)
    ax1.plot(0, 0, "r+", ms=12)
    ax1.set_aspect("equal")
    ax1.set_title("floor outline")
    ax1.set_xlabel("m")

    ax2 = fig.add_subplot(1, 2, 2, projection="3d")
    _extrude(ax2, x, y, height)
    ax2.set_title("extruded walls (crude)")

    plt.tight_layout()
    plt.savefig("scan.png", dpi=120)
    print("saved scan.png")
    if show:
        plt.show()


def _extrude(ax, x, y, height):
    """Draw the floor outline extruded up to `height` as a wall band. Shared by CLI + UI."""
    from mpl_toolkits.mplot3d.art3d import Poly3DCollection
    polys = [
        [(x[i], y[i], 0), (x[i + 1], y[i + 1], 0),
         (x[i + 1], y[i + 1], height), (x[i], y[i], height)]
        for i in range(len(x) - 1)
    ]
    ax.add_collection3d(Poly3DCollection(
        polys, alpha=0.4, facecolor="steelblue", edgecolor="k", linewidths=0.2))
    if len(x):
        ax.set_xlim(x.min(), x.max())
        ax.set_ylim(y.min(), y.max())
    ax.set_zlim(0, height)


# ---------- self-test (no hardware) ----------

def selftest():
    chirp = make_chirp()
    flen = int(FS * FRAME_MS / 1000)
    rng = np.random.default_rng(0)
    for true_r in (0.3, 0.5, 1.0, 2.5, 4.0):
        frame = np.zeros(flen, dtype=np.float32)
        frame[:len(chirp)] = chirp                              # direct path at t0
        lag = int(2 * true_r / C * FS)
        frame[lag:lag + len(chirp)] += 0.3 * chirp              # echo
        frame += rng.normal(0, 0.02, flen).astype(np.float32)   # noise
        est = estimate_range(frame, chirp)
        assert abs(est - true_r) < 0.03, f"range {true_r}m -> got {est}"
        print(f"ok  range {true_r}m -> {est:.3f}m")

    # gyro yaw integration: constant 0.5 rad/s for 4s -> 2.0 rad total, linear.
    samples = [(t / 100.0, 0.5) for t in range(401)]  # 100 Hz, 4s
    ts, yaw = integrate_yaw(samples, 0.0)
    assert abs(yaw[-1] - 2.0) < 1e-3, f"yaw end {yaw[-1]}"
    assert abs(float(np.interp(2.0, ts, yaw)) - 1.0) < 1e-3  # halfway -> 1.0 rad
    print(f"ok  yaw integrate -> {yaw[-1]:.3f} rad over 4s (expect 2.000)")
    print("selftest pass")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=12)
    ap.add_argument("--height", type=float, default=2.5, help="assumed room height (m)")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()

    if a.selftest:
        selftest()
        sys.exit()

    angles, ranges, mode = scan(a.seconds)
    if len(ranges) < 3:
        sys.exit("Too few echoes. Turn volume up, quieter room, aim at hard walls.")
    np.save("scan.npy", np.c_[angles, ranges])
    render(angles, ranges, a.height, mode)

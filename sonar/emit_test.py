#!/usr/bin/env python3
"""Emit + reception test for the 17-21kHz sonar chirp.

Not a mapper — just confirms the laptop speaker can EMIT the near-inaudible
chirp and the mic can RECEIVE it. Emits ~1s of chirps, records, and reports the
matched-filter SNR: the recording is cross-correlated with the emitted chirp,
and the peak-vs-median ratio spikes only if that specific 17-21kHz sweep came
back. The matched filter is frequency-selective, so a high SNR *is* evidence the
high-frequency content survived the speaker->mic path (a low-freq-only or noise
capture won't correlate with the chirp).

    python emit_test.py             # real test (emits sound briefly)
    python emit_test.py --selftest  # synthetic, no hardware
"""
import argparse
import sys
import numpy as np
from sonar import FS, F0, F1, make_chirp, build_emit


def snr(rec, chirp):
    corr = np.abs(np.correlate(rec, chirp, mode="valid"))
    return float(corr.max() / (np.median(corr) + 1e-12))


def verdict(value):
    # ponytail: single threshold; noise floors ~1-6, a returned chirp is >>8.
    ok = value > 8
    print(f"matched-filter SNR : {value:6.1f}   (>8 -> chirp emitted + received)")
    print("RESULT:", "PASS — emit + reception of 17-21kHz confirmed"
          if ok else "FAIL — check volume / device")
    return ok


def run(seconds, device):
    import sounddevice as sd
    emit, _, chirp, _ = build_emit(seconds)
    for dev in ([device] if device is not None else [4, 8]):
        try:
            sd.default.device = dev
            print(f"emitting {seconds:.1f}s of {F0 // 1000}-{F1 // 1000}kHz on device {dev}…")
            rec = sd.playrec(emit * 0.8, samplerate=FS, channels=1, dtype="float32")
            sd.wait()
            return snr(rec[:, 0], chirp)
        except Exception as e:
            print(f"device {dev} failed: {e}")
    sys.exit("no working audio device")


def selftest():
    chirp = make_chirp()
    n = FS  # 1s
    rng = np.random.default_rng(0)
    good = rng.normal(0, 0.01, n).astype(np.float32)      # chirps + faint noise
    for s in range(0, n - len(chirp), len(chirp) * 2):
        good[s:s + len(chirp)] += chirp
    assert snr(good, chirp) > 8, "chirp capture should pass"
    bad = rng.normal(0, 0.05, n).astype(np.float32)       # pure noise
    assert snr(bad, chirp) <= 8, "noise should fail"
    print(f"selftest pass  (chirp snr={snr(good, chirp):.0f} | noise snr={snr(bad, chirp):.1f})")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=1.0)
    ap.add_argument("--device", type=int, default=None, help="PortAudio device index (default: try 4 then 8)")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        selftest()
        sys.exit()
    verdict(run(a.seconds, a.device))

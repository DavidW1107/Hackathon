# Sean Sonar

Turn a laptop into an active sonar. The speakers emit near-inaudible
17–21 kHz chirps, the built-in mic listens, and matched filtering plus
coherent background subtraction turns the echoes into:

- **`sonar.py`** — 1D ranging: up to 3 targets (two hands work), mm-level
  distance, signed velocity, and the breathing rate of whoever is sitting
  in front of the machine.
- **`radar.py`** — 2D localization: left/right speakers transmit
  distinguishable chirps, every reflector yields two bistatic ranges
  (two ellipses), and their intersection puts blobs on a top-down ASCII
  radar view.

Both are built on **`echo_core.py`** (shared DSP pipeline) and can run on
**`simulate.py`**'s synthetic scenes with no audio hardware at all.

## Quick start

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python sonar.py            # or radar.py
```

Keep your hands away from the laptop during the ~2 s calibration.
After it, a health line reports the audio path quality, e.g.
`echo floor -52 dB re direct path (excellent)`.

### macOS checklist

- **Mic permission** for your terminal (System Settings → Privacy → Microphone).
- Mic mode **Standard** — Voice Isolation filters out the ultrasonic band.
- Output must go to the **built-in speakers** (not Bluetooth headphones).
- System volume around **60–80%** — the chirps are near-inaudible to adults,
  but kids and pets may hear the 17 kHz end.

## sonar.py

```
15:16:27 |   54.94 cm |    -6.3 mm/s |################..............| snr 37.0 | breath 14.8/min | t2  85.1 cm
```

| flag | what it does |
|---|---|
| `--debug` | append raw levels to each line, for tuning |
| `--waterfall` | range–time echo intensity rows with numbered track markers |
| `--json` | one JSON object per ping on stdout |
| `--udp HOST:PORT` | also stream the JSON messages over UDP |
| `--lock SECS` | lock the screen after SECS with nobody in front |
| `--simulate` | synthetic scene (a waving hand + a breathing person) |

JSON schema, one line per ping:

```json
{"t":1783260990.1,"state":"ok","presence":true,
 "targets":[{"id":1,"r":0.5494,"v":-0.0063,"snr":37.0,"breath":null}]}
```

`r` metres, `v` m/s (positive = moving away), `breath` breaths/min once
~30 s of continuous presence has accumulated. Pipe it anywhere — e.g.
`python sonar.py --json | websocat ws-listen:...` for a browser client,
or point `--udp` at another machine on the LAN.

`--lock` uses `pmset displaysleepnow`; enable "require password
immediately after sleep" for it to actually lock.

## radar.py

Live top-down view by default; `--log` for scrolling text, `--debug` for
per-channel diagnostics, `--json` / `--udp` / `--simulate` as above.
Elevation is ambiguous (planar projection). At startup the log mode
prints the mic offset implied by the two direct-path arrivals — edit
`MIC_X` if it is far off.

## How it works

- **Chirps + matched filter.** Each ping is a Hann-windowed linear chirp;
  correlating against its analytic version gives a complex profile whose
  magnitude is the echo envelope and whose phase carries the carrier.
- **Direct-path referencing.** The mic hears each chirp via the internal
  speaker→mic path first; all echo lags are measured relative to that
  arrival, so audio I/O latency cancels. The reference is slow-tracked
  (a close hand perturbs it) and snaps on genuine volume changes.
- **Coherent background subtraction.** Calibration learns the static
  scene as a complex profile; each ping subtracts it so only moving/new
  reflectors remain. Static residue is recognized by its frozen phase
  (temporal coherence ≈ 1) and re-absorbed; live targets — even someone
  just breathing — keep wandering in phase and are never absorbed.
- **CLEAN.** The strongest echo is fitted (sub-sample position +
  complex amplitude) with a template *measured from the live direct
  path* — i.e. the real speaker+mic response, not a textbook lobe —
  subtracted, and the search repeats. That is what separates two hands.
- **Kalman + phase unwrap** (`sonar.py`). Each target's filter fuses
  coarse envelope range (~mm) with carrier-phase steps (~10 µm, but
  aliased every λ/2 ≈ 9 mm). The predicted velocity picks the phase
  branch, so motion beyond the naive ~9 cm/s alias limit keeps the right
  sign. Track admission has hysteresis (enter 6σ, survive to 4σ).
- **Glitch re-anchoring.** A dropped audio block shifts every arrival by
  the same amount; the pipeline detects the misfit on the strong static
  bins, slides the lock back into alignment (no recalibration), and
  carrier-phase tracking continues seamlessly.

## Simulation and tests

`--simulate` runs the full app on synthetic audio. The test suite drives
the same pipeline through physically faithful scenes — moving targets,
static clutter, dropped samples, volume changes, breathing:

```sh
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m pytest tests
```

Use it when tuning constants: every regression that mattered (velocity
sign under fast motion, phantom echoes after a target leaves, recovery
from audio glitches) is pinned by a test.

## Ideas not yet built

- Startup band sweep: measure the speaker+mic response 16–24 kHz and
  auto-pick the chirp band per machine.
- Complementary (Golay) code pairs instead of up/down chirps for more
  L/R separation and lower sidelobes.
- WebSocket server built in (today: pipe `--json` through `websocat`).

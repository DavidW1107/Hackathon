# Acoustic void — laptop presence sensor

Turn a laptop's speaker + mic into a real-time acoustic motion sensor. It emits
a near-inaudible 17–21 kHz chirp, listens for echoes, detects and **classifies
moving targets** (human / hard object / falling), estimates coarse direction,
and streams detections to a **3D "void" web viewer**: black space, steel-grey
static clutter, glowing matrix-green humans.

Intended product: a fixed sensor station watches a room; on detection it can
text you a link; you open the viewer and see what moved.

## Pieces

| File | Role |
|---|---|
| `sonar.py` | DSP primitives — chirp + matched-filter range. `python sonar.py` self-tests. |
| `emit_test.py` | Confirms the 17–21 kHz chirp emits + is received. `--selftest` for no-hardware. |
| `sensor.py` | The station: emits, detects, classifies, serves detection frames as JSON. |
| `web/` | Static three.js viewer — deploys to Vercel. Renders the void. |

## Run

```bash
pip install -r requirements.txt          # + `sudo apt install libportaudio2` on Ubuntu

python sensor.py --sim                    # synthetic targets, no hardware
python sensor.py                          # live sensing (device 4 = analog mic/speaker)
python sensor.py --sim --record web/sample.json --seconds 15   # regenerate demo data
```

Viewer (local): serve `web/` and open it —

```bash
python -m http.server 8123 --directory web
# open http://localhost:8123/                       -> replays web/sample.json
# open http://localhost:8123/?sensor=http://localhost:8765  -> live from sensor.py
```

## Deploy the viewer to Vercel

`web/` is a static site (three.js via CDN, no build step).

```bash
cd web
vercel            # or: vercel --prod
```

It ships with `sample.json`, so the deployed link shows the void immediately.
Point it at a live station with `?sensor=<url>`. Note: a Vercel (https) page
can't reach a `http://localhost` sensor (mixed content) — for cross-device live,
tunnel the sensor over https (ngrok) or add a relay. Same-machine local dev is fine.

## How it senses (and its honest limits)

- **Range** — matched-filter echo timing. ~cm resolution, to a few metres.
- **Motion** — per window of 8 pulses, static clutter is the cross-pulse *mean*
  (grey), movement is the cross-pulse *std* (a moving echo fluctuates). One
  common `t0` per window keeps static clutter from faking motion.
- **Class** — reflectivity + range-spread + speed:
  - **human/soft** — weak, range-smeared (torso + limbs). *Micro-Doppler + gait is the upgrade.*
  - **hard** — strong, tight, coherent (door, wall).
  - **falling** — fast + compact, accelerating.
- **Direction** — coarse azimuth from the 2-mic time delay. Forward ~±50° cone;
  front/back ambiguous; assumes targets are in front. `MIC_BASELINE` needs
  calibrating per laptop.
- **Height** — *not measured* (mics are on one horizontal line). Inferred by
  class: human = tall column, hard = block, falling = dropping mass. Real z needs
  a vertically-offset mic (roadmap).

Best results: one mover, quiet room, hard walls, ≤ ~4 m, laptop steady.

## Roadmap

- Calibrate `MIC_BASELINE` → trustworthy azimuth.
- Micro-Doppler (proper Doppler across pulses) → better human/hard split + velocity.
- Bistatic elevation via a second node (phone hears the chirp) → true 3D.
- Detection → Twilio SMS with the viewer link.

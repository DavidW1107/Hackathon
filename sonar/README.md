# Laptop sonar — hand-sweep room mapper

Turn a laptop's speaker + mic into a crude acoustic sonar. Emit ultrasonic-ish
chirps, time the echoes for range, spin the laptop through one turn for bearing,
and get a 2D floor outline + a 3D wall extrusion.

## Run

```bash
pip install -r requirements.txt      # + `sudo apt install libportaudio2` on Ubuntu
python sonar.py --selftest           # verifies the DSP math, no hardware
python ui.py                         # graphical: set seconds/height, click Scan, spin
python sonar.py --seconds 12 --height 2.5   # headless, writes scan.png / scan.npy
```

In the UI: set the sweep length + room height, click **Scan**, then spin the
laptop ~360° at a *steady* speed until it stops. Best results: hard walls, quiet
room, volume up.

## How it works

- **Range** — a 5–20 kHz linear FM chirp is emitted every 40 ms. Each echo is
  matched-filtered (cross-correlation) against the chirp; the lag to the first
  peak after the direct speaker→mic path gives round-trip time → distance.
  Range resolution ≈ `c / 2B` ≈ **1 cm**.
- **Bearing** — one speaker + one mic gives range *only*, no direction. So you
  physically sweep the laptop and tag each echo with a heading:
  - **Gyro** (auto-detected via Linux IIO `in_anglvel_z_raw`) → integrated yaw. Good.
  - **No gyro** → constant-rotation-rate assumption. Rough; skews on an uneven spin.
- **Render** — echoes become `(range, angle)` polar points → floor outline →
  extruded to `height` for a crude 3D room shell.

## Honest limits

- A **static** laptop cannot map a room — no bearing. The sweep is the whole trick.
- Soft surfaces (curtains, sofas, people) reflect almost nothing → gaps.
- Room reverb/multipath dominates past a few metres.
- Constant-rate heading is approximate; a gyro is the real fix.

## Does this exist elsewhere?

Yes, in pieces — but not as a single-laptop room scanner:

- **Acoustic rangefinding** (laptop/phone chirp + mic → distance): common demo,
  many hobby repos. The solid, real part.
- **Room shape from echoes**: *"Can one hear the shape of a room?"* (Dokmanić et
  al., PNAS 2013) reconstructs room geometry from impulse-response echoes — but
  needs a **mic array**, not one mic.
- **Walk-and-map on one device**: *BatMapper* (MobiSys 2017) maps indoor spaces
  from a phone's acoustics while walking. **Acoustic SLAM** (Evers & Naylor 2018)
  is the general framework. Closest cousins to this hand-sweep hack.
- **Commercial room-sensing-by-sound**: Sonos Trueplay / Apple HomePod sense
  reflections to auto-tune audio — sensing, not geometry output.
- **Consumer 3D room scanning** went to **LiDAR** (iPhone Pro, Polycam) and
  photogrammetry, not sound.

So: ranging is common, sound-based room *geometry* is a research thing needing
arrays, and a one-static-mic 3D room map doesn't exist because of the bearing
problem this repo works around by sweeping.

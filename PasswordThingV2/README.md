# Keystroke Dynamics Password System (v2)

A password system that verifies **how** you type your password, not just **what**
it is. Even if someone knows your password, their different typing rhythm gets
them rejected. The system captures multiple typing characteristics, builds a
per-user profile, and prints everything to the console in realtime for review.

```
 PASS  [genuine]  within threshold
  anomaly score 0.406 vs threshold 2.200   confidence ███████████████████·  96.7%

 REJECT  [hunt_and_peck]  typing rhythm is outside threshold
  anomaly score 6.397 vs threshold 2.200   confidence ██··················  10.6%
```

## 60-second demo — no keyboard permission needed

The `demo` command runs the **entire idea** end to end using synthetic typists,
so you can see it work before installing anything or granting any permission:

```bash
cd PasswordThingV2
python3 -m keystroke_auth demo
```

It will:

1. **Enroll** a genuine user (types the password 8×, discards warmup attempts).
2. **Verify** that user — streaming every key event in realtime, then a PASS
   with a side-by-side rhythm comparison.
3. Send in a cast of **impostors** who know the password but type differently,
   and show which pass/fail at the default vs a tuned threshold.
4. Break down **why** an impostor fails — the exact gaps that gave them away.
5. Sweep the **threshold dial** to show the false-accept vs false-reject tension.

Try other passwords and seeds:

```bash
python3 -m keystroke_auth demo --password 'Str0ng!'   # exercises Shift on a symbol
python3 -m keystroke_auth demo --password hunter2 --seed 42
python3 -m keystroke_auth demo --delay-ms 18          # pace the event stream like live typing
```

The synthetic attempts flow through the **exact same** feature extractor and
scorer the live tool uses — nothing about the engine is faked.

## What it captures

| Signal | Feature(s) | Why it's distinctive |
| --- | --- | --- |
| **Dwell time** | `dwell.i` (keydown→keyup per key) | How long each key is held — often *more* distinctive than flight time. |
| **Flight time** | `flight_dd.i` (keydown→keydown), `flight_ud.i` (keyup→keydown) | Gaps between keys. `flight_ud` **goes negative on rollover**, revealing fast vs. hunt-and-peck typists. |
| **Total duration** | `total_duration` | First keydown to last keyup. |
| **Shift behavior** | `shift.<side>.{active,overlap,lead,lag}.i` | Left vs. right Shift and overlap timing on capitals/symbols. |
| **Corrections** | policy | Backspace **discards** the attempt and asks for a clean retype, keeping the timing vector honest. |

Minimum password length is **6 keystrokes**. `Hello123` (8 dwells + 7 gaps +
shift metrics = 39 features) is a sweet spot.

## How scoring works

* **Enrollment** — type the password ~5–8 times; store a **mean and standard
  deviation per feature**. The first attempt or two are discarded to avoid the
  "practice effect."
* **Verification** — for each feature compute its z-score,
  `z = (value − mean) / max(stdev, floor)`, then average the absolute z-scores
  into one anomaly score. This is the **scaled Manhattan distance** that won the
  CMU benchmark (Killourhy & Maxion, 2009). It's fully explainable — you can
  print exactly which gap betrayed the impostor.
* **Threshold** — the dial between false accepts (too loose, impostor gets in)
  and false rejects (too strict, real user locked out). A **confidence score**
  (100% = perfect match, 50% at the threshold) is derived from the anomaly score.
* **Adaptive update** (optional, `verify --adaptive`) — fold each successful
  login back into the profile to track natural drift. Two modes:
  * **Cumulative** (default) — every accepted login is weighted equally forever
    (Welford online update). Stable, but the profile stiffens over time: after
    *n* logins a new one only moves the mean by 1/*n*.
  * **Sliding window** (`enroll --window N`) — statistics are recomputed over
    only the **last N accepted attempts**; older ones are forgotten. Each recent
    login keeps a full 1/N weight, so the profile keeps following your current
    rhythm (new keyboard, muscle memory) instead of freezing. The window is
    seeded from your enrollment attempts and stored in the profile.

The profile is stored as JSON next to the script. It holds a **salted PBKDF2
password hash** (never the plaintext) plus the per-feature statistics.

## Live capture (the real thing)

Live capture uses [pynput](https://pynput.readthedocs.io/) for OS-level key
events, so you get the full feature set (dwell, flight, rollover, which physical
Shift) even in a terminal.

```bash
python3 -m pip install -e '.[live]'   # only the live commands need pynput
```

> **macOS:** the first live capture needs **Input Monitoring** permission for
> your terminal/host app (System Settings → Privacy & Security → Input
> Monitoring). Grant it, then restart the app. The listener sees all keys
> system-wide while running.

```bash
# Enroll: type the password naturally 7 times.
kdp enroll --profile profile.json --attempts 7 --discard 1 --threshold 2.2

# Verify one attempt.
kdp verify --profile profile.json

# Demo the trade-off without re-enrolling:
kdp verify --profile profile.json --threshold 1.5    # stricter
kdp verify --profile profile.json --threshold 3.0    # looser

# Let successful logins adapt to natural drift (cumulative):
kdp verify --profile profile.json --adaptive

# Prefer a sliding window? Choose it at enroll time, then verify --adaptive:
kdp enroll --profile profile.json --window 10   # stats track your last 10 attempts
kdp verify --profile profile.json --adaptive

# Inspect the stored statistics:
kdp inspect --profile profile.json
```

(`kdp` is installed as a console script; `python3 -m keystroke_auth` works too.)

### Troubleshooting: "I can only type when another window is focused"

If keystrokes are ignored while the terminal is focused but get captured when you
click into a *different* window, macOS **Secure Keyboard Entry** is blocking the
global event tap for your terminal (it exists to stop keyloggers — including
this one). Two fixes:

* **Keep full features:** turn it off — **Terminal menu → Secure Keyboard Entry**
  (uncheck), or use **iTerm2**, which doesn't enable it by default. Then typing in
  the focused terminal works normally.
* **No settings, no permission:** use `--stdin`, which reads the terminal's own
  input stream instead of a global tap. It's immune to Secure Keyboard Entry and
  needs no Input Monitoring — but a TTY only reports key *presses*, so this mode
  captures **flight timing only (no dwell, no Shift-side)**. Enroll and verify
  must both use it — the profile records its capture mode and `verify` refuses a
  mismatch (otherwise every dwell would read 0 and the score is meaningless):

  ```bash
  kdp enroll --profile me.json --stdin
  kdp verify --profile me.json --stdin
  ```

### Watching the adaptive learning

`verify --adaptive` now prints an **Adaptive learning** report after each accepted
login: this attempt's weight on the profile (`1/N`), the most-moved features with
their `old → new` mean and stdev, whether the attempt re-scores *toward* you after
learning, and (in window mode) which old run aged out. To see it end-to-end
without typing, run `python3 -m keystroke_auth demo` — its final section replays
repeated logins that drift faster over time and shows the profile tracking them.

## Development

```bash
python3 -m unittest discover -s tests -t .
```

The tests exercise feature extraction, scoring, and the simulator — **no
keyboard permission required**.

## Architecture — built to port to a website later

The engine is split so the pure, dependency-free core lifts straight into a
browser/Node port; only capture is platform-specific.

```
keystroke_auth/
  models.py      KeyEvent, Attempt, Stroke          ── shared value types
  features.py    raw events → {feature: ms} vector   ┐
  profile.py     FeatureStat, Profile, hashing       ├─ pure engine, no I/O
  scoring.py     z-scores, anomaly score, confidence ┘  (the website port)
  personas.py    seeded synthetic typists            ── the no-permission demo
  render.py      colours, bars, tables, meters       ── console presentation
  capture.py     live pynput capture                 ── the only platform bit
  cli.py         enroll / verify / inspect / demo
```

In the browser you would swap `capture.py` for DOM `keydown`/`keyup` listeners
producing the same `KeyEvent` stream; `features.py` + `profile.py` +
`scoring.py` port unchanged.

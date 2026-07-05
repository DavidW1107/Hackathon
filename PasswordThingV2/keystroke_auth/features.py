"""Turn a raw :class:`Attempt` into a flat ``{feature_name: milliseconds}`` map.

This is the heart of the system and is intentionally free of any I/O, hashing,
or scoring so it can be lifted verbatim into a browser/Node port later. The
features captured, per the project brief:

* ``dwell.i``       — how long key *i* is held (keydown -> keyup).
* ``flight_dd.i``   — keydown *i* -> keydown *i+1* (always positive).
* ``flight_ud.i``   — keyup *i* -> keydown *i+1* (**can go negative** when the
                      next key goes down before the previous is released — the
                      rollover signature of a fast typist).
* ``total_duration``— first keydown -> last keyup.
* ``shift.<side>.*``— per-character Shift behaviour: which side, overlap, lead
                      (shift-before-key) and lag (shift-held-after-key).

Dwell times are frequently *more* discriminative than flight times, which is
why every key contributes one.
"""

from __future__ import annotations

from .models import Attempt, KeyEvent, Stroke

# Per-feature minimum standard deviation (the "floor"), in the feature's own
# unit. A tiny measured stdev would make a z-score explode on sub-millisecond
# noise; the floor keeps scoring honest. Tuned to typical human jitter.
_SHIFT_ACTIVE_FLOOR = 0.05  # active is a 0/1 flag
_SHIFT_TIME_FLOOR = 10.0  # ms
_DURATION_FLOOR = 80.0  # ms
_DEFAULT_TIME_FLOOR = 12.0  # ms

_SHIFT_METRIC_ORDER = {"active": 0, "overlap": 1, "lead": 2, "lag": 3}
_SHIFT_SIDE_ORDER = {"left": 0, "right": 1}


def seconds_to_ms(value: float) -> float:
    return value * 1000.0


def extract_features(attempt: Attempt) -> dict[str, float]:
    """Compute the full feature vector for one attempt.

    Raises:
        ValueError: if the number of timed strokes does not match the typed
            text length (a corrupt capture we refuse to score).
    """
    strokes, shift_intervals = _strokes_and_shift_intervals(attempt.events)
    if len(strokes) != len(attempt.text):
        raise ValueError(
            f"captured {len(strokes)} timed strokes but the text has "
            f"{len(attempt.text)} characters"
        )

    features: dict[str, float] = {}
    if not strokes:
        return features

    first_down = strokes[0].down_t
    last_up = max(stroke.up_t for stroke in strokes)
    features["total_duration"] = seconds_to_ms(last_up - first_down)

    for stroke in strokes:
        features[f"dwell.{stroke.index}"] = seconds_to_ms(stroke.dwell)

    for i in range(len(strokes) - 1):
        cur, nxt = strokes[i], strokes[i + 1]
        features[f"flight_dd.{i}"] = seconds_to_ms(nxt.down_t - cur.down_t)
        features[f"flight_ud.{i}"] = seconds_to_ms(nxt.down_t - cur.up_t)

    for stroke in strokes:
        left = _shift_metrics(shift_intervals["left"], stroke)
        right = _shift_metrics(shift_intervals["right"], stroke)
        # Only emit shift features for characters that actually involved a
        # Shift, so lowercase passwords don't carry dead all-zero columns.
        if any(m["active"] or m["overlap"] for m in (left, right)):
            for side, metrics in (("left", left), ("right", right)):
                features[f"shift.{side}.active.{stroke.index}"] = metrics["active"]
                features[f"shift.{side}.overlap.{stroke.index}"] = metrics["overlap"]
                features[f"shift.{side}.lead.{stroke.index}"] = metrics["lead"]
                features[f"shift.{side}.lag.{stroke.index}"] = metrics["lag"]

    return features


def timing_feature_order(password_length: int) -> list[str]:
    """The canonical order for the always-present timing features."""
    order = ["total_duration"]
    order.extend(f"dwell.{i}" for i in range(password_length))
    for i in range(password_length - 1):
        order.append(f"flight_dd.{i}")
        order.append(f"flight_ud.{i}")
    return order


def feature_sort_key(name: str) -> tuple[int, int, int, int]:
    """Sort key that groups timing features by position, shift features last."""
    if name == "total_duration":
        return (0, -1, 0, 0)
    parts = name.split(".")
    kind = parts[0]
    if kind == "dwell":
        return (1, int(parts[1]), 0, 0)
    if kind == "flight_dd":
        return (2, int(parts[1]), 0, 0)
    if kind == "flight_ud":
        return (3, int(parts[1]), 0, 0)
    if kind == "shift":
        side, metric, index = parts[1], parts[2], int(parts[3])
        return (4, index, _SHIFT_METRIC_ORDER.get(metric, 9), _SHIFT_SIDE_ORDER.get(side, 9))
    return (9, 0, 0, 0)


def feature_floor(name: str) -> float:
    """Minimum stdev used when computing a z-score for this feature."""
    if name == "total_duration":
        return _DURATION_FLOOR
    if name.startswith("shift.") and ".active." in name:
        return _SHIFT_ACTIVE_FLOOR
    if name.startswith("shift."):
        return _SHIFT_TIME_FLOOR
    return _DEFAULT_TIME_FLOOR


def feature_unit(name: str) -> str:
    if name.startswith("shift.") and ".active." in name:
        return ""
    return "ms"


def feature_label(name: str, text: str | None = None) -> str:
    """Human-readable label, e.g. ``keydown->keydown 0->1 'h'->'e'``."""
    if name == "total_duration":
        return "total duration"
    parts = name.split(".")
    kind = parts[0]
    if kind == "dwell":
        i = int(parts[1])
        return f"dwell {i} {_char_label(text, i)}"
    if kind == "flight_dd":
        i = int(parts[1])
        return f"keydown->keydown {i}->{i + 1} {_gap_label(text, i)}"
    if kind == "flight_ud":
        i = int(parts[1])
        return f"keyup->keydown {i}->{i + 1} {_gap_label(text, i)}"
    if kind == "shift":
        side, metric, i = parts[1], parts[2], int(parts[3])
        return f"{side} shift {metric} {i} {_char_label(text, i)}"
    return name


# --------------------------------------------------------------------------- #
# Internal helpers
# --------------------------------------------------------------------------- #


def _strokes_and_shift_intervals(
    events: list[KeyEvent],
) -> tuple[list[Stroke], dict[str, list[tuple[float, float]]]]:
    """Pair down/up events into strokes and collect Shift hold intervals.

    Character keydowns become strokes in press order; their matching keyup is
    the next release of the same key id. Shift presses/releases are folded into
    per-side ``(start, end)`` intervals, closing any still-held Shift at the end
    of the attempt.
    """
    ordered = sorted(events, key=lambda e: e.t)
    strokes: list[Stroke] = []
    pending_by_key: dict[str, list[int]] = {}
    open_shifts: dict[str, list[float]] = {"left": [], "right": []}
    shift_intervals: dict[str, list[tuple[float, float]]] = {"left": [], "right": []}

    for event in ordered:
        if event.is_shift and event.shift_side in open_shifts:
            side = event.shift_side
            if event.kind == "down":
                open_shifts[side].append(event.t)
            elif event.kind == "up" and open_shifts[side]:
                start = open_shifts[side].pop()
                shift_intervals[side].append((start, max(event.t, start)))
            continue

        if event.kind == "down" and event.char is not None:
            index = len(strokes)
            strokes.append(
                Stroke(index=index, char=event.char, key=event.key, down_t=event.t, up_t=event.t)
            )
            pending_by_key.setdefault(event.key, []).append(index)
        elif event.kind == "up":
            pending = pending_by_key.get(event.key)
            if pending:
                idx = pending.pop(0)
                s = strokes[idx]
                strokes[idx] = Stroke(
                    index=s.index,
                    char=s.char,
                    key=s.key,
                    down_t=s.down_t,
                    up_t=max(event.t, s.down_t),
                )

    end_t = ordered[-1].t if ordered else 0.0
    for side, starts in open_shifts.items():
        for start in starts:
            shift_intervals[side].append((start, max(end_t, start)))

    return strokes, shift_intervals


def _shift_metrics(intervals: list[tuple[float, float]], stroke: Stroke) -> dict[str, float]:
    """How a single Shift side related to one character stroke.

    ``active`` is 1.0 when a Shift on this side was held at the moment the key
    went down (i.e. this key was actually capitalised with this Shift). We also
    report total overlap, and — for the active interval — the lead (how early
    Shift went down before the key) and lag (how long Shift stayed down after).
    """
    active_interval: tuple[float, float] | None = None
    total_overlap = 0.0

    for start, end in intervals:
        overlap = max(0.0, min(end, stroke.up_t) - max(start, stroke.down_t))
        total_overlap += overlap
        if start <= stroke.down_t <= end:
            active_interval = (start, end)

    if active_interval is None:
        return {"active": 0.0, "overlap": seconds_to_ms(total_overlap), "lead": 0.0, "lag": 0.0}

    start, end = active_interval
    return {
        "active": 1.0,
        "overlap": seconds_to_ms(total_overlap),
        "lead": seconds_to_ms(stroke.down_t - start),
        "lag": seconds_to_ms(end - stroke.up_t),
    }


def _char_label(text: str | None, index: int) -> str:
    if text is None or index >= len(text):
        return f"#{index}"
    char = text[index]
    if char == " ":
        return "'space'"
    if char == "\t":
        return "'tab'"
    return repr(char)


def _gap_label(text: str | None, index: int) -> str:
    if text is None or index + 1 >= len(text):
        return ""
    return f"{_char_label(text, index)}->{_char_label(text, index + 1)}"

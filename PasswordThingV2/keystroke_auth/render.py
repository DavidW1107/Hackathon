"""Everything the user sees in the terminal: colours, bars, tables, meters.

Kept apart from the engine so scoring stays pure and presentation stays swappable
(the website will render the same numbers very differently). Colour is opt-out
via ``NO_COLOR`` and auto-disabled when stdout is not a TTY.
"""

from __future__ import annotations

import os
import sys

from .features import feature_label, feature_unit
from .models import Attempt
from .profile import Profile
from .scoring import ScoreRow, VerificationResult

_RESET = "\033[0m"
_CODES = {
    "bold": "1",
    "dim": "2",
    "red": "31",
    "green": "32",
    "yellow": "33",
    "blue": "34",
    "magenta": "35",
    "cyan": "36",
    "grey": "90",
}

_FILLED = "█"
_EMPTY = "·"


def color_enabled(stream=None) -> bool:
    stream = stream or sys.stdout
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("FORCE_COLOR"):
        return True
    return bool(getattr(stream, "isatty", lambda: False)())


class Ink:
    """Tiny colour helper; ``enabled=False`` makes every call a no-op."""

    def __init__(self, enabled: bool) -> None:
        self.enabled = enabled

    def __call__(self, text: str, *styles: str) -> str:
        if not self.enabled or not styles:
            return text
        codes = ";".join(_CODES[s] for s in styles if s in _CODES)
        return f"\033[{codes}m{text}{_RESET}" if codes else text


# --------------------------------------------------------------------------- #
# Value formatting
# --------------------------------------------------------------------------- #


def format_value(name: str, value: float) -> str:
    if feature_unit(name) == "ms":
        return f"{value:.1f}ms"
    return f"{value:.0f}"


# --------------------------------------------------------------------------- #
# Realtime event stream
# --------------------------------------------------------------------------- #


def render_event_line(
    t_ms: float,
    kind: str,
    label: str,
    char: str | None,
    active_shifts: set[str],
    text: str,
    ink: Ink,
) -> str:
    shift_text = ",".join(sorted(active_shifts)) if active_shifts else "-"
    char_text = _display_char(char) if char is not None else "-"
    kind_styled = ink(f"{kind:<4}", "green" if kind == "DOWN" else "grey")
    ts = ink(f"+{t_ms:8.1f}ms", "grey")
    return (
        f"{ts} {kind_styled} "
        f"key={ink(f'{label:<12}', 'cyan')} char={char_text:<8} "
        f"shift={shift_text:<11} text={ink(repr(text), 'bold')}"
    )


def iter_event_lines(attempt: Attempt, ink: Ink):
    """Replay an attempt's events as the lines a live capture would have printed.

    Lets the ``demo`` command stream a *synthesized* attempt exactly the way the
    real keyboard listener streams a typed one — same format, same colours.
    """
    active_shifts: set[str] = set()
    text_chars: list[str] = []
    for event in sorted(attempt.events, key=lambda e: e.t):
        kind = "DOWN" if event.kind == "down" else "UP"
        if event.is_shift and event.shift_side:
            if event.kind == "down":
                active_shifts.add(event.shift_side)
            label = "shift_l" if event.shift_side == "left" else "shift_r"
            char = None
        else:
            base = event.key.split(":", 1)[-1]
            label = _display_char(base)
            char = event.char
        if event.kind == "down" and char is not None:
            text_chars.append(char)
        yield render_event_line(
            event.t * 1000.0, kind, label, char, active_shifts, "".join(text_chars), ink
        )
        if event.is_shift and event.shift_side and event.kind == "up":
            active_shifts.discard(event.shift_side)


# --------------------------------------------------------------------------- #
# Verdict + confidence meter
# --------------------------------------------------------------------------- #


def render_verdict(result: VerificationResult, ink: Ink, *, label: str | None = None) -> list[str]:
    lines: list[str] = []
    who = f" [{label}]" if label else ""
    if result.passed:
        badge = ink(" PASS ", "bold", "green")
    else:
        badge = ink(" REJECT ", "bold", "red")
    lines.append(f"{badge}{who}  {result.reason}")

    if result.anomaly_score == float("inf"):
        lines.append(ink("  anomaly score: not computed (failed pre-check)", "grey"))
        return lines

    verdict_color = "green" if result.passed else "red"
    lines.append(
        f"  anomaly score {ink(f'{result.anomaly_score:.3f}', 'bold', verdict_color)} "
        f"vs threshold {result.threshold:.3f}   "
        f"{render_confidence_meter(result.confidence, ink)}"
    )
    return lines


def render_confidence_meter(confidence: float, ink: Ink, width: int = 20) -> str:
    filled = int(round(confidence / 100.0 * width))
    style = "green" if confidence >= 50 else "yellow" if confidence >= 25 else "red"
    bar = ink(_FILLED * filled, style) + ink(_EMPTY * (width - filled), "grey")
    return f"confidence {bar} {confidence:5.1f}%"


# --------------------------------------------------------------------------- #
# Side-by-side rhythm comparison
# --------------------------------------------------------------------------- #


def render_comparison_table(rows: list[ScoreRow], text: str, ink: Ink) -> list[str]:
    header = f"{'feature':32} {'profile (mean±sd)':>20} {'attempt':>11} {'z':>7}  profile / attempt"
    lines = [ink(header, "bold"), ink("─" * len(header), "grey")]
    for row in rows:
        flag, flag_style = _z_flag(row.abs_z)
        profile_text = f"{format_value(row.name, row.mean)}±{format_value(row.name, row.stdev)}"
        z_text = ink(f"{row.z:>+6.2f}{flag}", flag_style)
        lines.append(
            f"{feature_label(row.name, text):32.32} "
            f"{profile_text:>20.20} "
            f"{format_value(row.name, row.value):>11} "
            f"{z_text} "
            f"{_dual_bar(row.mean, row.value, ink, row.abs_z)}"
        )
    return lines


def render_top_suspicious(
    rows: list[ScoreRow], text: str, ink: Ink, *, top: int
) -> list[str]:
    ranked = sorted(rows, key=lambda r: r.abs_z, reverse=True)[:top]
    lines = [ink(f"Most suspicious features (top {len(ranked)}) — the gaps that gave it away:", "bold")]
    for row in ranked:
        _flag, style = _z_flag(row.abs_z)
        lines.append(
            f"  {feature_label(row.name, text):34.34} "
            f"z={ink(f'{row.z:+6.2f}', style)}  "
            f"attempt {format_value(row.name, row.value):>9}  "
            f"profile {format_value(row.name, row.mean):>9}"
        )
    return lines


def render_adaptive_report(
    result: VerificationResult,
    profile: Profile,
    after_score: float,
    dropped: dict[str, float] | None,
    ink: Ink,
    *,
    text: str,
    top: int = 6,
) -> list[str]:
    """Show how folding this attempt in reshaped the profile.

    ``result`` is the *pre-update* comparison (its rows carry the old mean/stdev
    and this attempt's values); ``profile`` is already updated; ``after_score``
    is this same attempt re-scored against the new profile; ``dropped`` is the
    window sample that aged out (sliding-window mode only).
    """
    lines: list[str] = []
    order = profile.feature_order

    if profile.window_size:
        held = len(profile.samples)
        mode = f"sliding window · now holding the last {held}/{profile.window_size} attempts"
        weight = f"1/{profile.window_size} = {100.0 / profile.window_size:.0f}%"
    else:
        n = profile.stats[order[0]].n if order else 0
        mode = f"cumulative · {n} attempts averaged so far"
        weight = f"1/{n} = {100.0 / n:.0f}%" if n else "n/a"

    lines.append(ink(f"Adaptive learning  [{mode}]", "bold", "magenta"))
    lines.append(f"  weight of THIS login on the profile: {ink(weight, 'bold')}   "
                 f"(bigger = adapts faster; a smaller window keeps this high)")

    deltas = []
    for row in result.rows:
        after = profile.stats.get(row.name)
        if after is not None:
            deltas.append((abs(after.mean - row.mean), row, after))
    deltas.sort(key=lambda d: d[0], reverse=True)
    shown = [d for d in deltas if d[0] > 1e-9][:top]
    if shown:
        lines.append(ink("  most-moved features   (old profile → new profile):", "grey"))
        for _, row, after in shown:
            dmean = after.mean - row.mean
            arrow, style = ("▲", "yellow") if dmean > 0 else ("▼", "cyan") if dmean < 0 else ("=", "grey")
            delta = ink(f"{arrow}{format_value(row.name, abs(dmean))}", style)
            lines.append(
                f"    {feature_label(row.name, text):28.28}  "
                f"this run {format_value(row.name, row.value):>9}   "
                f"mean {format_value(row.name, row.mean):>8} → {format_value(row.name, after.mean):>8} {delta}   "
                f"sd {format_value(row.name, row.stdev):>7} → {format_value(row.name, after.stdev):>7}"
            )

    before = result.anomaly_score
    if after_score < before - 1e-9:
        trend = ink(f"{before:.3f} → {after_score:.3f}   moved TOWARD you ↓", "green")
    elif after_score > before + 1e-9:
        trend = ink(f"{before:.3f} → {after_score:.3f}   moved away ↑", "yellow")
    else:
        trend = f"{before:.3f} → {after_score:.3f}   unchanged"
    lines.append(f"  this attempt re-scored after learning: {trend}")

    if dropped:
        td = dropped.get("total_duration", 0.0)
        lines.append(ink(f"  ⤺ oldest run aged out of the window (its total duration was "
                         f"{format_value('total_duration', td)})", "grey"))
    return lines


def render_profile_table(profile: Profile, ink: Ink) -> list[str]:
    header = f"{'feature':34} {'mean':>12} {'stdev':>12} {'n':>5}"
    lines = [ink(header, "bold"), ink("─" * len(header), "grey")]
    for name in profile.feature_order:
        stat = profile.stats[name]
        lines.append(
            f"{feature_label(name):34.34} "
            f"{format_value(name, stat.mean):>12} "
            f"{format_value(name, stat.stdev):>12} "
            f"{stat.n:>5}"
        )
    return lines


# --------------------------------------------------------------------------- #
# Internals
# --------------------------------------------------------------------------- #


def _z_flag(abs_z: float) -> tuple[str, str]:
    if abs_z >= 3.0:
        return "!!", "red"
    if abs_z >= 2.0:
        return " !", "yellow"
    return "  ", "green"


def _dual_bar(mean: float, value: float, ink: Ink, abs_z: float, width: int = 12) -> str:
    scale = max(abs(mean), abs(value), 1.0)
    attempt_style = "green" if abs_z < 2.0 else "yellow" if abs_z < 3.0 else "red"
    prof = ink(_signed_bar(mean, scale, width), "grey")
    att = ink(_signed_bar(value, scale, width), attempt_style)
    return f"{prof} {att}"


def _signed_bar(value: float, scale: float, width: int) -> str:
    filled = min(width, int(round(abs(value) / scale * width)))
    body = (_FILLED * filled).ljust(width, _EMPTY)
    return ("-" if value < 0 else " ") + body


def _display_char(char: str) -> str:
    if char == " ":
        return "space"
    if char == "\t":
        return "tab"
    return repr(char)

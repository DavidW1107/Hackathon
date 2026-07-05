"""Command-line entry point: ``kdp <enroll|verify|inspect|demo>``.

``demo`` is fully self-contained (simulated typists, no keyboard permission),
which makes it the fastest way to see the whole idea work. ``enroll`` and
``verify`` use the live keyboard for the real thing.
"""

from __future__ import annotations

import argparse
import time
from dataclasses import replace
from pathlib import Path

from .capture import capture_attempt, capture_attempt_stdin
from .personas import impostor_cast, make_genuine, needs_shift, synthesize_attempts
from .profile import (
    DEFAULT_THRESHOLD,
    MIN_PASSWORD_LENGTH,
    Profile,
    build_profile,
    load_profile,
    save_profile,
    update_profile_with_attempt,
)
from .render import (
    Ink,
    color_enabled,
    format_value,
    iter_event_lines,
    render_adaptive_report,
    render_comparison_table,
    render_confidence_meter,
    render_profile_table,
    render_top_suspicious,
    render_verdict,
)
from .scoring import compare_attempt

_SWEEP_THRESHOLDS = [0.8, 1.0, 1.2, 1.5, 2.0, 2.5, 3.0]


def main(argv: list[str] | None = None) -> int:
    # A shared parent so --color works both before and after the subcommand.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--color", choices=["auto", "always", "never"], default="auto", help="colour output"
    )

    parser = argparse.ArgumentParser(
        prog="kdp", description="Keystroke dynamics password system.", parents=[common]
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_enroll = sub.add_parser("enroll", parents=[common],
                              help="capture enrollment attempts (live keyboard)")
    p_enroll.add_argument("--profile", default="profile.json")
    p_enroll.add_argument("--attempts", type=int, default=7, help="clean attempts to keep")
    p_enroll.add_argument("--discard", type=int, default=1, help="warmup attempts to discard")
    p_enroll.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD)
    p_enroll.add_argument("--min-length", type=int, default=MIN_PASSWORD_LENGTH)
    p_enroll.add_argument(
        "--window",
        type=int,
        default=0,
        help="adaptive sliding window: keep stats over the last N accepted attempts "
        "(0 = cumulative, the default)",
    )
    p_enroll.add_argument(
        "--stdin",
        action="store_true",
        help="read this terminal directly instead of the global keyboard tap "
        "(no permission, immune to Secure Keyboard Entry; flight-only, no dwell/shift)",
    )
    p_enroll.add_argument("--quiet-events", action="store_true", help="hide the raw event stream")
    p_enroll.set_defaults(func=run_enroll)

    p_verify = sub.add_parser("verify", parents=[common], help="verify one attempt (live keyboard)")
    p_verify.add_argument("--profile", default="profile.json")
    p_verify.add_argument("--threshold", type=float, default=None, help="override profile threshold")
    p_verify.add_argument("--adaptive", action="store_true", help="fold a pass back into the profile")
    p_verify.add_argument("--quiet-events", action="store_true")
    p_verify.add_argument("--top", type=int, default=8, help="suspicious features to list")
    p_verify.add_argument(
        "--stdin",
        action="store_true",
        help="read this terminal directly (see `enroll --stdin`); enroll and verify must match",
    )
    p_verify.set_defaults(func=run_verify)

    p_inspect = sub.add_parser("inspect", parents=[common], help="print a profile's statistics")
    p_inspect.add_argument("--profile", default="profile.json")
    p_inspect.set_defaults(func=run_inspect)

    p_demo = sub.add_parser("demo", parents=[common],
                            help="self-contained simulated demo (no keyboard needed)")
    p_demo.add_argument("--password", default="Hello123", help="password the cast will type")
    p_demo.add_argument("--attempts", type=int, default=8, help="enrollment attempts")
    p_demo.add_argument("--discard", type=int, default=2, help="warmup attempts discarded")
    p_demo.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD)
    p_demo.add_argument("--seed", type=int, default=7, help="genuine-user fingerprint seed")
    p_demo.add_argument("--top", type=int, default=8)
    p_demo.add_argument("--detail", default=None, help="impostor to break down (default: auto)")
    p_demo.add_argument("--delay-ms", type=float, default=0.0, help="pace the event stream")
    p_demo.add_argument("--hide-events", action="store_true", help="skip the realtime event stream")
    p_demo.set_defaults(func=run_demo)

    args = parser.parse_args(argv)
    ink = Ink(_resolve_color(args.color))
    try:
        return args.func(args, ink)
    except KeyboardInterrupt:
        print("\nCancelled.")
        return 130
    except (OSError, RuntimeError, ValueError) as exc:
        print(ink(f"Error: {exc}", "red"))
        return 1


def _resolve_color(mode: str) -> bool:
    if mode == "always":
        return True
    if mode == "never":
        return False
    return color_enabled()


# --------------------------------------------------------------------------- #
# Live commands
# --------------------------------------------------------------------------- #


def run_enroll(args: argparse.Namespace, ink: Ink) -> int:
    if args.discard >= args.attempts:
        print(ink("--discard must be smaller than --attempts", "red"))
        return 2

    print(ink("Enrollment stores a salted password hash, not the plaintext password.", "grey"))
    _print_capture_hint(args.stdin, ink)
    capture = capture_attempt_stdin if args.stdin else capture_attempt

    attempts: list = []
    password: str | None = None
    while len(attempts) < args.attempts:
        n = len(attempts) + 1
        attempt = capture(
            ink(f"\nEnrollment attempt {n}/{args.attempts}: type the password naturally.", "bold"),
            ink,
            realtime=not args.quiet_events,
        )
        if attempt.aborted:
            print("Enrollment cancelled.")
            return 130
        if attempt.corrected:
            print(ink("Attempt discarded. Retype cleanly without Backspace.", "yellow"))
            continue
        if password is None:
            if len(attempt.text) < args.min_length:
                print(ink(f"Password is {len(attempt.text)} keystrokes; use at least "
                          f"{args.min_length}.", "yellow"))
                continue
            password = attempt.text
            print(ink(f"Password text captured: {len(password)} keystrokes.", "cyan"))
        elif attempt.text != password:
            print(ink("Attempt discarded: text did not match the first attempt.", "yellow"))
            continue
        attempts.append(attempt)
        _print_capture_summary(attempt, ink)

    profile = build_profile(
        attempts,
        discard=args.discard,
        threshold=args.threshold,
        min_length=args.min_length,
        window_size=args.window,
        capture_mode=_mode_of(args.stdin),
    )
    save_profile(profile, args.profile)
    print()
    print(ink(f"Saved profile to {Path(args.profile).resolve()}", "green"))
    print(f"Used {profile.enrollment_attempts} attempts after discarding "
          f"{profile.discarded_attempts} warmup attempt(s).")
    print(f"Feature count: {len(profile.feature_order)} · threshold: {profile.threshold:.2f}")
    print(f"Capture mode: {_mode_label(profile.capture_mode)} "
          f"— verify must use the same mode{' (--stdin)' if args.stdin else ''}.")
    print(f"Adaptive mode: {_adaptive_mode(profile)} "
          f"(applies when you run `kdp verify --adaptive`).")
    return 0


def run_verify(args: argparse.Namespace, ink: Ink) -> int:
    profile = load_profile(args.profile)
    active = args.threshold if args.threshold is not None else profile.threshold
    print(ink(f"Loaded profile from {Path(args.profile).resolve()}", "grey"))
    print(f"Threshold: {active:.2f}")

    attempt_mode = _mode_of(args.stdin)
    if attempt_mode != profile.capture_mode:
        want_stdin = profile.capture_mode == "flight_only"
        fix = ("add --stdin" if want_stdin else "drop --stdin (and if you can't type in the "
               "terminal, turn off Terminal ▸ Secure Keyboard Entry instead)")
        print(ink(
            f"Capture-mode mismatch: this profile was enrolled as "
            f"{_mode_label(profile.capture_mode)}, but you're verifying as "
            f"{_mode_label(attempt_mode)}.\nThose capture different features "
            f"(stdin has no dwell or Shift), so every dwell would read 0 and the score "
            f"would be meaningless.\nFix: {fix} — or re-enroll in this mode.", "red"))
        return 2

    _print_capture_hint(args.stdin, ink)
    capture = capture_attempt_stdin if args.stdin else capture_attempt

    attempt = capture(
        ink("\nVerification: type the enrolled password naturally.", "bold"),
        ink,
        realtime=not args.quiet_events,
    )
    if attempt.aborted:
        print("Verification cancelled.")
        return 130

    result = compare_attempt(profile, attempt, threshold=args.threshold)
    print()
    _print_capture_summary(attempt, ink)
    for line in render_verdict(result, ink):
        print(line)
    if result.rows:
        print()
        print(ink("Side-by-side rhythm comparison (profile vs this attempt):", "bold"))
        for line in render_comparison_table(result.rows, attempt.text, ink):
            print(line)
        print()
        for line in render_top_suspicious(result.rows, attempt.text, ink, top=args.top):
            print(line)

    if result.passed and args.adaptive:
        before_samples = list(profile.samples)
        update_profile_with_attempt(profile, attempt)
        save_profile(profile, args.profile)
        after_score = compare_attempt(profile, attempt).anomaly_score
        dropped = (
            before_samples[0]
            if profile.window_size and len(before_samples) >= profile.window_size
            else None
        )
        print()
        _rule(ink, "Adaptive learning")
        for line in render_adaptive_report(result, profile, after_score, dropped, ink, text=attempt.text):
            print(line)
        print(ink(f"  saved to {Path(args.profile).resolve()} · "
                  f"total adaptive updates: {profile.adaptive_updates}", "grey"))
    elif args.adaptive and not result.passed:
        print(ink("Not folded into the profile — an attempt must pass to be learned from.", "grey"))
    return 0 if result.passed else 1


def run_inspect(args: argparse.Namespace, ink: Ink) -> int:
    profile = load_profile(args.profile)
    print(ink(f"Profile: {Path(args.profile).resolve()}", "bold"))
    print(f"Created: {profile.created_at}")
    print(f"Password length: {profile.password_length} keystrokes")
    print(f"Capture mode: {_mode_label(profile.capture_mode)}")
    print(f"Threshold: {profile.threshold:.2f}")
    print(f"Enrollment attempts used: {profile.enrollment_attempts} "
          f"(discarded {profile.discarded_attempts} warmup)")
    print(f"Adaptive mode: {_adaptive_mode(profile)}")
    print(f"Adaptive updates applied: {profile.adaptive_updates}")
    print(f"Feature count: {len(profile.feature_order)}")
    print()
    for line in render_profile_table(profile, ink):
        print(line)
    return 0


# --------------------------------------------------------------------------- #
# Self-contained simulated demo
# --------------------------------------------------------------------------- #


def run_demo(args: argparse.Namespace, ink: Ink) -> int:
    password = args.password
    if len(password) < MIN_PASSWORD_LENGTH:
        print(ink(f"Demo password must be at least {MIN_PASSWORD_LENGTH} keystrokes.", "red"))
        return 2

    _rule(ink, "KEYSTROKE DYNAMICS  ·  simulated demo")
    print("Verifies HOW you type your password, not just WHAT it is.")
    print(ink("Every typist below is synthetic — no keyboard or permissions needed. The same "
              "events\nflow through the same feature extractor and scorer the live tool uses.", "grey"))
    print(f"\nPassword under test: {ink(repr(password), 'bold')}  "
          f"({len(password)} keystrokes)")

    # 1) Enroll the genuine user.
    genuine = make_genuine(args.seed, shift_side="left")
    enroll_attempts = synthesize_attempts(password, genuine, args.attempts, seed=1)
    profile = build_profile(
        enroll_attempts, discard=args.discard, threshold=args.threshold, label="genuine"
    )
    print()
    _rule(ink, "1 · ENROLL the genuine user")
    print(f"Typed the password {ink(str(args.attempts), 'bold')} times, discarded "
          f"{args.discard} warmup attempt(s) (the practice effect).")
    print(f"Built a profile of {ink(str(len(profile.feature_order)), 'bold')} timing features "
          f"(dwell, flight, rollover, shift), each with a mean ± stdev.")

    # 2) Genuine verification — stream the events, then score.
    genuine_attempt = synthesize_attempts(password, genuine, 1, seed=900)[0]
    print()
    _rule(ink, "2 · VERIFY the genuine user")
    if not args.hide_events:
        print(ink("realtime capture:", "grey"))
        _play_events(genuine_attempt, ink, args.delay_ms)
        print()
    genuine_result = compare_attempt(profile, genuine_attempt)
    for line in render_verdict(genuine_result, ink, label="genuine"):
        print(line)
    print()
    print(ink("Side-by-side rhythm comparison (profile vs this attempt):", "bold"))
    for line in render_comparison_table(genuine_result.rows, password, ink):
        print(line)

    # 3) Score the impostor cast + genuine spread, and derive a tuned threshold.
    cast = impostor_cast(args.seed)
    if not any(needs_shift(c) for c in password):
        cast.pop("wrong_shift", None)  # nothing for the wrong-Shift tell to catch
    genuine_scores = [
        compare_attempt(profile, a).anomaly_score
        for a in synthesize_attempts(password, genuine, 5, seed=500)
    ]
    impostor_results = {
        name: compare_attempt(profile, synthesize_attempts(password, persona, 1, seed=500)[0])
        for name, persona in cast.items()
    }
    impostor_scores = [r.anomaly_score for r in impostor_results.values()]
    tuned = _recommend_threshold(genuine_scores, impostor_scores)

    print()
    _rule(ink, "3 · Send in the IMPOSTORS (same password, different rhythm)")
    _print_roster(genuine_scores, impostor_results, args.threshold, tuned, ink)

    # 4) Break down the most instructive impostor.
    detail_name = args.detail or _pick_detail(impostor_results, args.threshold)
    if detail_name in cast:
        detail_attempt = synthesize_attempts(password, cast[detail_name], 1, seed=500)[0]
        detail_result = compare_attempt(profile, detail_attempt)
        print()
        _rule(ink, f"4 · WHY '{detail_name}' fails")
        for line in render_verdict(detail_result, ink, label=detail_name):
            print(line)
        print()
        for line in render_top_suspicious(detail_result.rows, password, ink, top=args.top):
            print(line)

    # 5) The core tension: the threshold dial.
    print()
    _rule(ink, "5 · The THRESHOLD dial  (false accepts  vs  false rejects)")
    _print_sweep(genuine_scores, impostor_scores, ink)
    print()
    print(f"Recommended threshold for this profile: {ink(f'{tuned:.2f}', 'bold', 'green')} "
          f"— accepts every genuine attempt (max {max(genuine_scores):.2f}) while rejecting "
          f"every impostor (min {min(impostor_scores):.2f}).")
    print(ink("Loosen it and impostors slip in; tighten it and the real user gets locked out "
              "when tired\nor on a new keyboard. That dial is the whole game.", "grey"))

    # 6) Watch the profile adapt to drift over repeated logins.
    print()
    _rule(ink, "6 · ADAPTIVE learning — the profile follows you as you drift")
    window = 6
    adaptive_profile = build_profile(
        enroll_attempts, discard=args.discard, threshold=args.threshold,
        window_size=window, label="genuine",
    )
    print(f"Re-enrolled with a sliding window of {window}. The same user now logs in repeatedly,")
    print("typing a little faster each time (muscle memory). Watch the profile track the change:")
    for k in range(1, 7):
        drifted = replace(
            genuine,
            dwell_base=max(45.0, genuine.dwell_base - k * 4.0),
            flight_base=max(35.0, genuine.flight_base - k * 6.0),
        )
        att = synthesize_attempts(password, drifted, 1, seed=3000 + k)[0]
        pre = compare_attempt(adaptive_profile, att)
        before_samples = list(adaptive_profile.samples)
        update_profile_with_attempt(adaptive_profile, att)
        after_score = compare_attempt(adaptive_profile, att).anomaly_score
        dropped = before_samples[0] if len(before_samples) >= window else None
        print()
        print(ink(f"login {k}:", "bold"))
        for line in render_adaptive_report(pre, adaptive_profile, after_score, dropped, ink,
                                           text=password, top=3):
            print(line)
    print()
    print(ink("The means slide as fast recent logins replace slower old ones — a cumulative "
              "profile\n(window 0) would instead freeze and slowly start rejecting the real user.", "grey"))
    return 0


# --------------------------------------------------------------------------- #
# Demo helpers
# --------------------------------------------------------------------------- #


def _play_events(attempt, ink: Ink, delay_ms: float) -> None:
    for line in iter_event_lines(attempt, ink):
        print(line)
        if delay_ms > 0:
            time.sleep(delay_ms / 1000.0)


def _print_roster(genuine_scores, impostor_results, default_t: float, tuned: float, ink: Ink) -> None:
    width = 16 + 1 + 7 + 3 + 9 + 3 + 9
    print(ink(f"{'typist':16} {'score':>7}   {f'@{default_t:.2f}':>9}   {f'@{tuned:.2f}':>9}", "bold"))
    print(ink(f"{'':16} {'':>7}   {'(default)':>9}   {'(tuned)':>9}", "grey"))
    print(ink("─" * width, "grey"))
    # Genuine reference row (averaged over several fresh attempts).
    g_score = sum(genuine_scores) / len(genuine_scores)
    print(f"{ink('genuine (you)'.ljust(16), 'green')} {g_score:>7.2f}   "
          f"{_verdict_cell(g_score, default_t, want_reject=False, ink=ink)}   "
          f"{_verdict_cell(g_score, tuned, want_reject=False, ink=ink)}")
    for name, result in sorted(impostor_results.items(), key=lambda kv: kv[1].anomaly_score):
        score = result.anomaly_score
        print(f"{name:16} {score:>7.2f}   "
              f"{_verdict_cell(score, default_t, want_reject=True, ink=ink)}   "
              f"{_verdict_cell(score, tuned, want_reject=True, ink=ink)}")
    print(ink("  ✓ = desired outcome   ✗ = the threshold got this one wrong", "grey"))


def _verdict_cell(score: float, threshold: float, *, want_reject: bool, ink: Ink) -> str:
    rejected = score > threshold
    verdict = "REJECT" if rejected else "PASS"
    correct = rejected == want_reject
    mark = "✓" if correct else "✗"
    style = "green" if correct else "red"
    return ink(f"{verdict} {mark}".rjust(9), style)  # pad visible text, then colour


def _print_sweep(genuine_scores, impostor_scores, ink: Ink) -> None:
    print(ink(f"{'threshold':>10} {'genuine accepted':>16} {'impostors rejected':>18}   note", "bold"))
    print(ink("─" * 57, "grey"))
    g_n, i_n = len(genuine_scores), len(impostor_scores)
    for t in _SWEEP_THRESHOLDS:
        g_ok = sum(1 for s in genuine_scores if s <= t)
        i_rej = sum(1 for s in impostor_scores if s > t)
        if g_ok < g_n:
            note = ink("← real user locked out", "yellow")
        elif i_rej < i_n:
            note = ink("← impostor slips in", "yellow")
        else:
            note = ink("← clean separation", "green")
        g_style = "green" if g_ok == g_n else "red"
        i_style = "green" if i_rej == i_n else "red"
        print(f"{t:>10.2f} "
              f"{ink(f'{g_ok}/{g_n}'.rjust(16), g_style)} "
              f"{ink(f'{i_rej}/{i_n}'.rjust(18), i_style)}   {note}")


def _recommend_threshold(genuine_scores, impostor_scores) -> float:
    """A sensible operating threshold: a cushion above the genuine cluster, kept
    off the impostor edge. This lands meaningfully tighter than a loose default
    while preserving a false-reject margin for the real user's off days."""
    import statistics

    max_g = max(genuine_scores)
    min_i = min(impostor_scores)
    std_g = statistics.pstdev(genuine_scores) if len(genuine_scores) > 1 else 0.0

    if max_g >= min_i:
        # Can't fully separate on this sample — favour accepting the real user.
        return max(0.5, round(max_g + 0.15, 2))

    gap = min_i - max_g
    target = max_g + max(3.0 * std_g, 0.2 * gap)  # ~3σ above the genuine cluster
    cap = min_i - 0.15 * gap  # stay clear of the closest impostor
    return max(0.5, round(min(target, cap), 2))


def _pick_detail(impostor_results, threshold: float) -> str:
    # Prefer a clearly-rejected impostor with the highest anomaly to explain.
    rejected = {n: r for n, r in impostor_results.items() if r.anomaly_score > threshold}
    pool = rejected or impostor_results
    return max(pool, key=lambda n: pool[n].anomaly_score)


# --------------------------------------------------------------------------- #
# Shared helpers
# --------------------------------------------------------------------------- #


def _adaptive_mode(profile: Profile) -> str:
    if profile.window_size:
        return f"sliding window of the last {profile.window_size} attempts"
    return "cumulative (every accepted login weighted equally)"


def _mode_of(stdin: bool) -> str:
    return "flight_only" if stdin else "full"


def _mode_label(mode: str) -> str:
    if mode == "flight_only":
        return "flight-only (stdin — flight timing, no dwell/shift)"
    return "full (dwell + flight + shift)"


def _print_capture_hint(stdin_mode: bool, ink: Ink) -> None:
    if stdin_mode:
        print(ink("Input: reading THIS terminal directly (stdin) — no permission needed, "
                  "immune to Secure Keyboard Entry; captures flight timing only.", "grey"))
        return
    print(ink("Input: OS-level capture (full dwell/flight/shift). Type in THIS terminal.", "grey"))
    print(ink("  • macOS needs Input Monitoring permission (System Settings ▸ Privacy & Security).", "grey"))
    print(ink("  • If keys ONLY register when another window is focused, turn OFF "
              "Terminal ▸ Secure Keyboard Entry (or use iTerm2) — or fall back to --stdin.", "grey"))


def _print_capture_summary(attempt, ink: Ink) -> None:
    if attempt.events:
        span = max(e.t for e in attempt.events) - min(e.t for e in attempt.events)
    else:
        span = 0.0
    print(ink(f"captured text length={len(attempt.text)}, events={len(attempt.events)}, "
              f"duration={span * 1000:.1f}ms", "grey"))


def _rule(ink: Ink, title: str) -> None:
    print(ink(f"── {title} " + "─" * max(0, 74 - len(title)), "bold", "blue"))


if __name__ == "__main__":
    raise SystemExit(main())

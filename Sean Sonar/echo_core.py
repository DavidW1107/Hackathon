#!/usr/bin/env python3
"""
echo_core.py -- shared DSP core for sonar.py (1D ranging) and radar.py (2D).

One Channel = one transmit chirp heard by one mic. It owns the whole
per-ping pipeline both apps share:

  matched filtering  ->  direct-path lock (+ glitch re-anchoring)
                     ->  coherent background subtraction
                     ->  temporal-coherence stale rejection
                     ->  CLEAN multi-echo extraction with sub-sample
                         templates measured from the live direct path

plus RangeKalman, the small constant-velocity filter the 1D tracker uses
to fuse coarse (envelope) range with carrier-phase range steps.

Units inside Channel are lag SAMPLES relative to the direct-path
arrival; callers convert to metres (round-trip for sonar, bistatic
extra path for radar).
"""

from dataclasses import dataclass

import numpy as np


# ------------------------------ configuration -------------------------------
@dataclass
class EchoConfig:
    fs: int                 # sample rate (Hz)
    f0: float               # chirp band (Hz)
    f1: float
    chirp_dur: float        # chirp length (s)
    ping_period: float      # one ping every this many seconds
    volume: float           # output amplitude (0..1)
    min_lag: int            # echo lag window, samples after the direct path
    max_lag: int
    cal_pings: int          # pings used to learn the static background
    snr_enter: float = 6.0  # a NEW target must clear this (x noise sigma)
    snr_exit: float = 4.0   # an EXISTING target survives down to this
    abs_floor: float = 0.002  # min echo strength relative to direct path
    bg_adapt: float = 0.01  # slow background adaptation rate
    bg_fast: float = 0.30   # absorption rate of confirmed-stale residue
    ref_adapt: float = 0.025  # direct-path reference tracking rate
    ema_a: float = 0.10     # temporal-coherence smoothing per ping
    coh_stale: float = 0.95  # residue coherence above this = static scene
    stale_run: int = 40     # frames coherence must stay high before absorbing
    max_obj: int = 3        # max echoes extracted per ping (CLEAN passes)
    side_rej: float = 0.10  # secondary peaks below this fraction of the
                            # strongest are leftover fit error
    sub: int = 32           # sub-sample resolution of the echo templates
    kill_hw: int = 6        # bins masked around a CLEANed peak
    fit_hw: int = 45        # half-width of the template least-squares fit
    protect_hw: int = 45    # background-freeze guard around live bins

    def __post_init__(self) -> None:
        self.ping_n = int(self.fs * self.ping_period)
        self.chirp_n = int(self.fs * self.chirp_dur)
        self.win_n = 2 * self.ping_n          # analysis window: two periods
        self.corr_n = self.win_n - self.chirp_n + 1
        self.nfft = 1 << (self.win_n + self.chirp_n).bit_length()
        self.fc = (self.f0 + self.f1) / 2     # carrier for phase ranging
        if self.max_lag - self.min_lag > self.chirp_n:
            raise ValueError("echo lag window wider than template support: "
                             "need max_lag - min_lag <= chirp_n")


# ------------------------------ waveforms ------------------------------------
def make_chirp(cfg: EchoConfig, f0: float, f1: float
               ) -> tuple[np.ndarray, np.ndarray]:
    """Real transmit chirp and the FFT-conjugate of its analytic version."""
    t = np.arange(cfg.chirp_n) / cfg.fs
    phase = 2 * np.pi * (f0 * t + (f1 - f0) / (2 * cfg.chirp_dur) * t**2)
    win = np.hanning(cfg.chirp_n)
    tx = (cfg.volume * np.sin(phase) * win).astype(np.float32)
    analytic = np.exp(1j * phase) * win
    return tx, np.conj(np.fft.fft(analytic, cfg.nfft))


def ideal_lobe(cfg: EchoConfig, tx: np.ndarray,
               h_conj: np.ndarray) -> np.ndarray:
    """Matched-filter response of a unit echo, lags -chirp_n..+chirp_n."""
    resp = np.fft.ifft(np.fft.fft(tx.astype(np.float64), cfg.nfft) * h_conj)
    return np.concatenate([resp[-cfg.chirp_n:], resp[:cfg.chirp_n + 1]])


def subsample_bank(cfg: EchoConfig, lobe: np.ndarray) -> np.ndarray:
    """Tabulate `lobe` at cfg.sub sub-sample delays.

    Row s, column chirp_n + u = the lobe at integer lag u for an echo
    delayed by (s - sub/2)/sub of a sample. CLEAN fits these against the
    residual to subtract each detected echo's full correlation lobe.
    """
    m = len(lobe)
    nfft2 = 1 << (2 * m).bit_length()
    lf = np.fft.fft(lobe, nfft2)
    fr = np.fft.fftfreq(nfft2)
    tbl = np.empty((cfg.sub, m), dtype=complex)
    for s in range(cfg.sub):
        delta = (s - cfg.sub // 2) / cfg.sub
        tbl[s] = np.fft.ifft(lf * np.exp(-2j * np.pi * fr * delta))[:m]
    return tbl


# ------------------------------ channel --------------------------------------
class Channel:
    """One chirp's echo profile: background subtraction + CLEAN peaks.

    process() consumes the FFT of a win_n-sample mic window; windows must
    advance by exactly ping_n samples so the direct-path arrival stays at
    a fixed index (playback and capture share a clock). Returns a dict:
    state 'silent' | 'relock' | 'calibrating' | 'blip' | 'ok'; when 'ok',
    'peaks' is a list of dicts with sub-sample 'lag' (samples after the
    direct path), 'snr', 'amp', and 'z' (derotated complex residual for
    carrier-phase tracking), strongest first.
    """

    def __init__(self, cfg: EchoConfig, h_conj: np.ndarray,
                 tmpl: np.ndarray, name: str = "") -> None:
        self.cfg = cfg
        self.h_conj = h_conj
        self.tmpl = tmpl                    # ideal until calibration swaps in
        self.name = name                    # the measured direct-path lobe
        self.direct: int | None = None      # locked direct-path index
        self.ref: complex = 0j              # slow-tracked direct-path value
        self.cal: list[np.ndarray] = []
        self.cal_ref: list[complex] = []
        self.cal_lobe: np.ndarray | None = None
        self.bg: np.ndarray | None = None   # complex background profile
        self.bg_strong: np.ndarray | None = None  # bins with solid static bg
        self.med = 0.0                      # noise floor from calibration
        self.sigma = 1e-9
        self.floor_db = 0.0                 # echo floor re direct, for health
        self.ema_c: np.ndarray | None = None  # complex residual average
        self.ema_m: np.ndarray | None = None  # residual magnitude average
        self.stale_ct: np.ndarray | None = None  # consecutive high-coh frames
        self.xrun = False                   # audio glitch hint from callback
        self.misaligned = 0                 # pings the background hasn't fit
        self.ref_jump = 0                   # consecutive big direct-level jumps
        self.keep_profile = False           # attach resid to results (waterfall)

    def note_xrun(self) -> None:
        """Callback saw an over/underflow: samples may have been dropped."""
        self.xrun = True

    # -- calibration ----------------------------------------------------------
    def _reset(self, p: int) -> None:
        self.direct = p
        self.cal.clear()
        self.cal_ref.clear()
        self.cal_lobe = None
        self.bg = None
        self.misaligned = 0

    def _calibrate(self, corr: np.ndarray, d: int) -> dict:
        cfg = self.cfg
        self.cal.append(corr[d + cfg.min_lag: d + cfg.max_lag].copy())
        self.cal_ref.append(complex(corr[d]))
        # the direct arrival repeats one period later, so a lock near the
        # window start can still contribute a full +-chirp_n lobe capture
        dd = d + cfg.ping_n if d < cfg.chirp_n else d
        lobe = corr[dd - cfg.chirp_n: dd + cfg.chirp_n + 1]
        self.cal_lobe = lobe.copy() if self.cal_lobe is None \
            else self.cal_lobe + lobe
        if len(self.cal) >= cfg.cal_pings:
            self.ref = complex(np.mean(self.cal_ref))
            self.bg = np.mean(self.cal, axis=0) / self.ref
            # noise floor from the (echo-free) calibration residuals;
            # per-frame estimates would be inflated by the very echoes
            # we want to detect
            pool = np.concatenate(
                [np.abs(c / self.ref - self.bg) for c in self.cal])
            self.med = float(np.median(pool))
            self.sigma = float(
                1.4826 * np.median(np.abs(pool - self.med))) + 1e-9
            self.floor_db = float(
                20 * np.log10(self.med + 4 * self.sigma + 1e-12))
            n = len(self.bg)
            self.ema_c = np.zeros(n, dtype=complex)
            self.ema_m = np.zeros(n)
            self.stale_ct = np.zeros(n)
            # bins with solid static echoes anchor glitch re-alignment
            thr = max(10 * (self.med + 4 * self.sigma), 2 * cfg.abs_floor)
            self.bg_strong = np.where(np.abs(self.bg) > thr)[0]
            # swap the ideal template for the measured system response
            # (speaker + mic + chassis), so CLEAN subtracts real lobes,
            # not textbook ones
            lobe = self.cal_lobe / self.cal_lobe[cfg.chirp_n]
            self.tmpl = subsample_bank(cfg, lobe)
            self.cal.clear()
            self.cal_ref.clear()
            self.cal_lobe = None
        return {"state": "calibrating", "n": len(self.cal)}

    # -- glitch re-anchoring ---------------------------------------------------
    def _blown(self, resid: np.ndarray) -> bool:
        """Does the background no longer fit where the static scene is?"""
        idx = self.bg_strong
        if idx is None or idx.size < 5:
            return False
        return bool(np.median(resid[idx] / np.abs(self.bg[idx])) > 0.7)

    def _find_shift(self, corr: np.ndarray, d: int) -> int:
        """A dropped audio block shifts every arrival by the same amount;
        the scene itself is unchanged, so sliding the lock until the
        background fits again restores alignment exactly -- no recal."""
        cfg, idx = self.cfg, self.bg_strong
        if idx is None or idx.size < 5:
            return 0

        def score(dd: int) -> float:
            seg = corr[dd + cfg.min_lag: dd + cfg.max_lag] / self.ref
            return float(np.median(
                np.abs(seg[idx] - self.bg[idx]) / np.abs(self.bg[idx])))

        base = score(d)
        best_k, best = 0, base
        for k in range(-6, 7):
            dd = d + k
            if k == 0 or dd < 0 or dd + cfg.max_lag >= cfg.corr_n:
                continue
            s = score(dd)
            if s < best:
                best, best_k = s, k
        return best_k if best < 0.35 * base else 0

    # -- per-ping processing ---------------------------------------------------
    def process(self, X: np.ndarray) -> dict:
        cfg = self.cfg
        corr = np.fft.ifft(X * self.h_conj)[:cfg.corr_n]
        mag = np.abs(corr[:cfg.ping_n])
        p = int(np.argmax(mag))
        # a genuine direct-path arrival towers over the correlation floor;
        # anything else means the mic hears no chirp (muted / no permission)
        if mag[p] < 1e-6 or mag[p] < 20 * np.median(mag):
            return {"state": "silent"}

        # lock onto the direct-path arrival; full re-lock only if it truly
        # moved (e.g. audio device change), not on one-sample jitter
        if self.direct is None or mag[p] > 2 * mag[self.direct]:
            relocked = self.direct is not None
            self._reset(p)
            if relocked:
                return {"state": "relock"}

        d = self.direct
        if self.bg is None:
            return self._calibrate(corr, d)

        def extract(dd: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
            # instantaneous corr[d] must not rescale the profile -- a close
            # hand overlaps the direct chirp and perturbs it by a few
            # percent, which the large direct-path tail would amplify
            seg = corr[dd + cfg.min_lag: dd + cfg.max_lag] / self.ref
            cres = seg - self.bg
            return seg, cres, np.abs(cres)

        seg, cres, resid = extract(d)

        # alignment check first: a dropped block shifts arrivals AND rotates
        # the direct-path phase, which must not be mistaken for a level change
        note = None
        blown = self._blown(resid)
        if self.xrun or blown:
            self.xrun = False
            k = self._find_shift(corr, d)
            if k:
                self.direct = d = d + k
                seg, cres, resid = extract(d)
                blown = self._blown(resid)
                note = f"re-anchored {k:+d} samples"

        # direct-path reference: slow tracking absorbs drift; a two-ping
        # confirmed jump means the output level changed (volume knob), and
        # since seg and bg are both level-normalized, re-seating ref
        # rescales everything cleanly. The first jump ping reports a blip
        # so half-adapted profiles never reach the detector as targets.
        inst = complex(corr[d])
        if abs(inst - self.ref) > 0.15 * abs(self.ref):
            self.ref_jump += 1
            if self.ref_jump < 2:
                return {"state": "blip", "note": note or "level jump"}
            self.ref, self.ref_jump = inst, 0
            seg, cres, resid = extract(d)
            blown = self._blown(resid)
            note = note or "output level re-seated"
        else:
            self.ref_jump = 0
            self.ref = (1 - cfg.ref_adapt) * self.ref + cfg.ref_adapt * inst

        if blown:
            # alignment lost and nothing above explains it: report a blip
            # (no detections, no adaptation) and recalibrate if it persists
            self.misaligned += 1
            if self.misaligned >= 6:
                self._reset(p)
                return {"state": "relock"}
            return {"state": "blip", "note": note}
        self.misaligned = 0

        resid0 = resid                       # pre-CLEAN copy, for adaptation
        n = len(cres)

        # Temporal coherence per bin: |mean(residual)| / mean(|residual|).
        # A live target wanders in phase ping to ping (even breathing moves
        # the echo by >1 rad), so its coherence stays low; leftover static
        # residue (scene changed, or a target we partially absorbed) is
        # phase-frozen with coherence near 1.
        self.ema_c = (1 - cfg.ema_a) * self.ema_c + cfg.ema_a * cres
        self.ema_m = (1 - cfg.ema_a) * self.ema_m + cfg.ema_a * resid0
        coh = np.abs(self.ema_c) / (self.ema_m + 1e-12)

        loud = resid0 > self.med + 4 * self.sigma
        # "stale" requires SUSTAINED phase-frozen amplitude: the ema_m
        # gate keeps a freshly appeared target (whose EMAs are dominated by
        # one frame, faking coherence 1) from qualifying, and the run
        # counter keeps the brief still moments of a breathing person from
        # qualifying -- only residue frozen for seconds is absorbed
        high = loud & (coh > cfg.coh_stale) & (self.ema_m > 0.7 * resid0)
        self.stale_ct = np.where(high, self.stale_ct + 1, 0)
        stale = self.stale_ct >= cfg.stale_run

        # CLEAN: detect the strongest live echo, fit its exact complex lobe
        # (sub-sample position + amplitude), subtract it, repeat. Runs down
        # to snr_exit so the tracker can hold known targets with hysteresis;
        # BRAND-NEW targets are only admitted at snr_enter by the tracker.
        peaks: list[dict] = []
        killed = np.zeros(n, dtype=bool)
        for _ in range(cfg.max_obj):
            i = int(np.argmax(np.where(killed | stale, 0.0, resid)))
            amp = float(resid[i])
            snr = float((amp - self.med) / self.sigma)
            if snr < cfg.snr_exit or amp < cfg.abs_floor:
                break
            if peaks and amp < cfg.side_rej * peaks[0]["amp"]:
                break
            # carrier phase, derotated by the bin's own phase so the value
            # is comparable even when the peak hops to a neighbouring bin;
            # taken before subtraction, with stronger echoes already removed
            z = complex(cres[i]) * np.exp(
                -2j * np.pi * cfg.fc * (cfg.min_lag + i) / cfg.fs)
            best = None
            for s in range(cfg.sub):     # least-squares over sub-sample shift
                T = self.tmpl[s][cfg.chirp_n - i: cfg.chirp_n - i + n]
                lo, hi = max(i - cfg.fit_hw, 0), min(i + cfg.fit_hw + 1, n)
                tn, cn = T[lo:hi], cres[lo:hi]
                a = np.vdot(tn, cn) / (float(np.vdot(tn, tn).real) + 1e-18)
                err = float(np.sum(np.abs(cn - a * tn) ** 2))
                if best is None or err < best[0]:
                    best = (err, s, a, T)
            _, s, a, T = best
            lag = cfg.min_lag + i + (s - cfg.sub // 2) / cfg.sub
            peaks.append({"lag": lag, "i": i, "snr": snr, "amp": amp, "z": z})
            cres = cres - a * T              # remove this echo's entire lobe
            resid = np.abs(cres)
            killed[max(i - cfg.kill_hw, 0): i + cfg.kill_hw + 1] = True

        # background update rates by bin class: fast absorption of
        # phase-frozen residue, normal drift far from any activity, and
        # NEVER absorb a live target or its surrounding correlation skirt
        # -- a person standing still must stay visible indefinitely, and
        # nothing of them may leak into the background to resurface as a
        # phantom when they leave
        protect = np.convolve((loud & ~stale).astype(float),
                              np.ones(2 * cfg.protect_hw + 1), "same") > 0
        rate = np.where(stale, cfg.bg_fast,
                        np.where(protect | loud, 0.0, cfg.bg_adapt))
        self.bg = self.bg + rate * (seg - self.bg)

        # the noise floor may drift only while nothing is detected, so a
        # present target can never ratchet it up over itself
        if not peaks:
            q = resid0[~loud]
            if len(q) > 40:
                m = np.median(q)
                s = 1.4826 * np.median(np.abs(q - m)) + 1e-9
                self.med += 0.1 * (m - self.med)
                self.sigma += 0.1 * (s - self.sigma)

        out = {"state": "ok", "peaks": peaks, "note": note,
               "med": self.med, "sigma": self.sigma,
               "direct_amp": float(mag[d])}
        if self.keep_profile:
            out["profile"] = resid0
        return out

    def health(self) -> str:
        """One-line audio-path verdict, valid after calibration."""
        db = self.floor_db
        verdict = ("excellent" if db < -50 else
                   "good" if db < -40 else
                   "marginal -- check mic mode (use 'Standard', not Voice "
                   "Isolation) and volume" if db < -30 else
                   "poor -- is output going to the built-in speakers?")
        return f"echo floor {db:.0f} dB re direct path ({verdict})"


# ------------------------------ tracking -------------------------------------
def wrap_pi(x: float) -> float:
    return (x + np.pi) % (2 * np.pi) - np.pi


class RangeKalman:
    """Constant-velocity filter over [range, velocity].

    Fuses the coarse envelope range (unambiguous, ~mm noise) with
    carrier-phase range steps (tens of microns, but aliased every
    lambda/2); the predicted velocity selects the phase branch, which is
    what lets fast motion keep the right sign.
    """

    def __init__(self, r0: float, sigma_r: float = 0.004,
                 sigma_v: float = 0.3, accel: float = 3.0) -> None:
        self.x = np.array([r0, 0.0])
        self.P = np.diag([sigma_r**2, sigma_v**2])
        self.accel = accel                  # white-accel spectral level (m/s^2)

    @property
    def r(self) -> float:
        return float(self.x[0])

    @property
    def v(self) -> float:
        return float(self.x[1])

    def predict(self, dt: float) -> None:
        self.x = np.array([self.x[0] + dt * self.x[1], self.x[1]])
        F = np.array([[1.0, dt], [0.0, 1.0]])
        q = self.accel**2
        Q = q * np.array([[dt**4 / 4, dt**3 / 2], [dt**3 / 2, dt**2]])
        self.P = F @ self.P @ F.T + Q

    def _update(self, z: float, var: float, h: np.ndarray,
                gate: float | None) -> bool:
        S = float(h @ self.P @ h) + var
        innov = z - float(h @ self.x)
        if gate is not None and innov * innov / S > gate * gate:
            return False
        K = (self.P @ h) / S
        self.x = self.x + K * innov
        self.P = self.P - np.outer(K, h @ self.P)
        return True

    def update_range(self, z: float, var: float,
                     gate: float | None = None) -> bool:
        return self._update(z, var, np.array([1.0, 0.0]), gate)

    def update_velocity(self, z: float, var: float,
                        gate: float | None = None) -> bool:
        return self._update(z, var, np.array([0.0, 1.0]), gate)

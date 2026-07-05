#!/usr/bin/env python3
"""
simulate.py -- synthetic mic-signal generator for demos and tests.

Renders what the mic would hear: each speaker's chirp arriving via the
direct path plus delayed copies from static reflectors and moving
targets, evaluated analytically (per-sample delays, so carrier phase is
physically faithful and the phase-tracking pipeline sees real motion).

Scenes yield successive win_n-sample analysis windows advancing by
exactly ping_n samples -- the same alignment the live apps maintain --
or paced ping-sized blocks for the --simulate CLI modes.
"""

import time

import numpy as np

from echo_core import EchoConfig

C = 343.0


class ChirpGen:
    """The transmit chirp as a function of time-within-ping (vectorized)."""

    def __init__(self, cfg: EchoConfig, f0: float, f1: float) -> None:
        self.f0, self.slope = f0, (f1 - f0) / (2 * cfg.chirp_dur)
        self.dur, self.fs = cfg.chirp_dur, cfg.fs
        self.n, self.volume = cfg.chirp_n, cfg.volume

    def __call__(self, u: np.ndarray) -> np.ndarray:
        live = (u >= 0) & (u < self.dur)
        uu = np.where(live, u, 0.0)
        ph = 2 * np.pi * (self.f0 * uu + self.slope * uu**2)
        win = 0.5 - 0.5 * np.cos(2 * np.pi * uu * self.fs / (self.n - 1))
        return np.where(live, self.volume * np.sin(ph) * win, 0.0)


class _Scene:
    """Common machinery: absolute-sample clock, noise, drops, level scale."""

    def __init__(self, cfg: EchoConfig, noise: float, seed: int,
                 lat: int) -> None:
        self.cfg = cfg
        self.noise = noise
        self.rng = np.random.default_rng(seed)
        self.lat = lat          # device latency in samples
        self.m0 = 0             # absolute sample index of the next window
        self.scale = 1.0        # output level (set to !=1 to fake volume knob)

    def drop(self, k: int) -> None:
        """Lose k input samples, like an audio overrun does."""
        self.m0 += k

    def set_amp(self, k: int, amp: float) -> None:
        """Change target k's echo strength (0 = target leaves)."""
        self.targets[k][1] = amp

    def _emit(self, gen: ChirpGen, m: np.ndarray, delay, offset: float,
              amp: float) -> np.ndarray:
        """One arrival: chirp offset by `offset` s within the ping,
        delayed by `delay` s (scalar or per-sample array)."""
        u = ((m - self.lat) / self.cfg.fs - delay) % self.cfg.ping_period
        return amp * gen(u - offset)

    def next_window(self) -> np.ndarray:
        m = np.arange(self.cfg.win_n) + self.m0
        self.m0 += self.cfg.ping_n
        x = self.scale * self._render(m)
        return x + self.noise * self.rng.standard_normal(len(m))

    def blocks(self, pace: bool = True):
        """Yield (ping-sized block, xrun_flag) forever, for the CLI modes."""
        m_next = 0
        while True:
            m = np.arange(self.cfg.ping_n) + m_next
            m_next += self.cfg.ping_n
            x = self.scale * self._render(m) \
                + self.noise * self.rng.standard_normal(len(m))
            yield x.astype(np.float32), False
            if pace:
                time.sleep(self.cfg.ping_period)


class SonarScene(_Scene):
    """Mono sonar: direct path + reflectors at round-trip range r(t).

    statics: [(range_m, amp)]; targets: [(path(t)->range_m, amp)] with amp
    relative to the direct path. Target amps may be changed mid-run via
    set_amp(); path functions must accept a vector of times.
    """

    def __init__(self, cfg: EchoConfig, statics=(), targets=(),
                 noise: float = 1e-5, seed: int = 0, lat: int = 700) -> None:
        super().__init__(cfg, noise, seed, lat)
        self.gen = ChirpGen(cfg, cfg.f0, cfg.f1)
        self.statics = list(statics)
        self.targets = [[path, amp] for path, amp in targets]

    def _render(self, m: np.ndarray) -> np.ndarray:
        x = self._emit(self.gen, m, 0.0, 0.0, 1.0)
        for r, amp in self.statics:
            x += self._emit(self.gen, m, 2 * r / C, 0.0, amp)
        t = m / self.cfg.fs
        for path, amp in self.targets:
            if amp:
                x += self._emit(self.gen, m, 2 * np.asarray(path(t)) / C,
                                0.0, amp)
        return x


class RadarScene(_Scene):
    """Two speakers, one mic: L up-chirp at t=0, R down-chirp half a ping
    later. statics/targets give positions in the keyboard plane;
    targets: [(path(t)->(x_m, z_m), amp)].
    """

    def __init__(self, cfg: EchoConfig, spk_x=(-0.12, 0.12), mic_x=0.0,
                 statics=(), targets=(), noise: float = 1e-5,
                 seed: int = 0, lat: int = 500) -> None:
        super().__init__(cfg, noise, seed, lat)
        self.gens = [ChirpGen(cfg, cfg.f0, cfg.f1),
                     ChirpGen(cfg, cfg.f1, cfg.f0)]
        self.offsets = [0.0, cfg.ping_period / 2]
        self.spk_x, self.mic_x = spk_x, mic_x
        self.statics = list(statics)
        self.targets = [[path, amp] for path, amp in targets]

    def _delay(self, sx: float, x, z):
        return (np.hypot(x - sx, z) + np.hypot(x - self.mic_x, z)) / C

    def _render(self, m: np.ndarray) -> np.ndarray:
        t = m / self.cfg.fs
        x = np.zeros(len(m))
        for gen, off, sx in zip(self.gens, self.offsets, self.spk_x):
            x += self._emit(gen, m, abs(sx - self.mic_x) / C, off, 1.0)
            for (px, pz), amp in self.statics:
                x += self._emit(gen, m, self._delay(sx, px, pz), off, amp)
            for path, amp in self.targets:
                if amp:
                    px, pz = path(t)
                    x += self._emit(gen, m, self._delay(sx, px, pz), off, amp)
        return x

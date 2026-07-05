"""End-to-end regression tests on synthetic audio.

Every test renders physically faithful mic signals (direct path, static
room echoes, moving targets with per-sample delays) and drives the same
pipeline the live apps use -- no hardware, deterministic seeds.
"""

import numpy as np

import radar as RD
import simulate as sim
import sonar as S

STATICS_1D = [(0.32, 0.04), (0.57, 0.03)]        # fake desk + screen
STATICS_2D = [((0.0, 0.33), 0.04), ((-0.22, 0.62), 0.03)]


def make_sonar(targets, seed=0, noise=3e-4):
    """Calibrated pipeline + scene; targets start with their given amp."""
    scene = sim.SonarScene(S.CFG, statics=STATICS_1D, targets=targets,
                           noise=noise, seed=seed)
    pipe = S.SonarPipeline()
    n = 0
    while pipe.ch.bg is None:
        pipe.process(scene.next_window())
        n += 1
        assert n < 60, "calibration never finished"
    return pipe, scene


def run(pipe, scene, n):
    return [pipe.process(scene.next_window()) for _ in range(n)]


def t_now(pipe):
    return pipe.ping * S.PING_PERIOD


def wobble(r0, mm=2.0, hz=1.1, phase=0.0):
    """A target that is still but alive (mm-scale phase wander)."""
    return lambda t: r0 + mm * 1e-3 * np.sin(2 * np.pi * hz * t + phase)


# --------------------------- 1D sonar ----------------------------------------
def test_calibration_and_quiet_scene():
    pipe, scene = make_sonar([])
    assert pipe.ch.floor_db < -40            # health check: good audio path
    assert pipe.ch.bg_strong.size >= 10      # statics learned as anchors
    rs = run(pipe, scene, 30)
    assert all(r["state"] == "ok" and not r["peaks"] for r in rs)
    assert not pipe.tracks


def test_range_and_velocity_accuracy():
    t0 = {"on": 0.0}
    path = lambda t: 0.35 + 0.030 * (t - t0["on"])   # recede at 30 mm/s
    pipe, scene = make_sonar([(path, 0.0)])
    t0["on"] = t_now(pipe)
    scene.set_amp(0, 0.02)
    rs = run(pipe, scene, 120)
    assert all(r["state"] == "ok" and r["peaks"] for r in rs[10:])
    (tr,) = pipe.live()
    assert abs(tr.r - path(t_now(pipe))) < 0.006     # mm-level ranging
    assert abs(tr.v - 0.030) < 0.005


def test_fast_motion_keeps_right_sign():
    # 120 mm/s sits beyond the lambda/4-per-ping phase alias limit
    # (~90 mm/s); the old tracker reported this as -15 mm/s
    t0 = {"on": 0.0}
    path = lambda t: 0.30 + 0.120 * (t - t0["on"])
    pipe, scene = make_sonar([(path, 0.0)])
    t0["on"] = t_now(pipe)
    scene.set_amp(0, 0.02)
    vels = []
    for _ in range(80):
        pipe.process(scene.next_window())
        live = pipe.live()
        vels.append(live[0].v if live else np.nan)
    tail = np.array(vels[-20:])
    assert not np.isnan(tail).any()
    assert (tail > 0.09).all()               # right sign, right magnitude
    assert 0.105 < tail.mean() < 0.135


def test_no_phantom_after_target_leaves():
    pipe, scene = make_sonar([(wobble(0.45), 0.025)])
    run(pipe, scene, 60)
    assert pipe.live()
    scene.set_amp(0, 0.0)                    # hand leaves
    counts = []
    for r in run(pipe, scene, 80):
        assert r["state"] == "ok"
        counts.append(len(pipe.live()))
    assert all(c == 0 for c in counts[10:])  # nothing resurfaces


def test_two_targets_tracked_independently():
    pipe, scene = make_sonar([(wobble(0.30, hz=1.1), 0.025),
                              (wobble(0.55, hz=0.8, phase=0.5), 0.020)])
    n_live = []
    for _ in range(100):
        pipe.process(scene.next_window())
        n_live.append(len(pipe.live()))
    assert all(n == 2 for n in n_live[-20:])
    rs = sorted(t.r for t in pipe.live())
    assert abs(rs[0] - 0.30) < 0.02
    assert abs(rs[1] - 0.55) < 0.02


def test_dropped_samples_reanchor_without_recal():
    pipe, scene = make_sonar([(wobble(0.45), 0.025)])
    run(pipe, scene, 40)
    assert len(pipe.live()) == 1
    scene.drop(3)                            # audio overrun, no hint given
    notes = []
    for r in run(pipe, scene, 20):
        assert r["state"] in ("ok", "blip")  # never a full recalibration
        notes.append(r.get("note") or "")
        assert len(pipe.tracks) == 1         # no phantom spawns
        for t in pipe.live():
            assert abs(t.v) < 0.06           # phase continuity held
    assert any("re-anchored" in n for n in notes)
    (tr,) = pipe.live()
    assert abs(tr.r - 0.45) < 0.02


def test_volume_change_resettles_fast():
    pipe, scene = make_sonar([(wobble(0.40), 0.025)])
    run(pipe, scene, 40)
    scene.scale = 1.5                        # user turned the volume up
    rs = run(pipe, scene, 12)
    assert sum(r["state"] != "ok" for r in rs) <= 3
    assert all(r["state"] != "relock" for r in rs)
    assert len(pipe.tracks) == 1
    (tr,) = pipe.live()
    assert abs(tr.r - 0.40) < 0.02


def test_breathing_rate():
    # 1.5 mm chest wobble at 0.25 Hz = 15 breaths/min at 80 cm
    person = lambda t: 0.80 + 0.0015 * np.sin(2 * np.pi * 0.25 * t)
    pipe, scene = make_sonar([(person, 0.03)])
    run(pipe, scene, 700)                    # 35 s of history
    (tr,) = pipe.live()
    assert tr.breath is not None
    assert 13.5 < tr.breath < 16.5


def test_tracker_hysteresis():
    pipe = S.SonarPipeline()

    def det(r, snr):
        return {"lag": 2 * r / 343.0 * S.FS, "i": 0, "snr": snr,
                "amp": 0.01, "z": 1 + 0j}

    pipe.ping = 100
    pipe._track([det(0.5, 5.0)])             # below SNR_ENTER: ignored
    assert not pipe.tracks
    pipe.ping += 1
    pipe._track([det(0.5, 7.0)])             # clears SNR_ENTER: track born
    assert len(pipe.tracks) == 1
    for _ in range(30):                      # fades to 4.5 sigma: survives
        pipe.ping += 1
        pipe._track([det(0.5, 4.5)])
    assert pipe.live()
    for _ in range(S.MISS_MAX + 2):          # disappears: track dies
        pipe.ping += 1
        pipe._track([])
    assert not pipe.tracks


# --------------------------- 2D radar ----------------------------------------
def make_radar(targets, seed=1):
    scene = sim.RadarScene(RD.CFG, spk_x=(RD.SPK_L_X, RD.SPK_R_X),
                           mic_x=RD.MIC_X, statics=STATICS_2D,
                           targets=targets, noise=3e-4, seed=seed)
    radar = RD.Radar()
    n = 0
    while radar.ch_l.bg is None or radar.ch_r.bg is None:
        radar.process(scene.next_window())
        n += 1
        assert n < 40, "radar calibration never finished"
    return radar, scene


def wobble2(x0, z0, phase=0.0):
    return lambda t: (x0 + 0.002 * np.sin(2 * np.pi * 1.1 * t + phase),
                      z0 + 0.002 * np.sin(2 * np.pi * 0.9 * t + 1.0))


def test_radar_localizes_single_target():
    radar, scene = make_radar([(wobble2(0.15, 0.50), 0.03)])
    outs = [radar.process(scene.next_window()) for _ in range(50)]
    for r in outs[-10:]:
        assert r["state"] == "ok"
        assert any(abs(o["x"] - 0.15) < 0.05 and abs(o["z"] - 0.50) < 0.05
                   for o in r["objects"])
    # centred mic must read ~zero skew (the old formula said -857 cm)
    assert abs(RD.mic_skew_cm(radar.ch_l.direct, radar.ch_r.direct)) < 1.5


def test_radar_two_targets():
    radar, scene = make_radar([(wobble2(-0.20, 0.40), 0.03),
                               (wobble2(0.18, 0.70, 0.7), 0.025)])
    outs = [radar.process(scene.next_window()) for _ in range(60)]
    good = 0
    for r in outs[-10:]:
        hits = sum(any(abs(o["x"] - x) < 0.06 and abs(o["z"] - z) < 0.06
                       for o in r["objects"])
                   for x, z in [(-0.20, 0.40), (0.18, 0.70)])
        good += hits == 2
    assert good >= 8


def test_mic_skew_formula():
    half = RD.CFG.ping_n // 2
    assert RD.mic_skew_cm(500, 500 + half) == 0.0
    expect = -7 / RD.FS * 343.0 / 2 * 100
    assert abs(RD.mic_skew_cm(500, 507 + half) - expect) < 1e-9

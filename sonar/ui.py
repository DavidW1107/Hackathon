#!/usr/bin/env python3
"""Local UI for the hand-sweep sonar. Set seconds + room height, hit Scan,
spin the laptop through one turn, see the floor outline + 3D extrusion.

Non-live by design: you are physically spinning the laptop during the sweep,
so there is nothing to watch mid-scan. Click -> spin -> result.
ponytail: matplotlib widgets reuse the plotting dep (no Tk/Qt/web add). Upgrade
path if you want live frames: a sounddevice input stream + FuncAnimation.
"""
import numpy as np
import matplotlib.pyplot as plt
from matplotlib.widgets import Button, TextBox

import sonar

fig = plt.figure(figsize=(13, 6.5))
fig.canvas.manager.set_window_title("laptop sonar")
ax_floor = fig.add_axes([0.06, 0.30, 0.40, 0.60])
ax_3d = fig.add_axes([0.55, 0.30, 0.42, 0.60], projection="3d")
status = fig.text(0.06, 0.20, "Set seconds + height, then Scan.", fontsize=11)


def _reset_axes():
    ax_floor.clear()
    ax_3d.clear()
    ax_floor.set_aspect("equal")
    ax_floor.set_title("floor outline")
    ax_floor.set_xlabel("m")
    ax_3d.set_title("extruded walls")


def draw(angles, ranges, height, mode):
    _reset_axes()
    x = ranges * np.cos(angles)
    y = ranges * np.sin(angles)
    ax_floor.plot(x, y, ".-", ms=3, lw=0.6)
    ax_floor.plot(0, 0, "r+", ms=12)
    sonar._extrude(ax_3d, x, y, height)
    np.save("scan.npy", np.c_[angles, ranges])
    fig.savefig("scan.png", dpi=120)
    status.set_text(f"{len(ranges)} echoes · heading: {mode} · saved scan.png / scan.npy")
    fig.canvas.draw_idle()


def _floats():
    return float(tb_sec.text or 12), float(tb_h.text or 2.5)


def on_scan(_):
    try:
        seconds, height = _floats()
    except ValueError:
        status.set_text("seconds/height must be numbers.")
        fig.canvas.draw_idle()
        return
    status.set_text("scanning — SPIN NOW…")
    fig.canvas.draw()
    plt.pause(0.05)  # force repaint before the blocking sweep
    try:
        angles, ranges, mode = sonar.scan(seconds, on_status=lambda m: None)
    except Exception as e:  # keep the UI alive on audio-device errors
        status.set_text(f"scan failed: {e}")
        fig.canvas.draw_idle()
        return
    if len(ranges) < 3:
        status.set_text("Too few echoes — louder volume, quieter room, hard walls.")
        fig.canvas.draw_idle()
        return
    draw(angles, ranges, height, mode)


def on_load(_):
    try:
        data = np.load("scan.npy")
    except OSError:
        status.set_text("no scan.npy yet.")
        fig.canvas.draw_idle()
        return
    _, height = _floats()
    draw(data[:, 0], data[:, 1], height, "loaded")


# widgets (keep references or they get garbage-collected)
tb_sec = TextBox(plt.axes([0.12, 0.06, 0.08, 0.06]), "seconds ", initial="12")
tb_h = TextBox(plt.axes([0.32, 0.06, 0.08, 0.06]), "height ", initial="2.5")
b_scan = Button(plt.axes([0.50, 0.055, 0.14, 0.07]), "Scan")
b_load = Button(plt.axes([0.66, 0.055, 0.14, 0.07]), "Load last")
b_scan.on_clicked(on_scan)
b_load.on_clicked(on_load)

_reset_axes()
if __name__ == "__main__":
    plt.show()

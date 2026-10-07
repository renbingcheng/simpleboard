"""Reproducible synthetic tablet + actual Canvas QWidget render benchmark.

This does not measure hardware, Windows Ink delivery, display scanout, or pen
tip latency. Run with the same interpreter as the application.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import platform
import statistics
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6 import __version__ as pyside_version
from PySide6.QtCore import QEvent, QPointF, Qt, qVersion
from PySide6.QtGui import QImage, QInputDevice, QPointingDevice, QTabletEvent
from PySide6.QtWidgets import QApplication

from whiteboard.canvas import Canvas
from whiteboard.geometry import clear_geometry_cache
from whiteboard.models import BoardDocument, Brush, InkSample, Stroke
from whiteboard.scene import Scene


def summary(values):
    ordered = sorted(values)
    return {
        "count": len(values), "median_ms": round(statistics.median(values), 4),
        "p95_ms": round(ordered[max(0, math.ceil(len(ordered) * .95) - 1)], 4),
        "max_ms": round(max(values), 4),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--width", type=int, default=1920)
    parser.add_argument("--height", type=int, default=1080)
    parser.add_argument("--output", default="artifacts/performance.json")
    parser.add_argument("--history-profile", choices=("legacy-v1", "pressure-v2"), default="pressure-v2")
    args = parser.parse_args()
    started = time.perf_counter()
    app = QApplication.instance() or QApplication([])
    app.setAttribute(Qt.ApplicationAttribute.AA_CompressTabletEvents, False)
    strokes = []
    for i in range(2000):
        x = (i % 50) * (args.width - 60) / 50
        y = (i // 50) * (args.height - 80) / 40
        samples = [InkSample(x + j * .25, y + math.sin(j * .12) * 8, t=j * 4,
                             pressure=.3 + .6 * j / 100) for j in range(100)]
        strokes.append(Stroke(samples, Brush(width=3, render_profile=args.history_profile)))
    clear_geometry_cache()
    before = time.perf_counter()
    scene = Scene(BoardDocument(strokes))
    scene_ms = (time.perf_counter() - before) * 1000
    canvas = Canvas(scene)
    canvas.resize(args.width, args.height)
    canvas.set_brush(Brush(width=4))
    before = time.perf_counter()
    canvas.show()
    app.processEvents()
    cold_ms = (time.perf_counter() - before) * 1000
    cold_paint = list(canvas._paint_ms)
    image = canvas._new_layer()
    device = QPointingDevice("Synthetic benchmark stylus", 710,
                             QInputDevice.DeviceType.Stylus, QPointingDevice.PointerType.Pen,
                             QInputDevice.Capability.Position | QInputDevice.Capability.Pressure,
                             1, 2)
    tick = 0

    def send(kind, position, pressure=.6):
        nonlocal tick
        tick += 4
        released = kind == QEvent.Type.TabletRelease
        button = (Qt.MouseButton.LeftButton if kind != QEvent.Type.TabletMove
                  else Qt.MouseButton.NoButton)
        event = QTabletEvent(kind, device, position, position, 0 if released else pressure,
                             0, 0, 0, 0, 0, Qt.KeyboardModifier.NoModifier, button,
                             Qt.MouseButton.NoButton if released else Qt.MouseButton.LeftButton)
        event.setTimestamp(tick)
        before = time.perf_counter()
        QApplication.sendEvent(canvas, event)
        return (time.perf_counter() - before) * 1000

    def render():
        before = time.perf_counter()
        canvas.render(image)
        return (time.perf_counter() - before) * 1000

    idle = [render() for _ in range(30)]
    inputs, paints, render_wall = [], [], []
    send(QEvent.Type.TabletPress, QPointF(40, 160))
    for j in range(1, 501):
        position = QPointF(40 + (args.width - 80) * j / 500, 160 + math.sin(j * .065) * 95)
        inputs.append(send(QEvent.Type.TabletMove, position, .45 + .35 * math.sin(j * .04)))
        render_wall.append(render())
        paints.append(canvas._paint_ms[-1])
    long_commit = send(QEvent.Type.TabletRelease, position)
    render()
    commits, commit_renders = [], []
    for trial in range(30):
        x, y = 80 + trial * 15, 350 + trial * 10
        send(QEvent.Type.TabletPress, QPointF(x, y))
        for j in range(1, 100):
            position = QPointF(x + j * 1.5, y + math.sin(j * .09) * 30)
            send(QEvent.Type.TabletMove, position, .4 + .3 * math.sin(j * .03))
        commits.append(send(QEvent.Type.TabletRelease, position))
        commit_renders.append(render())
    result = {
        "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
        "benchmark": "actual Canvas QWidget paintEvent and QApplication synthetic QTabletEvent dispatch",
        "environment": {
            "os": platform.platform(), "architecture": platform.machine(),
            "processor": os.environ.get("PROCESSOR_IDENTIFIER", platform.processor()),
            "python": platform.python_version(), "pyside6": pyside_version, "qt": qVersion(),
            "qt_platform": app.platformName(), "logical_viewport": [canvas.width(), canvas.height()],
            "device_pixel_ratio": canvas.devicePixelRatioF(), "render_target_pixels": [image.width(), image.height()],
        },
        "workload": {"history_strokes": 2000, "history_samples": 200000,
                     "history_render_profile": args.history_profile,
                     "continuous_active_segments": 500, "long_stroke_samples": 501,
                     "commit_trials": 30, "samples_per_commit_trial": 100},
        "cold_scene_index_ms": round(scene_ms, 4),
        "cold_widget_show_and_paint_ms": round(cold_ms, 4),
        "cold_paint_event_ms": [round(value, 4) for value in cold_paint],
        "warm_idle_widget_render": summary(idle),
        "active_tablet_move_dispatch": summary(inputs),
        "active_paint_event": summary(paints),
        "active_widget_render": summary(render_wall),
        "long_501_sample_stroke_release_dispatch_ms": round(long_commit, 4),
        "stroke_release_dispatch_including_commit": summary(commits),
        "repaint_after_stroke_commit": summary(commit_renders),
        "tile_cache_mib": round(canvas.renderer.cache_bytes / 1024**2, 3),
        "tile_cache_budget_mib": canvas.renderer.budget_bytes / 1024**2,
        "elapsed_s": round(time.perf_counter() - started, 3),
        "limitations": [
            "Synthetic input with offscreen Qt backend; Surface Pen/Windows Ink/display latency is unmeasured.",
            "No 30-minute endurance run; memory leak and physical Surface acceptance remain unverified.",
            "No MainWindow autosave or file serialization is active during this canvas-only benchmark.",
        ],
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    canvas.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

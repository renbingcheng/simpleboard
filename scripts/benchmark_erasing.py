"""Bounded synthetic eraser benchmark through actual Canvas Qt event dispatch.

Each case runs in a separate process with a 45-second maximum. This is not a
Surface hardware test and does not run MainWindow, autosave, or disk recovery.
Use --preview-only to isolate live preview; that mode explicitly drops its
uncommitted synthetic gesture before closing, so cleanup cannot commit it.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import statistics
import subprocess
import sys
import time


ROOT = Path(__file__).resolve().parents[1]
CASES = ("history_tablet", "history_mouse", "dense_repeated_tablet")


def summary(values):
    if not values:
        return {"count": 0, "median_ms": None, "p95_ms": None, "max_ms": None}
    ordered = sorted(values)
    return {
        "count": len(values), "median_ms": round(statistics.median(values), 4),
        "p95_ms": round(ordered[math.ceil(len(ordered) * .95) - 1], 4),
        "max_ms": round(max(values), 4),
    }


def emit(value):
    # Flush checkpoints before commit so a timeout still preserves preview data.
    print(json.dumps(value, ensure_ascii=True), flush=True)


def compare_render_samples(preview, committed):
    """Check the entire left-turn strip and a sparse grid across the viewport."""
    if preview.size() != committed.size():
        return {"status": "size_mismatch"}
    width, height = preview.width(), preview.height()
    stride = preview.bytesPerLine()
    first, second = bytes(preview.constBits()), bytes(committed.constBits())
    strip = min(width-1, math.ceil(80*preview.devicePixelRatioF()))
    checked = differing = 0
    interior = []
    max_channel_delta = 0

    def pixel(data, x, y):
        offset = y*stride+x*4
        return data[offset:offset+4]

    for y in range(1, height-1):
        columns = range(1, strip)
        if y % 8 == 0:
            columns = (*columns, *range(strip, width-1, 8))
        for x in columns:
            checked += 1
            a, b = pixel(first, x, y), pixel(second, x, y)
            if a == b:
                continue
            differing += 1
            max_channel_delta = max(max_channel_delta, *(abs(aa-bb) for aa, bb in zip(a, b)))
            near_boundary = any(
                pixel(first, xx, yy) != a or pixel(second, xx, yy) != b
                for yy in range(y-1, y+2) for xx in range(x-1, x+2))
            if not near_boundary and len(interior) < 20:
                interior.append([x, y])
    return {
        "status": "pass" if not interior else "interior_mismatch",
        "exact_image_equal": first == second,
        "sampling": "Every interior pixel in the leftmost 80 logical px; 8-physical-px grid elsewhere",
        "checked_pixels": checked, "differing_sampled_pixels": differing,
        "max_sampled_channel_delta": max_channel_delta,
        "interior_mismatch_examples": interior,
        "edge_tolerance": "Differences allowed only adjacent to a color boundary in a 3x3 neighborhood",
        "limitations": "Sparse sampling outside the left strip is not an exhaustive equivalence proof",
    }


def child_case(args):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    sys.path.insert(0, str(ROOT))
    from PySide6 import __version__ as pyside_version
    from PySide6.QtCore import QEvent, QPointF, Qt, qVersion
    from PySide6.QtGui import QImage, QInputDevice, QMouseEvent, QPainter, QPointingDevice, QTabletEvent
    from PySide6.QtWidgets import QApplication
    import pyclipper
    from whiteboard.canvas import Canvas
    from whiteboard.geometry import clear_geometry_cache
    from whiteboard.models import BoardDocument, Brush, InkSample, Stroke
    from whiteboard.scene import Scene

    started = time.perf_counter()
    app = QApplication.instance() or QApplication([])
    app.setAttribute(Qt.ApplicationAttribute.AA_CompressTabletEvents, False)
    dense = args.case == "dense_repeated_tablet"
    tablet = args.case != "history_mouse"
    width, height = args.width, args.height
    strokes = []
    if dense:
        # Multiple traversals of a Lissajous path create a compound outline with
        # many intersections, not a simple line with redundant collinear points.
        samples = []
        for i in range(1000):
            t = 6 * math.pi * i / 999
            samples.append(InkSample(width*.5 + width*.35*math.sin(3*t),
                                     height*.5 + height*.32*math.sin(4*t),
                                     i / 240, .45 + .35 * math.sin(i*.035)))
        strokes.append(Stroke(samples, Brush(width=9, pressure_enabled=True)))
    else:
        for i in range(2000):
            x = 30 + (i % 50) * (width - 100) / 50
            y = 30 + (i // 50) * (height - 100) / 40
            samples = [InkSample(x+j*.25, y+math.sin(j*.12)*8, j/240,
                                 .3+.6*j/100) for j in range(100)]
            strokes.append(Stroke(samples, Brush(width=3)))
    emit({"stage": "document_built", "history_strokes": len(strokes),
          "history_samples": sum(len(s.samples) for s in strokes)})
    clear_geometry_cache()
    before = time.perf_counter()
    scene = Scene(BoardDocument(strokes))
    scene_ms = (time.perf_counter()-before)*1000
    canvas = Canvas(scene)
    canvas.resize(width, height)
    canvas.set_eraser(5 if dense else 12, False)
    if not tablet:
        canvas.set_tool("eraser")
    before = time.perf_counter()
    canvas.show()
    app.processEvents()
    cold_ms = (time.perf_counter()-before)*1000
    image = canvas._new_layer()
    device = QPointingDevice("Synthetic benchmark tail", 8201,
                             QInputDevice.DeviceType.Stylus, QPointingDevice.PointerType.Eraser,
                             QInputDevice.Capability.Position | QInputDevice.Capability.Pressure,
                             1, 3)
    tick = 0

    def send(action, position):
        nonlocal tick
        tick += 4
        releasing = action == "release"
        button = Qt.MouseButton.NoButton if action == "move" else Qt.MouseButton.LeftButton
        buttons = Qt.MouseButton.NoButton if releasing else Qt.MouseButton.LeftButton
        if tablet:
            kind = {"press": QEvent.Type.TabletPress, "move": QEvent.Type.TabletMove,
                    "release": QEvent.Type.TabletRelease}[action]
            event = QTabletEvent(kind, device, position, position, 0 if releasing else .6,
                                 0, 0, 0, 0, 0, Qt.KeyboardModifier.NoModifier, button, buttons)
        else:
            kind = {"press": QEvent.Type.MouseButtonPress, "move": QEvent.Type.MouseMove,
                    "release": QEvent.Type.MouseButtonRelease}[action]
            event = QMouseEvent(kind, position, position, position, button, buttons,
                                Qt.KeyboardModifier.NoModifier,
                                Qt.MouseEventSource.MouseEventNotSynthesized)
        event.setTimestamp(tick)
        before = time.perf_counter()
        QApplication.sendEvent(canvas, event)
        return (time.perf_counter()-before)*1000

    def render():
        before = time.perf_counter()
        canvas.render(image)
        return (time.perf_counter()-before)*1000, canvas._paint_ms[-1]

    def ink_snapshot():
        snapshot = canvas._new_layer()
        painter = QPainter(snapshot)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        canvas._paint_document(painter)
        painter.end()
        return snapshot

    for _ in range(3):
        render()
    emit({"stage": "history_warmed", "cold_scene_index_ms": round(scene_ms, 4),
          "cold_widget_show_and_paint_ms": round(cold_ms, 4)})
    press, moves, paints, render_wall, releases, post_commit_paint = [], [], [], [], [], []
    trials = 1 if args.preview_only or not dense else 12
    move_count = 16 if dense else 120
    progress = {}
    render_check = None
    for trial in range(trials):
        if dense:
            y = height*.34 + trial*height*.025
            points = [QPointF(width*(.17 if j % 2 == 0 else .83),
                              y + (j % 3-1)*2) for j in range(move_count+1)]
        else:
            # Sparse long jumps, reversals, and a gradual vertical sweep. Four
            # input updates per rendered frame approximate a 240/60 Hz ratio,
            # without sleeping or claiming to reproduce driver timing.
            points = [QPointF(40 if j % 2 == 0 else width-40,
                              40 + (height-80)*j/move_count)
                      for j in range(move_count+1)]
        press.append(send("press", points[0]))
        wall, paint = render()
        render_wall.append(wall)
        paints.append(paint)
        for index, point in enumerate(points[1:], 1):
            moves.append(send("move", point))
            if index % 4 == 0 or index == move_count:
                wall, paint = render()
                render_wall.append(wall)
                paints.append(paint)
        progress = {
            "completed_trial": trial+1,
            "press_dispatch": summary(press),
            "move_dispatch": summary(moves),
            "active_paint_event": summary(paints),
            "active_widget_render": summary(render_wall),
            "release_dispatch_including_commit": summary(releases),
            "repaint_after_commit": summary(post_commit_paint),
        }
        emit({"stage": "preview_complete", **progress})
        if args.preview_only:
            # Deliberately discard the benchmark gesture, rather than invoking
            # cancel_input/close (which preserve user work by committing it).
            canvas._interaction = ""
            canvas._source = ""
            canvas._tail_eraser_contact = False
            canvas._erase_points = []
            canvas._erase_layer = QImage()
            break
        preview = ink_snapshot() if args.render_check and trial == trials-1 else None
        releases.append(send("release", points[-1]))
        wall, _ = render()
        post_commit_paint.append(wall)
        emit({"stage": "commit_complete", "completed_trial": trial+1,
              "release_dispatch_including_commit": summary(releases),
              "repaint_after_commit": summary(post_commit_paint)})
        if preview is not None:
            # Snapshot/diff/disk I/O are outside all input and paint timings.
            committed = ink_snapshot()
            render_check = compare_render_samples(preview, committed)
            output_paths = {}
            for name, value in (("preview", preview), ("committed", committed)):
                path = ROOT / "artifacts" / f"erasing-{name}-final.png"
                path.parent.mkdir(parents=True, exist_ok=True)
                if not value.save(str(path), "PNG"):
                    raise OSError(f"Could not save render evidence: {path}")
                output_paths[name] = str(path)
            render_check["images"] = output_paths
            emit({"stage": "render_checked", **render_check})
    result = {
        "case": args.case, "status": "complete", "preview_only": args.preview_only,
        "environment": {
            "os": platform.platform(), "architecture": platform.machine(),
            "processor": os.environ.get("PROCESSOR_IDENTIFIER", ""),
            "python": platform.python_version(), "pyside6": pyside_version, "qt": qVersion(),
            "erase_boolean_backend": "Clipper integer polygon difference",
            "pyclipper": pyclipper.__version__,
            "qt_platform": app.platformName(), "logical_viewport": [canvas.width(), canvas.height()],
            "device_pixel_ratio": canvas.devicePixelRatioF(),
            "render_target_pixels": [image.width(), image.height()],
        },
        "workload": {"history_strokes": 1 if dense else 2000,
                     "history_samples": 1000 if dense else 200000,
                     "input": "QTabletEvent Eraser" if tablet else "QMouseEvent eraser tool",
                     "erase_trials": trials, "moves_per_trial": move_count,
                     "moves_per_frame": 4, "eraser_radius_dip": canvas.eraser_radius,
                     "mode": "local vector erase", "dense_self_intersection": dense},
        "cold_scene_index_ms": round(scene_ms, 4),
        "cold_widget_show_and_paint_ms": round(cold_ms, 4),
        "press_dispatch": summary(press), "move_dispatch": summary(moves),
        "active_paint_event": summary(paints), "active_widget_render": summary(render_wall),
        "release_dispatch_including_commit": summary(releases),
        "repaint_after_commit": summary(post_commit_paint),
        "undo_commands": scene.undo_stack.count(),
        "remaining_strokes": len(scene.document.strokes),
        "remaining_erase_masks": sum(len(s.erase_masks) for s in scene.document.strokes),
        "tile_cache_mib": round(canvas.renderer.cache_bytes / 1024**2, 3),
        "elapsed_s": round(time.perf_counter()-started, 3),
    }
    if render_check is not None:
        result["render_check"] = render_check
        if render_check["status"] != "pass":
            result["status"] = "render_mismatch"
    QApplication.instance().removeEventFilter(canvas)
    canvas.close()
    emit({"result": result})
    return 0


def parse_lines(output):
    if isinstance(output, bytes):
        output = output.decode("utf-8", errors="replace")
    values = []
    for line in (output or "").splitlines():
        try:
            values.append(json.loads(line))
        except (json.JSONDecodeError, TypeError):
            continue
    return values


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--width", type=int, default=1920)
    parser.add_argument("--height", type=int, default=1080)
    parser.add_argument("--output", default="artifacts/performance-erasing.json")
    parser.add_argument("--timeout", type=float, default=45)
    parser.add_argument("--preview-only", action="store_true")
    parser.add_argument("--case", choices=CASES)
    parser.add_argument("--child", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--render-check", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.width < 640 or args.height < 480 or not 0 < args.timeout <= 45:
        parser.error("Viewport must be at least 640x480; per-case timeout must be in (0,45].")
    if args.child:
        if not args.case:
            parser.error("--child requires --case")
        return child_case(args)
    started = time.perf_counter()
    source_hashes = {path: hashlib.sha256((ROOT/path).read_bytes()).hexdigest()
                     for path in ("whiteboard/canvas.py", "whiteboard/scene.py", "whiteboard/geometry.py")}
    cases = []
    for name in (args.case,) if args.case else CASES:
        command = [sys.executable, str(Path(__file__).resolve()), "--child", "--case", name,
                   "--width", str(args.width), "--height", str(args.height)]
        if args.preview_only:
            command.append("--preview-only")
        elif name == "history_tablet":
            command.append("--render-check")
        before = time.perf_counter()
        try:
            process = subprocess.run(command, cwd=ROOT, capture_output=True, text=True,
                                     encoding="utf-8", errors="replace", timeout=args.timeout)
            checkpoints = parse_lines(process.stdout)
            final = next((item["result"] for item in reversed(checkpoints) if "result" in item), None)
            case = final or {"case": name, "status": "error", "exit_code": process.returncode,
                             "checkpoints": checkpoints, "stderr": process.stderr[-4000:]}
        except subprocess.TimeoutExpired as exc:
            case = {"case": name, "status": "timeout", "timeout_s": args.timeout,
                    "checkpoints": parse_lines(exc.stdout)}
        case["subprocess_wall_s"] = round(time.perf_counter()-before, 3)
        cases.append(case)
        emit({"case": name, "status": case["status"], "wall_s": case["subprocess_wall_s"]})
    report = {
        "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
        "benchmark": "Canvas synthetic Qt eraser input and QWidget paintEvent; bounded child processes",
        "preview_only": args.preview_only, "case_timeout_s": args.timeout,
        "source_sha256": source_hashes,
        "cases": cases, "elapsed_s": round(time.perf_counter()-started, 3),
        "limitations": [
            "Synthetic Qt events; no physical Surface Pen, Windows Ink delivery, or display latency measured.",
            "Canvas only: MainWindow autosave, recovery serialization, and file export are not active.",
            "Input bursts are processed as fast as possible; synthetic timestamps do not simulate device scheduling.",
            "Offscreen QPA is the default; device pixel ratio and viewport are reported for every completed case.",
            "Preview-only runs deliberately discard the active test gesture and contain no release/commit measurements.",
        ],
    }
    output = ROOT / args.output if not Path(args.output).is_absolute() else Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    print(str(output), flush=True)
    return 0 if all(case["status"] == "complete" for case in cases) else 1


if __name__ == "__main__":
    raise SystemExit(main())

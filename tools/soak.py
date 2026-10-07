"""Bounded-document write/erase/undo soak; no Surface hardware claim."""
from __future__ import annotations
import argparse
import ctypes
import json
import math
import os
from pathlib import Path
import platform
import statistics
import sys
import time

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PySide6.QtCore import QPointF, QTimer
from PySide6.QtWidgets import QApplication
from whiteboard.canvas import Canvas
from whiteboard.models import BoardDocument, Brush, InkSample, Stroke
from whiteboard.scene import Scene


def resident_bytes():
    if os.name != 'nt':
        return None
    class Counters(ctypes.Structure):
        _fields_ = [('cb', ctypes.c_ulong), ('PageFaultCount', ctypes.c_ulong),
                    ('PeakWorkingSetSize', ctypes.c_size_t), ('WorkingSetSize', ctypes.c_size_t),
                    ('QuotaPeakPagedPoolUsage', ctypes.c_size_t), ('QuotaPagedPoolUsage', ctypes.c_size_t),
                    ('QuotaPeakNonPagedPoolUsage', ctypes.c_size_t), ('QuotaNonPagedPoolUsage', ctypes.c_size_t),
                    ('PagefileUsage', ctypes.c_size_t), ('PeakPagefileUsage', ctypes.c_size_t)]
    counters = Counters()
    counters.cb = ctypes.sizeof(counters)
    process = ctypes.windll.kernel32.GetCurrentProcess
    process.restype = ctypes.c_void_p
    query = ctypes.windll.psapi.GetProcessMemoryInfo
    query.argtypes = [ctypes.c_void_p, ctypes.POINTER(Counters), ctypes.c_ulong]
    if query(process(), ctypes.byref(counters), counters.cb):
        return counters.WorkingSetSize
    return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--seconds', type=float, default=1800)
    parser.add_argument('--output', default='artifacts/soak.json')
    args = parser.parse_args()
    app = QApplication([])
    scene = Scene()
    canvas = Canvas(scene)
    canvas.resize(1280, 800)
    canvas.show()
    app.processEvents()
    started = time.monotonic()
    records, durations = [], []
    cycles = 0
    error = None
    last_record = -60
    brush = Brush(color='#2563EB', width=5)

    def tick():
        nonlocal cycles, last_record, error
        t0 = time.perf_counter()
        try:
            phase = cycles % 4
            if phase == 0:
                canvas.set_brush(brush)
                canvas._begin(QPointF(120, 240), 'pen', 'pen', .2)
                for i in range(1, 80):
                    canvas._move(QPointF(120+i*8, 240+35*math.sin(i/9)), .2+.7*i/80, i*5)
                canvas.finish_interaction()
            elif phase == 1:
                scene.erase([(400, 180), (400, 310)], 10)
            elif phase == 2:
                scene.undo_stack.undo()
                scene.undo_stack.redo()
            else:
                scene.clear()
                scene.undo_stack.undo()
                scene.undo_stack.redo()
            canvas.repaint()
            assert canvas.renderer.cache_bytes <= 128*1024**2
            assert scene.undo_stack.count() <= 200
            assert len(scene.document.strokes) <= 1
            durations.append((time.perf_counter()-t0)*1000)
            cycles += 1
        except Exception as exc:
            error = repr(exc)
            finish()
            return
        elapsed = time.monotonic()-started
        if elapsed-last_record >= 60:
            records.append({'seconds':round(elapsed, 2), 'rss_bytes':resident_bytes(),
                            'tile_bytes':canvas.renderer.cache_bytes, 'cycles':cycles})
            last_record = elapsed
            print(json.dumps(records[-1]), flush=True)
        if elapsed >= args.seconds:
            finish()

    def finish():
        timer.stop()
        result = {'ok':error is None, 'error':error, 'seconds':round(time.monotonic()-started, 2),
                  'cycles':cycles, 'platform':platform.platform(), 'qpa':app.platformName(),
                  'canvas':[1280,800], 'scope':'synthetic input; bounded document; not Surface hardware',
                  'cycle_ms_p95': sorted(durations)[int(len(durations)*.95)] if durations else None,
                  'measurements':records, 'final_rss_bytes':resident_bytes(),
                  'tile_bytes':canvas.renderer.cache_bytes, 'undo_commands':scene.undo_stack.count()}
        target=Path(args.output)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps({'complete':result['ok'], 'seconds':result['seconds'], 'cycles':cycles}),flush=True)
        app.exit(0 if result['ok'] else 1)

    timer=QTimer()
    timer.setInterval(50)
    timer.timeout.connect(tick)
    timer.start()
    return app.exec()


if __name__ == '__main__':
    raise SystemExit(main())

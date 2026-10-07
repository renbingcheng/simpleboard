"""Exercise real Windows synthetic pen messages against this process's test window.

Run with the project Python on an interactive Windows desktop. No existing
Whiteboard window is opened, selected, or controlled. Every injection is guarded
by WindowFromPoint + GetAncestor(GA_ROOT), the HWND's owning PID, and a bare-Canvas
Qt hit test. Losing that target aborts immediately and destroys our device.

SDK references (independent injection structures, not the adapter's test doubles):
https://learn.microsoft.com/windows/win32/api/winuser/nf-winuser-createsyntheticpointerdevice
https://learn.microsoft.com/windows/win32/api/winuser/nf-winuser-injectsyntheticpointerinput
https://learn.microsoft.com/windows/win32/api/winuser/ns-winuser-pointer_type_info
https://learn.microsoft.com/windows/win32/api/winuser/ns-winuser-pointer_info
https://learn.microsoft.com/windows/win32/api/winuser/ns-winuser-pointer_pen_info
https://learn.microsoft.com/windows/win32/api/winuser/ns-winuser-pointer_touch_info

This validates the Windows/Qt adapter path, not physical Surface firmware,
Bluetooth behavior, or real pen-to-screen latency. Requires Windows 10 1809+.
"""
from __future__ import annotations

import argparse
import ctypes as C
from ctypes import wintypes
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import platform
import sys
import tempfile
import time
import traceback
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

PT_PEN = 3
FEEDBACK_NONE = 3
GA_ROOT = 2
NEW = 0x1
INRANGE = 0x2
INCONTACT = 0x4
FIRSTBUTTON = 0x10
SECONDBUTTON = 0x20
DOWN = 0x10000
UPDATE = 0x20000
UP = 0x40000
BARREL = 0x1
INVERTED = 0x2
ERASER = 0x4
WM_POINTER_MESSAGES = {0x245, 0x246, 0x247, 0x249, 0x24A, 0x24C}


class POINT(C.Structure):
    _fields_ = [("x", C.c_int32), ("y", C.c_int32)]


class RECT(C.Structure):
    _fields_ = [(name, C.c_int32) for name in ("left", "top", "right", "bottom")]


class POINTER_INFO(C.Structure):
    _fields_ = [
        ("pointerType", C.c_uint32), ("pointerId", C.c_uint32),
        ("frameId", C.c_uint32), ("pointerFlags", C.c_uint32),
        ("sourceDevice", C.c_void_p), ("hwndTarget", C.c_void_p),
        ("ptPixelLocation", POINT), ("ptHimetricLocation", POINT),
        ("ptPixelLocationRaw", POINT), ("ptHimetricLocationRaw", POINT),
        ("dwTime", C.c_uint32), ("historyCount", C.c_uint32),
        ("InputData", C.c_int32), ("dwKeyStates", C.c_uint32),
        ("PerformanceCount", C.c_uint64), ("ButtonChangeType", C.c_uint32),
    ]


class POINTER_PEN_INFO(C.Structure):
    _fields_ = [("pointerInfo", POINTER_INFO), ("penFlags", C.c_uint32),
                ("penMask", C.c_uint32), ("pressure", C.c_uint32),
                ("rotation", C.c_uint32), ("tiltX", C.c_int32), ("tiltY", C.c_int32)]


class POINTER_TOUCH_INFO(C.Structure):
    # The union must reserve the larger touch structure even for a pen device.
    _fields_ = [("pointerInfo", POINTER_INFO), ("touchFlags", C.c_uint32),
                ("touchMask", C.c_uint32), ("rcContact", RECT),
                ("rcContactRaw", RECT), ("orientation", C.c_uint32), ("pressure", C.c_uint32)]


class POINTER_DATA(C.Union):
    _fields_ = [("pointerInfo", POINTER_INFO), ("touchInfo", POINTER_TOUCH_INFO),
                ("penInfo", POINTER_PEN_INFO)]


class POINTER_TYPE_INFO(C.Structure):
    _anonymous_ = ("data",)
    _fields_ = [("type", C.c_uint32), ("data", POINTER_DATA)]


class Unavailable(RuntimeError):
    pass


class TargetLost(RuntimeError):
    pass


class NativeApi:
    def __init__(self):
        self.user32 = C.WinDLL("user32", use_last_error=True)
        signatures = {
            "CreateSyntheticPointerDevice": ([C.c_uint32, C.c_uint32, C.c_uint32], C.c_void_p),
            "DestroySyntheticPointerDevice": ([C.c_void_p], None),
            "InjectSyntheticPointerInput": ([C.c_void_p, C.POINTER(POINTER_TYPE_INFO), C.c_uint32], C.c_int32),
            "WindowFromPoint": ([POINT], C.c_void_p),
            "GetAncestor": ([C.c_void_p, C.c_uint32], C.c_void_p),
            "GetWindowThreadProcessId": ([C.c_void_p, C.POINTER(C.c_uint32)], C.c_uint32),
            "GetSystemMetrics": ([C.c_int32], C.c_int32),
            "GetCursorPos": ([C.POINTER(POINT)], C.c_int32),
            "IsTouchWindow": ([C.c_void_p, C.POINTER(C.c_uint32)], C.c_int32),
            "RegisterTouchWindow": ([C.c_void_p, C.c_uint32], C.c_int32),
            "ClientToScreen": ([C.c_void_p, C.POINTER(POINT)], C.c_int32),
            "GetPointerType": ([C.c_uint32, C.POINTER(C.c_uint32)], C.c_int32),
            "GetPointerPenInfo": ([C.c_uint32, C.POINTER(POINTER_PEN_INFO)], C.c_int32),
            "GetPointerPenInfoHistory": ([C.c_uint32, C.POINTER(C.c_uint32), C.POINTER(POINTER_PEN_INFO)], C.c_int32),
        }
        for name, (arguments, result) in signatures.items():
            try:
                function = getattr(self.user32, name)
            except AttributeError as exc:
                raise Unavailable(f"Windows API unavailable: {name}") from exc
            function.argtypes, function.restype = arguments, result
            setattr(self, name, function)

    @staticmethod
    def check(ok, name):
        if not ok:
            raise OSError(C.get_last_error(), f"{name} failed")

    @staticmethod
    def sample(value):
        info = value.pointerInfo
        return {
            "pointer_id": info.pointerId, "frame_id": info.frameId,
            "flags": info.pointerFlags, "pen_flags": value.penFlags,
            "pressure": value.pressure, "history_count": info.historyCount,
            "x": info.ptPixelLocation.x, "y": info.ptPixelLocation.y,
            "button_change": info.ButtonChangeType,
            "source_device": int(info.sourceDevice or 0), "target_hwnd": int(info.hwndTarget or 0),
            "time": info.dwTime, "performance_count": info.PerformanceCount,
        }

    def observe(self, native):
        """Read real GetPointer* APIs synchronously in the delivery callback."""
        pointer_id = int(native.wParam) & 0xFFFF
        row = {"message": int(native.message), "pointer_id": pointer_id}
        pointer_type = C.c_uint32()
        self.check(self.GetPointerType(pointer_id, C.byref(pointer_type)), "GetPointerType")
        row["pointer_type"] = pointer_type.value
        if pointer_type.value != PT_PEN:
            return row
        latest = POINTER_PEN_INFO()
        self.check(self.GetPointerPenInfo(pointer_id, C.byref(latest)), "GetPointerPenInfo")
        row["latest"] = self.sample(latest)
        count = C.c_uint32(512)
        values = (POINTER_PEN_INFO * 512)()
        self.check(self.GetPointerPenInfoHistory(pointer_id, C.byref(count), values), "GetPointerPenInfoHistory")
        if not 1 <= count.value <= 512:
            raise RuntimeError(f"Unexpected native history count: {count.value}")
        row["history"] = [self.sample(values[index]) for index in range(count.value)]
        return row


class Injector:
    def __init__(self, api, window, report):
        self.api, self.window, self.report = api, window, report
        self.hwnd = int(window.winId())
        self.device = api.CreateSyntheticPointerDevice(PT_PEN, 1, FEEDBACK_NONE)
        if not self.device:
            raise Unavailable(f"CreateSyntheticPointerDevice failed: {C.get_last_error()}")
        report["synthetic_device_created"] = True
        self.last_time = 0.0

    def close(self):
        if self.device:
            self.api.DestroySyntheticPointerDevice(self.device)
            self.device = None
            self.report["synthetic_device_destroyed"] = True

    def inject(self, x, y, flags, *, pen_flags=0, pressure=0, label=""):
        from PySide6.QtCore import QPoint

        # SDK assigns timestamps. Keep frames apart without inventing timestamps.
        delay = .002 - (time.perf_counter() - self.last_time)
        if delay > 0:
            time.sleep(delay)
        canvas = self.window.canvas
        logical = QPoint(round(x), round(y))
        in_window = canvas.mapTo(self.window, logical)
        dpr = self.window.devicePixelRatioF()
        physical = POINT(round(in_window.x() * dpr), round(in_window.y() * dpr))
        self.api.check(self.api.ClientToScreen(self.hwnd, C.byref(physical)), "ClientToScreen")
        # Unlike GetPointerPenInfo/WindowFromPoint's desktop coordinates, this
        # injection API explicitly takes pixels relative to the virtual desktop
        # top-left. A secondary monitor to the left makes that origin negative.
        virtual_origin = (self.api.GetSystemMetrics(76), self.api.GetSystemMetrics(77))
        relative = POINT(physical.x - virtual_origin[0], physical.y - virtual_origin[1])
        target = self.api.WindowFromPoint(physical)
        root = self.api.GetAncestor(target, GA_ROOT) if target else None
        pid = C.c_uint32()
        self.api.GetWindowThreadProcessId(root, C.byref(pid))
        if (not self.window.isVisible() or not canvas.rect().contains(logical)
                or self.window.childAt(in_window) is not canvas
                or int(root or 0) != self.hwnd or pid.value != os.getpid()):
            self.close()
            raise TargetLost(f"Injection aborted: bare Canvas/own HWND check failed at {x}, {y}; "
                             f"root={int(root or 0)}, expected={self.hwnd}, pid={pid.value}")
        # No event loop, focus change, or other action is allowed between this
        # final target check and the injection call.
        value = POINTER_TYPE_INFO()
        value.type = PT_PEN
        pen = value.penInfo
        info = pen.pointerInfo
        info.pointerType, info.pointerId = PT_PEN, 1
        info.pointerFlags = flags
        info.ptPixelLocation = relative
        pen.penFlags, pen.penMask = pen_flags, 1 | 4 | 8
        pen.pressure, pen.tiltX, pen.tiltY = pressure, 12, -8
        self.api.check(self.api.InjectSyntheticPointerInput(self.device, C.byref(value), 1),
                       "InjectSyntheticPointerInput")
        self.last_time = time.perf_counter()
        self.report["injections"].append({
            "label": label, "canvas": [x, y], "screen": [physical.x, physical.y],
            "injection_relative_to_virtual_origin": [relative.x, relative.y],
            "virtual_origin": virtual_origin,
            "checked_hwnd": int(root), "hit_hwnd": int(target or 0), "checked_pid": pid.value,
            "flags": flags, "pen_flags": pen_flags, "pressure": pressure,
            "wire_fields": {
                "type": value.type, "pointer_type": value.penInfo.pointerInfo.pointerType,
                "pointer_flags": value.penInfo.pointerInfo.pointerFlags,
                "pen_flags": value.penInfo.penFlags, "pen_mask": value.penInfo.penMask,
                "pressure": value.penInfo.pressure,
            },
        })


def run(output, register_touch=False):
    if sys.platform != "win32":
        raise Unavailable("This integration check requires an interactive Windows desktop")
    os.environ["QT_QPA_PLATFORM"] = "windows"
    from PySide6 import __version__ as pyside_version
    from PySide6.QtCore import QAbstractNativeEventFilter, QPointF, Qt
    from PySide6.QtGui import QInputDevice
    from PySide6.QtWidgets import QApplication
    from whiteboard.geometry import visible_path
    from whiteboard.models import BoardDocument, Brush, InkSample, Stroke
    from whiteboard.recovery import RecoveryManager
    from whiteboard.settings import AppSettings
    from whiteboard.window import MainWindow

    output.mkdir(parents=True, exist_ok=True)
    run_dir = Path(tempfile.mkdtemp(prefix="run-", dir=output))
    report = {
        "started_utc": datetime.now(timezone.utc).isoformat(), "status": "running",
        "platform": platform.platform(), "pyside": pyside_version, "run_directory": str(run_dir),
        "injections": [], "native_messages": [], "dispatcher_messages": [],
        "widget_message_counts": {}, "dispatcher_message_counts": {}, "checks": {},
        "native_callback_types": [],
        "native_callback_errors": [],
        "synthetic_device_created": False, "synthetic_device_destroyed": False,
        "limitation": "Synthetic Windows pen input; not physical Surface Pen acceptance.",
    }
    app = QApplication.instance() or QApplication([])
    api = None
    window = None
    injector = None
    observer = None

    def callback_seen(where, event_type, message):
        if len(report["native_callback_types"]) < 16:
            address = int(message) if message is not None else 0
            native = wintypes.MSG.from_address(address) if address else None
            report["native_callback_types"].append({
                "where": where, "type": str(type(event_type)), "repr": repr(event_type),
                "bytes": repr(bytes(event_type)), "message": hex(native.message) if native else None,
                "pointer_type": str(type(message)), "pointer_repr": repr(message),
                "pointer_bool": bool(message), "pointer_address": address,
            })

    class NativeObserver(QAbstractNativeEventFilter):
        def nativeEventFilter(self, event_type, message):
            callback_seen("dispatcher", event_type, message)
            if message is not None and int(message) and bytes(event_type) == b"windows_generic_MSG":
                native = wintypes.MSG.from_address(int(message))
                key = hex(native.message)
                counts = report["dispatcher_message_counts"]
                counts[key] = counts.get(key, 0) + 1
                if int(native.message) in WM_POINTER_MESSAGES and api is not None:
                    try:
                        row = api.observe(native)
                    except Exception as exc:
                        row = {"message": int(native.message), "api_error": repr(exc)}
                    row["hwnd"] = int(native.hWnd or 0)
                    report["dispatcher_messages"].append(row)
            return False, 0

    class VerificationWindow(MainWindow):
        def nativeEvent(self, event_type, message):
            callback_seen("widget", event_type, message)
            row = None
            if bytes(event_type) == b"windows_generic_MSG" and message is not None and int(message) and api is not None:
                native = wintypes.MSG.from_address(int(message))
                key = hex(native.message)
                counts = report["widget_message_counts"]
                counts[key] = counts.get(key, 0) + 1
                if int(native.message) in WM_POINTER_MESSAGES:
                    try:
                        row = api.observe(native)
                    except Exception as exc:
                        row = {"message": int(native.message), "api_error": repr(exc)}
                    report["native_messages"].append(row)
            try:
                result = super().nativeEvent(event_type, message)
            except Exception as exc:
                # Preserve adapter integration failures in the artifact instead
                # of letting PySide surface them later during cleanup.
                report["native_callback_errors"].append({"error": repr(exc), "traceback": traceback.format_exc()})
                return True, 0
            if row is not None and hasattr(self, "native_tail_eraser"):
                row["after_dispatch"] = {
                    "generation": self.native_tail_eraser._generation,
                    "active_pointer_id": self.native_tail_eraser.active_pointer_id,
                    "source": self.canvas._source,
                    "undo_count": self.scene.undo_stack.count(),
                }
            return result

        def _maybe_leave_document(self):
            # Only this disposable test window overrides the close prompt.
            self.canvas.finish_interaction()
            return True

    def pump(milliseconds=45, check_errors=True):
        deadline = time.monotonic() + milliseconds / 1000
        while time.monotonic() < deadline:
            app.processEvents()
            time.sleep(.002)
        app.processEvents()
        if check_errors and report["native_callback_errors"]:
            raise RuntimeError(report["native_callback_errors"][-1]["error"])

    def require(name, condition, detail=None):
        report["checks"][name] = {"passed": bool(condition), "detail": detail}
        if not condition:
            raise AssertionError(f"{name}: {detail}")

    try:
        api = NativeApi()
        observer = NativeObserver()
        app.installNativeEventFilter(observer)
        settings = AppSettings(run_dir / "settings.json")
        recovery_factory = lambda provider, parent=None: RecoveryManager(provider, parent, run_dir / "recovery")
        with patch("whiteboard.window.RecoveryManager", side_effect=recovery_factory):
            window = VerificationWindow(recovery_enabled=True, settings=settings)
        window._startup_done = True
        # Temporary test surface only. Keep it visible while the terminal/app
        # shows progress; the entire window is destroyed in finally below.
        window.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        window.resize(980, 680)
        available = app.primaryScreen().availableGeometry()
        window.move(available.x() + max(0, (available.width() - window.width()) // 2),
                    available.y() + max(0, (available.height() - window.height()) // 2))
        window.show()
        # Request visibility/focus only for our newly-created disposable window.
        # Never select, activate, restore, or move another application's HWND.
        window.raise_()
        window.activateWindow()
        pump(350)
        window.setWindowTitle("Native pen integration check — temporary test window")
        canvas = window.canvas
        canvas.set_diagnostics_enabled(True)
        canvas.set_grid_enabled(False)
        canvas.set_eraser(12, False)
        report["qpa"] = app.platformName()
        report["hwnd"] = int(window.winId())
        report["pid"] = os.getpid()
        report["dpr"] = window.devicePixelRatioF()
        report["adapter_enabled"] = window.native_tail_eraser.enabled
        report["sm_digitizer"] = api.GetSystemMetrics(94)
        report["sm_maximum_touches"] = api.GetSystemMetrics(95)
        report["virtual_desktop"] = [api.GetSystemMetrics(index) for index in (76, 77, 78, 79)]
        report["qt_input_devices"] = [{"name": device.name(), "type": device.type().name,
                                       "capabilities": device.capabilities().value}
                                      for device in QInputDevice.devices()]
        report["touch_registered_before_test"] = bool(api.IsTouchWindow(int(window.winId()), None))
        report["test_only_touch_registration"] = False
        if register_touch:
            # On a non-touch development PC Qt skips this registration. This
            # opt-in affects this HWND only, reproducing the registration that
            # Qt normally performs on a Surface with a physical touch digitizer.
            api.check(api.RegisterTouchWindow(int(window.winId()), 0), "RegisterTouchWindow(test HWND)")
            report["test_only_touch_registration"] = True
        report["touch_registered_during_test"] = bool(api.IsTouchWindow(int(window.winId()), None))
        report["struct_sizes"] = {name: C.sizeof(value) for name, value in
                                  (("POINTER_INFO", POINTER_INFO), ("POINTER_PEN_INFO", POINTER_PEN_INFO),
                                   ("POINTER_TOUCH_INFO", POINTER_TOUCH_INFO), ("POINTER_TYPE_INFO", POINTER_TYPE_INFO))}
        require("windows_qpa", app.platformName() == "windows")
        require("native_adapter_enabled", window.native_tail_eraser.enabled)
        stroke = Stroke([InkSample(x, 220) for x in range(100, 721, 10)],
                        Brush(color="#2563EB", width=56, pressure_enabled=False))
        window.scene.reset(BoardDocument(strokes=[stroke]))
        canvas.load_view_from_document()
        window.scene.undo_stack.setClean()
        pump(75)
        injector = Injector(api, window, report)

        injector.inject(150, 220, NEW | UPDATE | INRANGE, pen_flags=INVERTED | ERASER, label="tail_hover_before")
        pump()
        cursor = POINT()
        api.GetCursorPos(C.byref(cursor))
        report["cursor_after_first_hover"] = [cursor.x, cursor.y]
        expected_screen = report["injections"][-1]["screen"]
        hover_samples = [row["latest"] for row in report["native_messages"] if row.get("latest")]
        require("hover_delivery_verifies_target_before_contact", any(
            sample["target_hwnd"] == int(window.winId())
            and abs(sample["x"] - expected_screen[0]) <= 1
            and abs(sample["y"] - expected_screen[1]) <= 1 for sample in hover_samples),
            {"expected_screen": expected_screen, "received_samples": hover_samples})
        require("hover_does_not_edit", window.scene.undo_stack.count() == 0 and not canvas.has_active_edit,
                {"requested_pointer_flags": NEW | UPDATE | INRANGE,
                 "requested_pen_flags": INVERTED | ERASER, "received_samples": hover_samples})
        injector.inject(150, 220, DOWN | INRANGE | INCONTACT | FIRSTBUTTON,
                        pen_flags=INVERTED | ERASER, pressure=512, label="tail_down")
        pump()
        api.GetCursorPos(C.byref(cursor))
        report["cursor_after_first_down"] = [cursor.x, cursor.y]
        require("native_down_claimed", canvas.native_tail_active and window.native_tail_eraser._generation == 1,
                {"source": canvas._source, "generation": window.native_tail_eraser._generation})
        for change_index, (x, pen_flags, button, pressure) in enumerate((
            (270, INVERTED, FIRSTBUTTON, 600),
            (420, INVERTED | ERASER, FIRSTBUTTON, 0),
            (580, INVERTED | ERASER | BARREL, SECONDBUTTON, 750),
            (210, INVERTED, FIRSTBUTTON, 430),
            (480, BARREL, FIRSTBUTTON | SECONDBUTTON, 620),
            (210, 0, FIRSTBUTTON, 500),
        )):
            injector.inject(x, 220, UPDATE | INRANGE | INCONTACT | button,
                            pen_flags=pen_flags, pressure=pressure, label="tail_flags_change")
            pump()
            require(f"continuous_before_up_change_{change_index}", canvas.native_tail_active
                    and window.scene.undo_stack.count() == 0 and window.native_tail_eraser._generation == 1)
        # Keep dispatch pending while generating a short burst so Windows may
        # coalesce updates. GetPointerPenInfoHistory is independently observed.
        for index, x in enumerate(range(230, 651, 20)):
            injector.inject(x, 220, UPDATE | INRANGE | INCONTACT | FIRSTBUTTON,
                            pen_flags=INVERTED | (ERASER if index % 2 else 0),
                            pressure=512, label="tail_history_burst")
        pump(100)
        require("burst_keeps_single_contact", canvas.native_tail_active
                and window.scene.undo_stack.count() == 0 and window.native_tail_eraser._generation == 1)
        injector.inject(650, 220, UP | INRANGE, pen_flags=INVERTED, pressure=0, label="tail_up")
        pump(100)
        require("native_up_one_undo", not canvas.has_active_edit
                and window.native_tail_eraser.active_pointer_id is None and window.scene.undo_stack.count() == 1)
        erased_stroke = window.scene.get(stroke.id)
        path = visible_path(erased_stroke)
        missing = [x for x in range(160, 641) if path.contains(QPointF(x, 220))]
        retained = path.contains(QPointF(350, 244))
        require("continuous_erased_band", not missing and retained, {"remaining_center_pixels": missing, "outside_band_retained": retained})
        mask = erased_stroke.erase_masks[-1]
        first_error = abs(mask.points[0][0] - 150) + abs(mask.points[0][1] - 220)
        last_error = abs(mask.points[-1][0] - 650) + abs(mask.points[-1][1] - 220)
        require("native_dpr_coordinate_mapping", max(first_error, last_error) <= 2 / report["dpr"],
                {"first": mask.points[0], "last": mask.points[-1], "dpr": report["dpr"]})
        window.scene.undo_stack.undo()
        require("single_undo_restores_band", visible_path(window.scene.get(stroke.id)).contains(QPointF(350, 220)))
        window.scene.undo_stack.redo()
        require("single_redo_erases_band", not visible_path(window.scene.get(stroke.id)).contains(QPointF(350, 220)))
        injector.inject(650, 220, UPDATE | INRANGE, pen_flags=0, label="tip_hover_after")
        pump()
        require("hover_after_does_not_resume", not canvas.has_active_edit and window.scene.undo_stack.count() == 1)

        injector.inject(180, 335, DOWN | INRANGE | INCONTACT | FIRSTBUTTON, pressure=280, label="tip_down")
        pump()
        for x in (230, 280, 340, 410):
            injector.inject(x, 335, UPDATE | INRANGE | INCONTACT | FIRSTBUTTON, pressure=800, label="tip_move")
            pump()
        injector.inject(410, 335, UP | INRANGE, pressure=0, label="tip_up")
        pump(100)
        require("tip_remains_writable", len(window.scene.document.strokes) == 2
                and window.scene.undo_stack.count() == 2, {"strokes": len(window.scene.document.strokes), "undo": window.scene.undo_stack.count()})
        tip = window.scene.document.strokes[-1]
        require("tip_uses_qt_pressure_path", tip.brush.pressure_enabled and len(tip.samples) >= 2,
                {"pressure_enabled": tip.brush.pressure_enabled, "samples": len(tip.samples)})

        diagnostics = canvas.input_diagnostics.snapshot()
        events = diagnostics["events"]
        require("native_up_diagnostic", any(event.get("reason") == "native_up" for event in events))
        require("one_native_generation", window.native_tail_eraser._generation == 1)
        observed = [row for row in report["native_messages"] if row.get("history")]
        require("real_get_pointer_history", bool(observed), {"successful_calls": len(observed)})
        report["maximum_history_count"] = max((len(row["history"]) for row in observed), default=0)
        report["observed_pen_flags"] = sorted({sample["pen_flags"] for row in observed for sample in row["history"]})
        report["observed_pointer_flags"] = sorted({sample["flags"] for row in observed for sample in row["history"]})
        require("flags_changes_reached_windows", INVERTED in report["observed_pen_flags"]
                and INVERTED | ERASER in report["observed_pen_flags"])
        require("no_native_data_loss", not any(event.get("reason") in
                {"native_data_lost", "native_identity_changed", "native_history_up", "native_contact_lost"}
                for event in events))
        report["status"] = "passed"
    except Unavailable as exc:
        report["status"], report["error"] = "unavailable", str(exc)
    except TargetLost as exc:
        report["status"], report["error"] = "aborted_target_lost", str(exc)
    except Exception as exc:
        report["status"], report["error"] = "failed", repr(exc)
        report["traceback"] = traceback.format_exc()
    finally:
        if injector is not None:
            injector.close()
        if window is not None:
            pump(30, check_errors=False)
            observed = [row for row in report["native_messages"] if row.get("history")]
            report["get_pointer_history_successful_calls"] = len(observed)
            report["maximum_history_count"] = max((len(row["history"]) for row in observed), default=0)
            report["observed_pen_flags"] = sorted({sample["pen_flags"] for row in observed for sample in row["history"]})
            report["observed_pointer_flags"] = sorted({sample["flags"] for row in observed for sample in row["history"]})
            report["final_generation"] = window.native_tail_eraser._generation
            report["final_undo_count"] = window.scene.undo_stack.count()
            report["final_stroke_count"] = len(window.scene.document.strokes)
            diagnostics_path = run_dir / "input-diagnostics.json"
            window.canvas.input_diagnostics.export_json(diagnostics_path)
            report["diagnostics_path"] = str(diagnostics_path)
            window._settings_timer.stop()
            window.close()
            pump(20, check_errors=False)
            window.deleteLater()
        if observer is not None:
            app.removeNativeEventFilter(observer)
        report["finished_utc"] = datetime.now(timezone.utc).isoformat()
        report_path = output / "report.json"
        temporary = output / "report.tmp.json"
        temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(report_path)
        (run_dir / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"status": report["status"], "report": str(report_path),
                          "error": report.get("error"), "checks": report["checks"]}, ensure_ascii=False))
    return 0 if report["status"] == "passed" else 2


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts" / "native-tail-injection")
    parser.add_argument("--register-test-touch-window", action="store_true",
                        help="Register only the disposable HWND for touch when this PC lacks a physical digitizer")
    options = parser.parse_args()
    raise SystemExit(run(options.output.resolve(), options.register_test_touch_window))

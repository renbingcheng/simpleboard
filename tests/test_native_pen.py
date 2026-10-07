"""Native contact boundaries, bounded Win32 history and DPI mapping, without hardware.

These are adapter tests with an injected Win32 API, not Surface driver validation.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
from dataclasses import replace
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import unittest
from unittest.mock import patch

from PySide6.QtCore import QPointF
from PySide6.QtWidgets import QApplication, QWidget

from whiteboard.input_diagnostics import InputDiagnostics
from whiteboard.native_pen import (
    MAX_HISTORY, NativeInputError, NativeTailEraser, PenSample, WindowsPointerApi,
    POINT, POINTER_INFO, POINTER_PEN_INFO, PT_PEN,
    PEN_FLAG_ERASER, PEN_FLAG_INVERTED, PEN_MASK_PRESSURE, PEN_MASK_TILT_X,
    PEN_MASK_TILT_Y, POINTER_FLAG_CANCELED, POINTER_FLAG_DOWN,
    POINTER_FLAG_INCONTACT, POINTER_FLAG_UP,
    WM_POINTERCAPTURECHANGED, WM_POINTERDOWN, WM_POINTERENTER,
    WM_POINTERLEAVE, WM_POINTERUPDATE, WM_POINTERUP,
)


APP = QApplication.instance() or QApplication([])


class FakeWindow(QWidget):
    def devicePixelRatioF(self):
        return 2.0


class FakeCanvas(QWidget):
    def __init__(self, parent):
        super().__init__(parent)
        self.native_tail_active = False
        self.native_tail_claimed = False
        self.calls = []
        self.cleanup_calls = []
        self.pen_near = False
        self.other_source_active = False
        self.input_diagnostics = InputDiagnostics()
        self.input_boundary_generation = 0
        self.native_candidate = False
        self.qt_guarded = False

    def set_native_tail_qt_guard(self, guarded):
        self.qt_guarded = bool(guarded)

    def set_native_contact_candidate(self, eligible):
        self.native_candidate = bool(eligible)

    def clear_native_contact_candidate(self, reason, endpoint=None):
        self.native_candidate = False

    def observe_native_hover(self, *args):
        pass

    def adopt_native_tail(self, *args):
        self.begin_native_tail(*args)

    def set_native_tail_claimed(self, claimed):
        self.native_tail_claimed = bool(claimed)

    def begin_native_tail(self, position, pressure, timestamp, tilt_x, tilt_y):
        if not self.native_tail_claimed:
            raise AssertionError("native stream must be claimed before Canvas begins")
        self.native_tail_active = True
        self.pen_near = True
        self.calls.append(("begin", QPointF(position), pressure, timestamp, tilt_x, tilt_y))

    def move_native_tail(self, position, pressure, timestamp, tilt_x, tilt_y):
        if not self.native_tail_active:
            raise AssertionError("move must not resume a finished Canvas interaction")
        self.calls.append(("move", QPointF(position), pressure, timestamp, tilt_x, tilt_y))

    def end_native_tail(self, reason):
        # Mirror Canvas's contract: cleanup after external finish is safe;
        # another active input source is not ended by native-tail cleanup.
        self.cleanup_calls.append(reason)
        if self.native_tail_active:
            self.calls.append(("end", reason))
        self.native_tail_active = False
        if not self.other_source_active:
            self.pen_near = False


class FakeApi:
    # A monitor to the left of the primary screen, with a nonzero top offset.
    origin = (-1600, 300)

    def __init__(self):
        self.current = None
        self.history = ()
        self.kind = PT_PEN
        self.info_error = None
        self.history_error = None
        self.history_calls = 0
        self.info_calls = 0

    def pointer_type(self, pointer_id):
        return self.kind

    def pen_info(self, pointer_id):
        self.info_calls += 1
        if self.info_error:
            raise self.info_error
        return self.current

    def pen_history(self, pointer_id, count):
        self.history_calls += 1
        if self.history_error:
            raise self.history_error
        return self.history

    def screen_to_client(self, hwnd, x, y):
        return x - self.origin[0], y - self.origin[1]


class NativeTailEraserTests(unittest.TestCase):
    def setUp(self):
        self.window = FakeWindow()
        self.window.resize(640, 420)
        self.canvas = FakeCanvas(self.window)
        self.canvas.setGeometry(40, 60, 550, 320)
        self.window.show()
        APP.processEvents()
        self.hwnd = int(self.window.winId())
        self.api = FakeApi()
        self.adapter = NativeTailEraser(self.window, self.canvas, self.api)

    def tearDown(self):
        self.window.close()
        self.window.deleteLater()
        APP.processEvents()

    def sample(self, x=100, y=100, timestamp=10, **changes):
        # x/y are logical window coordinates; Win32 supplies global pixels.
        value = PenSample(
            pointer_id=7, source_device=0xAB1234, target_hwnd=self.hwnd,
            flags=POINTER_FLAG_INCONTACT, pen_flags=PEN_FLAG_INVERTED,
            x=self.api.origin[0] + x * 2,
            y=self.api.origin[1] + y * 2, timestamp=timestamp,
            frame_id=timestamp, pen_mask=PEN_MASK_PRESSURE | PEN_MASK_TILT_X | PEN_MASK_TILT_Y,
            pressure=512, tilt_x=12, tilt_y=-8,
        )
        return replace(value, **changes)

    def message(self, kind, pointer_id=7, **changes):
        # Use the actual Python Win32 ABI: MSG exposes hWnd, not hwnd.
        # A namespace hid this integration error despite passing unit tests.
        values = dict(hWnd=self.hwnd, message=kind, wParam=pointer_id, time=99999)
        if "hwnd" in changes:
            changes["hWnd"] = changes.pop("hwnd")
        values.update(changes)
        msg = wintypes.MSG()
        for name, value in values.items():
            setattr(msg, name, value)
        return msg

    def send(self, kind, sample=None, history=None, **message_changes):
        if sample is not None:
            self.api.current = sample
        if history is not None:
            self.api.history = tuple(history)
        return self.adapter.handle_message(self.message(kind, **message_changes))

    def start(self, **changes):
        self.assertTrue(self.send(WM_POINTERDOWN, self.sample(**changes)))
        self.assertTrue(self.canvas.native_tail_active)
        self.assertTrue(self.canvas.native_tail_claimed)

    def move_positions(self):
        return [(c[1].x(), c[1].y()) for c in self.canvas.calls if c[0] == "move"]

    def reasons(self):
        return [c[1] for c in self.canvas.calls if c[0] == "end"]

    def test_claim_requires_tail_contact_bare_canvas_and_own_hwnd(self):
        variants = (
            self.sample(pen_flags=0),
            self.sample(flags=0),
            self.sample(flags=POINTER_FLAG_INCONTACT | POINTER_FLAG_CANCELED),
            self.sample(flags=POINTER_FLAG_INCONTACT | POINTER_FLAG_UP),
            self.sample(pointer_type=2),
            self.sample(pointer_id=8),
            self.sample(target_hwnd=self.hwnd + 1),
            self.sample(x=5, y=5),
        )
        for sample in variants:
            with self.subTest(sample=sample):
                self.assertFalse(self.send(WM_POINTERDOWN, sample))
        self.assertFalse(self.send(WM_POINTERDOWN, self.sample(), hwnd=self.hwnd + 1))
        if ctypes.sizeof(ctypes.c_void_p) == 8:
            wide_hwnd = 0x123456789
            native = self.message(WM_POINTERDOWN, hwnd=wide_hwnd)
            self.assertEqual(native.hWnd, wide_hwnd)
            self.assertFalse(self.adapter.handle_message(native))
        self.api.kind = 2
        self.assertFalse(self.send(WM_POINTERDOWN, self.sample()))
        self.api.kind = PT_PEN
        overlay = QWidget(self.window)
        overlay.setGeometry(90, 90, 60, 60)
        overlay.show()
        overlay.raise_()
        APP.processEvents()
        self.assertFalse(self.send(WM_POINTERDOWN, self.sample()))
        overlay.hide()
        self.canvas.setEnabled(False)
        self.assertFalse(self.send(WM_POINTERDOWN, self.sample()))
        self.assertEqual(self.canvas.calls, [])

    def test_native_claim_maps_client_pixels_before_dpr_and_respects_masks(self):
        # Exercise a full-width HWND through both MSG and POINTER_INFO, not
        # merely the usual small handles Windows happens to allocate today.
        wide_hwnd = 0x123456789 if ctypes.sizeof(ctypes.c_void_p) == 8 else self.hwnd
        self.hwnd = wide_hwnd
        with patch.object(self.window, "winId", return_value=wide_hwnd):
            self.start(pen_flags=PEN_FLAG_ERASER, pressure=0)
            self.assertTrue(self.send(WM_POINTERUPDATE, self.sample(x=125, timestamp=11,
                                                                  pen_flags=0, pen_mask=0)))
        self.assertTrue(self.adapter.enabled)
        first = self.canvas.calls[0]
        self.assertEqual(first, ("begin", QPointF(60, 40), 0.0, 10, 12, -8))
        # Pressure zero does not imply a release, nor do pen flags changing to tip.
        self.assertEqual(self.canvas.calls[-1], ("move", QPointF(85, 40), 0.0, 11, 0, 0))
        self.assertEqual(self.reasons(), [])

    def test_history_is_oldest_first_with_independent_sample_timestamps(self):
        self.start()
        oldest = self.sample(x=110, timestamp=11)
        middle = self.sample(x=120, timestamp=12, pen_flags=0, pressure=0)
        latest = self.sample(x=130, timestamp=13, history_count=3)
        self.assertTrue(self.send(WM_POINTERUPDATE, latest, [latest, middle, oldest]))
        self.assertEqual(self.move_positions(), [(70, 40), (80, 40), (90, 40)])
        self.assertEqual([c[3] for c in self.canvas.calls if c[0] == "move"], [11, 12, 13])
        self.assertEqual(self.api.history_calls, 1)

    def test_current_up_keeps_endpoint_and_next_same_id_and_time_is_new_contact(self):
        self.start()
        final = self.sample(x=150, timestamp=10, flags=POINTER_FLAG_UP)
        self.assertTrue(self.send(WM_POINTERUP, final))
        self.assertEqual(self.move_positions(), [(110, 40)])
        self.assertEqual(self.reasons(), ["native_up"])
        self.assertIsNone(self.adapter.active_pointer_id)
        self.start(x=300, timestamp=10)
        self.assertEqual(self.canvas.calls[-1][1], QPointF(260, 40))
        self.assertEqual(sum(c[0] == "begin" for c in self.canvas.calls), 2)
        self.assertEqual(self.adapter._generation, 2)

    def test_up_reads_continuous_history_but_not_an_earlier_historical_up(self):
        self.start()
        oldest = self.sample(x=110, timestamp=11)
        latest = self.sample(x=130, timestamp=13, flags=POINTER_FLAG_UP, history_count=2)
        self.send(WM_POINTERUP, latest, [latest, oldest])
        self.assertEqual(self.move_positions(), [(70, 40), (90, 40)])
        self.start(x=200)
        historical_up = self.sample(x=210, timestamp=11, flags=POINTER_FLAG_UP)
        newest = self.sample(x=250, timestamp=14, flags=POINTER_FLAG_UP, history_count=3)
        self.send(WM_POINTERUP, newest, [newest, self.sample(x=240), historical_up])
        self.assertEqual(self.move_positions(), [(70, 40), (90, 40)])
        self.assertEqual(self.reasons(), ["native_up", "native_history_up"])
        self.assertIsNone(self.adapter.active_pointer_id)

    def test_history_hover_ends_continuity_and_updates_never_restart(self):
        self.start()
        contact = self.sample(x=110, timestamp=11)
        hover = self.sample(x=200, timestamp=12, flags=0)
        newest = self.sample(x=300, timestamp=13, history_count=3)
        self.send(WM_POINTERUPDATE, newest, [newest, hover, contact])
        self.assertEqual(self.move_positions(), [(70, 40)])
        self.assertEqual(self.reasons(), ["native_contact_lost"])
        self.assertTrue(self.send(WM_POINTERUPDATE, self.sample(x=400)))
        self.assertFalse(self.canvas.native_tail_active)
        self.send(WM_POINTERUP, self.sample(x=450, flags=POINTER_FLAG_UP))
        self.assertEqual(self.move_positions(), [(70, 40)])
        self.start(x=500)

    def test_cancellation_drains_updates_but_explicit_down_starts_new_contact(self):
        self.start()
        self.send(WM_POINTERUPDATE, self.sample(x=300, flags=POINTER_FLAG_CANCELED))
        self.assertEqual(self.reasons(), ["native_cancel"])
        self.assertEqual(self.move_positions(), [])
        self.assertEqual(self.adapter.active_pointer_id, 7)
        self.assertTrue(self.send(WM_POINTERUPDATE, self.sample(x=400)))
        self.assertFalse(self.canvas.native_tail_active)
        self.start(x=450)
        self.assertEqual(self.canvas.calls[-1][1], QPointF(410, 40))
        self.assertEqual(self.move_positions(), [])

    def test_new_native_down_never_bridges_missing_up_or_infers_from_buttons(self):
        self.start()
        self.send(WM_POINTERUPDATE, self.sample(x=120, button_change=4))
        self.assertTrue(self.canvas.native_tail_active)
        self.start(x=400, flags=POINTER_FLAG_INCONTACT | POINTER_FLAG_DOWN)
        self.assertEqual(self.reasons(), ["native_new_down"])
        self.assertEqual(self.move_positions(), [(80, 40)])
        self.assertEqual([c[1] for c in self.canvas.calls if c[0] == "begin"],
                         [QPointF(60, 40), QPointF(360, 40)])

    def test_new_tip_down_ends_old_claim_and_is_left_to_qt(self):
        self.start()
        self.assertFalse(self.send(WM_POINTERDOWN, self.sample(x=300, pen_flags=0)))
        self.assertEqual(self.reasons(), ["native_new_down"])
        self.assertIsNone(self.adapter.active_pointer_id)
        self.assertFalse(self.canvas.native_tail_claimed)
        self.assertFalse(self.canvas.native_tail_active)

    def test_external_finish_drains_without_ending_an_unrelated_canvas_source(self):
        self.start()
        self.canvas.native_tail_active = False  # Toolbar/save/focus already finished.
        self.assertTrue(self.send(WM_POINTERUPDATE, self.sample(x=400)))
        self.assertTrue(self.canvas.native_tail_claimed)
        self.assertEqual(self.reasons(), [])
        self.assertEqual(self.move_positions(), [])
        self.api.info_error = NativeInputError("expired native data")
        self.assertTrue(self.send(WM_POINTERUP))
        self.assertIsNone(self.adapter.active_pointer_id)
        self.assertFalse(self.canvas.native_tail_claimed)
        self.assertFalse(self.canvas.pen_near)
        self.assertEqual(self.canvas.cleanup_calls, ["native_up"])
        self.api.info_error = None
        self.start(x=450)

    def test_external_cancel_then_new_pointer_down_retires_stale_drain_without_old_up(self):
        self.start()
        self.canvas.native_tail_active = False
        self.assertTrue(self.send(WM_POINTERDOWN, self.sample(x=400, pointer_id=55), pointer_id=55))
        self.assertEqual(self.adapter.active_pointer_id, 55)
        self.assertEqual(self.canvas.calls[-1][1], QPointF(360, 40))
        self.assertEqual(self.move_positions(), [])
        self.assertEqual(self.canvas.cleanup_calls, ["native_new_pointer_down"])

    def test_other_pointer_cannot_steal_active_contact_and_cleanup_cannot_end_other_source(self):
        self.start()
        self.assertFalse(self.send(WM_POINTERDOWN, self.sample(pointer_id=55), pointer_id=55))
        self.assertEqual(self.adapter.active_pointer_id, 7)
        self.assertEqual(self.canvas.cleanup_calls, [])
        self.canvas.native_tail_active = False
        self.canvas.other_source_active = True
        self.assertTrue(self.send(WM_POINTERUP, self.sample(flags=POINTER_FLAG_UP)))
        self.assertTrue(self.canvas.other_source_active)
        self.assertEqual(self.reasons(), [])

    def test_capture_and_leave_are_hard_boundaries_without_reading_stale_info(self):
        for kind, reason in ((WM_POINTERCAPTURECHANGED, "native_capture_lost"),
                             (WM_POINTERLEAVE, "native_leave")):
            with self.subTest(kind=kind):
                self.start()
                calls = self.api.info_calls
                self.api.info_error = NativeInputError("capture info is stale")
                self.assertFalse(self.send(kind, pointer_id=99))
                self.assertTrue(self.canvas.native_tail_active)
                self.assertTrue(self.send(kind))
                self.assertEqual(self.api.info_calls, calls)
                self.assertEqual(self.reasons()[-1], reason)
                self.assertIsNone(self.adapter.active_pointer_id)
                self.api.info_error = None

    def test_bounded_or_failed_history_uses_valid_latest_contact_and_keeps_erasing(self):
        for count, history, error in ((MAX_HISTORY + 1, (), None),
                                      (0, (), None), (2, (), None),
                                      (2, (), NativeInputError("history gone"))):
            with self.subTest(count=count, error=error):
                self.start()
                before = self.api.history_calls
                self.api.history_error = error
                self.assertTrue(self.send(WM_POINTERUPDATE,
                                          self.sample(x=400, history_count=count), history))
                self.assertTrue(self.canvas.native_tail_active)
                self.assertEqual(self.canvas.calls[-1][1], QPointF(360, 40))
                if count not in range(1, MAX_HISTORY + 1):
                    self.assertEqual(self.api.history_calls, before)
                self.api.history_error = None
                self.assertTrue(self.send(WM_POINTERUPDATE, self.sample(x=450)))
                self.assertTrue(self.canvas.native_tail_active)
                self.assertEqual(self.canvas.calls[-1][1], QPointF(410, 40))
                self.send(WM_POINTERUP, self.sample(x=450, flags=POINTER_FLAG_UP))

    def test_native_read_error_after_claim_is_consumed_until_boundary(self):
        self.start()
        self.api.info_error = NativeInputError("pointer data unavailable")
        self.assertTrue(self.send(WM_POINTERUPDATE))
        self.assertEqual(self.reasons(), ["native_data_lost"])
        self.assertTrue(self.send(WM_POINTERUPDATE))
        self.assertTrue(self.send(WM_POINTERUP))
        self.assertIsNone(self.adapter.active_pointer_id)

    def test_identity_changes_do_not_draw_or_cancel_another_pointer(self):
        for changes in (dict(pointer_id=8), dict(source_device=33),
                        dict(target_hwnd=self.hwnd + 1), dict(pointer_type=2)):
            with self.subTest(changes=changes):
                self.start()
                self.assertFalse(self.send(WM_POINTERUPDATE, self.sample(), pointer_id=99))
                self.assertTrue(self.canvas.native_tail_active)
                self.send(WM_POINTERUPDATE, self.sample(x=400, **changes))
                self.assertEqual(self.reasons()[-1], "native_identity_changed")
                self.assertEqual(self.move_positions(), [])
                self.send(WM_POINTERUP)

    def test_canceled_up_and_hover_in_up_history_never_add_a_distant_endpoint(self):
        self.start()
        self.send(WM_POINTERUP, self.sample(x=500, flags=POINTER_FLAG_UP | POINTER_FLAG_CANCELED))
        self.assertEqual(self.move_positions(), [])
        self.assertIsNone(self.adapter.active_pointer_id)
        self.start()
        final = self.sample(x=500, flags=POINTER_FLAG_UP, history_count=2)
        self.send(WM_POINTERUP, final, [final, self.sample(x=300, flags=0)])
        self.assertEqual(self.move_positions(), [])
        self.assertIsNone(self.adapter.active_pointer_id)

    def test_opt_in_raw_diagnostics_include_scalar_identity_flags_and_generation(self):
        self.start()
        self.assertEqual(self.canvas.input_diagnostics.count, 0)
        self.canvas.input_diagnostics.set_enabled(True)
        sample = self.sample(x=123, timestamp=42, performance_count=999, button_change=4)
        self.send(WM_POINTERUPDATE, sample)
        events = self.canvas.input_diagnostics.snapshot()["events"]
        state = [e for e in events if e["reason"] == "native_pen_sample"][-1]["state"]
        self.assertEqual(state["sample_time"], 42)
        self.assertEqual(state["msg_time"], 99999)
        self.assertEqual(state["source_device"], 0xAB1234)
        self.assertEqual(state["pointer_id"], 7)
        self.assertEqual(state["pointer_flags"], POINTER_FLAG_INCONTACT)
        self.assertEqual(state["performance_count"], 999)
        self.assertEqual(state["button_change"], 4)
        self.assertEqual(state["generation"], 1)

    def test_unrelated_messages_and_offscreen_default_do_not_initialize_native_api(self):
        self.assertFalse(self.send(0x0010, self.sample()))
        self.assertFalse(self.send(WM_POINTERENTER, self.sample()))
        with patch("whiteboard.native_pen.WindowsPointerApi") as native:
            adapter = NativeTailEraser(self.window, self.canvas)
            if APP.platformName() != "windows":
                self.assertFalse(adapter.enabled)
                native.assert_not_called()
                self.assertFalse(adapter.handle_message(self.message(WM_POINTERDOWN)))


class NativeApiLayoutTests(unittest.TestCase):
    def test_sdk_structures_have_fixed_width_members_and_x64_alignment(self):
        self.assertEqual(ctypes.sizeof(POINT), 8)
        if ctypes.sizeof(ctypes.c_void_p) == 8:
            self.assertEqual(ctypes.sizeof(POINTER_INFO), 96)
            self.assertEqual(ctypes.sizeof(POINTER_PEN_INFO), 120)
            self.assertEqual(POINTER_INFO.sourceDevice.offset, 16)
            self.assertEqual(POINTER_INFO.ptPixelLocation.offset, 32)
            self.assertEqual(POINTER_INFO.PerformanceCount.offset, 80)
            self.assertEqual(POINTER_INFO.ButtonChangeType.offset, 88)
            self.assertEqual(POINTER_PEN_INFO.penFlags.offset, 96)
            self.assertEqual(POINTER_PEN_INFO.tiltY.offset, 116)

    def test_native_values_are_detached_and_history_buffer_remains_newest_first(self):
        saved_buffers = []

        def history(pointer_id, count_pointer, buffer):
            self.assertEqual(pointer_id, 7)
            self.assertEqual(ctypes.cast(count_pointer, ctypes.POINTER(ctypes.c_uint32))[0], 3)
            for index, timestamp in enumerate((30, 20, 10)):
                buffer[index].pointerInfo.pointerId = 7
                buffer[index].pointerInfo.pointerType = PT_PEN
                buffer[index].pointerInfo.sourceDevice = 0x123456789
                buffer[index].pointerInfo.dwTime = timestamp
                buffer[index].pointerInfo.ptPixelLocation.x = -1000 + index
                buffer[index].pointerInfo.historyCount = 3
                buffer[index].tiltY = -20
            saved_buffers.append(buffer)
            return 1

        api = WindowsPointerApi.__new__(WindowsPointerApi)
        api._history = history
        values = api.pen_history(7, 3)
        self.assertEqual([v.timestamp for v in values], [30, 20, 10])
        self.assertEqual(values[0].source_device, 0x123456789)
        self.assertEqual(values[0].x, -1000)
        self.assertEqual(values[0].tilt_y, -20)
        saved_buffers[0][0].pointerInfo.dwTime = 999
        self.assertEqual(values[0].timestamp, 30)
        with self.assertRaises(NativeInputError):
            api.pen_history(7, MAX_HISTORY + 1)

    def test_native_history_refuses_a_partial_changed_count(self):
        def history(pointer_id, count_pointer, buffer):
            ctypes.cast(count_pointer, ctypes.POINTER(ctypes.c_uint32))[0] = 1
            return 1

        api = WindowsPointerApi.__new__(WindowsPointerApi)
        api._history = history
        with self.assertRaises(NativeInputError):
            api.pen_history(7, 2)


if __name__ == "__main__":
    unittest.main()

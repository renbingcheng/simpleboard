"""Real-window eraser recovery across mixed native and Qt event delivery.

Injected Win32 samples verify application behavior, not Surface driver behavior.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QFocusEvent, QInputDevice, QPointingDevice, QTabletEvent
from PySide6.QtWidgets import QApplication

from whiteboard.geometry import visible_path
from whiteboard.models import Brush, InkSample, Stroke
from whiteboard.native_pen import (
    MAX_HISTORY, NativeInputError, NativeTailEraser, PenSample, PT_PEN,
    PEN_FLAG_ERASER, PEN_FLAG_INVERTED, PEN_MASK_PRESSURE,
    POINTER_FLAG_CANCELED, POINTER_FLAG_DOWN, POINTER_FLAG_INCONTACT, POINTER_FLAG_INRANGE, POINTER_FLAG_UP,
    WM_POINTERDOWN, WM_POINTERUPDATE, WM_POINTERUP,
)
from whiteboard.settings import AppSettings
from whiteboard.storage import document_to_dict, load_document, save_document
from whiteboard.window import MainWindow


APP = QApplication.instance() or QApplication([])


class FakeApi:
    """A pen on a monitor above and left of the primary desktop origin."""
    origin = (-1400, -900)

    def __init__(self):
        self.current = None
        self.history = ()
        self.info_error = None
        self.history_error = None

    def pointer_type(self, pointer_id):
        return PT_PEN

    def pen_info(self, pointer_id):
        if self.info_error is not None:
            raise self.info_error
        return self.current

    def pen_history(self, pointer_id, count):
        if self.history_error is not None:
            raise self.history_error
        return self.history

    def screen_to_client(self, hwnd, x, y):
        return x - self.origin[0], y - self.origin[1]


class TailRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="simpleboard-tail-recovery-")
        self.directory = Path(self.temporary.name)
        self.window = MainWindow(recovery_enabled=False, settings=AppSettings(self.directory / "settings.json"))
        self.window._startup_done = True
        self.window.resize(1000, 700)
        self.window.show()
        APP.processEvents()
        self.canvas = self.window.canvas
        self.scene = self.window.scene
        self.canvas.set_eraser(10, False)
        self.dpr = 1.0
        self.dpr_patch = patch.object(self.window, "devicePixelRatioF", side_effect=lambda: self.dpr)
        self.dpr_patch.start()
        self.api = FakeApi()
        self.adapter = NativeTailEraser(self.window, self.canvas, api=self.api)
        self.window.native_tail_eraser = self.adapter
        self.tick = 100
        self.pen = QPointingDevice("recovery tip", 9201, QInputDevice.DeviceType.Stylus,
                                   QPointingDevice.PointerType.Pen,
                                   QInputDevice.Capability.Position | QInputDevice.Capability.Pressure, 1, 3)
        self.tail = QPointingDevice("recovery tail", 9202, QInputDevice.DeviceType.Stylus,
                                    QPointingDevice.PointerType.Eraser,
                                    QInputDevice.Capability.Position | QInputDevice.Capability.Pressure, 1, 3)

    def tearDown(self):
        self.canvas.cancel_input("test_cleanup")
        self.canvas._grid_timer.stop()
        self.canvas._grid_fade_timer.stop()
        self.window._settings_timer.stop()
        self.window._closing = True
        APP.removeEventFilter(self.canvas)
        self.window.close()
        self.window.deleteLater()
        APP.processEvents()
        self.dpr_patch.stop()
        self.temporary.cleanup()

    def band(self):
        stroke = Stroke([InkSample(x, 240) for x in range(80, 881, 10)],
                        Brush(color="#2563EB", width=40, pressure_enabled=False))
        self.scene.add_stroke(stroke)
        self.stroke_id = stroke.id
        self.original = document_to_dict(self.scene.document)
        self.baseline = self.scene.undo_stack.count()

    def native(self, kind, position, *, pen_flags=PEN_FLAG_INVERTED,
               flags=None, pointer_id=7, source_device=0x1234, history_count=1, history=()):
        self.tick += 1
        in_window = self.canvas.mapTo(self.window, QPointF(*position))
        x = self.api.origin[0] + round(in_window.x() * self.dpr)
        y = self.api.origin[1] + round(in_window.y() * self.dpr)
        if flags is None:
            flags = POINTER_FLAG_INRANGE | (POINTER_FLAG_UP if kind == WM_POINTERUP else POINTER_FLAG_INCONTACT)
            if kind == WM_POINTERDOWN:
                flags |= POINTER_FLAG_DOWN
        self.api.current = PenSample(pointer_id=pointer_id, source_device=source_device,
                                     target_hwnd=int(self.window.winId()), flags=flags, pen_flags=pen_flags,
                                     x=x, y=y, timestamp=self.tick, frame_id=self.tick,
                                     history_count=history_count, pen_mask=PEN_MASK_PRESSURE, pressure=512)
        self.api.history = tuple(history)
        message = wintypes.MSG()
        message.hWnd = int(self.window.winId())
        message.message = kind
        message.wParam = pointer_id | ((flags & 0xFFFF) << 16)
        message.lParam = ctypes.c_int32((x & 0xFFFF) | ((y & 0xFFFF) << 16)).value
        message.time = self.tick
        # The actual window integration must let Qt see the entire native
        # stream; Canvas guards suppress duplicate translated ink instead.
        with patch("whiteboard.window.sys.platform", "win32"):
            handled, _ = self.window.nativeEvent(b"windows_generic_MSG", ctypes.addressof(message))
        self.assertFalse(handled, "pointer messages must remain available to Qt")

    def qt(self, kind, position, *, tip=False, pressure=.5):
        self.tick += 1
        button = Qt.MouseButton.NoButton if kind == QEvent.Type.TabletMove else Qt.MouseButton.LeftButton
        buttons = Qt.MouseButton.NoButton if kind == QEvent.Type.TabletRelease else Qt.MouseButton.LeftButton
        event = QTabletEvent(kind, self.pen if tip else self.tail,
                             QPointF(*position), QPointF(*position), pressure, 0, 0, 0, 0, 0,
                             Qt.KeyboardModifier.NoModifier, button, buttons)
        event.setTimestamp(self.tick)
        APP.sendEvent(self.canvas, event)

    def assert_band(self, *, erased=(), retained=()):
        stroke = self.scene.get(self.stroke_id)
        path = visible_path(stroke) if stroke is not None else None
        for x in erased:
            self.assertFalse(path is not None and path.contains(QPointF(x, 240)), f"ink remains at {x}")
        for x in retained:
            self.assertTrue(path is not None and path.contains(QPointF(x, 240)), f"ink missing at {x}")

    def assert_one_undo_restores(self):
        self.assertEqual(self.scene.undo_stack.count(), self.baseline + 1)
        erased = document_to_dict(self.scene.document)
        self.scene.undo_stack.undo()
        self.assertEqual(document_to_dict(self.scene.document), self.original)
        self.scene.undo_stack.redo()
        self.assertEqual(document_to_dict(self.scene.document), erased)

    def test_late_tail_flag_adopts_existing_qt_erase_without_splitting_undo(self):
        self.band()
        self.native(WM_POINTERDOWN, (100, 240), pen_flags=0)
        self.qt(QEvent.Type.TabletPress, (100, 240))
        self.qt(QEvent.Type.TabletMove, (180, 240))
        self.assertEqual(self.scene.undo_stack.count(), self.baseline)
        self.native(WM_POINTERUPDATE, (350, 240), pen_flags=PEN_FLAG_ERASER)
        self.assertTrue(self.canvas.native_tail_active)
        self.qt(QEvent.Type.TabletRelease, (400, 300), tip=True)
        self.native(WM_POINTERUPDATE, (550, 240), pen_flags=0)
        self.assertEqual(self.scene.undo_stack.count(), self.baseline)
        self.native(WM_POINTERUP, (600, 240))
        self.qt(QEvent.Type.TabletRelease, (600, 240))
        self.assert_band(erased=range(100, 601, 20), retained=(80, 650, 850))
        self.assert_one_undo_restores()
        target = self.directory / "continuous.qboard"
        save_document(target, self.scene.document)
        self.assertEqual(document_to_dict(load_document(target)), document_to_dict(self.scene.document))

    def test_real_up_separates_contacts_and_preserves_the_unvisited_gap(self):
        self.band()
        self.native(WM_POINTERDOWN, (100, 240))
        self.native(WM_POINTERUPDATE, (180, 240))
        self.native(WM_POINTERUP, (220, 240))
        self.qt(QEvent.Type.TabletPress, (300, 240), tip=True)
        self.qt(QEvent.Type.TabletRelease, (400, 240), tip=True)
        self.native(WM_POINTERDOWN, (520, 240), pen_flags=PEN_FLAG_ERASER)
        self.native(WM_POINTERUPDATE, (600, 240), pen_flags=PEN_FLAG_ERASER)
        self.native(WM_POINTERUP, (650, 240), pen_flags=PEN_FLAG_ERASER)
        self.assertEqual(self.scene.undo_stack.count(), self.baseline + 2)
        self.assert_band(erased=(100, 160, 220, 520, 580, 650), retained=(280, 350, 430, 750))
        self.scene.undo_stack.undo()
        self.assert_band(erased=(160,), retained=(350, 580))
        self.scene.undo_stack.undo()
        self.assertEqual(document_to_dict(self.scene.document), self.original)

    def test_candidate_keeps_qt_erase_across_false_release_before_late_tail_flag(self):
        self.band()
        self.native(WM_POINTERDOWN, (100, 240), pen_flags=0)
        self.qt(QEvent.Type.TabletPress, (100, 240))
        self.qt(QEvent.Type.TabletMove, (180, 240))
        boundary = self.canvas.input_boundary_generation
        self.qt(QEvent.Type.TabletRelease, (220, 240), pressure=0)
        self.assertEqual(self.canvas.input_boundary_generation, boundary)
        self.assertEqual(self.scene.undo_stack.count(), self.baseline)
        self.native(WM_POINTERUPDATE, (400, 240), pen_flags=PEN_FLAG_ERASER)
        self.assertTrue(self.canvas.native_tail_active)
        self.qt(QEvent.Type.TabletPress, (450, 300), tip=True)
        self.native(WM_POINTERUPDATE, (550, 240))
        self.native(WM_POINTERUP, (600, 240))
        self.qt(QEvent.Type.TabletRelease, (600, 240))
        self.assert_band(erased=range(100, 601, 20), retained=(80, 650, 850))
        self.assert_one_undo_restores()

    def test_qt_tail_candidate_without_native_tail_flag_waits_for_real_up(self):
        self.band()
        self.native(WM_POINTERDOWN, (100, 240), pen_flags=0)
        self.qt(QEvent.Type.TabletPress, (100, 240))
        self.native(WM_POINTERUPDATE, (200, 240), pen_flags=0)
        self.qt(QEvent.Type.TabletMove, (200, 240))
        self.qt(QEvent.Type.TabletRelease, (250, 240), pressure=0)
        self.assertEqual(self.scene.undo_stack.count(), self.baseline)
        self.native(WM_POINTERUPDATE, (400, 240), pen_flags=0)
        self.qt(QEvent.Type.TabletMove, (400, 240))
        self.native(WM_POINTERUP, (400, 240), pen_flags=0)
        self.qt(QEvent.Type.TabletRelease, (400, 240))
        self.assert_band(erased=range(100, 401, 20), retained=(80, 500, 850))
        self.assert_one_undo_restores()

    def test_focus_loss_without_old_up_allows_a_new_pointer_tip_to_draw(self):
        self.band()
        self.native(WM_POINTERDOWN, (100, 240))
        self.native(WM_POINTERUPDATE, (180, 240))
        APP.sendEvent(self.canvas, QFocusEvent(QEvent.Type.FocusOut))
        self.assertFalse(self.canvas.native_tail_active)
        self.assertEqual(self.scene.undo_stack.count(), self.baseline + 1)

        # Windows may allocate a new pointer ID after focus loss even when the
        # old contact never supplied UP. Its explicit DOWN must clear the guard.
        self.native(WM_POINTERDOWN, (650, 320), pen_flags=0, pointer_id=55)
        self.assertFalse(self.canvas.native_tail_owned)
        self.assertFalse(self.canvas._native_tail_qt_guard)
        self.qt(QEvent.Type.TabletPress, (650, 320), tip=True)
        self.native(WM_POINTERUPDATE, (750, 320), pen_flags=0, pointer_id=55)
        self.qt(QEvent.Type.TabletMove, (750, 320), tip=True)
        self.native(WM_POINTERUP, (780, 320), pen_flags=0, pointer_id=55)
        self.qt(QEvent.Type.TabletRelease, (780, 320), tip=True)

        added = [stroke for stroke in self.scene.document.strokes if stroke.id != self.stroke_id]
        self.assertEqual(len(added), 1)
        self.assertTrue(visible_path(added[0]).contains(QPointF(700, 320)))
        self.assert_band(erased=(150,), retained=(350, 600))
        self.assertEqual(self.scene.undo_stack.count(), self.baseline + 2)
        self.scene.undo_stack.undo()
        self.assertEqual(len(self.scene.document.strokes), 1)
        self.scene.undo_stack.undo()
        self.assertEqual(document_to_dict(self.scene.document), self.original)

    def test_candidate_native_up_includes_endpoint_before_queued_qt_release(self):
        self.band()
        self.native(WM_POINTERDOWN, (100, 240), pen_flags=0)
        self.qt(QEvent.Type.TabletPress, (100, 240))
        self.native(WM_POINTERUPDATE, (200, 240), pen_flags=0)
        self.qt(QEvent.Type.TabletMove, (200, 240))
        # The final contact position has not appeared in any earlier Move.
        self.native(WM_POINTERUP, (400, 240), pen_flags=0)
        self.qt(QEvent.Type.TabletRelease, (400, 240))

        self.assert_band(erased=range(100, 401, 20), retained=(80, 500, 850))
        self.assert_one_undo_restores()

    def test_candidate_cancel_blocks_queued_qt_press_until_a_new_native_down(self):
        self.band()
        self.native(WM_POINTERDOWN, (100, 240), pen_flags=0)
        self.qt(QEvent.Type.TabletPress, (100, 240))
        self.native(WM_POINTERUPDATE, (200, 240), pen_flags=0)
        self.qt(QEvent.Type.TabletMove, (200, 240))
        self.native(WM_POINTERUPDATE, (220, 240), pen_flags=0,
                    flags=POINTER_FLAG_INCONTACT | POINTER_FLAG_CANCELED)
        canceled = document_to_dict(self.scene.document)
        self.assertTrue(self.canvas._native_tail_qt_guard)

        # A button-derived Qt Press from the canceled contact is not a fresh
        # native DOWN and must not reopen an erase or create another undo item.
        self.qt(QEvent.Type.TabletPress, (500, 240))
        self.qt(QEvent.Type.TabletMove, (700, 240))
        self.native(WM_POINTERUP, (700, 240), pen_flags=0)
        self.qt(QEvent.Type.TabletRelease, (700, 240))
        self.assertEqual(document_to_dict(self.scene.document), canceled)
        self.assert_band(erased=(150,), retained=(300, 500, 600, 700))
        self.assert_one_undo_restores()

    def test_history_failure_and_oversized_history_keep_one_continuous_erase(self):
        self.band()
        self.native(WM_POINTERDOWN, (100, 240))
        self.api.history_error = NativeInputError("simulated ERROR_NO_DATA")
        self.native(WM_POINTERUPDATE, (300, 240), history_count=2)
        self.api.history_error = None
        self.assertTrue(self.canvas.native_tail_active)
        self.native(WM_POINTERUPDATE, (500, 240), history_count=MAX_HISTORY + 1)
        self.assertTrue(self.canvas.native_tail_active)
        self.qt(QEvent.Type.TabletRelease, (800, 300), tip=True)
        self.native(WM_POINTERUPDATE, (700, 240))
        self.native(WM_POINTERUP, (740, 240))
        self.assert_band(erased=range(100, 741, 20), retained=(80, 800))
        self.assert_one_undo_restores()

    def test_message_fallback_uses_signed_screen_coordinates_and_dpi_transform(self):
        self.band()
        self.dpr = 2.0
        self.canvas.set_view(1.5, QPointF(27, -36))
        # World (100, 240)..(520, 240) maps to these canvas positions. The
        # fallback LPARAM has negative desktop X and Y on this fake monitor.
        self.native(WM_POINTERDOWN, (177, 324))
        self.api.info_error = NativeInputError("simulated missing pen data")
        self.native(WM_POINTERUPDATE, (477, 324))
        self.assertTrue(self.canvas.native_tail_active)
        self.native(WM_POINTERUPDATE, (777, 324))
        self.api.info_error = None
        self.native(WM_POINTERUP, (807, 324))
        self.assert_band(erased=range(100, 521, 20), retained=(80, 600, 850))
        # Compare ink rather than view fields, which this test intentionally changed.
        self.assertEqual(self.scene.undo_stack.count(), self.baseline + 1)
        self.scene.undo_stack.undo()
        self.assert_band(retained=range(100, 521, 20))

    def assert_candidate_rejected_after(self, boundary):
        self.band()
        self.native(WM_POINTERDOWN, (100, 240), pen_flags=0)
        boundary()
        self.native(WM_POINTERUPDATE, (450, 240), pen_flags=PEN_FLAG_ERASER)
        self.assertFalse(self.canvas.native_tail_active)
        self.native(WM_POINTERUP, (500, 240), pen_flags=PEN_FLAG_ERASER)
        self.assertEqual(self.scene.undo_stack.count(), self.baseline)
        self.assertEqual(document_to_dict(self.scene.document), self.original)

    def test_candidate_cannot_restart_after_external_cancel(self):
        self.assert_candidate_rejected_after(lambda: self.canvas.cancel_input("external_focus_loss"))

    def test_candidate_cannot_restart_after_external_finish(self):
        self.assert_candidate_rejected_after(self.canvas.finish_interaction)

    def test_candidate_cannot_restart_after_explicit_tool_change(self):
        self.assert_candidate_rejected_after(lambda: self.window._select_tool("highlighter"))

    def test_contact_started_on_toolbar_cannot_be_adopted_after_dragging_into_canvas(self):
        self.band()
        button = self.window.pen_buttons[0]
        point = button.mapTo(self.canvas, button.rect().center())
        self.native(WM_POINTERDOWN, (point.x(), point.y()), pen_flags=0)
        self.native(WM_POINTERUPDATE, (450, 240), pen_flags=PEN_FLAG_ERASER)
        self.assertFalse(self.canvas.native_tail_active)
        self.native(WM_POINTERUP, (500, 240))
        self.assertEqual(document_to_dict(self.scene.document), self.original)

    def test_candidate_cannot_be_adopted_by_a_different_pen_device(self):
        self.band()
        self.native(WM_POINTERDOWN, (100, 240), pen_flags=0)
        self.native(WM_POINTERUPDATE, (450, 240), pen_flags=PEN_FLAG_ERASER, source_device=0x9876)
        self.assertFalse(self.canvas.native_tail_active)
        self.native(WM_POINTERUP, (500, 240), source_device=0x9876)
        self.assertEqual(document_to_dict(self.scene.document), self.original)

    def test_tail_up_guard_ignores_queued_qt_press_until_new_native_tip_contact(self):
        self.band()
        self.native(WM_POINTERDOWN, (100, 240))
        self.native(WM_POINTERUPDATE, (220, 240))
        self.native(WM_POINTERUP, (240, 240))
        erased = document_to_dict(self.scene.document)
        checks = [button.isChecked() for button in self.window.pen_buttons]
        checks += [button.isChecked() for button in self.window.tool_buttons.values()]
        self.qt(QEvent.Type.TabletPress, (400, 240), tip=True)
        self.qt(QEvent.Type.TabletMove, (600, 240))
        self.qt(QEvent.Type.TabletRelease, (600, 240))
        self.assertEqual(document_to_dict(self.scene.document), erased)
        self.assertEqual(self.scene.undo_stack.count(), self.baseline + 1)
        after = [button.isChecked() for button in self.window.pen_buttons]
        after += [button.isChecked() for button in self.window.tool_buttons.values()]
        self.assertEqual(after, checks)

        self.native(WM_POINTERDOWN, (650, 320), pen_flags=0)
        self.qt(QEvent.Type.TabletPress, (650, 320), tip=True)
        self.native(WM_POINTERUPDATE, (750, 320), pen_flags=0)
        self.qt(QEvent.Type.TabletMove, (750, 320), tip=True)
        self.native(WM_POINTERUP, (780, 320), pen_flags=0)
        self.qt(QEvent.Type.TabletRelease, (780, 320), tip=True)
        self.assertEqual(self.scene.undo_stack.count(), self.baseline + 2)
        added = [stroke for stroke in self.scene.document.strokes if stroke.id != self.stroke_id]
        self.assertEqual(len(added), 1)
        self.assertTrue(visible_path(added[0]).contains(QPointF(700, 320)))


if __name__ == "__main__":
    unittest.main()

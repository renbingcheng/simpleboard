"""Window lifecycle regressions: failed/cancelled actions must preserve work."""

from __future__ import annotations

import os
import ctypes
from ctypes import wintypes
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QByteArray, QCoreApplication, QEvent, QPointF, Qt
from PySide6.QtGui import QInputDevice, QPointingDevice, QTabletEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox

from whiteboard.models import BoardDocument, Brush, InkSample, Stroke
from whiteboard.recovery import RecoveryManager
from whiteboard.settings import AppSettings
from whiteboard.storage import DocumentError, load_document, save_document
from whiteboard.window import MainWindow


def click_message_button(caption):
    def execute(box):
        next(button for button in box.buttons() if button.text() == caption).click()
        return 0
    return execute


class WindowLifecycleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="qboard-window-test-")
        self.directory = Path(self.temp.name)
        self.windows = []

    def tearDown(self):
        for window in self.windows:
            window._settings_timer.stop()
            with patch.object(window, "_maybe_leave_document", return_value=True):
                window.close()
            window.deleteLater()
        self.app.processEvents()
        self.temp.cleanup()

    def window(self, *, recovery=False):
        settings = AppSettings(self.directory / "settings.json")
        factory = lambda provider, parent: RecoveryManager(provider, parent, self.directory / "recovery")
        with patch("whiteboard.window.RecoveryManager", side_effect=factory):
            window = MainWindow(recovery_enabled=recovery, settings=settings)
        window._startup_done = True
        self.windows.append(window)
        return window

    def ink(self, window, x=80):
        stroke = Stroke([InkSample(x, 80, 0, 0.2), InkSample(x + 50, 110, 0.1, 0.9)], Brush())
        window.scene.add_stroke(stroke)
        return stroke.id

    def test_save_keeps_editable_document_and_marks_clean(self):
        window = self.window()
        self.ink(window)
        self.assertTrue(window.is_dirty)
        target = self.directory / "lesson.qboard"
        self.assertTrue(window._save_path(target))
        self.assertFalse(window.is_dirty)
        self.assertEqual(window.current_path, target.resolve())
        self.assertEqual(len(load_document(target).strokes), 1)
        self.assertEqual(Path(window.settings.recent_files[0]), target.resolve())

    def test_invalid_open_never_prompts_to_discard_dirty_document(self):
        window = self.window()
        identifier = self.ink(window)
        target = self.directory / "broken.qboard"
        target.write_bytes(b"broken archive")
        with patch.object(window, "_show_error") as error, patch.object(window, "_maybe_leave_document") as leave:
            self.assertFalse(window._load_path(target))
            error.assert_called_once()
            leave.assert_not_called()
        self.assertTrue(window.is_dirty)
        self.assertEqual(window.scene.document.strokes[0].id, identifier)

    def test_failed_save_preserves_dirty_state_and_path(self):
        window = self.window()
        self.ink(window)
        with patch("whiteboard.window.save_document", side_effect=DocumentError("disk full")), patch.object(window, "_show_error"):
            self.assertFalse(window._save_path(self.directory / "lesson.qboard"))
        self.assertTrue(window.is_dirty)
        self.assertIsNone(window.current_path)
        self.assertEqual(len(window.scene.document.strokes), 1)

    def test_cancelled_new_preserves_current_ink(self):
        window = self.window()
        identifier = self.ink(window)
        with patch.object(QMessageBox, "exec", click_message_button("取消")):
            self.assertFalse(window.new_document())
        self.assertTrue(window.is_dirty)
        self.assertEqual(window.scene.document.strokes[0].id, identifier)

    def test_cancelled_save_as_preserves_dirty_state(self):
        window = self.window()
        self.ink(window)
        with patch("whiteboard.window.QFileDialog.getSaveFileName", return_value=("", "")):
            self.assertFalse(window.save_document())
        self.assertTrue(window.is_dirty)
        self.assertIsNone(window.current_path)

    def test_reopening_same_file_after_save_uses_new_content(self):
        window = self.window()
        target = self.directory / "lesson.qboard"
        self.ink(window)
        window._save_path(target)
        self.ink(window, 200)
        with patch.object(QMessageBox, "exec", click_message_button("保存")):
            self.assertTrue(window._load_path(target))
        self.assertEqual(len(window.scene.document.strokes), 2)
        self.assertFalse(window.is_dirty)

    def test_cancelled_recovery_survives_window_close(self):
        recovery_path = self.directory / "recovery" / "recovery.qboard"
        recovery_path.parent.mkdir()
        save_document(recovery_path, BoardDocument(strokes=[Stroke([InkSample(4, 8)], Brush())]))
        original = recovery_path.read_bytes()
        window = self.window(recovery=True)
        with patch.object(QMessageBox, "exec", click_message_button("稍后处理")):
            self.assertEqual(window._recovery_prompt(), "cancel")
        self.assertTrue(window._recovery_blocked)
        self.assertTrue(window.close())
        self.assertEqual(recovery_path.read_bytes(), original)

    def test_recovery_autosave_does_not_mark_document_clean(self):
        window = self.window(recovery=True)
        self.ink(window)
        self.assertTrue(window.recovery.flush())
        self.app.processEvents()
        self.assertTrue(window.is_dirty)
        self.assertTrue(window.recovery.available())

    def test_live_ink_is_dirty_and_recoverable_before_pen_lift(self):
        window = self.window(recovery=True)
        window.canvas._begin(QPointF(50, 100), "pen", "pen", 0.3, 1)
        window.canvas._move(QPointF(120, 115), 0.8, 2)
        self.assertTrue(window.is_dirty)
        self.assertEqual(window.scene.document.strokes, [])
        self.assertEqual(window.scene.undo_stack.count(), 0)
        self.assertTrue(window.recovery.flush())
        self.assertTrue(window.recovery.available())
        captured = load_document(window.recovery.path)
        self.assertEqual(len(captured.strokes), 1)
        self.assertGreaterEqual(len(captured.strokes[0].samples), 2)
        self.assertEqual(window.scene.document.strokes, [])
        self.assertTrue(window.is_dirty)
        target = self.directory / "live.qboard"
        with patch("whiteboard.window.QFileDialog.getSaveFileName", return_value=(str(target), "")):
            self.assertTrue(window.save_document())
        self.assertEqual(len(window.scene.document.strokes), 1)
        self.assertEqual(window.scene.undo_stack.count(), 1)
        saved = load_document(target)
        self.assertEqual(saved.strokes[0].id, captured.strokes[0].id)
        self.assertFalse(window.is_dirty)

    def test_added_document_suffix_requires_overwrite_confirmation(self):
        window = self.window()
        self.ink(window)
        existing = self.directory / "lesson.txt.qboard"
        original = b"existing document must not be overwritten silently"
        existing.write_bytes(original)
        selected = str(self.directory / "lesson.txt")
        with patch("whiteboard.window.QFileDialog.getSaveFileName", return_value=(selected, "")), \
                patch.object(QMessageBox, "exec", click_message_button("取消")):
            self.assertFalse(window.save_as_document())
        self.assertEqual(existing.read_bytes(), original)
        self.assertTrue(window.is_dirty)
        with patch("whiteboard.window.QFileDialog.getSaveFileName", return_value=(selected, "")), \
                patch.object(QMessageBox, "exec", click_message_button("替换")):
            self.assertTrue(window.save_as_document())
        self.assertEqual(len(load_document(existing).strokes), 1)

    def test_added_png_suffix_requires_overwrite_confirmation(self):
        window = self.window()
        self.ink(window)
        existing = self.directory / "slide.jpg.png"
        original = b"existing image"
        existing.write_bytes(original)
        selected = str(self.directory / "slide.jpg")
        with patch("whiteboard.window.QFileDialog.getSaveFileName", return_value=(selected, "")), \
                patch.object(QMessageBox, "exec", click_message_button("取消")), \
                patch.object(window.canvas, "export_png") as exporter:
            self.assertFalse(window.export_png())
            exporter.assert_not_called()
        self.assertEqual(existing.read_bytes(), original)
        self.assertTrue(window.is_dirty)

    def test_reserved_recovery_path_cannot_be_saved_or_marked_clean(self):
        window = self.window(recovery=True)
        self.ink(window)
        self.assertTrue(window.recovery.flush())
        original = window.recovery.path.read_bytes()
        alias = window.recovery.path.parent / ".." / window.recovery.path.parent.name / "RECOVERY.QBOARD"
        with patch.object(window, "_show_error") as error:
            self.assertFalse(window._save_path(alias))
            error.assert_called_once()
        self.assertTrue(window.is_dirty)
        self.assertIsNone(window.current_path)
        self.assertEqual(window.recovery.path.read_bytes(), original)

    def test_small_window_tools_fit_and_controls_are_touch_sized(self):
        window = self.window()
        window.resize(900, 600)
        window.show()
        self.app.processEvents()
        self.assertTrue(window.host.rect().contains(window.tools_card.geometry()))
        self.assertIs(window.zoom_card.parentWidget(), window.top_card)
        self.assertFalse(window.tools_card.geometry().intersects(window.top_card.geometry()))
        for button in window.pen_buttons + list(window.tool_buttons.values()):
            self.assertGreaterEqual(button.width(), 44)
            self.assertGreaterEqual(button.height(), 44)
        window._show_brush_popover(0)
        self.app.processEvents()
        for button in window._popover.color_group.buttons():
            self.assertGreaterEqual(button.width(), 44)
        self.assertLess(window._popover.height(), 500)
        window._popover.close()

    def test_portrait_surface_width_keeps_tools_and_file_actions_accessible(self):
        window = self.window()
        window.resize(720, 1000)
        window.show()
        self.app.processEvents()
        self.assertEqual(window.width(), 720)
        self.assertTrue(window.host.rect().contains(window.tools_card.geometry()))
        self.assertTrue(window.host.rect().contains(window.top_card.geometry()))
        self.assertTrue(window.new_button.isHidden())
        self.assertTrue(window.open_button.isHidden())
        self.assertIn(window.new_action, window.main_menu.actions())
        self.assertIn(window.open_action, window.main_menu.actions())
        self.assertIn(window.save_action, window.main_menu.actions())
        for button in (window.menu_button,):
            self.assertTrue(window.top_card.rect().contains(button.geometry()))
        for button in window.pen_buttons + list(window.tool_buttons.values()):
            self.assertTrue(window.tools_card.rect().contains(button.geometry()))
        window.resize(900, 600)
        self.app.processEvents()
        self.assertTrue(window.new_button.isHidden())
        self.assertTrue(window.open_button.isHidden())
        for button in (window.menu_button, window.undo_button, window.redo_button):
            self.assertTrue(window.top_card.rect().contains(button.geometry()))

    def test_real_qtest_touch_selects_pen_slot_without_canvas_ink(self):
        attribute = Qt.ApplicationAttribute.AA_SynthesizeMouseForUnhandledTouchEvents
        previous = QCoreApplication.testAttribute(attribute)
        QCoreApplication.setAttribute(attribute, True)
        try:
            window = self.window()
            window.show()
            self.app.processEvents()
            device = QTest.createTouchDevice()
            button = window.pen_buttons[2]
            center = button.rect().center()
            QTest.touchEvent(button, device).press(0, center, button).commit()
            self.app.processEvents()
            QTest.touchEvent(button, device).release(0, center, button).commit()
            self.app.processEvents()
            self.assertEqual(window._active_pen, 2)
            self.assertEqual(window._tool, "pen")
            self.assertTrue(button.isChecked())
            self.assertEqual(window.scene.document.strokes, [])
        finally:
            QCoreApplication.setAttribute(attribute, previous)

    @unittest.skipUnless(sys.platform == "win32", "Windows native MSG integration")
    def test_native_suspend_and_resume_preserve_ink_without_bridging(self):
        # Target runtime is Windows x64. MSG.message is a 32-bit UINT while
        # WPARAM/LPARAM follow native pointer alignment, including padding.
        self.assertEqual(ctypes.sizeof(ctypes.c_void_p), 8)
        self.assertEqual(ctypes.sizeof(wintypes.MSG), 48)
        self.assertEqual(wintypes.MSG.message.offset, 8)
        self.assertEqual(wintypes.MSG.wParam.offset, 16)
        self.assertEqual(wintypes.MSG.lParam.offset, 24)
        pen = QPointingDevice("synthetic pen", 9812, QInputDevice.DeviceType.Stylus,
                              QPointingDevice.PointerType.Pen,
                              QInputDevice.Capability.Position | QInputDevice.Capability.Pressure, 1, 2)

        def tablet(canvas, kind, x, y, buttons=Qt.MouseButton.LeftButton):
            event = QTabletEvent(kind, pen, QPointF(x, y), QPointF(x, y),
                                 0.6 if buttons else 0, 0, 0, 0, 0, 0,
                                 Qt.KeyboardModifier.NoModifier,
                                 Qt.MouseButton.NoButton if kind == QEvent.Type.TabletMove else Qt.MouseButton.LeftButton,
                                 buttons)
            self.app.sendEvent(canvas, event)

        for code in (0x0004, 0x0007, 0x0012):
            with self.subTest(power_event=code):
                window = self.window()
                canvas = window.canvas
                tablet(canvas, QEvent.Type.TabletPress, 40, 100)
                tablet(canvas, QEvent.Type.TabletMove, 80, 110)
                canvas._touch_ids = (42,)
                canvas._touch_blocked = True
                canvas._set_temporary_tool("eraser")
                message = wintypes.MSG()
                message.message, message.wParam = 0x0218, code
                self.assertEqual(window.nativeEvent(QByteArray(b"windows_generic_MSG"), ctypes.addressof(message)), (True, 1))
                self.assertFalse(canvas.has_active_ink)
                self.assertEqual(canvas._source, "")
                self.assertEqual(canvas._touch_ids, ())
                self.assertFalse(canvas._pen_near)
                self.assertEqual(canvas._temporary_tool, "")
                self.assertEqual(window.scene.undo_stack.count(), 1)
                tablet(canvas, QEvent.Type.TabletMove, 400, 300, Qt.MouseButton.NoButton)
                self.assertEqual(len(window.scene.document.strokes), 1)
                tablet(canvas, QEvent.Type.TabletPress, 400, 300)
                tablet(canvas, QEvent.Type.TabletMove, 430, 310)
                tablet(canvas, QEvent.Type.TabletRelease, 440, 315, Qt.MouseButton.NoButton)
                first, second = window.scene.document.strokes
                self.assertLessEqual(max(sample.x for sample in first.samples), 80)
                self.assertGreaterEqual(min(sample.x for sample in second.samples), 400)
                self.assertEqual(window.scene.undo_stack.count(), 2)
                # Automatic resume can be followed by a user-resume notification.
                window.nativeEvent(b"windows_generic_MSG", ctypes.addressof(message))
                self.assertEqual(window.scene.undo_stack.count(), 2)

    @unittest.skipUnless(sys.platform == "win32", "Windows native MSG integration")
    def test_native_messages_accept_valid_zero_size_shiboken_pointer(self):
        from shiboken6 import VoidPtr

        window = self.window()
        message = wintypes.MSG()
        message.hWnd = int(window.winId())
        message.message, message.wParam = 0x0246, 7
        pointer = VoidPtr(ctypes.addressof(message), 0)
        self.assertFalse(bool(pointer))  # The real Qt callback has no buffer size.
        self.assertNotEqual(int(pointer), 0)
        with patch.object(window.native_tail_eraser, "handle_message", return_value=True) as route:
            self.assertEqual(window.nativeEvent(QByteArray(b"windows_generic_MSG"), pointer), (False, 0))
            self.assertEqual(route.call_args.args[0].message, 0x0246)
            self.assertEqual(route.call_args.args[0].wParam, 7)
        window.canvas.begin_native_tail(QPointF(80, 200))
        window.canvas.move_native_tail(QPointF(160, 200))
        message.message, message.wParam = 0x0218, 0x0004
        self.assertEqual(window.nativeEvent(QByteArray(b"windows_generic_MSG"), pointer), (True, 1))
        self.assertFalse(window.canvas.native_tail_active)
        self.assertEqual(window.nativeEvent(QByteArray(b"windows_generic_MSG"), VoidPtr(0, 0)), (False, 0))

    @unittest.skipUnless(sys.platform == "win32", "Windows native MSG integration")
    def test_unrelated_native_messages_do_not_cancel_input(self):
        window = self.window()
        canvas = window.canvas
        canvas._begin(QPointF(40, 100), "pen", "pen", 0.5, 1)
        for event_type, message_id, wparam in (
                (b"windows_generic_MSG", 0x0200, 0x0004),  # WM_MOUSEMOVE
                (b"windows_generic_MSG", 0x0218, 0x000A),  # battery/AC status
                (b"windows_generic_MSG", 0x0218, 0x8013),  # power setting
                (b"windows_generic_MSG", 0x0218, 0x0000),  # no suspend/resume
                (b"other_native_message", 0x0218, 0x0004)):
            with self.subTest(event_type=event_type, message=message_id, wparam=wparam):
                message = wintypes.MSG()
                message.message, message.wParam = message_id, wparam
                result = window.nativeEvent(QByteArray(event_type), ctypes.addressof(message))
                self.assertFalse(result[0])
                self.assertTrue(canvas.has_active_ink)
                self.assertEqual(window.scene.undo_stack.count(), 0)
        canvas._move(QPointF(80, 100), 0.7, 2)
        canvas.finish_interaction()
        self.assertEqual(len(window.scene.document.strokes), 1)
        self.assertEqual(len(window.scene.document.strokes[0].samples), 2)


if __name__ == "__main__":
    unittest.main()

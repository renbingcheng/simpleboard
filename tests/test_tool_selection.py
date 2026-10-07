"""Temporary pen behavior must not move the user's toolbar selection."""
from __future__ import annotations

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QInputDevice, QPointingDevice, QTabletEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from whiteboard.geometry import visible_path
from whiteboard.models import BoardDocument, Brush, InkSample, Stroke
from whiteboard.settings import AppSettings
from whiteboard.window import MainWindow


class ToolSelectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="simpleboard-tool-selection-")
        self.window = MainWindow(recovery_enabled=False,
                                 settings=AppSettings(Path(self.temp.name) / "settings.json"))
        self.window._startup_done = True
        self.window.show()
        self.app.processEvents()
        self.canvas = self.window.canvas
        self.scene = self.window.scene
        self.devices = {
            name: QPointingDevice(name, 8500 + index, QInputDevice.DeviceType.Stylus, kind,
                                  QInputDevice.Capability.Position | QInputDevice.Capability.Pressure,
                                  1, 3)
            for index, (name, kind) in enumerate((
                ("tail", QPointingDevice.PointerType.Eraser),
                ("tip", QPointingDevice.PointerType.Pen)))
        }

    def tearDown(self):
        self.canvas.set_native_tail_claimed(False)
        self.canvas.cancel_input("test_cleanup")
        self.window._settings_timer.stop()
        with patch.object(self.window, "_maybe_leave_document", return_value=True):
            self.window.close()
        self.app.removeEventFilter(self.canvas)
        self.window.deleteLater()
        self.app.processEvents()
        self.temp.cleanup()

    def tablet(self, kind, x=150, y=200, *, device="tail", buttons=None, pressure=None):
        if buttons is None:
            buttons = (Qt.MouseButton.NoButton if kind == QEvent.Type.TabletRelease
                       else Qt.MouseButton.LeftButton)
        if pressure is None:
            pressure = 0 if buttons == Qt.MouseButton.NoButton else 0.6
        button = Qt.MouseButton.NoButton if kind == QEvent.Type.TabletMove else Qt.MouseButton.LeftButton
        point = QPointF(x, y)
        event = QTabletEvent(kind, self.devices[device], point,
                             self.canvas.mapToGlobal(point), pressure, 0, 0, 0, 0, 0,
                             Qt.KeyboardModifier.NoModifier, button, buttons)
        self.app.sendEvent(self.canvas, event)

    def choose_pen(self, index):
        QTest.mouseClick(self.window.pen_buttons[index], Qt.MouseButton.LeftButton)
        self.assert_pen_selected(index)

    def assert_pen_selected(self, index):
        self.assertEqual(self.window._tool, "pen")
        self.assertEqual(self.canvas.tool, "pen")
        self.assertEqual([b.isChecked() for b in self.window.pen_buttons],
                         [i == index for i in range(6)])
        self.assertFalse(any(b.isChecked() for b in self.window.tool_buttons.values()))

    def add_line(self):
        stroke = Stroke([InkSample(20, 200), InkSample(620, 200)],
                        Brush(width=60, pressure_enabled=False))
        self.scene.reset(BoardDocument([stroke]))
        return stroke.id

    def assert_tip_draws(self, index):
        before = len(self.scene.document.strokes)
        self.tablet(QEvent.Type.TabletPress, 200, 330, device="tip")
        self.assertTrue(self.canvas.has_active_ink)
        self.tablet(QEvent.Type.TabletMove, 280, 340, device="tip")
        self.tablet(QEvent.Type.TabletRelease, 330, 340, device="tip")
        self.assertEqual(len(self.scene.document.strokes), before + 1)
        self.assertEqual(self.scene.document.strokes[-1].brush.color,
                         self.window.settings.pens[index].color)
        self.assert_pen_selected(index)

    def test_hover_tail_tip_flicker_does_not_toggle_selected_pen_or_create_ink(self):
        self.choose_pen(3)
        changes = []
        for button in [*self.window.pen_buttons, *self.window.tool_buttons.values()]:
            button.toggled.connect(changes.append)
        for index, device in enumerate(("tail", "tip", "tail", "tip", "tail")):
            self.tablet(QEvent.Type.TabletMove, 150 + index * 20, device=device,
                        buttons=Qt.MouseButton.NoButton, pressure=0)
            self.assert_pen_selected(3)
        self.assertEqual(changes, [])
        self.assertFalse(self.canvas.has_active_edit)
        self.assertEqual(self.scene.document.strokes, [])

    def test_qt_tail_erases_without_selecting_eraser_then_tip_uses_original_pen(self):
        self.choose_pen(2)
        identifier = self.add_line()
        self.tablet(QEvent.Type.TabletPress, 100)
        self.tablet(QEvent.Type.TabletMove, 500)
        self.assertEqual(self.canvas._interaction, "erase")
        self.assert_pen_selected(2)
        self.tablet(QEvent.Type.TabletRelease, 500)
        self.assert_pen_selected(2)
        self.assertEqual(self.scene.undo_stack.count(), 1)
        self.assertFalse(visible_path(self.scene.get(identifier)).contains(QPointF(300, 200)))
        self.assert_tip_draws(2)

    def test_native_tail_keeps_selection_through_queued_qt_events_and_up(self):
        self.choose_pen(4)
        identifier = self.add_line()
        # Exercise the real Canvas lifecycle used by NativeTailEraser, without
        # depending on Windows injection or driver-specific inverted flags.
        self.canvas.set_native_tail_claimed(True)
        self.canvas.begin_native_tail(QPointF(100, 200), .5, 10)
        self.assertTrue(self.canvas.native_tail_active)
        self.assert_pen_selected(4)
        self.tablet(QEvent.Type.TabletRelease, 200, device="tip")
        self.tablet(QEvent.Type.TabletMove, 230, device="tail", buttons=Qt.MouseButton.NoButton)
        self.canvas.move_native_tail(QPointF(500, 200), 0, 20)
        self.assertTrue(self.canvas.native_tail_active)
        self.assertEqual(self.scene.undo_stack.count(), 0)
        self.assert_pen_selected(4)
        self.canvas.end_native_tail("native_up")
        self.canvas.set_native_tail_claimed(False)
        self.assertFalse(self.canvas.native_tail_active)
        self.assertEqual(self.scene.undo_stack.count(), 1)
        self.assertFalse(visible_path(self.scene.get(identifier)).contains(QPointF(300, 200)))
        self.assert_pen_selected(4)
        self.assert_tip_draws(4)

    def test_barrel_lasso_does_not_select_toolbar_lasso(self):
        self.choose_pen(1)
        buttons = Qt.MouseButton.LeftButton | Qt.MouseButton.RightButton
        self.tablet(QEvent.Type.TabletPress, 100, 100, device="tip", buttons=buttons)
        self.assertEqual(self.canvas._interaction, "lasso")
        self.assert_pen_selected(1)
        self.tablet(QEvent.Type.TabletMove, 300, 100, device="tip", buttons=buttons)
        self.tablet(QEvent.Type.TabletMove, 300, 250, device="tip", buttons=buttons)
        self.tablet(QEvent.Type.TabletRelease, 100, 250, device="tip")
        self.assert_pen_selected(1)
        self.assert_tip_draws(1)

    def test_explicit_eraser_click_remains_selected_and_works_with_tip(self):
        identifier = self.add_line()
        QTest.mouseClick(self.window.tool_buttons["eraser"], Qt.MouseButton.LeftButton)
        self.assertEqual(self.window._tool, "eraser")
        self.assertEqual(self.canvas.tool, "eraser")
        self.assertTrue(self.window.tool_buttons["eraser"].isChecked())
        self.assertFalse(any(b.isChecked() for b in self.window.pen_buttons))
        for device in ("tail", "tip", "tail", "tip"):
            self.tablet(QEvent.Type.TabletMove, device=device, buttons=Qt.MouseButton.NoButton)
            self.assertTrue(self.window.tool_buttons["eraser"].isChecked())
        self.tablet(QEvent.Type.TabletPress, 100, device="tip")
        self.tablet(QEvent.Type.TabletMove, 500, device="tip")
        self.tablet(QEvent.Type.TabletRelease, 500, device="tip")
        self.assertFalse(visible_path(self.scene.get(identifier)).contains(QPointF(300, 200)))
        self.assertTrue(self.window.tool_buttons["eraser"].isChecked())
        self.choose_pen(5)
        self.assert_tip_draws(5)


if __name__ == "__main__":
    unittest.main()

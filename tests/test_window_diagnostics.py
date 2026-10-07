"""Exercise the diagnostics shortcut and export control through the real window.

All settings and recovery paths are isolated. Tablet events are synthetic Qt
events; these tests make no claim about physical Surface driver behavior.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QInputDevice, QPointingDevice, QTabletEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QPushButton

from whiteboard.recovery import RecoveryManager
from whiteboard.settings import AppSettings
from whiteboard.window import MainWindow


class WindowDiagnosticsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="qboard-diagnostics-window-")
        self.directory = Path(self.temp.name)
        settings = AppSettings(self.directory / "settings.json")
        factory = lambda provider, parent: RecoveryManager(provider, parent, self.directory / "recovery")
        with patch("whiteboard.window.RecoveryManager", side_effect=factory):
            self.window = MainWindow(recovery_enabled=False, settings=settings)
        self.window._startup_done = True
        self.window.show()
        self.window.activateWindow()
        self.window.canvas.setFocus()
        self.app.processEvents()
        self.pen = QPointingDevice(
            "诊断测试 Surface Pen", 9812, QInputDevice.DeviceType.Stylus,
            QPointingDevice.PointerType.Pen,
            QInputDevice.Capability.Position | QInputDevice.Capability.Pressure
            | QInputDevice.Capability.XTilt | QInputDevice.Capability.YTilt,
            1, 2,
        )

    def tearDown(self):
        self.window._settings_timer.stop()
        with patch.object(self.window, "_maybe_leave_document", return_value=True):
            self.window.close()
        self.app.removeEventFilter(self.window.canvas)
        self.window.deleteLater()
        self.app.processEvents()
        self.temp.cleanup()

    def press_f12(self):
        QTest.keyClick(self.window.canvas, Qt.Key.Key_F12)
        self.app.processEvents()

    def export_button(self):
        return next(button for button in self.window.diagnostics.findChildren(QPushButton)
                    if button.text() == "导出输入记录…")

    def tablet(self, kind, position, pressure, timestamp):
        button = Qt.MouseButton.NoButton if kind == QEvent.Type.TabletMove else Qt.MouseButton.LeftButton
        buttons = Qt.MouseButton.NoButton if kind == QEvent.Type.TabletRelease else Qt.MouseButton.LeftButton
        event = QTabletEvent(kind, self.pen, QPointF(*position), QPointF(*position),
                             pressure, 12, -8, 0, 0, 0,
                             Qt.KeyboardModifier.NoModifier, button, buttons)
        event.setTimestamp(timestamp)
        self.app.sendEvent(self.window.canvas, event)

    def test_f12_controls_recording_and_preserves_raw_tablet_evidence(self):
        recorder = self.window.canvas.input_diagnostics
        self.assertFalse(recorder.enabled)
        self.assertEqual(recorder.count, 0)
        self.press_f12()
        self.assertTrue(self.window.diagnostics_action.isChecked())
        self.assertTrue(self.window.diagnostics.isVisible())
        self.assertTrue(recorder.enabled)

        self.tablet(QEvent.Type.TabletPress, (100, 180), .23, 101)
        self.tablet(QEvent.Type.TabletMove, (145, 185), .87, 112)
        self.tablet(QEvent.Type.TabletRelease, (180, 190), 0, 124)
        recorded = recorder.snapshot()["events"]
        tablets = [event for event in recorded if event["kind"] == "tablet"]
        self.assertEqual([event["event_type"] for event in tablets],
                         ["TabletPress", "TabletMove", "TabletRelease"])
        self.assertEqual([event["timestamp_ms"] for event in tablets], [101, 112, 124])
        self.assertEqual([event["pressure"] for event in tablets], [.23, .87, 0])
        self.assertEqual((tablets[1]["x"], tablets[1]["y"]), (145, 185))
        self.assertEqual((tablets[1]["tilt_x"], tablets[1]["tilt_y"]), (12, -8))
        self.assertEqual(tablets[1]["device"]["name"], "诊断测试 Surface Pen")
        # Recording is before dispatch: release still describes the active stroke.
        self.assertEqual(tablets[-1]["state"]["interaction"], "ink")
        self.assertTrue(any(event.get("reason") == "finish" for event in recorded))
        self.assertEqual(len(self.window.scene.document.strokes), 1)

        self.press_f12()
        self.assertFalse(self.window.diagnostics_action.isChecked())
        self.assertFalse(self.window.diagnostics.isVisible())
        self.assertFalse(recorder.enabled)
        count = recorder.count
        self.tablet(QEvent.Type.TabletMove, (210, 190), 0, 140)
        self.assertEqual(recorder.count, count)
        self.assertEqual(recorder.snapshot()["events"][-1]["reason"], "recording_disabled")

    def test_panel_close_disables_recording_and_action(self):
        self.press_f12()
        self.window.diagnostics.closed.emit()
        self.assertFalse(self.window.diagnostics_action.isChecked())
        self.assertFalse(self.window.diagnostics.isVisible())
        self.assertFalse(self.window.canvas.input_diagnostics.enabled)

    def test_export_button_writes_selected_unicode_path_without_saving_document(self):
        self.press_f12()
        self.tablet(QEvent.Type.TabletPress, (100, 180), .5, 101)
        self.tablet(QEvent.Type.TabletMove, (150, 190), .8, 112)
        target = self.directory / "课堂 输入记录.json"
        with patch("whiteboard.window.QFileDialog.getSaveFileName",
                   return_value=(str(target), "JSON (*.json)")) as dialog, \
                patch("whiteboard.window.save_document") as save:
            QTest.mouseClick(self.export_button(), Qt.MouseButton.LeftButton)
            dialog.assert_called_once()
            save.assert_not_called()

        exported = json.loads(target.read_text(encoding="utf-8"))
        self.assertEqual(exported["schema"], "local-whiteboard.input-diagnostics")
        self.assertEqual(exported["version"], 1)
        self.assertTrue(any(event.get("reason") == "finish" for event in exported["events"]))
        self.assertEqual(len([event for event in exported["events"] if event["kind"] == "tablet"]), 2)
        self.assertEqual(self.window.canvas._interaction, "")
        self.assertEqual(len(self.window.scene.document.strokes), 1)
        self.assertEqual(self.window.scene.undo_stack.count(), 1)
        self.assertTrue(self.window.is_dirty)
        self.assertIsNone(self.window.current_path)
        self.assertEqual(list(self.directory.glob("*.qboard")), [])

    def test_cancelled_export_button_does_not_write_or_call_exporter(self):
        self.press_f12()
        before = sorted(path.relative_to(self.directory) for path in self.directory.rglob("*"))
        with patch("whiteboard.window.QFileDialog.getSaveFileName", return_value=("", "")), \
                patch.object(self.window.canvas.input_diagnostics, "export_json") as export, \
                patch("whiteboard.window.QMessageBox.warning") as warning:
            QTest.mouseClick(self.export_button(), Qt.MouseButton.LeftButton)
            export.assert_not_called()
            warning.assert_not_called()
        self.assertEqual(sorted(path.relative_to(self.directory) for path in self.directory.rglob("*")), before)

    def test_export_failures_are_shown_and_do_not_clear_recording(self):
        self.press_f12()
        target = self.directory / "failed.json"
        original = b"existing diagnostics must survive"
        target.write_bytes(original)
        recorder = self.window.canvas.input_diagnostics
        for error in (OSError("磁盘写入失败"), ValueError("无效输入记录")):
            with self.subTest(error=type(error).__name__), \
                    patch("whiteboard.window.QFileDialog.getSaveFileName", return_value=(str(target), "JSON (*.json)")), \
                    patch.object(recorder, "export_json", side_effect=error) as export, \
                    patch("whiteboard.window.QMessageBox.warning") as warning:
                QTest.mouseClick(self.export_button(), Qt.MouseButton.LeftButton)
                export.assert_called_once_with(str(target))
                warning.assert_called_once_with(self.window, "导出失败", str(error))
            self.assertEqual(target.read_bytes(), original)
            self.assertTrue(recorder.enabled)
            self.assertGreater(recorder.count, 0)


if __name__ == "__main__":
    unittest.main()

"""Inverted-pen contact must be continuous without joining separate contacts."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path
import tempfile
import unittest

from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QInputDevice, QPainter, QPointingDevice, QTabletEvent
from PySide6.QtWidgets import QApplication

from whiteboard.canvas import Canvas
from whiteboard.geometry import visible_path
from whiteboard.models import BoardDocument, Brush, InkSample, Stroke
from whiteboard.scene import Scene
from whiteboard.storage import load_document, save_document


APP = QApplication.instance() or QApplication([])


class TabletEraserTests(unittest.TestCase):
    def setUp(self):
        self.original = Stroke([InkSample(20, 200), InkSample(620, 200)],
                               Brush(color="#2563EB", width=80, pressure_enabled=False))
        self.scene = Scene(BoardDocument([self.original]))
        self.canvas = Canvas(self.scene)
        self.canvas.resize(640, 480)
        self.devices = {
            name: QPointingDevice(name, 7100+i, QInputDevice.DeviceType.Stylus, pointer,
                                  QInputDevice.Capability.Position | QInputDevice.Capability.Pressure, 1, 3)
            for i, (name, pointer) in enumerate((
                ("tail", QPointingDevice.PointerType.Eraser),
                ("tip", QPointingDevice.PointerType.Pen)))
        }

    def tearDown(self):
        self.canvas.cancel_input()
        APP.removeEventFilter(self.canvas)
        self.canvas.close()
        self.canvas.deleteLater()
        APP.processEvents()

    def send(self, kind, x, *, device="tail", pressure=.6, barrel=False):
        releasing = kind == QEvent.Type.TabletRelease
        buttons = Qt.MouseButton.NoButton if releasing else Qt.MouseButton.LeftButton
        if barrel:
            buttons |= Qt.MouseButton.RightButton
        event = QTabletEvent(kind, self.devices[device], QPointF(x, 200), QPointF(x, 200),
                             0 if releasing else pressure, 0, 0, 0, 0, 0,
                             Qt.KeyboardModifier.NoModifier,
                             Qt.MouseButton.NoButton if kind == QEvent.Type.TabletMove else Qt.MouseButton.LeftButton,
                             buttons)
        APP.sendEvent(self.canvas, event)

    def assert_band_erased(self, path, start=80, end=500):
        for y in (194, 200, 206):
            for x in range(start, end+1, 2):
                self.assertFalse(path.contains(QPointF(x, y)), f"gap at {x}, {y}")
        self.assertTrue(path.contains(QPointF(300, 225)), "ink beyond eraser width must survive")

    def test_tail_type_flicker_stays_one_continuous_erase_and_no_new_ink(self):
        self.send(QEvent.Type.TabletPress, 80)
        self.send(QEvent.Type.TabletMove, 150)
        self.send(QEvent.Type.TabletMove, 250, device="tip", barrel=True)
        self.send(QEvent.Type.TabletMove, 350, pressure=0)
        self.send(QEvent.Type.TabletMove, 500, device="tip")
        self.assertEqual(self.canvas._interaction, "erase")
        self.assertEqual(self.canvas._temporary_tool, "eraser")
        image = self.canvas._new_layer()
        painter = QPainter(image)
        self.canvas._paint_document(painter)
        painter.end()
        self.assertTrue(all(image.pixelColor(x, 200).name() == "#ffffff" for x in range(80, 501)))
        self.send(QEvent.Type.TabletRelease, 500, device="tip")
        self.assertEqual(len(self.scene.document.strokes), 1)
        self.assertEqual(len(self.scene.document.strokes[0].erase_masks), 1)
        self.assertEqual(self.scene.undo_stack.count(), 1)
        self.assert_band_erased(visible_path(self.scene.document.strokes[0]))
        self.scene.undo_stack.undo()
        self.assertTrue(all(visible_path(self.scene.document.strokes[0]).contains(QPointF(x, 200))
                            for x in range(80, 501)))
        self.scene.undo_stack.redo()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tail.qboard"
            save_document(path, self.scene.document)
            self.assert_band_erased(visible_path(load_document(path).strokes[0]))
        # A real release ends the latch; the tip can draw with its chosen tool.
        self.send(QEvent.Type.TabletPress, 300, device="tip")
        self.assertEqual(self.canvas._interaction, "ink")
        self.send(QEvent.Type.TabletRelease, 320, device="tip")
        self.assertEqual(len(self.scene.document.strokes), 2)

    def test_repeated_press_without_release_connects_to_previous_endpoint(self):
        self.send(QEvent.Type.TabletPress, 80)
        self.send(QEvent.Type.TabletMove, 140)
        self.send(QEvent.Type.TabletPress, 260, device="tip")
        self.send(QEvent.Type.TabletMove, 350)
        self.send(QEvent.Type.TabletPress, 440)
        self.send(QEvent.Type.TabletRelease, 500)
        self.assertEqual(self.scene.undo_stack.count(), 1)
        self.assertEqual(len(self.scene.document.strokes[0].erase_masks), 1)
        self.assert_band_erased(visible_path(self.scene.document.strokes[0]))

    def test_true_release_keeps_separate_contacts_unconnected(self):
        self.send(QEvent.Type.TabletPress, 80)
        self.send(QEvent.Type.TabletRelease, 140)
        self.send(QEvent.Type.TabletMove, 300, pressure=0)
        self.send(QEvent.Type.TabletPress, 400)
        self.send(QEvent.Type.TabletRelease, 500)
        path = visible_path(self.scene.document.strokes[0])
        self.assertTrue(all(path.contains(QPointF(x, 200)) for x in range(180, 360)))
        self.assertEqual(self.scene.undo_stack.count(), 2)

    def test_opt_in_diagnostics_preserves_raw_events_and_finish_boundary(self):
        self.canvas.set_diagnostics_enabled(True)
        self.send(QEvent.Type.TabletPress, 80)
        self.send(QEvent.Type.TabletMove, 140, device="tip", pressure=0)
        self.send(QEvent.Type.TabletRelease, 180)
        self.canvas.set_diagnostics_enabled(False)
        records = self.canvas.input_diagnostics.snapshot()["events"]
        raw = [event for event in records if event["kind"] == "tablet"]
        self.assertEqual([event["event_type"] for event in raw],
                         ["TabletPress", "TabletMove", "TabletRelease"])
        self.assertEqual(raw[1]["pointer_type"], "Pen")
        self.assertEqual(raw[1]["pressure"], 0)
        self.assertTrue(raw[1]["state"]["tail_contact"])
        self.assertTrue(any(event.get("reason") == "finish" for event in records))
        self.send(QEvent.Type.TabletPress, 400)
        self.assertEqual(self.canvas.input_diagnostics.snapshot()["events"], records)

    def test_cancellation_and_proximity_loss_never_bridge_to_next_contact(self):
        for end_event in (QEvent.Type.TabletLeaveProximity, QEvent.Type.ApplicationDeactivate):
            with self.subTest(boundary=end_event):
                self.scene.reset(BoardDocument([self.original]))
                self.send(QEvent.Type.TabletPress, 80)
                self.send(QEvent.Type.TabletMove, 140)
                self.canvas.eventFilter(APP, QEvent(end_event))
                self.assertEqual(self.canvas._source, "")
                self.send(QEvent.Type.TabletMove, 300, pressure=0)
                self.send(QEvent.Type.TabletPress, 400)
                self.send(QEvent.Type.TabletRelease, 500)
                path = visible_path(self.scene.document.strokes[0])
                self.assertTrue(all(path.contains(QPointF(x, 200)) for x in range(180, 360)))
                self.assertEqual(self.scene.undo_stack.count(), 2)


if __name__ == "__main__":
    unittest.main()

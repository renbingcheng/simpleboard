"""Behavioral checks at the native contact / Canvas boundary.

The Windows message decoder has separate tests. These inject confirmed native
contacts and deliberately contradictory Qt events; they are not hardware tests.
"""
from __future__ import annotations

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QFocusEvent, QInputDevice, QPainter, QPointingDevice, QTabletEvent
from PySide6.QtWidgets import QApplication

from whiteboard.canvas import Canvas
from whiteboard.geometry import clear_geometry_cache, visible_path
from whiteboard.models import Brush, InkSample, Stroke
from whiteboard.scene import Scene
from whiteboard.storage import document_to_dict, load_document, save_document
from tests import test_erase_preview as preview_helpers


APP = QApplication.instance() or QApplication([])


class NativeTailCanvasTests(unittest.TestCase):
    def setUp(self):
        self.scene = Scene()
        self.canvas = Canvas(self.scene)
        self.canvas.resize(640, 360)
        self.pen = QPointingDevice("native-test pen", 8101, QInputDevice.DeviceType.Stylus,
                                   QPointingDevice.PointerType.Pen,
                                   QInputDevice.Capability.Position | QInputDevice.Capability.Pressure, 1, 3)
        self.tail = QPointingDevice("native-test tail", 8102, QInputDevice.DeviceType.Stylus,
                                    QPointingDevice.PointerType.Eraser,
                                    QInputDevice.Capability.Position | QInputDevice.Capability.Pressure, 1, 3)
        self.canvas.set_eraser(10, False)

    def tearDown(self):
        self.canvas.cancel_input()
        self.canvas._grid_timer.stop()
        self.canvas._grid_fade_timer.stop()
        APP.removeEventFilter(self.canvas)
        self.canvas.close()
        self.canvas.deleteLater()
        APP.processEvents()

    def board_line(self, y=150, width=70, kind="pen", opacity=1):
        stroke = Stroke([InkSample(x, y) for x in range(40, 591, 10)],
                        Brush(color="#2563EB", width=width, kind=kind,
                              pressure_enabled=False, opacity=opacity))
        self.scene.add_stroke(stroke)
        return stroke.id

    def qt_tablet(self, kind, position=(600, 300), device=None, pressure=0,
                  buttons=Qt.MouseButton.NoButton):
        button = Qt.MouseButton.NoButton if kind == QEvent.Type.TabletMove else Qt.MouseButton.LeftButton
        event = QTabletEvent(kind, device or self.tail, QPointF(*position), QPointF(*position),
                             pressure, 0, 0, 0, 0, 0, Qt.KeyboardModifier.NoModifier, button, buttons)
        event.setTimestamp(80)
        APP.sendEvent(self.canvas, event)

    def render(self):
        image = self.canvas._new_layer()
        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.canvas._paint_document(painter)
        painter.end()
        return image

    def assert_interiors_equal(self, first, second):
        preview_helpers.ErasePreviewTests.assert_same_interiors(self, first, second)

    def assert_ink(self, stroke_id, point, present):
        stroke = self.scene.get(stroke_id)
        filled = stroke is not None and visible_path(stroke).contains(QPointF(*point))
        self.assertEqual(filled, present, point)

    def test_qt_release_press_type_flip_and_proximity_do_not_split_native_sweep(self):
        stroke_id = self.board_line()
        original = self.render()
        baseline = self.scene.undo_stack.count()
        self.canvas.set_tool("highlighter")
        self.canvas.begin_native_tail(QPointF(70, 150), pressure=.5)
        self.canvas.move_native_tail(QPointF(220, 150), pressure=0)
        self.qt_tablet(QEvent.Type.TabletRelease)
        self.qt_tablet(QEvent.Type.TabletPress, device=self.pen, pressure=.8,
                       buttons=Qt.MouseButton.LeftButton)
        self.qt_tablet(QEvent.Type.TabletMove, device=self.pen, pressure=.4,
                       buttons=Qt.MouseButton.RightButton)
        self.canvas.eventFilter(APP, QEvent(QEvent.Type.TabletLeaveProximity))
        self.assertTrue(self.canvas.native_tail_active)
        self.assertEqual(self.scene.undo_stack.count(), baseline)
        self.canvas.move_native_tail(QPointF(560, 150), pressure=0)
        self.canvas.move_native_tail(QPointF(130, 150), pressure=1)
        preview = self.render()
        for x in range(80, 551):
            self.assertEqual(preview.pixelColor(x, 150).name(), "#ffffff", x)
        self.assertEqual(preview.pixelColor(300, 177).name(), "#2563eb")
        self.canvas.end_native_tail("native_up")
        self.assertFalse(self.canvas.native_tail_active)
        self.assertEqual(self.canvas.tool, "highlighter")
        self.assertEqual(self.canvas._temporary_tool, "")
        self.assertEqual(self.scene.undo_stack.count(), baseline + 1)
        self.assertEqual(len(self.scene.document.strokes), 1)
        self.assert_interiors_equal(preview, self.render())
        self.assert_ink(stroke_id, (350, 150), False)
        self.scene.undo_stack.undo()
        self.assert_interiors_equal(self.render(), original)
        self.scene.undo_stack.redo()
        self.assert_interiors_equal(self.render(), preview)

    def test_two_real_contacts_make_two_undos_and_preserve_the_gap(self):
        stroke_id = self.board_line()
        baseline = self.scene.undo_stack.count()
        for first, last in ((80, 180), (410, 530)):
            self.canvas.begin_native_tail(QPointF(first, 150))
            self.canvas.move_native_tail(QPointF(last, 150))
            self.canvas.end_native_tail("native_up")
        self.assertEqual(self.scene.undo_stack.count(), baseline + 2)
        self.assert_ink(stroke_id, (130, 150), False)
        self.assert_ink(stroke_id, (470, 150), False)
        self.assert_ink(stroke_id, (290, 150), True)
        self.scene.undo_stack.undo()
        self.assert_ink(stroke_id, (130, 150), False)
        self.assert_ink(stroke_id, (470, 150), True)
        self.scene.undo_stack.undo()
        self.assert_ink(stroke_id, (130, 150), True)

    def test_native_claim_blocks_queued_qt_after_external_finish_until_release(self):
        self.board_line()
        self.canvas.set_native_tail_claimed(True)
        self.canvas.begin_native_tail(QPointF(80, 150))
        self.canvas.move_native_tail(QPointF(180, 150))
        self.canvas.finish_interaction()  # Undo/save can commit before native UP.
        self.assertFalse(self.canvas.native_tail_active)
        self.assertTrue(self.canvas.native_tail_owned)
        baseline = self.scene.undo_stack.count()
        committed = document_to_dict(self.scene.document)
        self.qt_tablet(QEvent.Type.TabletPress, (300, 260), device=self.pen,
                       pressure=.5, buttons=Qt.MouseButton.LeftButton)
        self.canvas.eventFilter(APP, QEvent(QEvent.Type.TabletLeaveProximity))
        self.qt_tablet(QEvent.Type.TabletRelease, (360, 260), device=self.pen)
        self.assertFalse(self.canvas.has_active_edit)
        self.assertEqual(self.scene.undo_stack.count(), baseline)
        self.assertEqual(document_to_dict(self.scene.document), committed)
        self.canvas.move_native_tail(QPointF(500, 150))
        self.assertEqual(document_to_dict(self.scene.document), committed)
        self.canvas.set_native_tail_claimed(False)
        self.canvas.end_native_tail("native_up")
        self.assertFalse(self.canvas.native_tail_owned)
        self.qt_tablet(QEvent.Type.TabletPress, (300, 260), device=self.pen,
                       pressure=.5, buttons=Qt.MouseButton.LeftButton)
        self.qt_tablet(QEvent.Type.TabletMove, (330, 260), device=self.pen,
                       pressure=.5, buttons=Qt.MouseButton.LeftButton)
        self.qt_tablet(QEvent.Type.TabletRelease, (360, 260), device=self.pen)
        self.assertEqual(self.scene.undo_stack.count(), baseline + 1)
        self.assertEqual(len(self.scene.document.strokes), 2)
        self.assertTrue(visible_path(self.scene.document.strokes[-1]).contains(QPointF(330, 260)))

    def test_draining_native_proximity_and_end_do_not_commit_another_source(self):
        self.board_line()
        self.canvas.set_native_tail_claimed(True)
        self.canvas.begin_native_tail(QPointF(80, 150))
        self.canvas.move_native_tail(QPointF(180, 150))
        self.canvas.finish_interaction()
        # An external input controller can own a different edit while the old
        # native contact is still draining. Its undo boundary must stay intact.
        self.canvas._begin(QPointF(300, 260), "pen", "pen", .5)
        self.canvas._move(QPointF(330, 260), .5)
        baseline = self.scene.undo_stack.count()
        self.canvas.eventFilter(APP, QEvent(QEvent.Type.TabletLeaveProximity))
        self.assertTrue(self.canvas.has_active_ink)
        self.assertEqual(self.scene.undo_stack.count(), baseline)
        self.canvas.set_native_tail_claimed(False)
        self.canvas.end_native_tail("late_native_up")
        self.assertTrue(self.canvas.has_active_ink)
        self.assertEqual(self.scene.undo_stack.count(), baseline)
        self.qt_tablet(QEvent.Type.TabletRelease, (360, 260), device=self.pen)
        self.assertEqual(self.scene.undo_stack.count(), baseline + 1)
        self.assertEqual(len(self.scene.document.strokes), 2)

    def test_focus_loss_commits_then_ignores_old_contact_updates(self):
        self._check_cancellation(lambda: APP.sendEvent(
            self.canvas, QFocusEvent(QEvent.Type.FocusOut, Qt.FocusReason.OtherFocusReason)))

    def test_suspend_cancellation_cannot_bridge_the_next_contact(self):
        self._check_cancellation(lambda: self.canvas.cancel_input("power_suspend"))

    def test_native_capture_loss_cannot_bridge_the_next_contact(self):
        self._check_cancellation(lambda: self.canvas.end_native_tail("native_capture_lost"))

    def _check_cancellation(self, cancel):
        stroke_id = self.board_line()
        baseline = self.scene.undo_stack.count()
        self.canvas.begin_native_tail(QPointF(80, 150))
        self.canvas.move_native_tail(QPointF(180, 150))
        cancel()
        self.assertFalse(self.canvas.native_tail_active)
        self.assertEqual(self.scene.undo_stack.count(), baseline + 1)
        self.canvas.move_native_tail(QPointF(400, 150))
        self.canvas.end_native_tail("late_native_up")
        self.assertEqual(self.scene.undo_stack.count(), baseline + 1)
        self.canvas.begin_native_tail(QPointF(450, 150))
        self.canvas.move_native_tail(QPointF(530, 150))
        self.canvas.end_native_tail("native_up")
        self.assertEqual(self.scene.undo_stack.count(), baseline + 2)
        self.assert_ink(stroke_id, (130, 150), False)
        self.assert_ink(stroke_id, (490, 150), False)
        for x in (240, 300, 390):
            self.assert_ink(stroke_id, (x, 150), True)

    def test_active_snapshot_keeps_one_continuous_native_erase_without_committing(self):
        stroke_id = self.board_line()
        baseline = self.scene.undo_stack.count()
        self.canvas.begin_native_tail(QPointF(80, 150))
        self.canvas.move_native_tail(QPointF(220, 150))
        self.qt_tablet(QEvent.Type.TabletRelease)
        first_snapshot = self.canvas.snapshot_document()
        self.canvas.move_native_tail(QPointF(550, 150))
        second_snapshot = self.canvas.snapshot_document()
        self.assertTrue(self.canvas.native_tail_active)
        self.assertEqual(self.scene.undo_stack.count(), baseline)
        self.assert_ink(stroke_id, (140, 150), True)
        self.assertTrue(visible_path(first_snapshot.strokes[0]).contains(QPointF(430, 150)))
        self.assertFalse(visible_path(second_snapshot.strokes[0]).contains(QPointF(430, 150)))
        with TemporaryDirectory() as directory:
            path = Path(directory) / "native-active.qboard"
            save_document(path, second_snapshot)
            recovered = load_document(path)
        clear_geometry_cache()
        self.assertFalse(visible_path(recovered.strokes[0]).contains(QPointF(430, 150)))
        self.canvas.end_native_tail("native_up")
        self.assertEqual(self.scene.undo_stack.count(), baseline + 1)
        self.assertEqual(document_to_dict(second_snapshot), document_to_dict(self.scene.document))

    def test_high_dpi_zoomed_native_sweep_matches_commit_undo_and_cold_reopen(self):
        with patch.object(Canvas, "devicePixelRatioF", return_value=2.0):
            self.canvas.set_view(3.25, QPointF(17.25, -24.75))
            self.canvas._grid_opacity = 0
            world_points = [self.canvas.screen_to_world(QPointF(x, 150)) for x in range(40, 591, 10)]
            stroke = Stroke([InkSample(p.x(), p.y()) for p in world_points],
                            Brush(color="#2563eb", width=16, pressure_enabled=False,
                                  kind="highlighter", opacity=.35))
            self.scene.add_stroke(stroke)
            original = self.render()
            self.assertEqual((original.width(), original.height()), (1280, 720))
            self.canvas.begin_native_tail(QPointF(80, 150))
            self.qt_tablet(QEvent.Type.TabletRelease, device=self.pen)
            self.canvas.move_native_tail(QPointF(550, 150))
            preview = self.render()
            for x in (120, 300, 500):
                self.assertEqual(preview.pixelColor(x * 2, 300).name(), "#ffffff")
                self.assertNotEqual(preview.pixelColor(x * 2, 340).name(), "#ffffff")
            self.canvas.end_native_tail("native_up")
            self.assert_interiors_equal(preview, self.render())
            self.scene.undo_stack.undo()
            self.assert_interiors_equal(original, self.render())
            self.scene.undo_stack.redo()
            with TemporaryDirectory() as directory:
                path = Path(directory) / "native-zoomed.qboard"
                save_document(path, self.scene.document)
                document = load_document(path)
            self.scene.reset(document)
            self.canvas.load_view_from_document()
            self.canvas._grid_opacity = 0
            self.assert_interiors_equal(preview, self.render())

    def test_late_native_end_does_not_finish_a_new_pen_stroke(self):
        self.board_line()
        self.canvas.begin_native_tail(QPointF(80, 150))
        self.canvas.move_native_tail(QPointF(180, 150))
        self.canvas.end_native_tail("native_up")
        self.qt_tablet(QEvent.Type.TabletPress, (300, 260), device=self.pen,
                       pressure=.5, buttons=Qt.MouseButton.LeftButton)
        self.qt_tablet(QEvent.Type.TabletMove, (330, 260), device=self.pen,
                       pressure=.5, buttons=Qt.MouseButton.LeftButton)
        baseline = self.scene.undo_stack.count()
        self.canvas.end_native_tail("late_capture_changed")
        self.assertTrue(self.canvas.has_active_ink)
        self.assertEqual(self.scene.undo_stack.count(), baseline)
        self.qt_tablet(QEvent.Type.TabletRelease, (360, 260), device=self.pen)
        self.assertEqual(self.scene.undo_stack.count(), baseline + 1)


if __name__ == "__main__":
    unittest.main()

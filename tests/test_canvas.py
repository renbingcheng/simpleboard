"""Headless input and rendered-output regression tests.

These simulate Qt events; real Surface driver, palm rejection and input latency
still need hardware acceptance testing.
"""
from __future__ import annotations

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import (
    QEventPoint, QFocusEvent, QImage, QInputDevice, QMouseEvent, QPainter,
    QPointingDevice, QTabletEvent, QTouchEvent,
)
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from whiteboard.canvas import Canvas
from whiteboard.geometry import visible_path
from whiteboard.models import BoardDocument, Brush, EraseMask, InkSample, Stroke
from whiteboard.scene import Scene


APP = QApplication.instance() or QApplication([])


class CanvasTests(unittest.TestCase):
    def setUp(self):
        self.scene = Scene()
        self.canvas = Canvas(self.scene)
        self.canvas.resize(640, 480)
        self.canvas.show()
        APP.processEvents()
        self.pen = QPointingDevice(
            "test pen", 9001, QInputDevice.DeviceType.Stylus,
            QPointingDevice.PointerType.Pen,
            QInputDevice.Capability.Position | QInputDevice.Capability.Pressure | QInputDevice.Capability.XTilt | QInputDevice.Capability.YTilt,
            1, 3,
        )
        self.eraser = QPointingDevice(
            "test pen eraser", 9002, QInputDevice.DeviceType.Stylus,
            QPointingDevice.PointerType.Eraser,
            QInputDevice.Capability.Position | QInputDevice.Capability.Pressure,
            1, 1,
        )

    def tearDown(self):
        self.canvas.cancel_input()
        APP.removeEventFilter(self.canvas)
        self.canvas.close()
        self.canvas.deleteLater()
        APP.processEvents()

    def add_line(self, y=100, width=12, mask=False):
        stroke = Stroke([InkSample(50, y), InkSample(250, y)], Brush(width=width, pressure_enabled=False))
        if mask:
            stroke.erase_masks.append(EraseMask([(150, y-30), (150, y+30)], 12))
        self.scene.add_stroke(stroke)
        return self.scene.document.strokes[-1]

    def mouse(self, kind, position, buttons=Qt.MouseButton.LeftButton,
              source=Qt.MouseEventSource.MouseEventNotSynthesized):
        button = Qt.MouseButton.NoButton if kind == QEvent.Type.MouseMove else Qt.MouseButton.LeftButton
        point = QPointF(*position)
        event = QMouseEvent(kind, point, point, point, button, buttons,
                            Qt.KeyboardModifier.NoModifier, source)
        APP.sendEvent(self.canvas, event)

    def tablet(self, kind, position, pressure=0.5, device=None, buttons=Qt.MouseButton.LeftButton):
        button = Qt.MouseButton.NoButton if kind == QEvent.Type.TabletMove else Qt.MouseButton.LeftButton
        event = QTabletEvent(kind, device or self.pen, QPointF(*position), QPointF(*position),
                             pressure, 12, -8, 0, 0, 0,
                             Qt.KeyboardModifier.NoModifier, button, buttons)
        event.setTimestamp(100)
        APP.sendEvent(self.canvas, event)

    def touch(self, kind, points):
        # QEventPoint's Python constructor cannot set its local position. Use
        # positioned point doubles here, and a separate QTest dispatch test below.
        objects = [SimpleNamespace(id=lambda i=i: i, position=lambda p=p: QPointF(*p),
                                   state=lambda: QEventPoint.State.Updated)
                   for i, p in points.items()]
        event = SimpleNamespace(type=lambda: kind, points=lambda: objects, accept=lambda: None)
        self.canvas._handle_touch(event)

    def paint_ink(self, preview=True):
        image = self.canvas._new_layer()
        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.canvas._paint_document(painter, allow_preview=preview)
        painter.end()
        return image

    def test_qtest_mouse_stroke_single_command_and_synthetic_mouse_ignored(self):
        QTest.mousePress(self.canvas, Qt.MouseButton.LeftButton, pos=QPoint(30, 60))
        QTest.mouseMove(self.canvas, QPoint(100, 70))
        QTest.mouseRelease(self.canvas, Qt.MouseButton.LeftButton, pos=QPoint(160, 80))
        self.assertEqual(len(self.scene.document.strokes), 1)
        self.assertEqual(self.scene.undo_stack.count(), 1)
        stroke = self.scene.document.strokes[0]
        self.assertFalse(stroke.brush.pressure_enabled)
        self.assertGreaterEqual(len(stroke.samples), 2)
        for kind in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseMove, QEvent.Type.MouseButtonRelease):
            self.mouse(kind, (300, 200), source=Qt.MouseEventSource.MouseEventSynthesizedBySystem)
        self.assertEqual(len(self.scene.document.strokes), 1)
        self.scene.undo_stack.undo()
        self.assertEqual(self.scene.document.strokes, [])

    def test_tablet_pressure_tilt_and_release_ends_at_last_drawing_sample(self):
        self.canvas.set_view(2, QPointF(10, 20))
        self.tablet(QEvent.Type.TabletPress, (50, 80), pressure=0.2)
        self.tablet(QEvent.Type.TabletMove, (90, 100), pressure=0.85)
        self.tablet(QEvent.Type.TabletRelease, (110, 110), pressure=0, buttons=Qt.MouseButton.NoButton)
        self.assertEqual(len(self.scene.document.strokes), 1)
        stroke = self.scene.document.strokes[0]
        self.assertTrue(stroke.brush.pressure_enabled)
        self.assertEqual((stroke.samples[0].x, stroke.samples[0].y), (20, 30))
        self.assertAlmostEqual(stroke.samples[0].pressure, 0.2)
        self.assertAlmostEqual(stroke.samples[-1].pressure, 0.85)
        self.assertEqual((stroke.samples[-1].x, stroke.samples[-1].y), (40, 40))
        self.assertEqual(stroke.brush.render_profile, "pressure-v2")
        self.assertEqual(stroke.brush.input_scale, 2)
        self.assertEqual((stroke.samples[0].tilt_x, stroke.samples[0].tilt_y), (12, -8))
        self.assertEqual(self.scene.undo_stack.count(), 1)

    def test_pen_tail_and_side_button_are_temporary_tools(self):
        stroke = self.add_line()
        self.tablet(QEvent.Type.TabletPress, (150, 100), device=self.eraser)
        self.assertEqual(self.canvas._temporary_tool, "eraser")
        self.tablet(QEvent.Type.TabletRelease, (150, 100), pressure=0, device=self.eraser, buttons=Qt.MouseButton.NoButton)
        self.assertEqual(self.canvas.tool, "pen")
        self.assertFalse(visible_path(self.scene.get(stroke.id)).contains(QPointF(150, 100)))
        self.tablet(QEvent.Type.TabletMove, (10, 10), pressure=0, buttons=Qt.MouseButton.RightButton)
        self.assertEqual(self.canvas._temporary_tool, "lasso")
        self.tablet(QEvent.Type.TabletMove, (10, 10), pressure=0, buttons=Qt.MouseButton.NoButton)
        self.assertEqual(self.canvas._temporary_tool, "")
        self.assertEqual(self.canvas.tool, "pen")

    def test_barrel_lasso_selection_can_be_dragged_after_button_release(self):
        stroke = self.add_line()
        buttons = Qt.MouseButton.LeftButton | Qt.MouseButton.RightButton
        self.tablet(QEvent.Type.TabletPress, (20, 70), buttons=buttons)
        for point in ((300, 70), (300, 130), (20, 130), (20, 70)):
            self.tablet(QEvent.Type.TabletMove, point, buttons=buttons)
        self.tablet(QEvent.Type.TabletRelease, (20, 70), pressure=0, buttons=Qt.MouseButton.NoButton)
        self.assertEqual(self.canvas.selection_ids, {stroke.id})
        self.assertEqual(self.canvas.tool, "pen")
        self.tablet(QEvent.Type.TabletPress, (100, 100))
        self.assertEqual(self.canvas._interaction, "move")
        self.tablet(QEvent.Type.TabletMove, (120, 150))
        self.tablet(QEvent.Type.TabletRelease, (120, 150), pressure=0, buttons=Qt.MouseButton.NoButton)
        self.assertEqual(len(self.scene.document.strokes), 1)
        self.assertEqual(self.scene.get(stroke.id).offset_x, 20)
        self.assertEqual(self.scene.get(stroke.id).offset_y, 50)

    def test_local_erase_live_preview_matches_commit_and_is_one_command(self):
        stroke = self.add_line()
        self.canvas.set_tool("eraser")
        self.mouse(QEvent.Type.MouseButtonPress, (120, 90))
        self.mouse(QEvent.Type.MouseMove, (180, 110))
        preview = self.paint_ink()
        self.assertTrue(self.canvas._erase_preview)
        self.assertEqual(len(self.scene.get(stroke.id).erase_masks), 0)
        self.mouse(QEvent.Type.MouseButtonRelease, (180, 110), buttons=Qt.MouseButton.NoButton)
        committed = self.paint_ink()
        for x in (60, 100, 125, 150, 175, 200, 240):
            with self.subTest(x=x):
                self.assertEqual(preview.pixelColor(x, 100), committed.pixelColor(x, 100))
        self.assertEqual(len(self.scene.get(stroke.id).erase_masks), 1)
        self.assertEqual(self.scene.undo_stack.count(), 2)
        self.scene.undo_stack.undo()
        self.assertEqual(self.scene.get(stroke.id).erase_masks, [])

    def test_whole_erase_live_preview_and_undo(self):
        self.add_line()
        self.canvas.set_tool("eraser")
        self.canvas.set_eraser(12, True)
        self.mouse(QEvent.Type.MouseButtonPress, (150, 100))
        preview = self.paint_ink()
        self.assertEqual(preview.pixelColor(60, 100).name(), "#ffffff")
        self.mouse(QEvent.Type.MouseButtonRelease, (150, 100), buttons=Qt.MouseButton.NoButton)
        self.assertEqual(self.scene.document.strokes, [])
        self.scene.undo_stack.undo()
        self.assertEqual(len(self.scene.document.strokes), 1)

    def test_lasso_moves_both_fragments_and_the_erase_mask(self):
        stroke = self.add_line(mask=True)
        self.canvas.set_tool("lasso")
        self.mouse(QEvent.Type.MouseButtonPress, (60, 80))
        for point in ((100, 80), (100, 120), (60, 120), (60, 80)):
            self.mouse(QEvent.Type.MouseMove, point)
        self.mouse(QEvent.Type.MouseButtonRelease, (60, 80), buttons=Qt.MouseButton.NoButton)
        self.assertEqual(self.canvas.selection_ids, {stroke.id})
        self.mouse(QEvent.Type.MouseButtonPress, (80, 100))
        self.mouse(QEvent.Type.MouseMove, (130, 200))
        self.mouse(QEvent.Type.MouseButtonRelease, (130, 200), buttons=Qt.MouseButton.NoButton)
        moved = self.scene.get(stroke.id)
        self.assertEqual((moved.offset_x, moved.offset_y), (50, 100))
        self.assertFalse(visible_path(moved).contains(QPointF(200, 200)))
        self.assertTrue(visible_path(moved).contains(QPointF(125, 200)))
        self.assertTrue(visible_path(moved).contains(QPointF(275, 200)))
        self.assertEqual(self.scene.undo_stack.count(), 2)
        QTest.keyClick(self.canvas, Qt.Key.Key_Delete)
        self.assertEqual(self.scene.document.strokes, [])
        self.scene.undo_stack.undo()
        self.assertEqual(len(self.scene.document.strokes), 1)

    def test_zoom_clamps_and_preserves_world_anchor(self):
        anchor = QPointF(100, 150)
        self.canvas.set_view(1.5, QPointF(30, -20))
        original = self.canvas.screen_to_world(anchor)
        for factor, expected in ((100, 8), (0.00001, 0.1), (10, 1)):
            self.canvas.zoom_by(factor, anchor)
            self.assertAlmostEqual(self.canvas.view_scale, expected)
            actual = self.canvas.screen_to_world(anchor)
            self.assertAlmostEqual(actual.x(), original.x())
            self.assertAlmostEqual(actual.y(), original.y())
            self.assertEqual(self.scene.document.view_scale, expected)

    def test_touch_single_pan_two_finger_zoom_and_transition_are_continuous(self):
        self.touch(QEvent.Type.TouchBegin, {0: (100, 100)})
        self.touch(QEvent.Type.TouchUpdate, {0: (130, 120)})
        self.assertEqual(self.canvas.view_offset, QPointF(30, 20))
        self.touch(QEvent.Type.TouchUpdate, {0: (130, 120), 1: (230, 120)})
        self.assertEqual(self.canvas.view_scale, 1)
        self.assertEqual(self.canvas.view_offset, QPointF(30, 20))
        self.touch(QEvent.Type.TouchUpdate, {0: (80, 120), 1: (280, 120)})
        self.assertEqual(self.canvas.view_scale, 2)
        old_offset = QPointF(self.canvas.view_offset)
        self.touch(QEvent.Type.TouchUpdate, {1: (280, 120)})
        self.assertEqual(self.canvas.view_offset, old_offset)
        self.touch(QEvent.Type.TouchUpdate, {1: (300, 130)})
        self.assertEqual(self.canvas.view_offset, old_offset + QPointF(20, 10))
        self.assertEqual(self.canvas.view_scale, 2)
        self.touch(QEvent.Type.TouchCancel, {})
        self.assertEqual(self.canvas._touch_ids, ())
        self.assertTrue(self.canvas._gesture_image.isNull())
        self.assertEqual(self.scene.document.strokes, [])

    def test_qtest_touch_dispatch_pans_without_ink(self):
        device = QTest.createTouchDevice()
        QTest.touchEvent(self.canvas, device).press(0, QPoint(100, 100), self.canvas).commit()
        APP.processEvents()
        QTest.touchEvent(self.canvas, device).move(0, QPoint(130, 120), self.canvas).commit()
        APP.processEvents()
        QTest.touchEvent(self.canvas, device).release(0, QPoint(130, 120), self.canvas).commit()
        APP.processEvents()
        self.assertEqual(self.canvas.view_offset, QPointF(30, 20))
        self.assertEqual(self.scene.document.strokes, [])
        self.assertEqual(self.canvas._touch_ids, ())

    def test_pen_proximity_blocks_touch_until_all_contacts_release(self):
        self.touch(QEvent.Type.TouchBegin, {0: (100, 100)})
        self.canvas.eventFilter(APP, QEvent(QEvent.Type.TabletEnterProximity))
        self.touch(QEvent.Type.TouchUpdate, {0: (200, 200)})
        self.assertTrue(self.canvas._touch_blocked)
        self.assertEqual(self.canvas.view_offset, QPointF())
        self.canvas.eventFilter(APP, QEvent(QEvent.Type.TabletLeaveProximity))
        self.touch(QEvent.Type.TouchUpdate, {0: (300, 300)})
        self.assertEqual(self.canvas.view_offset, QPointF())
        self.touch(QEvent.Type.TouchEnd, {})
        self.touch(QEvent.Type.TouchBegin, {1: (100, 100)})
        self.touch(QEvent.Type.TouchUpdate, {1: (120, 100)})
        self.assertEqual(self.canvas.view_offset, QPointF(20, 0))

    def test_cancel_commits_received_samples_without_bridging_next_press(self):
        self.mouse(QEvent.Type.MouseButtonPress, (30, 50))
        self.mouse(QEvent.Type.MouseMove, (60, 50))
        self.canvas.eventFilter(APP, QEvent(QEvent.Type.ApplicationDeactivate))
        self.assertEqual(len(self.scene.document.strokes), 1)
        self.mouse(QEvent.Type.MouseMove, (400, 400))
        self.assertEqual(self.canvas._interaction, "")
        QTest.mouseClick(self.canvas, Qt.MouseButton.LeftButton, pos=QPoint(500, 300))
        self.assertEqual(len(self.scene.document.strokes), 2)
        self.assertLess(max(point.x for point in self.scene.document.strokes[0].samples), 100)
        self.assertGreater(min(point.x for point in self.scene.document.strokes[1].samples), 400)

    def test_focus_loss_finishes_active_stroke(self):
        self.mouse(QEvent.Type.MouseButtonPress, (30, 50))
        self.mouse(QEvent.Type.MouseMove, (60, 50))
        APP.sendEvent(self.canvas, QFocusEvent(QEvent.Type.FocusOut, Qt.FocusReason.TabFocusReason))
        self.assertEqual(self.canvas._interaction, "")
        self.assertEqual(len(self.scene.document.strokes), 1)
        self.mouse(QEvent.Type.MouseMove, (400, 400))
        self.assertLess(max(point.x for point in self.scene.document.strokes[0].samples), 100)

    def test_finishing_touch_before_undo_drops_frozen_preview(self):
        self.add_line()
        self.touch(QEvent.Type.TouchBegin, {0: (100, 100)})
        self.assertFalse(self.canvas._gesture_image.isNull())
        self.canvas.finish_interaction()
        self.scene.undo_stack.undo()
        self.assertTrue(self.canvas._gesture_image.isNull())
        self.assertEqual(self.scene.document.strokes, [])
        self.touch(QEvent.Type.TouchUpdate, {0: (200, 100)})
        self.assertEqual(self.canvas.view_offset, QPointF())
        self.touch(QEvent.Type.TouchEnd, {})
        self.touch(QEvent.Type.TouchBegin, {1: (100, 100)})
        self.touch(QEvent.Type.TouchUpdate, {1: (130, 100)})
        self.assertEqual(self.canvas.view_offset, QPointF(30, 0))

    def test_recovery_snapshot_includes_live_ink_without_committing(self):
        notifications = []
        self.canvas.edit_in_progress.connect(lambda: notifications.append(True))
        self.canvas._begin(QPointF(30, 50), "pen", "pen", .25)
        self.canvas._move(QPointF(60, 50), .75, 10)
        snapshot = self.canvas.snapshot_document()
        self.assertTrue(self.canvas.has_active_ink)
        self.assertTrue(notifications)
        self.assertEqual(self.scene.document.strokes, [])
        self.assertEqual(len(snapshot.strokes[0].samples), 2)
        self.canvas._move(QPointF(90, 50), .5, 20)
        self.assertEqual(len(snapshot.strokes[0].samples), 2)
        self.canvas.finish_interaction()
        self.assertFalse(self.canvas.has_active_ink)
        self.assertEqual(self.scene.document.strokes[0].id, snapshot.strokes[0].id)
        self.assertEqual(self.scene.undo_stack.count(), 1)

    def test_recovery_captures_continuous_erase_without_splitting_undo(self):
        stroke = self.add_line()
        self.canvas.set_tool("eraser")
        notifications = []
        self.canvas.edit_in_progress.connect(lambda: notifications.append(True))
        self.canvas._begin(QPointF(150, 100), "pen", "eraser")
        snapshot = self.canvas.snapshot_document()
        self.assertTrue(self.canvas.has_active_edit)
        self.assertTrue(notifications)
        self.assertFalse(visible_path(snapshot.strokes[0]).contains(QPointF(150, 100)))
        self.assertTrue(visible_path(self.scene.get(stroke.id)).contains(QPointF(150, 100)))
        self.assertEqual(self.scene.undo_stack.count(), 1)
        self.canvas._move(QPointF(200, 100))
        self.assertTrue(visible_path(snapshot.strokes[0]).contains(QPointF(200, 100)))
        self.canvas.finish_interaction()
        self.assertEqual(self.scene.undo_stack.count(), 2)
        self.assertFalse(visible_path(self.scene.get(stroke.id)).contains(QPointF(200, 100)))
        self.canvas.set_eraser(12, True)
        self.canvas._begin(QPointF(70, 100), "pen", "eraser")
        self.assertEqual(self.canvas.snapshot_document().strokes, [])
        self.assertEqual(len(self.scene.document.strokes), 1)

    def test_recovery_captures_live_move_and_retains_local_masks(self):
        stroke = self.add_line(mask=True)
        self.canvas.selection_ids = {stroke.id}
        self.canvas._begin(QPointF(80, 100), "pen", "pen")
        self.canvas._move(QPointF(130, 200))
        snapshot = self.canvas.snapshot_document()
        self.assertTrue(self.canvas.has_active_edit)
        self.assertEqual((snapshot.strokes[0].offset_x, snapshot.strokes[0].offset_y), (50, 100))
        self.assertFalse(visible_path(snapshot.strokes[0]).contains(QPointF(200, 200)))
        self.assertEqual(self.scene.get(stroke.id).offset_x, 0)
        self.canvas._move(QPointF(180, 200))
        self.assertEqual(snapshot.strokes[0].offset_x, 50)
        self.canvas.finish_interaction()
        self.assertEqual(self.scene.undo_stack.count(), 2)

    def test_png_export_omits_selection_and_eraser_cursor(self):
        stroke = self.add_line()
        self.canvas.selection_ids = {stroke.id}
        self.canvas.tool = "eraser"
        self.canvas._pointer_inside = True
        self.canvas._last_pointer = QPointF(400, 300)
        self.canvas.update()
        APP.processEvents()
        screenshot = self.canvas.grab().toImage()
        self.assertFalse(screenshot.isNull())
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "当前视图.png"
            self.assertTrue(self.canvas.export_png(path))
            exported = QImage(str(path))
            self.assertEqual(exported.size(), self.canvas._new_layer().size())
            self.assertEqual(exported.pixelColor(100, 100).name(), "#222222")
            self.assertEqual(exported.pixelColor(400, 300).name(), "#ffffff")
            # The shaded selection background is chrome, never exported.
            self.assertEqual(exported.pixelColor(100, 91).name(), "#ffffff")
            self.assertNotEqual(screenshot.pixelColor(100, 91), exported.pixelColor(100, 91))


if __name__ == "__main__":
    unittest.main()

"""Raster objects stay editable, embedded and below ink in all paint paths."""
from __future__ import annotations

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path
import tempfile
import unittest
from dataclasses import replace
from unittest.mock import patch

from PySide6.QtCore import QEvent, QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QInputDevice, QPainter, QPointingDevice, QTabletEvent
from PySide6.QtWidgets import QApplication

from whiteboard.canvas import Canvas
from whiteboard.exporting import export_pdf, export_png
from whiteboard.images import ImageError, MAX_IMAGE_WORLD_SIZE, image_rect, validate_images
from whiteboard.models import Brush, InkSample, Stroke
from whiteboard.scene import Scene
from whiteboard.renderer import ImageRenderer
from whiteboard.storage import load_document, save_document


APP = QApplication.instance() or QApplication([])


class ImageCanvasTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "课程 图片.png"
        image = QImage(120, 80, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(QColor("#DF563A"))
        self.assertTrue(image.save(str(self.path)))
        self.scene = Scene()
        self.canvas = Canvas(self.scene)
        self.canvas.resize(640, 480)
        self.canvas.show()
        APP.processEvents()

    def tearDown(self):
        self.canvas.cancel_input()
        self.canvas._grid_timer.stop()
        self.canvas._grid_fade_timer.stop()
        APP.removeEventFilter(self.canvas)
        self.canvas.close()
        self.canvas.deleteLater()
        APP.processEvents()

    def insert(self):
        image_id = self.canvas.insert_image_path(self.path)
        self.canvas.set_tool("select")
        return self.scene.get_image(image_id)

    def render(self, preview=True):
        image = self.canvas._new_layer()
        painter = QPainter(image)
        self.canvas._paint_document(painter, allow_preview=preview)
        painter.end()
        return image

    def test_insert_at_zoom_is_centered_self_contained_and_undoable(self):
        self.canvas.set_view(2, QPointF(13, 27))
        item = self.insert()
        center = self.canvas.world_to_screen(image_rect(item).center())
        self.assertEqual(center, QPointF(self.canvas.rect().center()))
        self.assertEqual((item.width, item.height), (60, 40))
        self.assertEqual(self.canvas.selection_ids, {item.id})
        self.assertEqual(self.scene.undo_stack.count(), 1)
        self.path.unlink()
        target = Path(self.directory.name) / "image.qboard"
        save_document(target, self.scene.document)
        restored = load_document(target)
        self.assertEqual(restored.images, [item])
        self.assertEqual(self.render(False).pixelColor(center.toPoint()), QColor("#DF563A"))
        self.scene.undo_stack.undo()
        self.assertFalse(self.scene.document.images)
        self.scene.undo_stack.redo()
        self.assertEqual(self.scene.document.images, [item])

    def test_drag_snapshot_commits_once_and_delete_undo_restores_image(self):
        item = self.insert()
        center = self.canvas.world_to_screen(image_rect(item).center())
        self.canvas._begin(center, "mouse", "select")
        self.canvas._move(center + QPointF(45, 30))
        self.assertEqual(self.scene.get_image(item.id).x, item.x)
        snapshot = self.canvas.snapshot_document()
        self.assertEqual(snapshot.images[0].x, item.x + 45)
        self.assertTrue(self.canvas.has_active_edit)
        self.canvas.finish_interaction()
        self.assertEqual(self.scene.undo_stack.count(), 2)
        self.assertEqual(self.scene.get_image(item.id).y, item.y + 30)
        self.scene.undo_stack.undo()
        self.assertEqual(self.scene.get_image(item.id), item)
        self.canvas.selection_ids = {item.id}
        self.canvas.delete_selection()
        self.assertFalse(self.scene.document.images)
        self.scene.undo_stack.undo()
        self.assertEqual(self.scene.get_image(item.id), item)

    def test_four_corners_resize_about_opposite_anchor_and_keep_aspect(self):
        self.canvas.set_view(1.5, QPointF(17, -5))
        item = self.insert()
        original = image_rect(item)
        corners = self.canvas._image_corners(original)
        for corner in range(4):
            with self.subTest(corner=corner):
                self.canvas.selection_ids = {item.id}
                point = corners[corner]
                anchor = corners[(corner + 2) % 4]
                target = anchor + (point - anchor) * 1.5
                self.canvas._begin(self.canvas.world_to_screen(point), "mouse", "select")
                self.assertEqual(self.canvas._interaction, "resize-image")
                self.canvas._move(self.canvas.world_to_screen(target))
                snapshot = self.canvas.snapshot_document()
                self.assertAlmostEqual(snapshot.images[0].width, original.width() * 1.5)
                self.assertEqual(self.scene.get_image(item.id), item)
                self.canvas.finish_interaction()
                changed = self.scene.get_image(item.id)
                self.assertAlmostEqual(changed.width / changed.height, 1.5)
                self.assertEqual(self.canvas._image_corners(image_rect(changed))[(corner + 2) % 4], anchor)
                self.assertEqual(self.scene.document.strokes, [])
                self.scene.undo_stack.undo()
                self.assertEqual(self.scene.get_image(item.id), item)

    def test_lasso_inside_image_selects_moves_and_deletes_only_ink(self):
        item = self.insert()
        rect = image_rect(item)
        stroke = Stroke([InkSample(rect.left() + 30, rect.center().y()),
                         InkSample(rect.right() - 30, rect.center().y())],
                        Brush(width=4, pressure_enabled=False))
        self.scene.add_stroke(stroke)
        self.canvas.set_tool("lasso")
        self.assertFalse(self.canvas.selection_ids)
        area = rect.adjusted(20, 20, -20, -20)
        self.canvas._begin(area.topLeft(), "mouse", "lasso")
        self.assertEqual(self.canvas._interaction, "lasso")
        for point in (area.topRight(), area.bottomRight(), area.bottomLeft(), area.topLeft()):
            self.canvas._move(point)
        self.canvas.finish_interaction()
        self.assertEqual(self.canvas.selection_ids, {stroke.id})
        self.canvas._begin(rect.center(), "mouse", "lasso")
        self.canvas._move(rect.center() + QPointF(25, 10))
        self.canvas.finish_interaction()
        self.assertEqual(self.scene.undo_stack.count(), 3)
        self.assertEqual(self.scene.get(stroke.id).offset_x, 25)
        self.assertEqual(self.scene.get_image(item.id), item)
        self.scene.undo_stack.undo()
        self.assertEqual(self.scene.get(stroke.id).offset_x, 0)
        self.assertEqual(self.scene.get_image(item.id), item)
        self.canvas.delete_selection()
        self.assertFalse(self.scene.document.strokes)
        self.assertEqual(self.scene.get_image(item.id), item)
        self.scene.undo_stack.undo()
        self.assertEqual(self.scene.get(stroke.id), stroke)

    def test_drawing_tools_cannot_move_or_resize_a_selected_image(self):
        item = self.insert()
        rect = image_rect(item)
        for tool in ("pen", "highlighter"):
            for position in (rect.center(), rect.topLeft(), rect.bottomRight()):
                with self.subTest(tool=tool, position=position):
                    # Also covers a temporary tool whose selection has not
                    # passed through the normal toolbar set_tool transition.
                    self.canvas.selection_ids = {item.id}
                    self.canvas._begin(position, "pen", tool, .6)
                    self.assertEqual(self.canvas._interaction, "ink")
                    self.canvas._move(position + QPointF(20, 10), .7)
                    self.canvas.finish_interaction()
                    self.assertEqual(self.scene.get_image(item.id), item)
        self.assertEqual(len(self.scene.document.strokes), 6)

    def test_pointer_selects_top_image_through_ink_and_blank_clears_selection(self):
        lower = self.insert()
        upper = self.insert()
        center = image_rect(upper).center()
        stroke = Stroke([InkSample(center.x() - 20, center.y()),
                         InkSample(center.x() + 20, center.y())], Brush(width=10))
        self.scene.add_stroke(stroke)
        self.canvas.selection_ids = {stroke.id, lower.id}
        self.canvas._begin(center, "pen", "select")
        self.assertEqual(self.canvas.selection_ids, {upper.id})
        self.canvas._move(center + QPointF(20, 10))
        self.canvas.finish_interaction()
        self.assertEqual(self.scene.get(stroke.id), stroke)
        self.assertEqual(self.scene.get_image(lower.id), lower)
        self.assertEqual(self.scene.get_image(upper.id).x, upper.x + 20)
        self.canvas._begin(QPointF(10, 10), "mouse", "select")
        self.canvas._move(QPointF(30, 30))
        self.canvas.finish_interaction()
        self.assertFalse(self.canvas.selection_ids)
        self.assertEqual(self.scene.document.strokes, [stroke])

    def test_lasso_enclosing_whole_image_still_selects_only_ink(self):
        item = self.insert()
        rect = image_rect(item)
        stroke = Stroke([InkSample(rect.left(), rect.center().y()),
                         InkSample(rect.right(), rect.center().y())], Brush(width=4))
        self.scene.add_stroke(stroke)
        area = rect.adjusted(-30, -30, 30, 30)
        self.canvas._begin(area.topLeft(), "pen", "lasso")
        for point in (area.topRight(), area.bottomRight(), area.bottomLeft(), area.topLeft()):
            self.canvas._move(point)
        self.canvas.finish_interaction()
        self.assertEqual(self.canvas.selection_ids, {stroke.id})
        # Returning to the pointer cannot reuse an ink selection for dragging.
        self.canvas._begin(rect.center(), "pen", "select")
        self.assertEqual(self.canvas.selection_ids, {item.id})
        self.canvas._move(rect.center() + QPointF(10, 20))
        self.canvas.finish_interaction()
        self.assertEqual(self.scene.get(stroke.id), stroke)

    def test_barrel_release_before_lift_cannot_turn_ink_lasso_into_image_drag(self):
        item = self.insert()
        center = image_rect(item).center()
        stroke = Stroke([InkSample(center.x() - 20, center.y()),
                         InkSample(center.x() + 20, center.y())], Brush(width=4))
        self.scene.add_stroke(stroke)
        device = QPointingDevice("image selection test pen", 8720,
                                 QInputDevice.DeviceType.Stylus, QPointingDevice.PointerType.Pen,
                                 QInputDevice.Capability.Position | QInputDevice.Capability.Pressure, 1, 3)

        def send(kind, point, buttons):
            button = Qt.MouseButton.NoButton if kind == QEvent.Type.TabletMove else Qt.MouseButton.LeftButton
            event = QTabletEvent(kind, device, point, self.canvas.mapToGlobal(point),
                                 0 if kind == QEvent.Type.TabletRelease else .6,
                                 0, 0, 0, 0, 0, Qt.KeyboardModifier.NoModifier, button, buttons)
            APP.sendEvent(self.canvas, event)

        ring = [center + QPointF(x, y) for x, y in ((-35, -25), (35, -25), (35, 25), (-35, 25), (-35, -25))]
        side = Qt.MouseButton.LeftButton | Qt.MouseButton.RightButton
        send(QEvent.Type.TabletPress, ring[0], side)
        for index, point in enumerate(ring[1:]):
            send(QEvent.Type.TabletMove, point, side if index == 0 else Qt.MouseButton.LeftButton)
            self.assertEqual(self.canvas._interaction, "lasso")
        send(QEvent.Type.TabletRelease, ring[-1], Qt.MouseButton.NoButton)
        self.assertEqual(self.canvas.selection_ids, {stroke.id})
        self.assertEqual(self.canvas.tool, "select")
        self.assertEqual(self.scene.get_image(item.id), item)
        self.assertEqual(self.scene.undo_stack.count(), 2)
        # The next contact belongs to the restored pointer, so only the image moves.
        send(QEvent.Type.TabletPress, center, Qt.MouseButton.LeftButton)
        send(QEvent.Type.TabletMove, center + QPointF(15, 10), Qt.MouseButton.LeftButton)
        send(QEvent.Type.TabletRelease, center + QPointF(15, 10), Qt.MouseButton.NoButton)
        self.assertEqual(self.scene.get_image(item.id).x, item.x + 15)
        self.assertEqual(self.scene.get(stroke.id), stroke)

    def test_eraser_preview_and_commit_reveal_image_without_erasing_it(self):
        item = self.insert()
        rect = image_rect(item)
        center = rect.center()
        stroke = Stroke([InkSample(rect.left(), center.y()), InkSample(rect.right(), center.y())],
                        Brush(color="#222222", width=14, pressure_enabled=False))
        self.scene.add_stroke(stroke)
        self.canvas.clear_selection()
        self.assertEqual(self.render().pixelColor(center.toPoint()), QColor("#222222"))
        self.canvas._begin(center, "mouse", "eraser")
        self.canvas._move(center + QPointF(0, 3))
        self.assertEqual(self.render().pixelColor(center.toPoint()), QColor("#DF563A"))
        self.canvas.finish_interaction()
        self.assertEqual(self.render().pixelColor(center.toPoint()), QColor("#DF563A"))
        self.assertEqual(self.scene.document.images, [item])
        self.scene.undo_stack.undo()
        self.assertEqual(self.render().pixelColor(center.toPoint()), QColor("#222222"))

    def test_image_only_export_png_pdf_and_fit_all(self):
        item = self.insert()
        self.canvas.clear_selection()
        png, pdf = Path(self.directory.name) / "out.png", Path(self.directory.name) / "out.pdf"
        args = (self.scene, self.canvas.size(), self.canvas.view_scale, self.canvas.view_offset)
        export_png(png, *args)
        export_pdf(pdf, *args)
        self.assertEqual(QImage(str(png)).pixelColor(image_rect(item).center().toPoint()), QColor("#DF563A"))
        contents = pdf.read_bytes()
        self.assertIn(b"/Subtype /Image", contents)
        self.assertIn(b"%%EOF", contents[-128:])
        self.scene.move_items({item.id}, 4000, 5000)
        self.canvas.fit_all()
        self.assertLess((self.canvas.world_to_screen(self.scene.bounds().center())
                         - QPointF(self.canvas.rect().center())).manhattanLength(), 0.01)

    def test_invalid_image_does_not_change_document_or_undo(self):
        self.insert()
        before = self.scene.document.images[:]
        count = self.scene.undo_stack.count()
        self.path.write_bytes(b"not an image")
        with self.assertRaises(ImageError):
            self.canvas.insert_image_path(self.path)
        self.assertEqual(self.scene.document.images, before)
        self.assertEqual(self.scene.undo_stack.count(), count)

    def test_thin_image_at_max_zoom_can_be_saved(self):
        pixels = QImage(1, 4096, QImage.Format.Format_RGB32)
        pixels.fill(QColor("#DF563A"))
        self.assertTrue(pixels.save(str(self.path)))
        self.canvas.set_view(8, QPointF())
        item = self.insert()
        self.assertGreaterEqual(item.width, 0.01)
        validate_images(self.canvas.snapshot_document().images)

    def test_movement_and_resize_at_coordinate_limit_remain_saveable(self):
        item = self.insert()
        limit = MAX_IMAGE_WORLD_SIZE
        item = replace(item, x=limit - 50, y=limit - 30)
        self.scene.reset(replace(self.scene.document, images=[item]))
        self.canvas.set_view(1, QPointF(-limit + 300, -limit + 200))
        self.canvas.selection_ids = {item.id}
        center = self.canvas.world_to_screen(image_rect(item).center())
        self.canvas._begin(center, "mouse", "select")
        self.canvas._move(center + QPointF(1000, 1000))
        validate_images(self.canvas.snapshot_document().images)
        self.canvas.finish_interaction()
        self.assertEqual((self.scene.get_image(item.id).x, self.scene.get_image(item.id).y), (limit, limit))
        corner = self.canvas.world_to_screen(image_rect(self.scene.get_image(item.id)).topLeft())
        self.canvas._begin(corner, "mouse", "select")
        self.canvas._move(corner + QPointF(1000, 1000))
        validate_images(self.canvas.snapshot_document().images)
        self.canvas.finish_interaction()
        self.assertLessEqual(self.scene.get_image(item.id).x, limit)

    def test_pixel_cache_reuses_decoded_data_when_image_geometry_changes(self):
        item = self.insert()
        renderer = ImageRenderer()
        with patch("whiteboard.renderer.decode_image", wraps=__import__("whiteboard.images", fromlist=["decode_image"]).decode_image) as decode:
            original = renderer.pixels(item)
            moved = renderer.pixels(replace(item, x=item.x + 100, width=item.width * 2))
        self.assertEqual(original.cacheKey(), moved.cacheKey())
        self.assertEqual(decode.call_count, 1)
        self.assertEqual(renderer.cache_bytes, original.sizeInBytes())

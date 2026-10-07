"""Behavioral checks for the raster eraser preview and vector transactions.

Pixel assertions compare filled interiors exactly. Only pixels within one pixel
of a color boundary may differ between raster preview and vector antialiasing.
"""
from __future__ import annotations

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QImage, QPainter
from PySide6.QtWidgets import QApplication

from whiteboard.canvas import Canvas
from whiteboard.geometry import visible_path
from whiteboard.models import Brush, EraseMask, InkSample, Stroke
from whiteboard.recovery import RecoveryManager
from whiteboard.scene import Scene
from whiteboard.storage import document_to_dict, load_document, save_document


APP = QApplication.instance() or QApplication([])


class ErasePreviewTests(unittest.TestCase):
    def setUp(self):
        self.scene = Scene()
        self.canvas = Canvas(self.scene)
        self.canvas.resize(640, 360)

    def tearDown(self):
        self.canvas.cancel_input()
        self.canvas._grid_timer.stop()
        self.canvas._grid_fade_timer.stop()
        APP.removeEventFilter(self.canvas)
        self.canvas.close()
        self.canvas.deleteLater()
        APP.processEvents()

    def line(self, points, color="#2563EB", width=40, kind="pen", opacity=1,
             offset=(0, 0), masks=None):
        stroke = Stroke([InkSample(x, y, t=index, pressure=1) for index, (x, y) in enumerate(points)],
                        Brush(color=color, width=width, kind=kind, pressure_enabled=False, opacity=opacity),
                        offset_x=offset[0], offset_y=offset[1], erase_masks=masks or [])
        self.scene.add_stroke(stroke)
        return self.scene.get(stroke.id)

    def horizontal(self, y, **kwargs):
        return self.line([(x, y) for x in range(40, 581, 10)], **kwargs)

    def render(self):
        image = self.canvas._new_layer()
        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.canvas._paint_document(painter)
        painter.end()
        return image

    def begin_erase(self, position, radius=12, whole=False):
        self.canvas.set_eraser(radius, whole)
        self.canvas._begin(QPointF(*position), "mouse", "eraser")

    def assert_same_interiors(self, actual, expected):
        self.assertEqual(actual.size(), expected.size())
        width, height = actual.width(), actual.height()
        aa = actual.convertToFormat(QImage.Format.Format_ARGB32)
        bb = expected.convertToFormat(QImage.Format.Format_ARGB32)
        a, b = bytes(aa.constBits()), bytes(bb.constBits())
        stride = aa.bytesPerLine()

        def pixel(buffer, x, y):
            start = y * stride + x * 4
            return buffer[start:start + 4]

        errors = []
        for y in range(1, height - 1):
            start = y * stride
            if a[start:start + stride] == b[start:start + stride]:
                continue
            for x in range(1, width - 1):
                first, second = pixel(a, x, y), pixel(b, x, y)
                if first == second:
                    continue
                # A differing pixel is allowed only next to an actual boundary.
                # Large missing strips or retained ink have flat 3x3 interiors
                # and therefore fail, even when the global image difference is small.
                near_boundary = any(
                    pixel(a, nx, ny) != first or pixel(b, nx, ny) != second
                    for ny in range(y - 1, y + 2) for nx in range(x - 1, x + 2)
                )
                if not near_boundary:
                    errors.append((x, y, actual.pixelColor(x, y).name(), expected.pixelColor(x, y).name()))
                    if len(errors) >= 5:
                        break
            if len(errors) >= 5:
                break
        self.assertEqual(errors, [], f"Filled interiors differ: {errors}")

    def reload_committed(self):
        with tempfile.TemporaryDirectory(prefix="qboard-erase-roundtrip-") as directory:
            path = Path(directory) / "擦除 后白板.qboard"
            save_document(path, self.scene.document)
            self.scene.reset(load_document(path))
            self.canvas.load_view_from_document()
        return self.render()

    def test_many_long_jumps_and_reversals_have_no_unerased_gaps(self):
        self.horizontal(120, width=90)
        original = self.render()
        original_commands = self.scene.undo_stack.count()
        self.begin_erase((80, 110), radius=12)
        # Many events arrive before a paint; spans cross the preview's batching
        # boundary repeatedly and reverse hundreds of screen pixels at a time.
        for _ in range(24):
            for point in ((540, 110), (80, 130), (540, 130), (80, 110)):
                self.canvas._move(QPointF(*point))
        preview = self.render()
        for y in (108, 110, 120, 130, 132):
            for x in range(95, 526):
                self.assertEqual(preview.pixelColor(x, y).name(), "#ffffff", (x, y))
        self.assertEqual(preview.pixelColor(250, 80).name(), "#2563eb")
        self.assertEqual(self.scene.undo_stack.count(), original_commands)
        self.canvas.finish_interaction()
        committed = self.render()
        self.assert_same_interiors(preview, committed)
        self.assertEqual(self.scene.undo_stack.count(), original_commands + 1)
        self.scene.undo_stack.undo()
        self.assert_same_interiors(self.render(), original)
        self.scene.undo_stack.redo()
        self.assert_same_interiors(self.render(), committed)
        self.assert_same_interiors(self.reload_committed(), committed)

    def test_incremental_frames_and_single_batch_have_equivalent_interiors(self):
        self.horizontal(160, color="#8B5CF6", width=100)
        baseline_commands = self.scene.undo_stack.count()
        points = [(x, 150 + (x % 35) / 7) for x in range(60, 561, 4)]
        self.begin_erase(points[0], radius=10)
        for point in points[1:]:
            self.canvas._move(QPointF(*point))
            self.render()
        frequent_preview = self.render()
        self.canvas.finish_interaction()
        committed = self.render()
        self.assert_same_interiors(frequent_preview, committed)
        self.scene.undo_stack.undo()
        self.begin_erase(points[0], radius=10)
        for point in points[1:]:
            self.canvas._move(QPointF(*point))
        batched_preview = self.render()
        self.assert_same_interiors(frequent_preview, batched_preview)
        self.canvas.finish_interaction()
        self.assertEqual(self.scene.undo_stack.count(), baseline_commands + 1)
        self.assert_same_interiors(self.render(), committed)

    def test_pen_and_overlapping_highlighters_erase_together_and_reveal_grid(self):
        self.canvas.set_grid_enabled(True)
        blank_grid = self.render()
        self.horizontal(120, color="#FFD84D", width=64, kind="highlighter", opacity=.3)
        self.horizontal(132, color="#8B5CF6", width=50, kind="highlighter", opacity=.4)
        self.horizontal(120, color="#222222", width=18)
        original = self.render()
        self.assertEqual(original.pixelColor(160, 120).name(), "#222222")
        self.begin_erase((160, 70), radius=14)
        self.canvas._move(QPointF(160, 185))
        preview = self.render()
        for point in ((160, 120), (160, 140), (155, 132), (160, 80)):
            self.assertEqual(preview.pixelColor(*point), blank_grid.pixelColor(*point), point)
        self.assertNotEqual(preview.pixelColor(160, 120).name(), "#ffffff")
        self.assertEqual(preview.pixelColor(240, 120), original.pixelColor(240, 120))
        self.assertEqual(preview.pixelColor(240, 142), original.pixelColor(240, 142))
        self.canvas.finish_interaction()
        committed = self.render()
        self.assert_same_interiors(preview, committed)
        self.assertEqual(committed.pixelColor(160, 120), blank_grid.pixelColor(160, 120))
        self.scene.undo_stack.undo()
        self.assert_same_interiors(self.render(), original)
        self.scene.undo_stack.redo()
        self.assert_same_interiors(self.reload_committed(), committed)

    def test_whole_stroke_removal_recomposes_untouched_overlapping_ink(self):
        self.canvas.set_grid_enabled(True)
        red = self.horizontal(140, color="#E5484D", width=22)
        blue = self.line([(400, y) for y in range(60, 271, 10)], color="#2563EB", width=30)
        yellow = self.line([(x, 140) for x in range(360, 511, 10)], color="#FFD84D", width=42,
                           kind="highlighter", opacity=.3)
        original = self.render()
        self.begin_erase((140, 140), radius=8, whole=True)
        preview = self.render()
        self.assertEqual(self.canvas._erase_preview, {red.id})
        self.assertEqual(preview.pixelColor(140, 140).name(), "#ffffff")
        self.assertEqual(preview.pixelColor(400, 140), original.pixelColor(400, 140))
        self.assertNotEqual(preview.pixelColor(450, 140), original.pixelColor(450, 140))
        self.assertEqual(preview.pixelColor(400, 210), original.pixelColor(400, 210))
        self.canvas.finish_interaction()
        committed = self.render()
        self.assertEqual({stroke.id for stroke in self.scene.document.strokes}, {blue.id, yellow.id})
        self.assert_same_interiors(preview, committed)
        self.scene.undo_stack.undo()
        self.assert_same_interiors(self.render(), original)
        self.scene.undo_stack.redo()
        self.assert_same_interiors(self.reload_committed(), committed)

    def test_live_recovery_keeps_local_mask_coordinates_without_committing_edit(self):
        stroke = self.line([(x, 80) for x in range(40, 501, 10)], width=38, offset=(30, 40),
                           masks=[EraseMask([(300, 40), (300, 120)], 7)])
        self.canvas.set_view(1.5, QPointF(-10, 15))
        self.canvas.set_grid_enabled(False)  # Disable transient zoom grid for comparison.
        start, finish = QPointF(220, 90), QPointF(220, 150)
        self.begin_erase((self.canvas.world_to_screen(start).x(), self.canvas.world_to_screen(start).y()), radius=15)
        self.canvas._move(self.canvas.world_to_screen(finish))
        preview = self.render()
        old_count = self.scene.undo_stack.count()
        with tempfile.TemporaryDirectory(prefix="qboard-live-erase-") as directory:
            recovery = RecoveryManager(self.canvas.snapshot_document, directory=directory)
            try:
                recovery.source_path = str(Path(directory) / "原始 白板.qboard")
                recovery.schedule()
                self.assertTrue(recovery.flush())
                captured = recovery.recover()
                changed = next(item for item in captured.strokes if item.id == stroke.id)
                self.assertEqual(len(changed.erase_masks), 2)
                self.assertEqual(changed.erase_masks[-1].points, [(190, 50), (190, 110)])
                self.assertEqual(changed.erase_masks[-1].radius, 10)
                self.assertFalse(visible_path(changed).contains(QPointF(220, 120)))
                self.assertEqual(len(self.scene.get(stroke.id).erase_masks), 1)
                self.assertEqual(self.scene.undo_stack.count(), old_count)
                self.assertTrue(self.canvas.has_active_edit)
            finally:
                recovery.shutdown()
        self.canvas.finish_interaction()
        self.assert_same_interiors(preview, self.render())
        self.assertEqual(document_to_dict(captured), document_to_dict(self.scene.document))

    def test_preview_image_and_hit_cache_are_released_when_interaction_ends(self):
        self.horizontal(120, width=30)
        for end in (self.canvas.finish_interaction, self.canvas.cancel_input,
                    lambda: self.canvas.set_tool("pen")):
            with self.subTest(end=end):
                self.begin_erase((120, 120), radius=10)
                self.render()
                self.assertFalse(self.canvas._erase_layer.isNull())
                self.assertGreater(self.canvas._erase_layer.sizeInBytes(), 0)
                end()
                self.assertTrue(self.canvas._erase_layer.isNull())
                self.assertEqual(self.canvas._erase_layer.sizeInBytes(), 0)
                self.assertFalse(self.canvas._erase_preview)
                self.assertEqual(self.canvas._erase_points, [])
                self.assertFalse(self.canvas.has_active_edit)

    def test_warmed_local_preview_never_replays_visible_paths_or_vector_differences(self):
        self.horizontal(120, width=60)
        self.horizontal(145, color="#FFD84D", width=45, kind="highlighter", opacity=.3)
        self.render()  # Warm the complete viewport's history tiles first.
        self.begin_erase((80, 120), radius=10)
        with patch("whiteboard.canvas.visible_path", side_effect=AssertionError("Live preview replayed a vector path")), \
                patch("whiteboard.renderer.visible_path", side_effect=AssertionError("Warm history was replayed")), \
                patch("whiteboard.geometry.QPainterPath.subtracted", side_effect=AssertionError("Live preview calculated vector differences")):
            for point in ((160, 120), (520, 130), (90, 145), (450, 150), (140, 135)):
                self.canvas._move(QPointF(*point))
                self.render()
            self.assertFalse(self.canvas._erase_layer.isNull())
            self.assertTrue(self.canvas._erase_preview)
        # Vector clipping belongs to the completed document transaction only.
        self.canvas.finish_interaction()
        self.assertTrue(self.canvas._erase_layer.isNull())

    def test_high_dpi_zoomed_end_caps_survive_erase_commit_and_reopen(self):
        # Keep the global QApplication DPI untouched for the rest of the suite.
        # Two device pixels per logical pixel and 800% zoom magnify any world-
        # space curve flattening introduced by the final boolean difference.
        with patch.object(self.canvas, "devicePixelRatioF", return_value=2.0):
            offset = QPointF(27, 33)
            self.canvas.set_view(8.0, offset)
            self.canvas.set_grid_enabled(False)
            points = [((x - offset.x()) / 8, (145 - offset.y()) / 8)
                      for x in (45, 585)]
            self.line(points, width=7.5)
            self.line(points, width=10, kind="highlighter", opacity=.35, color="#FACC15")
            original = self.render()
            self.assertEqual(original.devicePixelRatio(), 2.0)
            self.assertEqual(original.width(), self.canvas.width() * 2)

            self.begin_erase((75, 145), radius=12)
            for x in (530, 100, 510, 90):
                self.canvas._move(QPointF(x, 145))
            preview = self.render()
            # This pixel lies well outside the eraser's vertical extent, inside
            # the left round cap. The raster preview must leave it untouched.
            self.assertEqual(preview.pixelColor(72, 234), original.pixelColor(72, 234))
            for x in (80, 120, 260, 460, 520):
                self.assertEqual(preview.pixelColor(x * 2, 145 * 2).name(), "#ffffff")

            self.canvas.finish_interaction()
            committed = self.render()
            # The existing helper permits only one *physical* pixel of edge
            # antialiasing, including around untouched end caps; do not widen it.
            self.assert_same_interiors(preview, committed)
            self.scene.undo_stack.undo()
            self.assert_same_interiors(self.render(), original)
            self.scene.undo_stack.redo()
            self.assert_same_interiors(self.render(), committed)
            self.assert_same_interiors(self.reload_committed(), committed)


if __name__ == "__main__":
    unittest.main()

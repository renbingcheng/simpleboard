"""The navigation grid stays below ink and never enters exported documents."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path
import tempfile
import unittest

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QImage, QPainter
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from whiteboard.canvas import Canvas
from whiteboard.models import Brush, InkSample, Stroke
from whiteboard.scene import Scene


APP = QApplication.instance() or QApplication([])


class GridTests(unittest.TestCase):
    def setUp(self):
        self.scene = Scene()
        self.canvas = Canvas(self.scene)
        self.canvas.resize(640, 480)

    def tearDown(self):
        APP.removeEventFilter(self.canvas)
        self.canvas.close()
        self.canvas.deleteLater()
        APP.processEvents()

    def render(self):
        image = self.canvas._new_layer()
        painter = QPainter(image)
        self.canvas._paint_document(painter)
        painter.end()
        return image

    def test_zoom_reveals_grid_then_fades_without_editing_document(self):
        self.assertEqual(self.render().pixelColor(80, 83).name(), "#ffffff")
        self.canvas.zoom_by(2, QPointF())
        self.assertNotEqual(self.render().pixelColor(80, 83).name(), "#ffffff")
        self.assertEqual(self.scene.undo_stack.count(), 0)
        self.assertEqual(self.scene.document.strokes, [])
        QTest.qWait(1550)
        self.assertEqual(self.render().pixelColor(80, 83).name(), "#ffffff")
        self.canvas.set_grid_enabled(True)
        self.assertNotEqual(self.render().pixelColor(80, 83).name(), "#ffffff")

    def test_grid_tracks_world_coordinates_and_is_bounded_at_zoom_extremes(self):
        self.canvas.set_grid_enabled(True)
        self.canvas.set_view(2, QPointF(10, 15))
        image = self.render()
        self.assertNotEqual(image.pixelColor(90, 70).name(), "#ffffff")
        self.assertEqual(image.pixelColor(80, 70).name(), "#ffffff")
        self.canvas.set_view(2, QPointF(30, 15))
        image = self.render()
        self.assertNotEqual(image.pixelColor(110, 70).name(), "#ffffff")
        self.assertEqual(image.pixelColor(90, 70).name(), "#ffffff")
        for scale in (.1, .25, 1, 3, 8):
            self.canvas.set_view(scale, QPointF(-137, 63))
            minor, major = self.canvas._grid_lines()
            self.assertLess(len(minor)+len(major), 100)
            self.assertGreater(len(minor)+len(major), 10)

    def test_grid_under_ink_and_transparent_pan_preview_without_export_grid(self):
        self.canvas.set_grid_enabled(True)
        self.scene.add_stroke(Stroke([InkSample(50, 80), InkSample(200, 80)],
                                    Brush(color="#2563EB", width=12, pressure_enabled=False)))
        self.assertEqual(self.render().pixelColor(80, 80).name(), "#2563eb")
        self.canvas._begin(QPointF(100, 100), "mouse", "pan")
        self.canvas._move(QPointF(110, 100))
        self.assertNotEqual(self.render().pixelColor(90, 130).name(), "#ffffff")
        self.canvas.finish_interaction()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "grid-free.png"
            self.assertTrue(self.canvas.export_png(path))
            output = QImage(str(path))
            self.assertEqual(output.pixelColor(90, 130).name(), "#ffffff")
            self.assertEqual(output.pixelColor(90, 80).name(), "#2563eb")

    def test_live_erase_restores_grid_in_the_erased_area(self):
        self.scene.add_stroke(Stroke([InkSample(50, 80), InkSample(200, 80)],
                                    Brush(width=20, pressure_enabled=False)))
        self.canvas.set_grid_enabled(True)
        self.canvas._begin(QPointF(80, 80), "mouse", "eraser")
        preview = self.render()
        self.canvas.finish_interaction()
        committed = self.render()
        self.assertEqual(preview.pixelColor(80, 80), committed.pixelColor(80, 80))
        self.assertNotEqual(preview.pixelColor(80, 80).name(), "#ffffff")
        self.assertNotEqual(preview.pixelColor(80, 80).name(), "#222222")


if __name__ == "__main__":
    unittest.main()

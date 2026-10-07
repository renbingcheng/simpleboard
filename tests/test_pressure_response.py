"""Pressure-v2 behavior and frozen legacy geometry, independent of pen hardware."""
from __future__ import annotations

import hashlib
import math
import os
from pathlib import Path
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QImage, QInputDevice, QPainter, QPainterPath, QPointingDevice, QTabletEvent
from PySide6.QtWidgets import QApplication

from whiteboard.canvas import Canvas
from whiteboard.geometry import (
    IncrementalStrokeBuilder, _raw_ink_parts, clear_geometry_cache, pressure_width, visible_path,
)
from whiteboard.models import BoardDocument, Brush, InkSample, Stroke
from whiteboard.scene import Scene
from whiteboard.storage import load_document, save_document


APP = QApplication.instance() or QApplication([])


def brush_v2(**values):
    return Brush(render_profile="pressure-v2", **values)


def path_image(path):
    image = QImage(420, 200, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.fillPath(path, Qt.GlobalColor.black)
    painter.end()
    return image


class PressureGeometryTests(unittest.TestCase):
    def tearDown(self):
        clear_geometry_cache()

    def test_position_and_pressure_advance_with_the_same_weight(self):
        builder = IncrementalStrokeBuilder(brush_v2(width=12))
        builder.add(InkSample(20, 30, 100, .2))
        builder.add(InkSample(22, 34, 101, .9))
        x_weight = (builder._last[0] - 20) / 2
        y_weight = (builder._last[1] - 30) / 4
        pressure_weight = (builder._filtered_pressure - .2) / .7
        self.assertGreater(pressure_weight, 0)
        self.assertLess(pressure_weight, 1)
        self.assertAlmostEqual(x_weight, y_weight)
        self.assertAlmostEqual(x_weight, pressure_weight)
        self.assertLess(builder._radius, pressure_width(builder.brush, .9) / 2)

    def test_fast_path_lag_is_bounded_in_screen_units_and_zoom_invariant(self):
        screen_points = [(0, 0), (40, 15), (140, 40), (30, 90), (300, 20), (320, 140)]
        for interval in (0, 1, 8, 16):
            baseline = None
            for scale in (.1, .5, 1, 2, 8):
                with self.subTest(interval=interval, scale=scale):
                    builder = IncrementalStrokeBuilder(brush_v2(input_scale=scale))
                    filtered = []
                    for index, (x, y) in enumerate(screen_points):
                        builder.add(InkSample(x / scale, y / scale, index * interval, .6))
                        point = (builder._last[0] * scale, builder._last[1] * scale)
                        self.assertLessEqual(math.hypot(x - point[0], y - point[1]), .650000001)
                        filtered.append(point)
                    if baseline is None:
                        baseline = filtered
                    else:
                        for expected, actual in zip(baseline, filtered):
                            self.assertAlmostEqual(expected[0], actual[0], places=9)
                            self.assertAlmostEqual(expected[1], actual[1], places=9)

    def test_zero_move_is_retained_without_ink_and_positive_pressure_resumes(self):
        builder = IncrementalStrokeBuilder(brush_v2(width=8))
        first = InkSample(20, 40, 100, .6)
        zero = InkSample(180, 120, 110, 0)
        builder.add(first)
        original = QPainterPath(builder.path)
        self.assertTrue(builder.add(zero).isEmpty())
        self.assertEqual(builder.path, original)
        self.assertEqual(builder.samples, [first, zero])
        self.assertTrue(builder.last_segment.isEmpty())
        self.assertFalse(builder.add(InkSample(50, 40, 120, .3)).isEmpty())
        self.assertTrue(builder.path.contains(QPointF(40, 40)))
        self.assertFalse(builder.path.contains(QPointF(150, 100)))

    def test_finish_reaches_last_trusted_sample_without_extending_to_zero_move(self):
        builder = IncrementalStrokeBuilder(brush_v2(width=8))
        builder.add(InkSample(20, 40, 100, .8))
        builder.add(InkSample(100, 40, 108, .15))
        builder.add(InkSample(250, 140, 110, 0))
        raw = list(builder.samples)
        builder.finish()
        self.assertEqual(builder._last, (100, 40))
        self.assertAlmostEqual(builder._filtered_pressure, .15)
        self.assertEqual(builder.samples, raw)
        self.assertLess(builder.path.boundingRect().right(), 105)
        final = QPainterPath(builder.path)
        self.assertTrue(builder.finish().isEmpty())
        self.assertEqual(builder.path, final)

    def test_repeated_and_nonmonotonic_timestamps_remain_finite_and_finish(self):
        builder = IncrementalStrokeBuilder(brush_v2())
        for x, y, timestamp, pressure in ((10, 20, 100, .2), (30, 20, 100, .8),
                                           (30, 20, 100, .4), (40, 25, 99, .6),
                                           (60, 20, 101, .1)):
            builder.add(InkSample(x, y, timestamp, pressure))
            self.assertTrue(all(math.isfinite(value) for value in (*builder._last, builder._radius)))
            self.assertGreaterEqual(builder._filtered_pressure, 0)
            self.assertLessEqual(builder._filtered_pressure, 1)
        builder.finish()
        self.assertEqual(builder._last, (60, 20))
        self.assertAlmostEqual(builder._filtered_pressure, .1)

    def test_live_cold_and_24_sample_parts_replay_the_same_finished_ink(self):
        for count in (24, 25, 48, 49, 73):
            with self.subTest(count=count):
                brush = brush_v2(width=10, sensitivity=1.4, input_scale=2)
                samples = [InkSample(20 + index * 4, 80 + math.sin(index * .35) * 30,
                                     index * 4, .15 + .7 * (index % 9) / 8)
                           for index in range(count)]
                samples[-1].pressure = .12
                builder = IncrementalStrokeBuilder(brush)
                for sample in samples:
                    builder.add(sample)
                builder.finish()
                stroke = Stroke(samples, brush)
                clear_geometry_cache()
                self.assertEqual(builder.path, visible_path(stroke))
                grouped = QPainterPath()
                grouped.setFillRule(Qt.FillRule.WindingFill)
                for part in _raw_ink_parts(stroke):
                    grouped.addPath(part.path)
                self.assertEqual(path_image(builder.path), path_image(grouped))

    def test_erased_finished_ink_survives_cold_replay_move_and_save(self):
        samples = [InkSample(20 + index * 4, 80 + math.sin(index * .18) * 20,
                             index * 3, .2 + .7 * (index % 11) / 10)
                   for index in range(73)]
        stroke = Stroke(samples, brush_v2(width=14, sensitivity=1.2, input_scale=1.5))
        builder = IncrementalStrokeBuilder(stroke.brush)
        for sample in samples:
            builder.add(sample)
        builder.finish()
        scene = Scene()
        scene.add_stroke(stroke, cached_path=builder.path)
        scene.erase([(100, 20), (100, 145), (165, 20)], 4)
        erased = scene.get(stroke.id)
        self.assertIsNotNone(erased)
        self.assertTrue(erased.erase_masks)
        expected = visible_path(erased)
        clear_geometry_cache()
        self.assertEqual(expected, visible_path(erased))
        scene.move_strokes({stroke.id}, 3.125, -2.75)
        expected = visible_path(scene.get(stroke.id))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "pressure.qboard"
            save_document(path, scene.document)
            restored = load_document(path)
        clear_geometry_cache()
        self.assertEqual(restored.strokes[0].brush.render_profile, "pressure-v2")
        self.assertEqual(restored.strokes[0].brush.input_scale, 1.5)
        self.assertEqual(restored.strokes[0].samples, samples)
        self.assertEqual(expected, visible_path(restored.strokes[0]))

    def test_legacy_outline_matches_published_104_reference(self):
        # Generated independently from IncrementalStrokeBuilder extracted in
        # memory from the released SimpleBoard-1.0.4-source.zip. Its geometry.py
        # SHA256: 2c48a035568d1952f704da9e85cda7e56f02772cd7fd2dfae3b553873dfc10e5.
        # Rounded path elements make the reference stable to float formatting;
        # no dist archive or old source copy is required to run this test.
        expected = {
            ("pen", True): "fc8580553071c7f956146d59c120c6a49ebbdc728041cd036bb7b65018088e9e",
            ("pen", False): "5d0eb12a5b24ff60cd97721160a1f209e2ea64f71e554db2488efaee34f7a06e",
            ("highlighter", True): "5d0eb12a5b24ff60cd97721160a1f209e2ea64f71e554db2488efaee34f7a06e",
        }
        samples = [InkSample(10, 20, 1, .1), InkSample(20, 24, 1, .8), InkSample(21, 21, 6, 0),
                   InkSample(21, 21, 8, .3), InkSample(65, 33, 19, 1), InkSample(15, 60, 31, .06),
                   InkSample(23, 24, 35, .6)]
        for (kind, enabled), digest in expected.items():
            with self.subTest(kind=kind, enabled=enabled):
                builder = IncrementalStrokeBuilder(Brush(width=7.3, sensitivity=1.7, kind=kind,
                                                         pressure_enabled=enabled, render_profile="legacy-v1"))
                for sample in samples:
                    builder.add(sample)
                builder.finish()
                elements = [(int(builder.path.elementAt(i).type.value),
                             round(builder.path.elementAt(i).x, 9), round(builder.path.elementAt(i).y, 9))
                            for i in range(builder.path.elementCount())]
                self.assertEqual(len(elements), 121)
                self.assertEqual(hashlib.sha256(repr(elements).encode("ascii")).hexdigest(), digest)


class PressureCanvasTests(unittest.TestCase):
    def setUp(self):
        self.scene = Scene()
        self.canvas = Canvas(self.scene)
        self.canvas.resize(640, 480)
        self.canvas.show()
        APP.processEvents()
        self.canvas.set_brush(Brush(width=8))
        self.pen = QPointingDevice("pressure response pen", 9301, QInputDevice.DeviceType.Stylus,
                                   QPointingDevice.PointerType.Pen,
                                   QInputDevice.Capability.Position | QInputDevice.Capability.Pressure, 1, 3)

    def tearDown(self):
        self.canvas.cancel_input("pressure_test_cleanup")
        self.canvas._grid_timer.stop()
        self.canvas._grid_fade_timer.stop()
        APP.removeEventFilter(self.canvas)
        self.canvas.close()
        self.canvas.deleteLater()
        APP.processEvents()
        clear_geometry_cache()

    def tablet(self, kind, position, pressure, timestamp=100):
        button = Qt.MouseButton.NoButton if kind == QEvent.Type.TabletMove else Qt.MouseButton.LeftButton
        buttons = Qt.MouseButton.NoButton if kind == QEvent.Type.TabletRelease else Qt.MouseButton.LeftButton
        event = QTabletEvent(kind, self.pen, QPointF(*position), QPointF(*position), pressure,
                             0, 0, 0, 0, 0, Qt.KeyboardModifier.NoModifier, button, buttons)
        event.setTimestamp(timestamp)
        APP.sendEvent(self.canvas, event)

    def test_new_stroke_captures_zoom_and_zero_move_does_not_end_contact(self):
        self.canvas.set_view(2, QPointF(10, 20))
        self.tablet(QEvent.Type.TabletPress, (50, 80), .8)
        self.tablet(QEvent.Type.TabletMove, (150, 80), .4, 108)
        before = QPainterPath(self.canvas._builder.path)
        self.tablet(QEvent.Type.TabletMove, (300, 180), 0, 110)
        self.assertEqual(self.canvas._builder.path, before)
        self.assertEqual(self.canvas._builder.samples[-1].pressure, 0)
        self.assertTrue(self.canvas.has_active_ink)
        self.assertEqual(self.scene.undo_stack.count(), 0)
        self.tablet(QEvent.Type.TabletMove, (190, 80), .2, 112)
        self.tablet(QEvent.Type.TabletRelease, (400, 200), 0, 115)
        stroke = self.scene.document.strokes[0]
        self.assertEqual(stroke.brush.render_profile, "pressure-v2")
        self.assertEqual(stroke.brush.input_scale, 2)
        self.assertEqual((stroke.samples[-1].x, stroke.samples[-1].y), (90, 30))
        self.assertEqual(len(stroke.samples), 4)
        self.assertLess(visible_path(stroke).boundingRect().right(), 95)
        self.assertEqual(self.scene.undo_stack.count(), 1)

    def test_zero_pressure_release_does_not_extend_or_reinflate_ink(self):
        self.tablet(QEvent.Type.TabletPress, (100, 100), .8)
        self.tablet(QEvent.Type.TabletMove, (200, 100), .15, 108)
        self.tablet(QEvent.Type.TabletMove, (220, 100), 0, 110)
        snapshot = self.canvas.snapshot_document()
        self.tablet(QEvent.Type.TabletRelease, (260, 100), 0, 112)
        stroke = self.scene.document.strokes[0]
        self.assertEqual([(s.x, s.pressure) for s in stroke.samples], [(100, .8), (200, .15), (220, 0)])
        self.assertFalse(visible_path(stroke).contains(QPointF(250, 100)))
        self.assertLess(visible_path(stroke).boundingRect().right(), 202)
        self.assertEqual(visible_path(stroke), visible_path(snapshot.strokes[0]))
        clear_geometry_cache()
        self.assertEqual(visible_path(stroke), visible_path(snapshot.strokes[0]))

    def test_stationary_constant_pressure_samples_continue_to_settle_before_release(self):
        position = (100, 100)
        self.tablet(QEvent.Type.TabletPress, position, .1, 0)
        self.tablet(QEvent.Type.TabletMove, position, .9, 1)
        filtered = [self.canvas._builder._filtered_pressure]
        for timestamp in (9, 17, 25):
            self.tablet(QEvent.Type.TabletMove, position, .9, timestamp)
            builder = self.canvas._builder
            filtered.append(builder._filtered_pressure)
            self.assertEqual(builder._last, position)
            self.assertEqual(builder.path.boundingRect().center(), QPointF(*position))

        # Constant raw input still needs time to settle. Deduplication must not
        # freeze the first filtered value until finish() jumps to full pressure.
        self.assertTrue(all(previous < current < .9 for previous, current in zip(filtered, filtered[1:])))
        self.assertLess(.9 - filtered[-1], .04)
        self.assertEqual([sample.t for sample in builder.samples], [0, 1, 9, 17, 25])
        before_width = builder.path.boundingRect().width()
        self.assertEqual(self.scene.undo_stack.count(), 0)
        self.tablet(QEvent.Type.TabletRelease, position, 0, 33)
        stroke = self.scene.document.strokes[0]
        path = visible_path(stroke)
        self.assertEqual(path.boundingRect().center(), QPointF(*position))
        # With this 8-DIP brush, the residual correction after 25 ms is small
        # (<0.3 DIP), instead of the several-DIP jump caused by premature dedup.
        self.assertLess(path.boundingRect().width() - before_width, .3)
        self.assertEqual(self.scene.undo_stack.count(), 1)

    def test_zero_pressure_tap_is_visible_at_down_position_only(self):
        self.tablet(QEvent.Type.TabletPress, (80, 90), 0)
        self.tablet(QEvent.Type.TabletRelease, (180, 150), 0, 108)
        stroke = self.scene.document.strokes[0]
        self.assertEqual(len(stroke.samples), 1)
        self.assertTrue(visible_path(stroke).contains(QPointF(80, 90)))
        self.assertFalse(visible_path(stroke).contains(QPointF(150, 120)))
        self.assertEqual(self.scene.undo_stack.count(), 1)

    def test_single_point_release_pressure_thickens_at_original_down_position(self):
        self.tablet(QEvent.Type.TabletPress, (80, 90), 0)
        self.tablet(QEvent.Type.TabletRelease, (180, 150), .7, 108)
        stroke = self.scene.document.strokes[0]
        self.assertEqual([(s.x, s.y, s.pressure) for s in stroke.samples], [(80, 90, 0), (80, 90, .7)])
        path = visible_path(stroke)
        self.assertAlmostEqual(path.boundingRect().width(), pressure_width(stroke.brush, .7))
        self.assertEqual(path.boundingRect().center(), QPointF(80, 90))
        self.assertFalse(path.contains(QPointF(150, 120)))


if __name__ == "__main__":
    unittest.main()

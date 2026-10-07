"""Headless correctness checks for drawing, erasing, history, and tile caching."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import unittest
import math
import random
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from PySide6.QtCore import QPointF, QRectF
from PySide6.QtGui import QImage, QPainter
from PySide6.QtWidgets import QApplication

from whiteboard.geometry import (IncrementalStrokeBuilder, clear_geometry_cache, erase_path,
                                 eraser_chunks, pressure_width, visible_path)
from whiteboard.models import BoardDocument, Brush, EraseMask, InkSample, Stroke
from whiteboard.renderer import TileRenderer, paint_stroke
from whiteboard.scene import Scene, SpatialIndex
from whiteboard.storage import load_document, save_document


APP = QApplication.instance() or QApplication([])


def line(y=0, width=10, offset=0):
    return Stroke([InkSample(0, y), InkSample(100, y)], Brush(width=width, pressure_enabled=False), offset_x=offset)


class GeometryTests(unittest.TestCase):
    def test_pressure_and_dot(self):
        brush = Brush(width=20)
        self.assertLess(pressure_width(brush, 0.1), pressure_width(brush, 0.9))
        self.assertGreater(pressure_width(Brush(width=20, sensitivity=2), 0.2), pressure_width(brush, 0.2))
        path = visible_path(Stroke([InkSample(30, 40, pressure=1)], brush))
        self.assertTrue(path.contains(QPointF(30, 40)))
        self.assertAlmostEqual(path.boundingRect().width(), 20)

    def test_replay_matches_live_path(self):
        brush = Brush(width=8)
        samples = [InkSample(x, (x % 3) * 5, pressure=0.2 + x / 25) for x in range(20)]
        builder = IncrementalStrokeBuilder(brush)
        for sample in samples:
            builder.add(sample)
        replay = visible_path(Stroke(samples, brush))
        self.assertEqual(builder.path, replay)

    def test_eraser_is_a_round_sweep_even_with_sparse_points(self):
        path = erase_path([(10, 10), (90, 10)], 5)
        self.assertTrue(path.contains(QPointF(50, 14)))
        self.assertTrue(path.contains(QPointF(6, 10)))
        self.assertFalse(path.contains(QPointF(5, 5)))
        self.assertTrue(erase_path([(10, 10), (10, 10)], 5).contains(QPointF(10, 10)))

    def test_local_mask_moves_with_stroke(self):
        stroke = line(offset=200)
        stroke.erase_masks = [EraseMask([(50, -20), (50, 20)], 4)]
        path = visible_path(stroke)
        self.assertFalse(path.contains(QPointF(250, 0)))
        self.assertTrue(path.contains(QPointF(220, 0)))
        self.assertTrue(path.contains(QPointF(270, 0)))

    def test_highlighter_self_intersection_has_one_alpha(self):
        stroke = Stroke([InkSample(20, 50), InkSample(80, 50), InkSample(20, 50)],
                        Brush(color="#ff0000", width=20, kind="highlighter", opacity=0.3))
        image = QImage(100, 100, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill("white")
        painter = QPainter(image)
        paint_stroke(painter, stroke)
        painter.end()
        self.assertEqual(image.pixelColor(25, 50), image.pixelColor(55, 50))
        self.assertAlmostEqual(image.pixelColor(55, 50).green(), 178, delta=1)
        painter = QPainter(image)
        paint_stroke(painter, stroke)
        painter.end()
        self.assertLess(image.pixelColor(55, 50).green(), 178)


class SceneTests(unittest.TestCase):
    def test_repeated_random_erasing_never_restores_erased_interiors(self):
        probes = [QPointF(x + .37, y + .61) for x in range(25, 177, 2) for y in range(30, 171, 2)]
        for seed, repeat in ((6606, True), (6606, False), (91027, True), (715, False)):
            with self.subTest(seed=seed, repeat=repeat):
                rng = random.Random(seed)
                stroke = Stroke([InkSample(100 + 55 * math.cos(i * .16),
                                          100 + 45 * math.sin(i * .16), pressure=.5 + .4 * math.sin(i * .09))
                                 for i in range(100)], Brush(width=28))
                scene = Scene(BoardDocument([stroke]))
                initial = visible_path(stroke)
                previous = {i for i, p in enumerate(probes) if initial.contains(p)}
                for gesture in range(12):
                    if not repeat or gesture % 3 == 0:
                        points = [(rng.uniform(50, 150), rng.uniform(55, 145)) for _ in range(7)]
                        points += list(reversed(points)) + points
                    scene.erase(points, 3.75)
                    current_stroke = scene.get(stroke.id)
                    path = visible_path(current_stroke) if current_stroke is not None else None
                    current = {i for i, p in enumerate(probes) if path is not None and path.contains(p)}
                    self.assertFalse(current - previous,
                                     f"gesture {gesture} restored {len(current - previous)} erased points")
                    previous = current
                    if current_stroke is not None:
                        clear_geometry_cache()
                        self.assertEqual(path, visible_path(current_stroke))

    def test_fractional_offsets_and_multiple_masks_reopen_identically(self):
        stroke = Stroke([InkSample(i * 2.1, 30 + 6 * math.sin(i * .25)) for i in range(70)],
                        Brush(width=18), offset_x=113.137, offset_y=-87.293)
        scene = Scene(BoardDocument([stroke]))
        scene.erase([(135.83, -85.32), (159.78, -28.63), (125.21, -72.74)], 3.317)
        scene.erase([(175.127, -80.99), (181.277, -30.67)], 4.119)
        expected = visible_path(scene.get(stroke.id))
        clear_geometry_cache()
        self.assertEqual(expected, visible_path(scene.get(stroke.id)))
        scene.move_strokes([stroke.id], 17.719, -53.317)
        expected = visible_path(scene.get(stroke.id))
        with TemporaryDirectory() as directory:
            path = Path(directory) / "fractional.qboard"
            save_document(path, scene.document)
            restored = load_document(path)
        clear_geometry_cache()
        self.assertEqual(expected, visible_path(restored.strokes[0]))

    def test_ring_sweep_must_not_fast_delete_ink_inside_its_hole(self):
        stroke = Stroke([InkSample(50, 50)], Brush(width=30, pressure_enabled=False))
        scene = Scene(BoardDocument([stroke]))
        points = [(50 + 20 * math.cos(i * math.tau / 16), 50 + 20 * math.sin(i * math.tau / 16))
                  for i in range(17)]
        sweep = erase_path(points, 8)
        # All four box corners lie in the swept ring; the interior does not.
        box = QRectF(35, 35, 30, 30)
        self.assertTrue(all(sweep.contains(p) for p in (box.topLeft(), box.topRight(), box.bottomLeft(), box.bottomRight())))
        self.assertFalse(sweep.contains(box))
        scene.erase(points, 8)
        path = visible_path(scene.get(stroke.id))
        self.assertTrue(path.contains(QPointF(50, 50)))
        self.assertFalse(path.contains(QPointF(64, 50)))

    def test_eraser_hole_tangent_to_ink_boundary_stays_empty(self):
        for angle in (0, math.pi / 2, math.pi, math.pi * 1.5):
            with self.subTest(angle=angle):
                stroke = Stroke([InkSample(50, 50)], Brush(width=40, pressure_enabled=False))
                scene = Scene(BoardDocument([stroke]))
                dx, dy = math.cos(angle), math.sin(angle)
                scene.erase([(50 + dx * 13, 50 + dy * 13)], 7)
                path = visible_path(scene.get(stroke.id))
                self.assertFalse(path.contains(QPointF(50 + dx * 13, 50 + dy * 13)))
                self.assertFalse(path.contains(QPointF(50 + dx * 19, 50 + dy * 19)))
                self.assertTrue(path.contains(QPointF(50 - dx * 15, 50 - dy * 15)))

    def test_tangent_holes_and_shared_group_boundaries_stay_empty(self):
        stroke = Stroke([InkSample(50, 50) for _ in range(65)], Brush(width=50, pressure_enabled=False))
        scene = Scene(BoardDocument([stroke]))
        scene.erase([(43, 50)], 7)
        scene.erase([(57, 50)], 7)
        path = visible_path(scene.get(stroke.id))
        for x in (43, 49.5, 50.5, 57):
            self.assertFalse(path.contains(QPointF(x, 50)))
        self.assertTrue(path.contains(QPointF(50, 60)))
        clear_geometry_cache()
        self.assertEqual(path, visible_path(scene.get(stroke.id)))

    def test_backtracking_self_intersecting_eraser_removes_interior(self):
        stroke = Stroke([InkSample(50, 50) for _ in range(49)], Brush(width=100, pressure_enabled=False))
        scene = Scene(BoardDocument([stroke]))
        points = [(30, 30), (70, 70), (30, 70), (70, 30), (30, 30), (70, 70), (30, 30)]
        scene.erase(points, 4)
        path = visible_path(scene.get(stroke.id))
        for x, y in ((35, 35), (50, 50), (65, 65), (35, 65), (65, 35), (50, 30), (50, 70)):
            self.assertFalse(path.contains(QPointF(x, y)))
        self.assertTrue(path.contains(QPointF(15, 50)))
        self.assertTrue(path.contains(QPointF(85, 50)))

    def test_nested_eraser_holes_keep_the_central_island(self):
        stroke = Stroke([InkSample(50, 50)], Brush(width=100, pressure_enabled=False))
        scene = Scene(BoardDocument([stroke]))
        ring = [(50 + 20 * math.cos(i * math.tau / 64), 50 + 20 * math.sin(i * math.tau / 64))
                for i in range(65)]
        scene.erase(ring, 4)
        path = visible_path(scene.get(stroke.id))
        self.assertTrue(path.contains(QPointF(50, 50)))
        self.assertFalse(path.contains(QPointF(70, 50)))
        self.assertTrue(path.contains(QPointF(85, 50)))
        scene.erase([(50, 50)], 3)
        path = visible_path(scene.get(stroke.id))
        self.assertFalse(path.contains(QPointF(50, 50)))
        self.assertTrue(path.contains(QPointF(60, 50)))
        self.assertFalse(path.contains(QPointF(70, 50)))

    def test_overlapping_groups_do_not_fill_erased_holes(self):
        stroke = Stroke([InkSample(50, 50) for _ in range(65)],
                        Brush(width=30, pressure_enabled=False))
        scene = Scene(BoardDocument([stroke]))
        scene.erase([(50, 50)], 4)
        path = visible_path(scene.get(stroke.id))
        self.assertFalse(path.contains(QPointF(50, 50)))
        self.assertTrue(path.contains(QPointF(60, 50)))
        scene.undo_stack.undo()
        self.assertTrue(visible_path(scene.get(stroke.id)).contains(QPointF(50, 50)))
        scene.undo_stack.redo()
        self.assertFalse(visible_path(scene.get(stroke.id)).contains(QPointF(50, 50)))

    def test_second_erase_reuses_cached_groups(self):
        stroke = Stroke([InkSample(i * 2, 50) for i in range(80)], Brush(width=12))
        scene = Scene(BoardDocument([stroke]))
        scene.erase([(40, 30), (40, 70)], 3)
        with patch("whiteboard.geometry._replay_parts", side_effect=AssertionError("unnecessary mask replay")):
            scene.erase([(90, 30), (90, 70)], 3)
        path = visible_path(scene.get(stroke.id))
        self.assertFalse(path.contains(QPointF(40, 50)))
        self.assertFalse(path.contains(QPointF(90, 50)))
        self.assertTrue(path.contains(QPointF(65, 50)))

    def test_partial_erase_replays_raw_ink_only_once(self):
        from whiteboard import geometry
        stroke = Stroke([InkSample(i * 2, 50) for i in range(80)], Brush(width=12))
        scene = Scene(BoardDocument([stroke]))
        clear_geometry_cache()
        with patch("whiteboard.geometry._raw_ink_parts", wraps=geometry._raw_ink_parts) as replay:
            with patch("whiteboard.scene.visible_path", side_effect=AssertionError("duplicate full outline")):
                scene.erase([(40, 30), (40, 70)], 3)
        self.assertEqual(replay.call_count, 1)
        count = scene.undo_stack.count()
        scene.erase([(40, 49), (40, 51)], 1)
        self.assertEqual(scene.undo_stack.count(), count)

    def test_masks_movement_and_file_reload_have_identical_geometry(self):
        stroke = Stroke([InkSample(i * 2, 0) for i in range(80)], Brush(width=12),
                        offset_x=201.25, offset_y=-42.5)
        scene = Scene(BoardDocument([stroke]))
        scene.erase([(250, -100), (250, 20)], 4)
        scene.erase([(270.5, -100), (270.5, 20)], 3)
        scene.move_strokes([stroke.id], 123.5, 19.25)
        expected = visible_path(scene.get(stroke.id))
        self.assertFalse(expected.contains(QPointF(373.5, -23.25)))
        self.assertFalse(expected.contains(QPointF(394, -23.25)))
        self.assertTrue(expected.contains(QPointF(350, -23.25)))
        with TemporaryDirectory() as directory:
            path = Path(directory) / "masked.qboard"
            save_document(path, scene.document)
            reopened = load_document(path)
        clear_geometry_cache()
        self.assertEqual(expected, visible_path(reopened.strokes[0]))

    def test_grouped_highlighter_self_crossing_preserves_opacity(self):
        samples = [InkSample(10 + i * 2, 50) for i in range(40)]
        samples += [InkSample(88 - i * 2, 50) for i in range(40)]
        stroke = Stroke(samples, Brush(color="#ff0000", width=20, kind="highlighter", opacity=0.3))
        scene = Scene(BoardDocument([stroke]))
        scene.erase([(50, 30), (50, 70)], 4)
        image = QImage(100, 100, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill("white")
        painter = QPainter(image)
        paint_stroke(painter, scene.get(stroke.id))
        painter.end()
        self.assertEqual(image.pixelColor(50, 50).name(), "#ffffff")
        self.assertAlmostEqual(image.pixelColor(25, 50).green(), 178, delta=1)
        self.assertEqual(image.pixelColor(25, 50), image.pixelColor(75, 50))

    def test_full_group_coverage_skips_boolean(self):
        stroke = Stroke([InkSample(10, 10), InkSample(12, 10)], Brush(width=4))
        scene = Scene(BoardDocument([stroke]))
        with patch("whiteboard.geometry._difference", side_effect=AssertionError("unnecessary boolean")):
            scene.erase([(10, 10)], 20)
        self.assertEqual(scene.document.strokes, [])

    def test_gesture_erase_is_one_undo_command(self):
        scene = Scene()
        scene.add_stroke(line())
        scene.add_stroke(line(y=20))
        count = scene.undo_stack.count()
        scene.erase([(50, -20), (50, 40)], 6)
        self.assertEqual(scene.undo_stack.count(), count + 1)
        self.assertEqual(len(scene.document.strokes), 2)
        for stroke in scene.document.strokes:
            self.assertFalse(visible_path(stroke).contains(QPointF(50, stroke.samples[0].y)))
        scene.undo_stack.undo()
        self.assertTrue(all(not s.erase_masks for s in scene.document.strokes))
        scene.undo_stack.redo()
        self.assertTrue(all(s.erase_masks for s in scene.document.strokes))

    def test_edge_erase_and_visible_ink_hit(self):
        scene = Scene(BoardDocument([line(width=20)]))
        scene.erase([(40, 13)], 4, whole=True)
        self.assertEqual(len(scene.document.strokes), 0)
        scene.undo_stack.undo()
        scene.erase([(50, -30), (50, 30)], 10)
        before = scene.undo_stack.count()
        scene.erase([(50, 0)], 2, whole=True)
        self.assertEqual(scene.undo_stack.count(), before)
        self.assertEqual(len(scene.document.strokes), 1)

    def test_move_masks_undo_and_query_order(self):
        scene = Scene(BoardDocument([line(), line(y=5)]))
        ids = [s.id for s in scene.document.strokes]
        scene.erase([(50, -30), (50, 30)], 4)
        scene.move_strokes(ids, 200, 300)
        self.assertEqual([s.id for s in scene.query(QRectF(200, 290, 100, 50))], ids)
        self.assertFalse(visible_path(scene.get(ids[0])).contains(QPointF(250, 300)))
        self.assertTrue(visible_path(scene.get(ids[0])).contains(QPointF(220, 300)))
        scene.undo_stack.undo()
        self.assertEqual(scene.get(ids[0]).offset_x, 0)
        scene.delete_strokes(ids)
        self.assertTrue(scene.bounds().isEmpty())
        scene.undo_stack.undo()
        self.assertEqual(len(scene.document.strokes), 2)

    def test_input_values_do_not_mutate_history(self):
        scene = Scene()
        stroke = line()
        scene.add_stroke(stroke)
        stroke.samples.clear()
        stroke.brush.width = 100
        self.assertEqual(len(scene.document.strokes[0].samples), 2)
        self.assertEqual(scene.document.strokes[0].brush.width, 10)

    def test_completed_live_geometry_is_reused(self):
        scene = Scene()
        stroke = line()
        builder = IncrementalStrokeBuilder(stroke.brush)
        for sample in stroke.samples:
            builder.add(sample)
        with patch("whiteboard.geometry.IncrementalStrokeBuilder", side_effect=AssertionError("unexpected replay")):
            scene.add_stroke(stroke, cached_path=builder.path)
            self.assertEqual(visible_path(scene.document.strokes[0]), builder.path)

    def test_history_is_bounded_to_two_hundred_transactions(self):
        scene = Scene()
        for i in range(205):
            scene.add_stroke(Stroke([InkSample(i, i)], Brush()))
        self.assertEqual(scene.undo_stack.count(), 200)
        for _ in range(200):
            scene.undo_stack.undo()
        self.assertEqual(len(scene.document.strokes), 5)

    def test_reset_drops_history_and_rebuilds_index(self):
        scene = Scene()
        scene.add_stroke(line())
        replacement = line(y=500)
        scene.reset(BoardDocument([replacement]))
        self.assertTrue(scene.undo_stack.isClean())
        self.assertFalse(scene.undo_stack.canUndo())
        self.assertEqual(scene.query(QRectF(-20, -20, 150, 50)), [])
        self.assertEqual(scene.query(QRectF(-20, 490, 150, 50)), [replacement])

    def test_huge_index_entries_are_bounded(self):
        index = SpatialIndex(cell_size=10, max_cells=20)
        index.insert("huge", QRectF(-1e8, -1e8, 2e8, 2e8))
        index.insert("small", QRectF(0, 0, 1, 1))
        self.assertEqual(len(index.cells), 1)
        self.assertEqual(index.query(QRectF(-1, -1, 5, 5)), {"huge", "small"})
        self.assertEqual(index.query(QRectF(1e6, 1e6, 5, 5)), {"huge"})


class RendererTests(unittest.TestCase):
    def test_append_composes_cached_tiles_without_history_replay(self):
        base = Stroke([InkSample(10, 50), InkSample(90, 50)],
                      Brush(color="#ff0000", width=20, kind="highlighter", opacity=0.3))
        scene = Scene(BoardDocument([base]))
        renderer = TileRenderer(scene)
        def render():
            image = QImage(100, 100, QImage.Format.Format_ARGB32_Premultiplied)
            painter = QPainter(image)
            renderer.paint(painter, QRectF(0, 0, 100, 100), 1, QPointF())
            painter.end()
            return image
        first = render()
        scene.add_stroke(Stroke([InkSample(50, 10), InkSample(50, 90)], base.brush))
        with patch.object(renderer, "_make_tile", side_effect=AssertionError("unnecessary history repaint")):
            second = render()
        self.assertLess(second.pixelColor(50, 50).green(), first.pixelColor(50, 50).green())
        scene.undo_stack.undo()
        self.assertEqual(render().pixelColor(50, 50), first.pixelColor(50, 50))
        scene.undo_stack.redo()
        self.assertEqual(render().pixelColor(50, 50), second.pixelColor(50, 50))

    def test_highlighter_has_no_tile_boundary_darkening(self):
        stroke = Stroke([InkSample(200, 50), InkSample(320, 50)],
                        Brush(color="#ff0000", width=20, kind="highlighter", opacity=0.3))
        scene = Scene(BoardDocument([stroke]))
        renderer = TileRenderer(scene)
        image = QImage(400, 100, QImage.Format.Format_ARGB32_Premultiplied)
        painter = QPainter(image)
        renderer.paint(painter, QRectF(0, 0, 400, 100), 1, QPointF())
        painter.end()
        colors = [image.pixelColor(x, 50) for x in range(250, 262)]
        self.assertTrue(all(color == colors[0] for color in colors))

    def test_lru_budget_and_invalidation(self):
        scene = Scene(BoardDocument([line(y=50)]))
        renderer = TileRenderer(scene, budget_bytes=600_000)
        image = QImage(600, 300, QImage.Format.Format_ARGB32_Premultiplied)
        painter = QPainter(image)
        renderer.paint(painter, QRectF(0, 0, 600, 300), 1, QPointF())
        painter.end()
        self.assertGreater(renderer.cache_bytes, 0)
        self.assertLessEqual(renderer.cache_bytes, renderer.budget_bytes)
        scene.clear()
        self.assertEqual(renderer.cache_bytes, 0)

    def test_exclusion_does_not_poison_cache(self):
        stroke = line(y=50)
        scene = Scene(BoardDocument([stroke]))
        renderer = TileRenderer(scene)
        def render(exclude=None):
            image = QImage(100, 100, QImage.Format.Format_ARGB32_Premultiplied)
            painter = QPainter(image)
            renderer.paint(painter, QRectF(0, 0, 100, 100), 1, QPointF(), exclude_ids=exclude)
            painter.end()
            return image.pixelColor(30, 50)
        ink = render()
        self.assertEqual(render({stroke.id}).name(), "#ffffff")
        self.assertEqual(render(), ink)


if __name__ == "__main__":
    unittest.main()

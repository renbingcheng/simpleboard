"""Image and ink edits share one history without erasing each other's state."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import unittest

from PySide6.QtCore import QRectF
from PySide6.QtWidgets import QApplication

from whiteboard.images import MAX_IMAGE_WORLD_SIZE, MIN_IMAGE_WORLD_SIZE, image_rect
from whiteboard.models import BoardDocument, BoardImage, Brush, InkSample, Stroke
from whiteboard.scene import Scene


APP = QApplication.instance() or QApplication([])


def picture(x=0, y=0, width=100, height=50):
    # Scene owns geometry/history; bounded PNG decoding is tested at import and
    # storage boundaries, so no image decoder is required for these transactions.
    return BoardImage(b"opaque image payload", x, y, width, height)


def ink():
    return Stroke([InkSample(0, 10), InkSample(80, 10)], Brush(width=4, pressure_enabled=False))


class ImageSceneTests(unittest.TestCase):
    def test_insert_snapshots_input_and_interleaves_with_ink_undo(self):
        scene = Scene()
        image = picture()
        scene.add_image(image)
        original = scene.get_image(image.id)
        image.x = 800
        image.png_data = b"changed outside history"
        self.assertEqual(original.x, 0)
        self.assertEqual(original.png_data, b"opaque image payload")
        stroke = ink()
        scene.add_stroke(stroke)
        scene.undo_stack.undo()
        self.assertIs(scene.get_image(image.id), original)
        self.assertIsNone(scene.get(stroke.id))
        scene.undo_stack.undo()
        self.assertEqual(scene.document.images, [])
        scene.undo_stack.redo()
        scene.undo_stack.redo()
        self.assertIs(scene.get_image(image.id), original)
        self.assertIsNotNone(scene.get(stroke.id))

    def test_mixed_move_and_delete_each_form_one_transaction(self):
        stroke, image = ink(), picture()
        scene = Scene(BoardDocument(strokes=[stroke], images=[image]))
        scene.move_items([stroke.id, image.id], 200, -40)
        self.assertEqual(scene.undo_stack.count(), 1)
        self.assertEqual((scene.get(stroke.id).offset_x, scene.get(stroke.id).offset_y), (200, -40))
        self.assertEqual(image_rect(scene.get_image(image.id)), QRectF(200, -40, 100, 50))
        self.assertEqual(image_rect(image), QRectF(0, 0, 100, 50))
        self.assertEqual((stroke.offset_x, stroke.offset_y), (0, 0))
        scene.undo_stack.undo()
        self.assertIs(scene.get(stroke.id), stroke)
        self.assertIs(scene.get_image(image.id), image)
        scene.undo_stack.redo()
        scene.delete_items([stroke.id, image.id])
        self.assertEqual(scene.undo_stack.count(), 2)
        self.assertEqual(scene.document.strokes, [])
        self.assertEqual(scene.document.images, [])
        scene.undo_stack.undo()
        self.assertEqual(scene.get_image(image.id).x, 200)
        self.assertEqual(scene.get(stroke.id).offset_x, 200)

    def test_image_resize_updates_index_bounds_and_undo(self):
        image = picture()
        scene = Scene(BoardDocument(images=[image]))
        target = QRectF(1000, 1200, 200, 100)
        scene.resize_image(image.id, target)
        self.assertEqual(scene.query_images(QRectF(0, 0, 100, 50)), [])
        self.assertEqual([item.id for item in scene.query_images(target)], [image.id])
        self.assertEqual(scene.bounds(), target)
        self.assertEqual(scene.document.images[0].png_data, image.png_data)
        scene.undo_stack.undo()
        self.assertEqual(scene.bounds(), image_rect(image))
        scene.undo_stack.redo()
        self.assertEqual(scene.bounds(), target)

    def test_picture_queries_preserve_order_and_do_not_enter_ink_index(self):
        first, second = picture(), picture(20, 20)
        stroke = ink()
        scene = Scene(BoardDocument(strokes=[stroke], images=[first, second]))
        rect = QRectF(-20, -20, 200, 200)
        self.assertEqual(scene.query_images(rect), [first, second])
        self.assertEqual(scene.query(rect), [stroke])
        scene.erase([(-50, 10), (150, 10)], 100, whole=True)
        self.assertEqual(scene.document.strokes, [])
        self.assertEqual(scene.document.images, [first, second])
        scene.undo_stack.undo()
        self.assertEqual(scene.document.images, [first, second])
        self.assertEqual(scene.document.strokes, [stroke])

    def test_clear_image_only_and_mixed_documents_restores_all_in_one_undo(self):
        for strokes in ([], [ink()]):
            with self.subTest(with_ink=bool(strokes)):
                image = picture()
                scene = Scene(BoardDocument(strokes=strokes, images=[image]))
                scene.clear()
                self.assertEqual(scene.undo_stack.count(), 1)
                self.assertTrue(scene.bounds().isEmpty())
                scene.undo_stack.undo()
                self.assertEqual(scene.document.strokes, strokes)
                self.assertEqual(scene.document.images, [image])

    def test_reset_replaces_both_indexes_and_starts_clean(self):
        old, new = picture(), picture(2000, 3000)
        scene = Scene()
        scene.add_image(old)
        scene.reset(BoardDocument(images=[new]))
        self.assertEqual(scene.undo_stack.count(), 0)
        self.assertTrue(scene.undo_stack.isClean())
        self.assertIsNone(scene.get_image(old.id))
        self.assertEqual(scene.query_images(QRectF(0, 0, 100, 100)), [])
        self.assertEqual(scene.query_images(image_rect(new)), [new])
        self.assertEqual(scene.bounds(), image_rect(new))

    def test_ids_are_unique_across_ink_and_pictures(self):
        image, stroke = picture(), ink()
        stroke.id = image.id
        scene = Scene(BoardDocument(images=[image]))
        with self.assertRaises(ValueError):
            scene.add_stroke(stroke)
        with self.assertRaises(ValueError):
            scene.add_image(image)
        scene = Scene(BoardDocument(strokes=[stroke]))
        with self.assertRaises(ValueError):
            scene.add_image(image)
        with self.assertRaises(ValueError):
            Scene(BoardDocument(strokes=[stroke], images=[image]))

    def test_legacy_stroke_methods_leave_pictures_untouched(self):
        stroke, image = ink(), picture()
        scene = Scene(BoardDocument(strokes=[stroke], images=[image]))
        scene.move_strokes([stroke.id, image.id], 30, 40)
        self.assertIs(scene.get_image(image.id), image)
        scene.delete_strokes([stroke.id, image.id])
        self.assertIs(scene.get_image(image.id), image)
        scene.undo_stack.undo()
        scene.undo_stack.undo()
        self.assertIs(scene.get_image(image.id), image)

    def test_unchanged_unknown_and_invalid_geometry_do_not_add_history(self):
        image = picture()
        scene = Scene(BoardDocument(images=[image]))
        scene.move_items([image.id], 0, 0)
        scene.move_items(["missing"], 30, 40)
        scene.delete_items(["missing"])
        scene.resize_image("missing", QRectF(0, 0, 10, 10))
        scene.resize_image(image.id, image_rect(image))
        with self.assertRaises(ValueError):
            scene.move_items([image.id], float("nan"), 0)
        for rect in (QRectF(0, 0, 0, 10), QRectF(0, 0, -1, 10), QRectF(float("inf"), 0, 10, 10)):
            with self.assertRaises(ValueError):
                scene.resize_image(image.id, rect)
        with self.assertRaises(ValueError):
            scene.add_image(picture(width=-1))
        self.assertEqual(scene.undo_stack.count(), 0)

    def test_dirty_rect_covers_old_and_new_image_location(self):
        image = picture()
        scene = Scene(BoardDocument(images=[image]))
        changes = []
        scene.changed.connect(changes.append)
        scene.move_items([image.id], 1000, 1200)
        expected = image_rect(image).united(image_rect(scene.get_image(image.id)))
        self.assertEqual(changes, [expected])
        scene.undo_stack.undo()
        self.assertEqual(changes, [expected, expected])

    def test_insert_and_resize_reject_geometry_outside_document_limits(self):
        image = picture()
        scene = Scene(BoardDocument(images=[image]))
        for candidate in (picture(width=MIN_IMAGE_WORLD_SIZE / 2),
                          picture(height=MAX_IMAGE_WORLD_SIZE + 1),
                          picture(x=-MAX_IMAGE_WORLD_SIZE - 1), picture(y=True)):
            with self.subTest(candidate=candidate):
                with self.assertRaises(ValueError):
                    scene.add_image(candidate)
        for rect in (QRectF(0, 0, MIN_IMAGE_WORLD_SIZE / 2, 10),
                     QRectF(MAX_IMAGE_WORLD_SIZE + 1, 0, 10, 10),
                     QRectF(0, 0, 10, MAX_IMAGE_WORLD_SIZE + 1)):
            with self.subTest(rect=rect):
                with self.assertRaises(ValueError):
                    scene.resize_image(image.id, rect)
        self.assertEqual(scene.undo_stack.count(), 0)
        self.assertEqual(scene.document.images, [image])
        scene.resize_image(image.id, QRectF(MAX_IMAGE_WORLD_SIZE, -MAX_IMAGE_WORLD_SIZE,
                                           MIN_IMAGE_WORLD_SIZE, MAX_IMAGE_WORLD_SIZE))
        self.assertEqual(scene.get_image(image.id).width, MIN_IMAGE_WORLD_SIZE)
        scene.undo_stack.undo()
        self.assertEqual(scene.document.images, [image])

    def test_out_of_range_mixed_move_is_atomic(self):
        image = picture(x=MAX_IMAGE_WORLD_SIZE - 10)
        stroke = ink()
        scene = Scene(BoardDocument(strokes=[stroke], images=[image]))
        with self.assertRaises(ValueError):
            scene.move_items([stroke.id, image.id], 20, 0)
        self.assertEqual(scene.undo_stack.count(), 0)
        self.assertIs(scene.get_image(image.id), image)
        self.assertIs(scene.get(stroke.id), stroke)
        self.assertEqual(scene.query_images(image_rect(image)), [image])


if __name__ == "__main__":
    unittest.main()

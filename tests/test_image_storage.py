"""Embedded images remain editable, self-contained and bounded on disk."""
from __future__ import annotations

import base64
import copy
from dataclasses import replace
from pathlib import Path
import struct
import tempfile
import threading
import unittest
from unittest.mock import patch
import zlib

from PySide6.QtCore import QBuffer, QIODevice
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication

from whiteboard import images
from whiteboard.images import ImageError, decode_image, image_size, load_image, validate_images
from whiteboard.models import BoardDocument, BoardImage, Brush, InkSample, Stroke
from whiteboard.recovery import RecoveryManager
from whiteboard.storage import (DocumentError, FORMAT_VERSION, document_from_dict, document_to_dict,
                                load_document, load_snapshot, save_document, save_snapshot, validate_snapshot)


def encoded_image(width=20, height=10, image_format='PNG'):
    image = QImage(width, height, QImage.Format.Format_ARGB32)
    image.fill(QColor(20, 90, 160, 100))
    output = QBuffer()
    output.open(QIODevice.OpenModeFlag.WriteOnly)
    assert image.save(output, image_format)
    return bytes(output.data())


def board_image(**values):
    return BoardImage(png_data=values.pop('png_data', encoded_image()),
                      x=values.pop('x', -30), y=values.pop('y', 50),
                      width=values.pop('width', 200), height=values.pop('height', 100), **values)


def rewrite_png_chunk(data, kind, replacement):
    position = 8
    while position + 12 <= len(data):
        length = struct.unpack_from('>I', data, position)[0]
        end = position + length + 12
        if data[position + 4:position + 8] == kind:
            chunk = struct.pack('>I', len(replacement)) + kind + replacement
            return data[:position] + chunk + struct.pack('>I', zlib.crc32(kind + replacement)) + data[end:]
        position = end
    raise AssertionError('Missing PNG chunk')


class ImageStorageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / '图片.qboard'

    def example_document(self):
        return BoardDocument(strokes=[Stroke([InkSample(1, 2)], Brush(), id='ink')],
                             images=[board_image(id='photo')], view_scale=2.5)

    def test_image_and_ink_roundtrip_is_self_contained(self):
        document = self.example_document()
        snapshot = document_to_dict(document)
        self.assertEqual(snapshot['version'], 3)
        self.assertEqual(snapshot['version'], FORMAT_VERSION)
        self.assertEqual(base64.b64decode(snapshot['images'][0]['png']), document.images[0].png_data)
        save_document(self.path, document)
        restored = load_document(self.path)
        self.assertEqual(restored, document)
        self.assertEqual(decode_image(restored.images[0].png_data).size().width(), 20)
        self.assertEqual(images.image_rect(restored.images[0]).getRect(), (-30, 50, 200, 100))

    def test_v1_v2_without_images_load_and_reject_mislabeled_image_content(self):
        current = document_to_dict(self.example_document())
        for version in (1, 2):
            with self.subTest(version=version):
                legacy = copy.deepcopy(current)
                legacy['version'] = version
                del legacy['images']
                restored = document_from_dict(legacy)
                self.assertEqual(restored.images, [])
                self.assertEqual(len(restored.strokes), 1)
                legacy['images'] = current['images']
                with self.assertRaises(DocumentError):
                    document_from_dict(legacy)

    def test_snapshot_detaches_placement_and_image_list(self):
        document = self.example_document()
        snapshot = document_to_dict(document)
        document.images[0] = replace(document.images[0], x=900)
        document.images.clear()
        self.assertEqual(snapshot['images'][0]['x'], -30)
        self.assertEqual(len(document_from_dict(snapshot).images), 1)

    def test_invalid_encodings_and_corrupt_png_are_rejected(self):
        snapshot = document_to_dict(self.example_document())
        png = self.example_document().images[0].png_data
        for invalid in ('', 'not base64!', '中文', None, 3,
                        base64.b64encode(b'not a PNG').decode(),
                        base64.b64encode(png[:-12]).decode(),
                        base64.b64encode(encoded_image(image_format='JPEG')).decode()):
            with self.subTest(invalid=str(invalid)[:40]):
                value = copy.deepcopy(snapshot)
                value['images'][0]['png'] = invalid
                with self.assertRaises(DocumentError):
                    document_from_dict(value)
                with self.assertRaises(DocumentError):
                    validate_snapshot(value)

    def test_pixel_stream_corruption_is_rejected_even_with_valid_crc(self):
        corrupted = rewrite_png_chunk(encoded_image(), b'IDAT', b'not a compressed pixel stream')
        image_size(corrupted)  # Structure alone is insufficient.
        with self.assertRaises(ImageError):
            validate_images([board_image(png_data=corrupted)])

    def test_png_header_limits_are_checked_before_pixel_decode(self):
        png = encoded_image()
        header = struct.pack('>II', 100_000, 100_000) + png[24:29]
        oversized = rewrite_png_chunk(png, b'IHDR', header)
        with patch('whiteboard.images._reader', side_effect=AssertionError('must not decode')):
            with self.assertRaises(ImageError):
                decode_image(oversized)

    def test_invalid_placement_and_duplicate_ids_reject_without_replacing_file(self):
        document = self.example_document()
        save_document(self.path, document)
        old = self.path.read_bytes()
        for changes in ({'width': 0}, {'height': -1}, {'x': float('nan')}, {'y': float('inf')},
                        {'width': 1e10}, {'x': True}, {'id': 'ink'}, {'id': ''}, {'id': None}):
            with self.subTest(changes=changes):
                invalid = replace(document, images=[replace(document.images[0], **changes)])
                with self.assertRaises(DocumentError):
                    save_document(self.path, invalid)
                self.assertEqual(self.path.read_bytes(), old)
        duplicated = replace(document, images=[document.images[0], document.images[0]])
        with self.assertRaises(DocumentError):
            save_document(self.path, duplicated)
        self.assertEqual(self.path.read_bytes(), old)

    def test_total_pixel_byte_and_image_count_budgets_reject_save_and_load(self):
        document = self.example_document()
        snapshot = document_to_dict(document)
        png_length = len(document.images[0].png_data)
        cases = [('MAX_IMAGES', 0), ('MAX_TOTAL_IMAGE_PIXELS', 199),
                 ('MAX_TOTAL_IMAGE_BYTES', png_length - 1), ('MAX_IMAGE_BYTES', png_length - 1)]
        for constant, limit in cases:
            with self.subTest(constant=constant), patch.object(images, constant, limit):
                with self.assertRaises(ImageError):
                    validate_images(document.images)
                with self.assertRaises(DocumentError):
                    document_to_dict(document)
                with self.assertRaises(DocumentError):
                    document_from_dict(snapshot)

    def test_recovery_snapshot_preserves_png_and_metadata(self):
        snapshot = document_to_dict(self.example_document())
        snapshot['recovery'] = {'source_path': '课件.qboard'}
        save_snapshot(self.path, snapshot)
        restored = load_snapshot(self.path)
        self.assertEqual(restored, snapshot)
        self.assertEqual(document_from_dict(restored).images, self.example_document().images)

    def test_atomic_failure_preserves_previous_image_document(self):
        original = self.example_document()
        save_document(self.path, original)
        previous = self.path.read_bytes()
        changed = replace(original, images=[replace(original.images[0], x=300)])
        with patch('whiteboard.storage.os.replace', side_effect=PermissionError('file locked')):
            with self.assertRaises(DocumentError):
                save_document(self.path, changed)
        self.assertEqual(self.path.read_bytes(), previous)
        self.assertEqual(list(Path(self.directory.name).iterdir()), [self.path])

    def test_recovery_captures_images_before_worker_runs(self):
        document = self.example_document()
        manager = RecoveryManager(lambda: document, directory=self.directory.name)
        self.addCleanup(manager.shutdown)
        entered = threading.Event()
        release = threading.Event()

        def delayed_snapshot(captured):
            entered.set()
            if not release.wait(3):
                raise AssertionError('worker wait expired')
            return document_to_dict(captured)

        with patch('whiteboard.recovery.document_to_dict', side_effect=delayed_snapshot):
            manager.schedule()
            manager._start_write()
            self.assertTrue(entered.wait(2))
            document.images.clear()
            release.set()
            self.assertTrue(manager.flush())
        self.assertEqual(len(manager.recover().images), 1)

    def test_png_bmp_jpeg_import_normalizes_alpha_and_embeds_without_source_path(self):
        for image_format in ('PNG', 'BMP', 'JPEG'):
            with self.subTest(image_format=image_format):
                path = Path(self.directory.name) / f'课堂.{image_format.lower()}'
                path.write_bytes(encoded_image(image_format=image_format))
                png = load_image(path)
                path.unlink()
                decoded = decode_image(png)
                self.assertEqual((decoded.width(), decoded.height()), (20, 10))
                if image_format == 'PNG':
                    self.assertEqual(decoded.pixelColor(0, 0).alpha(), 100)

    def test_large_import_is_downscaled_and_exif_orientation_applied(self):
        path = Path(self.directory.name) / 'large.png'
        path.write_bytes(encoded_image(width=5000, height=20))
        normalized = decode_image(load_image(path))
        self.assertEqual(normalized.width(), 4096)
        self.assertLessEqual(normalized.width() * normalized.height(), images.MAX_IMAGE_PIXELS)
        jpeg = encoded_image(width=30, height=20, image_format='JPEG')
        # TIFF orientation 6 rotates 90 degrees clockwise; no Pillow dependency.
        tiff = b'II\x2a\x00' + struct.pack('<I', 8) + struct.pack('<H', 1)
        tiff += struct.pack('<HHI', 0x112, 3, 1) + struct.pack('<H', 6) + b'\0\0' + struct.pack('<I', 0)
        exif = b'Exif\0\0' + tiff
        jpeg = jpeg[:2] + b'\xff\xe1' + struct.pack('>H', len(exif) + 2) + exif + jpeg[2:]
        path = Path(self.directory.name) / '旋转.jpg'
        path.write_bytes(jpeg)
        oriented = decode_image(load_image(path))
        self.assertEqual((oriented.width(), oriented.height()), (20, 30))

    def test_truncated_png_and_unsupported_content_are_not_imported(self):
        path = Path(self.directory.name) / 'image.png'
        for contents in (encoded_image()[:-12], b'<svg xmlns="http://www.w3.org/2000/svg"/>', b''):
            path.write_bytes(contents)
            with self.assertRaises(ImageError):
                load_image(path)

    def test_truncated_jpeg_and_bmp_are_not_silently_repaired_on_import(self):
        path = Path(self.directory.name) / 'image'
        for image_format in ('JPEG', 'BMP'):
            original = encoded_image(image_format=image_format)
            for missing in (2, 20, len(original) // 2):
                with self.subTest(image_format=image_format, missing=missing):
                    path.write_bytes(original[:-missing])
                    with self.assertRaises(ImageError):
                        load_image(path)

    def test_validation_cache_does_not_redecode_or_retain_pixel_buffers(self):
        document = self.example_document()
        validate_images(document.images)
        with patch('whiteboard.images.decode_image', side_effect=AssertionError('Unchanged PNG must not decode again')):
            validate_images(document.images)
        with images._validation_lock:
            self.assertLessEqual(len(images._validated_pngs), 256)
            self.assertTrue(all(isinstance(key, bytes) and len(key) == 32 and value is None
                                for key, value in images._validated_pngs.items()))


if __name__ == '__main__':
    unittest.main()

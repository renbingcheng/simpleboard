"""Current-view exports keep vectors, viewport geometry and atomic replacement."""
from __future__ import annotations

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch

from PySide6.QtCore import QPointF, QSize
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication

from whiteboard.exporting import ExportError, export_pdf, export_png
from whiteboard.models import BoardDocument, Brush, EraseMask, InkSample, Stroke
from whiteboard.scene import Scene


APP = QApplication.instance() or QApplication([])


class ExportingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="qboard-export-")
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.scene = Scene(BoardDocument(strokes=[
            Stroke([InkSample(30, 60), InkSample(230, 60)], Brush(color="#2563EB", width=12, pressure_enabled=False),
                   erase_masks=[EraseMask([(130, 30), (130, 90)], 8)]),
            Stroke([InkSample(60, 130), InkSample(210, 130), InkSample(60, 130)],
                   Brush(color="#FFFF00", width=26, kind="highlighter", pressure_enabled=False, opacity=.3)),
            Stroke([InkSample(1200, 180), InkSample(1400, 180)], Brush()),
        ]))

    def test_pdf_is_single_vector_page_matching_landscape_and_portrait_views(self):
        for size in (QSize(640, 360), QSize(400, 700)):
            with self.subTest(size=size):
                target = self.directory / "课堂 视图.pdf"
                export_pdf(target, self.scene, size, 1, QPointF())
                data = target.read_bytes()
                self.assertTrue(data.startswith(b"%PDF-"))
                self.assertIn(b"%%EOF", data[-100:])
                self.assertEqual(len(re.findall(rb"/Type\s*/Page\b", data)), 1)
                self.assertNotIn(b"/Subtype /Image", data)
                match = re.search(rb"/MediaBox\s*\[\s*0\s+0\s+([\d.]+)\s+([\d.]+)\s*\]", data)
                self.assertIsNotNone(match)
                width, height = map(float, match.groups())
                self.assertAlmostEqual(width / height, size.width() / size.height(), places=5)
                self.assertAlmostEqual(width, size.width() * .75, places=2)
                self.assertAlmostEqual(height, size.height() * .75, places=2)

    def test_png_preserves_mask_highlighter_and_transform_without_grid(self):
        target = self.directory / "课堂 视图.png"
        export_png(target, self.scene, QSize(400, 220), 1, QPointF())
        image = QImage(str(target))
        self.assertEqual(image.size(), QSize(400, 220))
        self.assertEqual(image.pixelColor(80, 60).name(), "#2563eb")
        self.assertEqual(image.pixelColor(130, 60).name(), "#ffffff")
        self.assertEqual(image.pixelColor(90, 130), image.pixelColor(150, 130))
        self.assertAlmostEqual(image.pixelColor(90, 130).blue(), 178, delta=1)
        self.assertEqual(image.pixelColor(320, 200).name(), "#ffffff")
        export_png(target, self.scene, QSize(400, 220), 2, QPointF(5, -20), dpr=2)
        image = QImage(str(target))
        self.assertEqual(image.size(), QSize(800, 440))
        self.assertEqual(image.pixelColor(330, 200).name(), "#2563eb")
        self.assertEqual(image.pixelColor(530, 200).name(), "#ffffff")

    def test_failed_render_preserves_existing_png_and_pdf(self):
        for extension, exporter in (("png", export_png), ("pdf", export_pdf)):
            with self.subTest(format=extension):
                target = self.directory / f"existing.{extension}"
                original = b"existing file must survive failed export"
                target.write_bytes(original)
                with patch("whiteboard.exporting.render_document", side_effect=ExportError("simulated render failure")):
                    with self.assertRaises(ExportError):
                        exporter(target, self.scene, QSize(640, 360), 1, QPointF())
                self.assertEqual(target.read_bytes(), original)
                self.assertEqual(sorted(path.name for path in self.directory.iterdir()),
                                 sorted(path.name for path in self.directory.iterdir() if path.suffix in {".png", ".pdf"}))

    def test_failed_atomic_commit_preserves_existing_destination(self):
        target = self.directory / "existing.pdf"
        target.write_bytes(b"previous PDF")
        with patch("whiteboard.exporting.QSaveFile.commit", return_value=False):
            with self.assertRaises(ExportError):
                export_pdf(target, self.scene, QSize(640, 360), 1, QPointF())
        self.assertEqual(target.read_bytes(), b"previous PDF")

    def test_invalid_view_and_missing_directory_report_errors(self):
        for exporter in (export_png, export_pdf):
            with self.subTest(exporter=exporter.__name__):
                with self.assertRaises(ExportError):
                    exporter(self.directory / "invalid", self.scene, QSize(0, 10), 1, QPointF())
                with self.assertRaises(ExportError):
                    exporter(self.directory / "invalid", self.scene, QSize(640, 360), 0, QPointF())
                with self.assertRaises(ExportError):
                    exporter(self.directory / "missing" / "view", self.scene, QSize(640, 360), 1, QPointF())


if __name__ == "__main__":
    unittest.main()

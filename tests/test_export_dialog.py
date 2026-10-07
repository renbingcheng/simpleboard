"""Export dialogs choose a path/type without changing editable-document state."""
from __future__ import annotations

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox

from whiteboard.exporting import ExportError
from whiteboard.models import Brush, InkSample, Stroke
from whiteboard.settings import AppSettings
from whiteboard.window import MainWindow


APP = QApplication.instance() or QApplication([])


def click_button(caption):
    def execute(box):
        next(button for button in box.buttons() if button.text() == caption).click()
        return 0
    return execute


class ExportDialogTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="qboard-export-dialog-")
        self.directory = Path(self.temp.name)
        self.window = MainWindow(recovery_enabled=False, settings=AppSettings(self.directory / "settings.json"))
        self.window._startup_done = True
        self.window.scene.add_stroke(Stroke([InkSample(40, 100), InkSample(140, 110)], Brush()))

    def tearDown(self):
        self.window._settings_timer.stop()
        with patch.object(self.window, "_maybe_leave_document", return_value=True):
            self.window.close()
        self.window.deleteLater()
        APP.processEvents()
        self.temp.cleanup()

    def test_unified_action_and_both_types_are_available(self):
        self.assertEqual(self.window.export_action.text(), "导出…")
        self.assertEqual(self.window.export_action.shortcut().toString(), "Ctrl+Shift+E")
        with patch.object(QFileDialog, "getSaveFileName", return_value=("", "")) as dialog:
            self.window.export_action.trigger()
        self.assertIn("*.png", dialog.call_args.args[3])
        self.assertIn("*.pdf", dialog.call_args.args[3])
        self.assertTrue(dialog.call_args.kwargs["options"] & QFileDialog.Option.DontConfirmOverwrite)

    def test_pdf_selection_replaces_png_suffix_and_keeps_document_dirty(self):
        self.window.current_path = self.directory / "可编辑 白板.qboard"
        selected = self.directory / "课堂 视图.png"
        target = selected.with_suffix(".pdf")
        with patch.object(QFileDialog, "getSaveFileName", return_value=(str(selected), "PDF 文档 (*.pdf)")):
            self.assertTrue(self.window.export_document())
        self.assertTrue(target.read_bytes().startswith(b"%PDF-"))
        self.assertFalse(selected.exists())
        self.assertTrue(self.window.is_dirty)
        self.assertEqual(self.window.current_path.name, "可编辑 白板.qboard")

    def test_missing_extension_uses_selected_format_and_cancel_writes_nothing(self):
        selected = self.directory / "无 后缀"
        with patch.object(QFileDialog, "getSaveFileName", return_value=(str(selected), "PNG 图片 (*.png)")):
            self.assertTrue(self.window.export_document())
        self.assertTrue(Path(str(selected) + ".png").read_bytes().startswith(b"\x89PNG"))
        with patch.object(QFileDialog, "getSaveFileName", return_value=("", "")), \
                patch("whiteboard.exporting.export_png") as png, patch("whiteboard.exporting.export_pdf") as pdf:
            self.assertFalse(self.window.export_document())
            png.assert_not_called()
            pdf.assert_not_called()

    def test_final_existing_target_always_requires_confirmation(self):
        target = self.directory / "已有 文档.pdf"
        target.write_bytes(b"existing file")
        for raw in (target, target.with_suffix(".png"), target.with_suffix("")):
            with self.subTest(raw=raw), \
                    patch.object(QFileDialog, "getSaveFileName", return_value=(str(raw), "PDF 文档 (*.pdf)")), \
                    patch.object(QMessageBox, "exec", click_button("取消")), \
                    patch("whiteboard.exporting.export_pdf") as exporter:
                self.assertFalse(self.window.export_document())
                exporter.assert_not_called()
                self.assertEqual(target.read_bytes(), b"existing file")
        with patch.object(QFileDialog, "getSaveFileName", return_value=(str(target), "PDF 文档 (*.pdf)")), \
                patch.object(QMessageBox, "exec", click_button("替换")):
            self.assertTrue(self.window.export_document())
        self.assertTrue(target.read_bytes().startswith(b"%PDF-"))

    def test_encoding_failure_reports_error_without_saving_editable_document(self):
        with patch.object(QFileDialog, "getSaveFileName", return_value=(str(self.directory / "课堂.pdf"), "PDF 文档 (*.pdf)")), \
                patch("whiteboard.exporting.export_pdf", side_effect=ExportError("disk full")), \
                patch.object(self.window, "_show_error") as error:
            self.assertFalse(self.window.export_document())
        error.assert_called_once_with("导出失败", "disk full")
        self.assertTrue(self.window.is_dirty)
        self.assertIsNone(self.window.current_path)

    def test_explicit_png_and_pdf_compatibility_methods_still_choose_paths(self):
        for extension, method in (("png", self.window.export_png), ("pdf", self.window.export_pdf)):
            with self.subTest(extension=extension):
                path = self.directory / f"显式 导出.{extension}"
                with patch.object(QFileDialog, "getSaveFileName", return_value=(str(path), "")) as dialog:
                    self.assertTrue(method())
                self.assertEqual(dialog.call_count, 1)
                self.assertTrue(path.exists())


if __name__ == "__main__":
    unittest.main()

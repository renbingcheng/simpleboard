"""Image file commands delegate to the canvas and preserve cancelled work."""
from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication, QMessageBox

from whiteboard.i18n import set_language
from whiteboard.models import BoardImage
from whiteboard.settings import AppSettings
from whiteboard.window import MainWindow


class ImageMenuTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="simpleboard-image-menu-")
        self.directory = Path(self.temp.name)
        self.window = MainWindow(recovery_enabled=False, settings=AppSettings(self.directory / "settings.json"))
        self.window._startup_done = True

    def tearDown(self):
        self.window._settings_timer.stop()
        self.window._closing = True
        self.window.close()
        self.app.removeEventFilter(self.window.canvas)
        self.window.deleteLater()
        self.app.processEvents()
        set_language("zh_CN")
        self.temp.cleanup()

    def test_insert_action_is_in_menu_and_has_unique_shortcut(self):
        w = self.window
        self.assertIn(w.insert_image_action, w.main_menu.actions())
        self.assertEqual(w.insert_image_action.shortcut().toString(), "Ctrl+Shift+I")
        actions = [action for action in w.actions() if any(key.toString() == "Ctrl+Shift+I" for key in action.shortcuts())]
        self.assertEqual(actions, [w.insert_image_action])

    def test_cancelled_dialog_preserves_clean_document(self):
        w = self.window
        with patch("whiteboard.window.QFileDialog.getOpenFileName", return_value=("", "")), \
                patch.object(w.canvas, "insert_image_path", create=True) as insert:
            w.insert_image_button.click()
            insert.assert_not_called()
        self.assertFalse(w.is_dirty)
        self.assertEqual(w.scene.document.images, [])
        self.assertEqual(w._tool, "pen")
        self.assertFalse(w.insert_image_button.isCheckable())

    def test_success_delegates_path_and_selects_image_tool(self):
        w = self.window
        path = str(self.directory / "sample.png")
        with patch("whiteboard.window.QFileDialog.getOpenFileName", return_value=(path, "")), \
                patch.object(w.canvas, "insert_image_path", create=True) as insert:
            w.insert_image_button.click()
            insert.assert_called_once_with(path)
        self.assertEqual(w._tool, "select")
        self.assertTrue(w.tool_buttons["select"].isChecked())
        self.assertFalse(w.tool_buttons["lasso"].isChecked())

    def test_insert_keeps_new_image_selected_for_immediate_dragging(self):
        w = self.window
        path = self.directory / "sample.png"
        image = QImage(60, 40, QImage.Format.Format_RGB32)
        image.fill(QColor("#3586BC"))
        self.assertTrue(image.save(str(path), "PNG"))
        with patch("whiteboard.window.QFileDialog.getOpenFileName", return_value=(str(path), "")):
            self.assertTrue(w.insert_image())
        self.assertEqual(w.canvas.tool, "select")
        self.assertEqual(w.canvas.selection_ids, {w.scene.document.images[0].id})
        self.assertTrue(w.delete_action.isEnabled())

    def test_image_select_shortcut_and_button_are_separate_from_lasso(self):
        w = self.window
        shortcuts = [action for action in w.actions() if any(key.toString() == "S" for key in action.shortcuts())]
        self.assertEqual(shortcuts, [w.select_image_action])
        w.select_image_action.trigger()
        self.assertEqual(w.canvas.tool, "select")
        self.assertTrue(w.tool_buttons["select"].isChecked())
        self.assertFalse(w.tool_buttons["lasso"].isChecked())
        w.tool_buttons["lasso"].click()
        self.assertEqual(w.canvas.tool, "lasso")
        self.assertFalse(w.tool_buttons["select"].isChecked())
        w.tool_buttons["select"].click()
        self.assertEqual(w.canvas.tool, "select")

    def test_bad_image_reports_error_and_preserves_document(self):
        w = self.window
        path = str(self.directory / "broken.png")
        with patch("whiteboard.window.QFileDialog.getOpenFileName", return_value=(path, "")), \
                patch.object(w.canvas, "insert_image_path", side_effect=ValueError("Unreadable image"), create=True), \
                patch.object(w, "_show_error") as error:
            self.assertFalse(w.insert_image())
            error.assert_called_once_with("无法插入图片", "Unreadable image")
        self.assertFalse(w.is_dirty)
        self.assertEqual(w._tool, "pen")

    def test_image_only_document_enables_export_and_clear(self):
        w = self.window
        # The window only inspects existence. Real image validity/rendering is
        # exercised by the image-storage and canvas integration suites.
        w.scene.document.images.append(BoardImage(b"window-test", 0, 0, 20, 20))
        w._update_title()
        self.assertTrue(w.export_action.isEnabled())
        self.assertTrue(w.clear_action.isEnabled())
        target = str(self.directory / "view.png")
        with patch("whiteboard.window.QFileDialog.getSaveFileName", return_value=(target, "PNG (*.png)")), \
                patch("whiteboard.exporting.export_png") as export:
            self.assertTrue(w.export_png())
            export.assert_called_once()
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Cancel) as confirm:
            w.clear_document()
            confirm.assert_called_once()
        self.assertEqual(len(w.scene.document.images), 1)

    def test_image_commands_and_selection_are_translated(self):
        w = self.window
        image = BoardImage(b"window-test", 0, 0, 20, 20)
        w.scene.document.images.append(image)
        w.canvas.selection_ids = {image.id}
        w.change_language("en")
        self.assertEqual(w.insert_image_action.text(), "Insert image…")
        self.assertEqual(w.insert_image_button.toolTip(), "Insert image · Ctrl+Shift+I")
        self.assertEqual(w.select_image_action.text(), "Select image")
        self.assertTrue(w.tool_buttons["select"].toolTip().startswith("Select image · S"))
        self.assertIn("Images are not selected", w.tool_buttons["lasso"].toolTip())
        w.diagnostics.update_values({"tool": "select"})
        self.assertEqual(w.diagnostics.values["tool"].text(), "Select image")
        self.assertEqual(w.delete_action.text(), "Delete selected objects")
        self.assertEqual(w.selection_label.text(), "Selected items: 1")
        with patch("whiteboard.window.QFileDialog.getOpenFileName", return_value=("", "")) as dialog:
            self.assertFalse(w.insert_image())
        self.assertEqual(dialog.call_args.args[1], "Insert image")
        self.assertIn("JPEG images", dialog.call_args.args[3])


if __name__ == "__main__":
    unittest.main()

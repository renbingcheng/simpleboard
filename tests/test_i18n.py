"""Language changes must preserve work and remain usable after packaging."""
from __future__ import annotations

import ast
import contextlib
import io
import json
import os
from pathlib import Path
import re
from string import Formatter
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QPointF, Qt
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import QApplication, QLabel, QMessageBox, QPushButton

from scripts.package_source import source_files
from scripts.public_distribution import distribution_resources
from whiteboard.app import main
from whiteboard.dialogs import BrushPopover, EraserPopover
from whiteboard.i18n import DEFAULT_LANGUAGE, language, set_language, tr
from whiteboard.models import Brush, InkSample, Stroke
from whiteboard.settings import AppSettings
from whiteboard.storage import DocumentError, document_to_dict, load_document, save_document
from whiteboard.translations import EN
from whiteboard.window import MainWindow, file_filter

ROOT = Path(__file__).resolve().parents[1]


class TranslationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        # The Windows offscreen plugin does not discover system fonts itself.
        # Use the installed application font so measurements aren't tofu boxes.
        font = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts/msyh.ttc"
        if font.is_file():
            QFontDatabase.addApplicationFont(str(font))

    def setUp(self):
        set_language(DEFAULT_LANGUAGE)
        self.temp = tempfile.TemporaryDirectory(prefix="simpleboard-i18n-")
        self.directory = Path(self.temp.name)
        self.windows = []

    def tearDown(self):
        for window in self.windows:
            window._settings_timer.stop()
            window._closing = True
            window.close()
            window.deleteLater()
        self.app.processEvents()
        set_language(DEFAULT_LANGUAGE)
        self.temp.cleanup()

    def window(self, code="zh_CN"):
        settings = AppSettings(self.directory / "settings.json")
        settings.language = code
        window = MainWindow(recovery_enabled=False, settings=settings)
        window._startup_done = True
        self.windows.append(window)
        return window

    def test_catalog_covers_source_strings_and_preserves_format_fields(self):
        formatter = Formatter()
        for source, translated in EN.items():
            fields = lambda text: sorted((name, spec, conversion) for _, name, spec, conversion
                                         in formatter.parse(text) if name is not None)
            self.assertEqual(fields(source), fields(translated), source)
            self.assertTrue(translated.strip(), source)
        # Catch new unextracted Chinese UI text as well as missing catalog entries.
        exempt = {"简体中文", "语言 / Language"}
        for path in (ROOT / "whiteboard").glob("*.py"):
            if path.name in {"translations.py", "smoke.py"}:
                continue
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    if re.search(r"[\u4e00-\u9fff]", node.value) and node.value not in exempt:
                        self.assertIn(node.value, EN, f"{path.name}:{node.lineno}")

    def test_missing_translations_and_invalid_language_fall_back(self):
        set_language("en")
        self.assertEqual(tr("Missing {value}", value=4), "Missing 4")
        set_language("unsupported")
        self.assertEqual(language(), "zh_CN")
        self.assertEqual(tr("保存"), "保存")

    def test_settings_round_trip_and_unknown_language_preserve_tools(self):
        path = self.directory / "settings.json"
        settings = AppSettings(path)
        self.assertEqual(settings.language, "zh_CN")
        settings.language = "en"
        settings.pens[0].color = "#123456"
        settings.save()
        self.assertEqual(AppSettings(path).language, "en")
        data = json.loads(path.read_text(encoding="utf-8"))
        for value in ("fr", None, [], 12):
            data["language"] = value
            path.write_text(json.dumps(data), encoding="utf-8")
            loaded = AppSettings(path)
            self.assertEqual(loaded.language, "zh_CN")
            self.assertEqual(loaded.pens[0].color, "#123456")
        del data["language"]
        path.write_text(json.dumps(data), encoding="utf-8")
        self.assertEqual(AppSettings(path).language, "zh_CN")

    def test_invalid_saved_tools_report_errors_in_saved_language(self):
        path = self.directory / "settings.json"
        path.write_text('{"language":"en","pens":[]}', encoding="utf-8")
        settings = AppSettings(path)
        self.assertIn("Cannot read settings", settings.load_error)
        self.assertIn("Invalid pen slot settings", settings.load_error)
        self.assertEqual(language(), "zh_CN")

    def test_switch_in_place_preserves_board_history_selection_and_view(self):
        window = self.window()
        stroke = Stroke([InkSample(10, 20), InkSample(50, 40)], Brush())
        window.scene.add_stroke(stroke)
        window.canvas.selection_ids = {stroke.id}
        window.canvas.view_scale = 1.5
        window.canvas.view_offset = QPointF(20, 30)
        window.current_path = self.directory / "保存.qboard"
        snapshot = document_to_dict(window.scene.document)
        stack = window.scene.undo_stack
        history = (stack.count(), stack.index(), stack.isClean())
        action = window.save_action
        canvas = window.canvas
        window.language_actions["en"].trigger()
        self.assertIs(window.canvas, canvas)
        self.assertIs(window.save_action, action)
        self.assertEqual(action.text(), "Save")
        self.assertEqual(window.menu_button.text(), "")
        self.assertEqual(window.menu_button.accessibleName(), "Menu")
        self.assertIn("Pen 1", window.pen_buttons[0].toolTip())
        self.assertEqual(window.selection_label.text(), "Selected strokes: 1")
        self.assertIn("保存.qboard", window.windowTitle())
        self.assertEqual(window.file_status.text(), "Unsaved changes")
        self.assertEqual(document_to_dict(window.scene.document), snapshot)
        self.assertEqual((stack.count(), stack.index(), stack.isClean()), history)
        self.assertEqual(window.canvas.selection_ids, {stroke.id})
        self.assertEqual(window.canvas.view_scale, 1.5)
        self.assertEqual(window.canvas.view_offset, QPointF(20, 30))
        self.assertEqual(AppSettings(window.settings.path).language, "en")
        window.language_actions["zh_CN"].trigger()
        self.assertEqual(action.text(), "保存")
        self.assertEqual(window.menu_button.text(), "")
        self.assertEqual(window.menu_button.accessibleName(), "菜单")
        self.assertEqual(document_to_dict(window.scene.document), snapshot)
        window._undo()
        self.assertEqual(window.scene.document.strokes, [])

    def test_saved_english_language_is_applied_on_window_creation(self):
        settings = AppSettings(self.directory / "settings.json")
        settings.language = "en"
        settings.save()
        window = MainWindow(recovery_enabled=False, settings=AppSettings(settings.path))
        window._startup_done = True
        self.windows.append(window)
        self.assertEqual(window.windowTitle(), "Untitled board — SimpleBoard")
        self.assertTrue(window.language_actions["en"].isChecked())
        self.assertEqual(window.recent_menu.actions()[0].text(), "No recent files")

    def test_filters_errors_and_document_schema_follow_language_contract(self):
        window = self.window("en")
        self.assertIn("All files", file_filter())
        with self.assertRaisesRegex(DocumentError, "Cannot open the board"):
            load_document(self.directory / "missing.qboard")
        target = self.directory / "未翻译.qboard"
        save_document(target, window.scene.document)
        self.assertEqual(document_to_dict(load_document(target)), document_to_dict(window.scene.document))
        with patch("whiteboard.window.QFileDialog.getSaveFileName", return_value=("", "")) as dialog:
            self.assertFalse(window.save_as_document())
        self.assertEqual(dialog.call_args.args[1], "Save board")
        self.assertTrue(dialog.call_args.args[2].endswith("Board.qboard"))

    def test_english_cancel_save_preserves_dirty_document(self):
        window = self.window("en")
        window.scene.add_stroke(Stroke([InkSample(10, 20)], Brush()))
        before = document_to_dict(window.scene.document)

        def cancel(box):
            self.assertEqual(box.windowTitle(), "Save board")
            next(button for button in box.buttons() if button.text() == "Cancel").click()
            return 0

        with patch.object(QMessageBox, "exec", cancel):
            self.assertFalse(window.new_document())
        self.assertTrue(window.is_dirty)
        self.assertEqual(document_to_dict(window.scene.document), before)

    def test_diagnostics_retranslate_values_without_changing_payload(self):
        window = self.window()
        data = {"source": "mouse", "tool": "eraser", "pressure": 0.5}
        window.diagnostics.update_values(data)
        self.assertEqual(window.diagnostics.values["tool"].text(), "橡皮擦")
        window.change_language("en")
        self.assertEqual(window.diagnostics.values["tool"].text(), "Eraser")
        self.assertEqual(window.diagnostics.values["source"].text(), "Mouse")
        self.assertEqual(data, {"source": "mouse", "tool": "eraser", "pressure": 0.5})

    def test_english_minimum_layout_and_popovers(self):
        window = self.window("en")
        window.resize(720, 560)
        window.show()
        self.app.processEvents()
        for widget in (window.top_card, window.tools_card):
            self.assertTrue(window.host.rect().contains(widget.geometry()))
        for widget in (window.menu_button, window.undo_button, window.redo_button, window.zoom_card):
            self.assertTrue(window.top_card.rect().contains(widget.geometry()))
        for popover in (BrushPopover(Brush(), True, 1), EraserPopover(12, False)):
            popover.adjustSize()
            self.assertLess(popover.height(), 500)
            for button in popover.findChildren(QPushButton):
                self.assertLess(button.fontMetrics().horizontalAdvance(button.text()), button.width())
            for label in popover.findChildren(QLabel):
                if not label.wordWrap():
                    self.assertLessEqual(label.fontMetrics().horizontalAdvance(label.text()), label.width())
            popover.deleteLater()

    def test_qt_standard_buttons_switch_and_cli_help_is_english(self):
        set_language("zh_CN")
        self.assertNotEqual(QCoreApplication.translate("QPlatformTheme", "Cancel"), "Cancel")
        set_language("en")
        self.assertEqual(QCoreApplication.translate("QPlatformTheme", "Cancel"), "Cancel")
        output = io.StringIO()
        with contextlib.redirect_stdout(output), self.assertRaises(SystemExit) as stopped:
            main(["--language", "en", "--help"])
        self.assertEqual(stopped.exception.code, 0)
        self.assertIn("Offline handwriting", output.getvalue())
        self.assertIn("Open a .qboard file", output.getvalue())

    def test_release_inventories_include_catalogs_and_bilingual_docs(self):
        source = {p.relative_to(ROOT).as_posix() for p in source_files(ROOT)}
        resources = {p.relative_to(ROOT).as_posix() for p in distribution_resources(ROOT)}
        for name in ("README.md", "README.en.md", "CONTRIBUTING.md", "CONTRIBUTING.en.md"):
            self.assertIn(name, source)
            self.assertIn(name, resources)
        self.assertIn("whiteboard/i18n.py", source)
        self.assertIn("whiteboard/translations.py", source)
        for name in ("README.md", "README.en.md", "CONTRIBUTING.md", "CONTRIBUTING.en.md"):
            for link in re.findall(r"\]\(([^)]+)\)", (ROOT / name).read_text(encoding="utf-8")):
                if not link.startswith(("https://", "http://")):
                    self.assertTrue((ROOT / link.split("#")[0]).exists(), (name, link))


if __name__ == "__main__":
    unittest.main()

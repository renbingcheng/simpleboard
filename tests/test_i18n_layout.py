"""Rendered layout regressions for translated and unusually long UI text."""
from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtWidgets import QApplication, QLabel, QMenu, QStyle

from whiteboard.dialogs import BrushPopover, EraserPopover
from whiteboard.i18n import set_language, tr
from whiteboard.settings import AppSettings
from whiteboard.window import MainWindow


class TranslatedLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.previous_font = cls.app.font()
        cls.app.setFont(QFont("Microsoft YaHei UI", 10))
        font = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts/msyh.ttc"
        if font.is_file():
            QFontDatabase.addApplicationFont(str(font))

    @classmethod
    def tearDownClass(cls):
        cls.app.setFont(cls.previous_font)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="simpleboard-translated-layout-")
        settings = AppSettings(Path(self.temp.name) / "settings.json")
        self.window = MainWindow(recovery_enabled=False, settings=settings)
        self.window._startup_done = True
        self.window.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
        self.window.resize(720, 560)
        self.window.show()
        self.app.processEvents()

    def tearDown(self):
        self.window._settings_timer.stop()
        self.window._closing = True
        self.window.close()
        self.window.deleteLater()
        self.app.processEvents()
        set_language("zh_CN")
        self.temp.cleanup()

    def assert_text_fits(self, label):
        self.assertTrue(label.parentWidget().rect().contains(label.geometry()), label.text())
        if label.wordWrap():
            self.assertLessEqual(label.heightForWidth(label.width()), label.height(), label.text())
        else:
            self.assertGreaterEqual(label.height(), label.fontMetrics().height(), label.text())
            self.assertLessEqual(label.fontMetrics().horizontalAdvance(label.text()),
                                 label.contentsRect().width() + 1, label.text())

    def test_diagnostics_and_selection_remain_readable_across_repeated_switches(self):
        w = self.window
        w.diagnostics_action.setChecked(True)
        for code in ("en", "zh_CN", "en", "zh_CN"):
            w.change_language(code)
            w._on_selection_changed(100000)
            w.diagnostics.update_values({"source": "Surface Pen · Windows Ink",
                "tool": "highlighter", "pressure": .999, "tilt_x": -90, "tilt_y": 90,
                "samples": 1000000, "paint_ms": 12345.67, "cache_mb": 128, "scale": 8})
            self.app.processEvents()
            with self.subTest(language=code):
                for panel in (w.diagnostics, w.selection_card):
                    self.assertTrue(w.host.rect().contains(panel.geometry()))
                    self.assertFalse(panel.geometry().intersects(w.tools_card.geometry()))
                    for label in panel.findChildren(QLabel):
                        self.assert_text_fits(label)
                self.assertFalse(w.diagnostics.geometry().intersects(w.selection_card.geometry()))

    def test_long_toast_reflows_after_resize_and_does_not_cover_controls(self):
        w = self.window
        w.diagnostics_action.setChecked(True)
        for code in ("en", "zh_CN"):
            w.change_language(code)
            message = tr("无法保存恢复副本：{p0}", p0="[WinError 5] " + "Long folder name/" * 18)
            w.resize(1280, 800)
            self.app.processEvents()
            w._toast(message)
            w.resize(720, 560)
            self.app.processEvents()
            self.assertEqual(w.toast_label.text(), message)
            self.assert_text_fits(w.toast_label)
            self.assertTrue(w.host.rect().contains(w.toast_label.geometry()))
            for panel in (w.top_card, w.tools_card, w.diagnostics):
                self.assertFalse(w.toast_label.geometry().intersects(panel.geometry()))

    def test_extreme_error_text_is_bounded_and_full_message_remains_accessible(self):
        w = self.window
        w.change_language("en")
        w.diagnostics_action.setChecked(True)
        for message in ("Long error message with a path/" * 200, "x" * 6000):
            w._toast(message)
            self.app.processEvents()
            self.assertEqual(w.toast_label.accessibleName(), message)
            self.assertIn("…", w.toast_label.text())
            self.assertTrue(w.host.rect().contains(w.toast_label.geometry()))
            for panel in (w.top_card, w.tools_card, w.diagnostics):
                self.assertFalse(w.toast_label.geometry().intersects(panel.geometry()))

    def test_recent_menu_bounds_names_preserves_literal_ampersand_and_original_path(self):
        w = self.window
        paths = [Path(self.temp.name) / ("Research_课程_" + "very_long_name_" * 12 + ".qboard"),
                 Path(self.temp.name) / "Research & notes.qboard"]
        w.settings.recent_files = [str(path) for path in paths]
        for code in ("en", "zh_CN"):
            w.change_language(code)
            w._refresh_recent_menu()
            w.recent_menu.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
            w.recent_menu.show()
            self.app.processEvents()
            self.assertLess(w.recent_menu.width(), 480)
            actions = w.recent_menu.actions()
            self.assertIn("…", actions[0].text())
            self.assertEqual(actions[0].toolTip(), str(paths[0]))
            self.assertIn("&&", actions[1].text())
            with patch.object(w, "_load_path") as open_path:
                actions[0].trigger()
                open_path.assert_called_once_with(paths[0])
            w.recent_menu.hide()

    def test_popovers_wrap_both_languages_and_switch_closes_stale_popup(self):
        w = self.window
        for code in ("en", "zh_CN"):
            w.change_language(code)
            popups = (BrushPopover(w.settings.pens[0], True, 1, w),
                      BrushPopover(w.settings.highlighter, False, 1, w),
                      EraserPopover(80, True, w))
            for popup in popups:
                popup.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
                popup.adjustSize()
                popup.show()
                self.app.processEvents()
                self.assertLess(popup.height(), 510)
                for label in popup.findChildren(QLabel):
                    self.assert_text_fits(label)
                popup.close()
        popup = BrushPopover(w.settings.pens[0], True, 1, w)
        popup.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
        w._show_popup(popup, w.pen_buttons[0])
        w.change_language("en")
        self.assertIsNone(w._popover)

    def test_long_status_elides_with_full_text_and_menus_allow_scrolling(self):
        w = self.window
        w.change_language("en")
        w._recovery_error = True
        long_status = "Recovery save failed · Please save manually " * 4
        with patch.dict("whiteboard.translations.EN", {"恢复副本保存失败 · 请手动保存": long_status}):
            w._update_title()
            self.app.processEvents()
            self.assert_text_fits(w.file_status)
            self.assertTrue(w.file_status.text().endswith("…"))
            self.assertEqual(w.file_status.toolTip(), long_status)
        w.main_menu.ensurePolished()
        self.assertEqual(w.main_menu.style().styleHint(QStyle.StyleHint.SH_Menu_Scrollable,
                                                      None, w.main_menu), 1)


if __name__ == "__main__":
    unittest.main()

"""Responsive layout contracts for the single-header, single-palette UI."""

from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtWidgets import QApplication, QToolButton

from whiteboard.settings import AppSettings
from whiteboard.window import MainWindow


class LayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="qboard-layout-")
        self.window = MainWindow(recovery_enabled=False,
                                 settings=AppSettings(Path(self.temp.name) / "settings.json"))
        self.window._startup_done = True
        self.window.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)

    def tearDown(self):
        self.window._settings_timer.stop()
        with patch.object(self.window, "_maybe_leave_document", return_value=True):
            self.window.close()
        self.window.deleteLater()
        self.app.processEvents()
        self.temp.cleanup()

    def show_size(self, width, height):
        self.window.resize(width, height)
        self.window.show()
        self.app.processEvents()

    def test_one_fixed_header_and_one_drawing_palette_fit_all_supported_widths(self):
        window = self.window
        for width, height in ((720, 1000), (720, 560), (900, 600), (1180, 760)):
            with self.subTest(width=width, height=height):
                self.show_size(width, height)
                self.assertEqual(window.top_card.geometry(), QRect(0, 0, width, 56))
                self.assertEqual(window.canvas.y(), window.top_card.height())
                self.assertIs(window.zoom_card.parentWidget(), window.top_card)
                self.assertIsNone(window.top_card.graphicsEffect())
                self.assertIsNone(window.zoom_card.graphicsEffect())
                self.assertTrue(window.host.rect().contains(window.tools_card.geometry()))
                self.assertFalse(window.top_card.geometry().intersects(window.tools_card.geometry()))
                self.assertEqual(window.tools_card.findChildren(QToolButton),
                                 window.pen_buttons + list(window.tool_buttons.values()))
                self.assertIs(window.undo_button.parentWidget(), window.top_card)
                self.assertIs(window.redo_button.parentWidget(), window.top_card)
                self.assertTrue(window.brand_label.isHidden())
                self.assertTrue(window.offline_badge.isHidden())

    def test_header_controls_never_overlap_or_clip_and_keep_touch_targets(self):
        window = self.window
        window.current_path = Path("课堂讲解与会议记录_很长的文件名用于验证窄屏布局.qboard")
        for width, height in ((720, 1000), (900, 600), (1180, 760)):
            with self.subTest(width=width):
                self.show_size(width, height)
                controls = [window.menu_button, window.file_title,
                            window.undo_button, window.redo_button, window.zoom_card]
                for control in controls:
                    self.assertTrue(window.top_card.rect().contains(control.geometry()))
                for index, control in enumerate(controls):
                    for other in controls[index + 1:]:
                        self.assertFalse(control.geometry().intersects(other.geometry()))
                self.assertLessEqual(window.file_title.fontMetrics().horizontalAdvance(window.file_title.text()),
                                     window.file_title.width())
                for button in (window.menu_button, window.undo_button,
                               window.redo_button, window.zoom_label,
                               *window.zoom_card.findChildren(QToolButton),
                               *window.pen_buttons, *window.tool_buttons.values()):
                    self.assertGreaterEqual(button.width(), 44)
                    self.assertGreaterEqual(button.height(), 44)
                    self.assertTrue(button.isVisible())
                    self.assertTrue(button.parentWidget().rect().contains(button.geometry()))
                for zoom in ("10%", "100%", "800%"):
                    window.zoom_label.setText(zoom)
                    self.assertLess(window.zoom_label.fontMetrics().horizontalAdvance(zoom),
                                    window.zoom_label.width())

    def test_document_and_view_actions_remain_accessible_without_extra_cards(self):
        window = self.window
        self.show_size(720, 1000)
        actions = window.main_menu.actions()
        for action in (window.new_action, window.open_action, window.save_action,
                       window.save_as_action, window.export_action, window.grid_action,
                       window.fullscreen_action, window.diagnostics_action):
            self.assertIn(action, actions)
        self.assertTrue(window.grid_action.isCheckable())
        self.assertFalse(window.grid_action.isChecked())
        for button in (window.new_button, window.open_button):
            self.assertTrue(button.isHidden())
        zoom_on_host = QRect(window.zoom_card.mapTo(window.host, QPoint()), window.zoom_card.size())
        self.assertTrue(window.top_card.geometry().contains(zoom_on_host))


if __name__ == "__main__":
    unittest.main()

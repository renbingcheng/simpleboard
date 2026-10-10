"""Palette placement is persistent, touch-sized, and safe during input."""
from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, QPointF, QRect, Qt
from PySide6.QtWidgets import QApplication

from whiteboard.i18n import set_language
from whiteboard.settings import AppSettings
from whiteboard.window import MainWindow


class ToolbarPositionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="simpleboard-toolbar-")
        self.path = Path(self.temp.name) / "settings.json"
        self.window = MainWindow(recovery_enabled=False, settings=AppSettings(self.path))
        self.window._startup_done = True
        self.window.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
        self.window.show()
        self.app.processEvents()

    def tearDown(self):
        self.window._settings_timer.stop()
        self.window._closing = True
        self.window.close()
        self.app.removeEventFilter(self.window.canvas)
        self.window.deleteLater()
        self.app.processEvents()
        set_language("zh_CN")
        self.temp.cleanup()

    def test_menu_is_exclusive_and_persists_each_position(self):
        w = self.window
        self.assertTrue(w.toolbar_position_actions["bottom"].isChecked())
        for position in ("left", "right", "bottom"):
            w.toolbar_position_actions[position].trigger()
            self.assertEqual(w.settings.toolbar_position, position)
            self.assertEqual(AppSettings(self.path).toolbar_position, position)
            self.assertEqual([name for name, action in w.toolbar_position_actions.items() if action.isChecked()], [position])

    def test_invalid_saved_positions_preserve_other_settings(self):
        settings = AppSettings(self.path)
        settings.pens[0].color = "#123456"
        settings.save()
        value = json.loads(self.path.read_text(encoding="utf-8"))
        for invalid in ("top", None, [], 42):
            value["toolbar_position"] = invalid
            self.path.write_text(json.dumps(value), encoding="utf-8")
            loaded = AppSettings(self.path)
            self.assertEqual(loaded.toolbar_position, "bottom")
            self.assertEqual(loaded.pens[0].color, "#123456")
            self.assertIsNone(loaded.load_error)

    def test_repeated_resize_and_reposition_keep_all_buttons_visible(self):
        w = self.window
        buttons = w.pen_buttons + list(w.tool_buttons.values()) + [w.insert_image_button]
        for width, height in ((720, 560), (900, 600), (720, 1000), (1280, 800), (720, 560)):
            w.resize(width, height)
            for position in ("left", "right", "bottom"):
                with self.subTest(size=(width, height), position=position):
                    w.set_toolbar_position(position)
                    self.app.processEvents()
                    self.assertTrue(w.host.rect().contains(w.tools_card.geometry()))
                    self.assertFalse(w.tools_card.geometry().intersects(w.top_card.geometry()))
                    for index, button in enumerate(buttons):
                        self.assertTrue(button.isVisible())
                        self.assertGreaterEqual(button.width(), 44)
                        self.assertGreaterEqual(button.height(), 44)
                        self.assertTrue(w.tools_card.rect().contains(button.geometry()))
                        for other in buttons[index + 1:]:
                            self.assertFalse(button.geometry().intersects(other.geometry()))
                    if position == "bottom":
                        self.assertEqual(len({button.y() for button in buttons}), 1)
                    elif height == 560:
                        self.assertEqual(len({button.x() for button in buttons}), 2)
                    elif height >= 800:
                        self.assertEqual(len({button.x() for button in buttons}), 1)

    def test_image_insert_lasso_and_image_selection_keep_requested_order(self):
        w = self.window
        ordered = w.pen_buttons + [w.tool_buttons["highlighter"], w.tool_buttons["eraser"],
                                  w.insert_image_button, w.tool_buttons["lasso"],
                                  w.tool_buttons["select"], w.tool_buttons["pan"]]
        self.assertEqual(len(ordered), 12)
        for size in ((720, 560), (720, 1000)):
            w.resize(*size)
            for position in ("left", "right", "bottom"):
                w.set_toolbar_position(position)
                self.app.processEvents()
                layout = w.tools_card.layout()
                cells = [layout.getItemPosition(layout.indexOf(button))[:2] for button in ordered]
                self.assertEqual(cells, sorted(cells))
                self.assertTrue(w.host.rect().contains(w.tools_card.geometry()))

    def test_switch_finishes_stroke_and_closes_old_popup(self):
        w = self.window
        w._show_brush_popover(0)
        self.app.processEvents()
        w.canvas._begin(QPointF(200, 200), "pen", "pen", .5, 1)
        w.canvas._move(QPointF(250, 210), .6, 2)
        self.assertTrue(w.canvas.has_active_edit)
        w.set_toolbar_position("left")
        self.assertFalse(w.canvas.has_active_edit)
        self.assertIsNone(w._popover)
        self.assertEqual(len(w.scene.document.strokes), 1)
        self.assertEqual(w.scene.undo_stack.count(), 1)

    def test_popovers_open_toward_canvas_and_stay_on_screen(self):
        w = self.window
        w.resize(720, 560)
        self.app.processEvents()
        for position in ("left", "right", "bottom"):
            w.set_toolbar_position(position)
            for index in (0, 5):
                w._show_brush_popover(index)
                self.app.processEvents()
                popup = w._popover
                self.assertTrue(popup.screen().availableGeometry().contains(popup.geometry()))
                tools = QRect(w.tools_card.mapToGlobal(QPoint()), w.tools_card.size())
                self.assertFalse(tools.intersects(popup.geometry()))
                popup.close()

    def test_sidebar_toast_and_panels_avoid_the_tools(self):
        w = self.window
        w.resize(720, 560)
        self.app.processEvents()
        for position in ("left", "right"):
            w.set_toolbar_position(position)
            w.diagnostics_action.setChecked(True)
            w._on_selection_changed(100000)
            w._toast("Image inserted · Drag to move, drag a corner to resize")
            self.app.processEvents()
            for panel in (w.toast_label, w.diagnostics, w.selection_card):
                self.assertTrue(w.host.rect().contains(panel.geometry()))
                self.assertFalse(panel.geometry().intersects(w.tools_card.geometry()))
            self.assertFalse(w.selection_card.geometry().intersects(w.diagnostics.geometry()))

    def test_toolbar_menu_retranslates_without_changing_position(self):
        w = self.window
        w.set_toolbar_position("right")
        w.change_language("en")
        self.assertEqual(w.toolbar_position_menu.title(), "Toolbar position")
        self.assertEqual(w.toolbar_position_actions["right"].text(), "Right")
        self.assertTrue(w.toolbar_position_actions["right"].isChecked())


if __name__ == "__main__":
    unittest.main()

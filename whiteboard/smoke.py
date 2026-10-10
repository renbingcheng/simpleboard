"""Executable-level smoke check, also usable after packaging without Python."""
from __future__ import annotations

import json
import math
from pathlib import Path
import platform
import re
import sys
import traceback
import zlib

from PySide6 import QtCore
from PySide6.QtCore import QCoreApplication, QPointF, QRectF, Qt, qVersion
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPen, QPolygonF

from .images import image_rect
from .models import Brush, InkSample, Stroke
from .exporting import export_pdf, export_png
from .storage import document_to_dict, load_document, save_document


def _make_teaching_images(directory):
    """Create deterministic raster fixtures with the same Qt shipped in the EXE."""
    image = QImage(512, 288, QImage.Format.Format_RGB32)
    image.fill(QColor("#EAF4FF"))
    painter = QPainter(image)
    try:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QColor("#17385E"))
        painter.setFont(QFont("Segoe UI", 20, QFont.Weight.DemiBold))
        painter.drawText(QRectF(24, 15, 464, 42), "Right triangle")
        painter.setPen(QPen(QColor("#2563EB"), 4))
        painter.setBrush(QColor("#B8D8FF"))
        painter.drawPolygon(QPolygonF([QPointF(95, 85), QPointF(95, 235), QPointF(405, 235)]))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(QRectF(95, 214, 21, 21))
        painter.setFont(QFont("Segoe UI", 17))
        painter.setPen(QColor("#17385E"))
        painter.drawText(QRectF(56, 135, 35, 40), "a")
        painter.drawText(QRectF(238, 241, 35, 35), "b")
        painter.drawText(QRectF(265, 134, 35, 40), "c")
    finally:
        painter.end()
    png_path = directory / "smoke-import-source.png"
    assert image.save(str(png_path), "PNG")
    formula = QImage(340, 190, QImage.Format.Format_RGB32)
    formula.fill(QColor("#FFF1D6"))
    painter = QPainter(formula)
    try:
        painter.setPen(QColor("#7B4A16"))
        painter.setFont(QFont("Segoe UI", 17, QFont.Weight.DemiBold))
        painter.drawText(QRectF(22, 20, 296, 45), "Triangle area")
        painter.setFont(QFont("Segoe UI", 24))
        painter.drawText(QRectF(22, 78, 296, 66), "A = a × b / 2")
    finally:
        painter.end()
    jpeg_path = directory / "smoke-import-source.jpg"
    assert formula.save(str(jpeg_path), "JPEG", 95), "The packaged JPEG codec is missing"
    return png_path, jpeg_path


def _move_image(canvas, image_id, target):
    """Use the image pointer's drag transaction path."""
    canvas.set_tool("select")
    item = canvas.scene.get_image(image_id)
    original = image_rect(item)
    center = canvas.world_to_screen(original.center())
    canvas.selection_ids = {image_id}
    before = canvas.scene.undo_stack.count()
    canvas._begin(center, "mouse", "select")
    assert canvas._interaction == "move"
    canvas._move(center + (target - original.topLeft()) * canvas.view_scale)
    canvas.finish_interaction()
    moved = image_rect(canvas.scene.get_image(image_id))
    assert moved.topLeft() == target and moved.size() == original.size()
    assert canvas.scene.undo_stack.count() == before + 1


def _assert_pdf_draws_images(contents, minimum):
    """Check embedded XObjects and page draw commands without adding a PDF DLL."""
    assert len(re.findall(rb"/Subtype\s*/Image\b", contents)) >= minimum
    drawing_commands = 0
    for match in re.finditer(rb"stream\r?\n(.*?)\r?\nendstream", contents, re.DOTALL):
        stream = match.group(1)
        try:
            stream = zlib.decompress(stream)
        except zlib.error:
            pass
        drawing_commands += len(re.findall(rb"/Im\d+\s+Do\b", stream))
    assert drawing_commands >= minimum, "PDF image resources are not drawn on the page"


def run_smoke(app, window, directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    result = {"ok": False, "qt": qVersion(), "platform": platform.platform(),
              "qpa": app.platformName(), "frozen": bool(getattr(sys, "frozen", False)),
              "executable": sys.executable, "qt_core": QtCore.__file__,
              "runtime_directory": str(getattr(sys, "_MEIPASS", "")),
              "plugin_paths": QCoreApplication.libraryPaths()}
    try:
        window.resize(1180, 760)
        app.processEvents()
        canvas, scene = window.canvas, window.scene
        initial_toolbar_position = window.settings.toolbar_position
        window.set_toolbar_position("bottom")
        canvas.set_view(1.0, QPointF())
        png_path, jpeg_path = _make_teaching_images(directory)
        first_id = canvas.insert_image_path(png_path)
        _move_image(canvas, first_id, QPointF(120, 100))
        original = image_rect(scene.get_image(first_id))
        before_resize = scene.undo_stack.count()
        canvas._begin(canvas.world_to_screen(original.bottomRight()), "mouse", "select")
        assert canvas._interaction == "resize-image"
        target = original.topLeft() + QPointF(original.width() * .8, original.height() * .8)
        canvas._move(canvas.world_to_screen(target))
        canvas.finish_interaction()
        resized = image_rect(scene.get_image(first_id))
        assert math.isclose(resized.width(), original.width() * .8)
        assert math.isclose(resized.height(), original.height() * .8)
        assert scene.undo_stack.count() == before_resize + 1
        scene.undo_stack.undo()
        assert image_rect(scene.get_image(first_id)) == original
        scene.undo_stack.redo()
        assert image_rect(scene.get_image(first_id)) == resized
        second_id = canvas.insert_image_path(jpeg_path)
        _move_image(canvas, second_id, QPointF(660, 100))
        window._select_pen(0)
        # These files were created above. Removing them verifies the board
        # owns its pixels and can reopen without any original image path.
        png_path.unlink()
        jpeg_path.unlink()
        assert not png_path.exists() and not jpeg_path.exists()
        pen = Brush(color="#2563EB", width=7)
        points = [InkSample(160 + i*7, 235 + 35*math.sin(i/7), i*4, 0.15+0.8*i/54)
                  for i in range(55)]
        scene.add_stroke(Stroke(points, pen))
        scene.add_stroke(Stroke([InkSample(170, 325), InkSample(530, 325)],
                                Brush(color="#FFD84D", kind="highlighter", width=25, opacity=0.3)))
        scene.add_stroke(Stroke([InkSample(140, 165), InkSample(290, 165)],
                                Brush(color="#D73B3E", width=12, pressure_enabled=False)))
        canvas.set_view(1.0, QPointF())
        # Ring a handwritten annotation entirely inside the background image.
        # Moving it and undoing must leave both embedded pictures untouched.
        images_before_lasso = list(scene.document.images)
        annotation_id = scene.document.strokes[-1].id
        canvas.set_tool("lasso")
        canvas._begin(QPointF(130, 145), "mouse", "lasso")
        for point in (QPointF(310, 145), QPointF(310, 185), QPointF(130, 185), QPointF(130, 145)):
            canvas._move(point)
        canvas.finish_interaction()
        assert canvas.selection_ids == {annotation_id}, "Lasso must select ink only over an image"
        canvas._begin(QPointF(180, 165), "mouse", "lasso")
        canvas._move(QPointF(190, 175))
        canvas.finish_interaction()
        assert scene.get(annotation_id).offset_x == 10
        assert scene.document.images == images_before_lasso
        scene.undo_stack.undo()
        assert scene.get(annotation_id).offset_x == 0
        canvas.clear_selection()
        window._select_pen(0)
        scene.erase([(340, 205), (340, 270)], 9)
        scene.undo_stack.undo()
        scene.undo_stack.redo()
        save_document(directory / "smoke.qboard", scene.document)
        reopened = load_document(directory / "smoke.qboard")
        assert len(reopened.strokes) == 3 and reopened.strokes[0].erase_masks
        assert len(reopened.images) == 2
        assert document_to_dict(reopened) == document_to_dict(scene.document)
        scene.reset(reopened)
        canvas.load_view_from_document()
        canvas.set_grid_enabled(True)
        export_args = (scene, canvas.size(), canvas.view_scale, canvas.view_offset)
        export_png(directory / "canvas.png", *export_args, dpr=canvas.devicePixelRatioF())
        export_pdf(directory / "canvas.pdf", *export_args)
        pdf_bytes = (directory / "canvas.pdf").read_bytes()
        assert pdf_bytes.startswith(b"%PDF-") and b"%%EOF" in pdf_bytes[-128:]
        _assert_pdf_draws_images(pdf_bytes, 2)
        exported = QImage(str(directory / "canvas.png"))
        dpr = canvas.devicePixelRatioF()
        pixel = lambda x, y: exported.pixelColor(round(x * dpr), round(y * dpr)).name().upper()
        assert pixel(130, 315) == "#EAF4FF", "Embedded PNG is missing from the export"
        assert pixel(180, 165) == "#D73B3E", "Ink must render over embedded images"
        jpeg_color = exported.pixelColor(round(670 * dpr), round(270 * dpr))
        expected = QColor("#FFF1D6")
        assert max(abs(jpeg_color.red() - expected.red()), abs(jpeg_color.green() - expected.green()),
                   abs(jpeg_color.blue() - expected.blue())) < 8, "Embedded JPEG is missing from the export"
        app.processEvents()
        assert window.grab().save(str(directory / "window-grid.png"))
        canvas.set_grid_enabled(False)
        app.processEvents()
        assert window.grab().save(str(directory / "window.png"))
        # Exercise the actual frozen catalog and Qt standard-button resources,
        # rather than inferring their presence from a successful build.
        initial_language = window.settings.language
        snapshot = document_to_dict(scene.document)
        history = (scene.undo_stack.count(), scene.undo_stack.index())
        checked_languages = []
        window.resize(720, 560)
        window.diagnostics_action.setChecked(True)
        window.diagnostics.update_values({"source": "Surface Pen · Windows Ink", "tool": "pen"})
        for language, caption in (("en", "Save"), ("zh_CN", "保存")):
            window.change_language(language)
            app.processEvents()
            assert window.save_action.text() == caption
            cancel = QCoreApplication.translate("QPlatformTheme", "Cancel")
            assert (cancel == "Cancel") == (language == "en"), (language, cancel)
            assert document_to_dict(scene.document) == snapshot
            assert (scene.undo_stack.count(), scene.undo_stack.index()) == history
            assert window.host.rect().contains(window.diagnostics.geometry())
            assert not window.diagnostics.geometry().intersects(window.tools_card.geometry())
            assert window.grab().save(str(directory / f"window-{language}.png"))
            checked_languages.append(language)
        window.change_language(initial_language)
        result["languages_checked"] = checked_languages
        checked_positions = []
        for size in ((720, 560), (1180, 760)):
            window.resize(*size)
            app.processEvents()
            for position in ("bottom", "left", "right"):
                window.set_toolbar_position(position)
                app.processEvents()
                assert window.host.rect().contains(window.tools_card.geometry())
                assert not window.top_card.geometry().intersects(window.tools_card.geometry())
                assert window.host.rect().contains(window.diagnostics.geometry())
                assert not window.diagnostics.geometry().intersects(window.tools_card.geometry())
                buttons = window.pen_buttons + list(window.tool_buttons.values()) + [window.insert_image_button]
                for index, button in enumerate(buttons):
                    assert button.width() >= 44 and button.height() >= 44 and button.isVisible()
                    assert window.tools_card.rect().contains(button.geometry())
                    assert all(not button.geometry().intersects(other.geometry()) for other in buttons[index + 1:])
                assert window.grab().save(str(directory / f"toolbar-{position}-{size[0]}x{size[1]}.png"))
                checked_positions.append({"position": position, "window": list(size)})
        window.set_toolbar_position(initial_toolbar_position)
        checked_popovers = []
        for language in ("zh_CN", "en"):
            window.change_language(language)
            assert window.pressure_action in window.main_menu.actions()
            original_pressure = window.settings.pressure_enabled
            for enabled in (True, False):
                if window.pressure_action.isChecked() != enabled:
                    window.pressure_action.trigger()
                window._show_brush_popover(0)
                app.processEvents()
                popup = window._popover
                assert popup.width() == 348 and popup.height() < 500
                assert not hasattr(popup, "pressure_toggle")
                assert all(button.width() >= 44 and button.height() >= 44
                           for button in popup.color_group.buttons())
                assert popup.sensitivity_slider.isEnabled() == enabled
                assert popup.sensitivity_label.isEnabled() == enabled
                assert window.settings.pressure_enabled == enabled
                assert popup.grab().save(str(directory / f"pen-settings-{language}-{'on' if enabled else 'off'}.png"))
                popup.close()
            if window.pressure_action.isChecked() != original_pressure:
                window.pressure_action.trigger()
            for name, open_popup in (("highlighter", lambda: window._show_brush_popover(None)),
                                      ("eraser", window._show_eraser_popover)):
                open_popup()
                app.processEvents()
                popup = window._popover
                assert popup.height() < 500
                assert popup.grab().save(str(directory / f"{name}-settings-{language}.png"))
                popup.close()
            checked_popovers.append(language)
        window.change_language(initial_language)
        result.update(ok=True, strokes=len(reopened.strokes), images=len(reopened.images),
                      image_formats=["PNG", "JPEG"], image_source_files_removed=True,
                      image_move_resize_undo_verified=True, image_under_ink_verified=True,
                      ink_lasso_over_image_verified=True,
                      pdf_image_draws_verified=True, toolbar_positions_checked=checked_positions,
                      tool_settings_checked=checked_popovers,
                      window=[window.width(), window.height()],
                      native_tail_api_enabled=window.native_tail_eraser.enabled,
                      device_pixel_ratio=canvas.devicePixelRatioF(),
                      exports=["PNG", "PDF"], cache_bytes=canvas.renderer.cache_bytes)
        scene.undo_stack.setClean()
    except Exception:
        result["error"] = traceback.format_exc()
    (directory / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    # QApplication.quit avoids a file-save dialog in automated test sessions.
    app.exit(0 if result["ok"] else 1)

"""Executable-level smoke check, also usable after packaging without Python."""
from __future__ import annotations

import json
from pathlib import Path
import platform
import sys
import traceback

from PySide6 import QtCore
from PySide6.QtCore import QCoreApplication, QPointF, qVersion

from .models import Brush, InkSample, Stroke
from .exporting import export_pdf, export_png
from .storage import document_to_dict, load_document, save_document


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
        pen = Brush(color="#2563EB", width=7)
        points = [InkSample(160 + i*7, 235 + 35*__import__('math').sin(i/7), i*4, 0.15+0.8*i/54)
                  for i in range(55)]
        scene.add_stroke(Stroke(points, pen))
        scene.add_stroke(Stroke([InkSample(170, 325), InkSample(530, 325)],
                                Brush(color="#FFD84D", kind="highlighter", width=25, opacity=0.3)))
        canvas.set_view(1.0, QPointF())
        scene.erase([(340, 205), (340, 270)], 9)
        scene.undo_stack.undo()
        scene.undo_stack.redo()
        save_document(directory / "smoke.qboard", scene.document)
        reopened = load_document(directory / "smoke.qboard")
        assert len(reopened.strokes) == 2 and reopened.strokes[0].erase_masks
        canvas.set_grid_enabled(True)
        export_args = (scene, canvas.size(), canvas.view_scale, canvas.view_offset)
        export_png(directory / "canvas.png", *export_args, dpr=canvas.devicePixelRatioF())
        export_pdf(directory / "canvas.pdf", *export_args)
        pdf_bytes = (directory / "canvas.pdf").read_bytes()
        assert pdf_bytes.startswith(b"%PDF-") and b"%%EOF" in pdf_bytes[-128:]
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
        result.update(ok=True, strokes=len(reopened.strokes), window=[window.width(), window.height()],
                      native_tail_api_enabled=window.native_tail_eraser.enabled,
                      device_pixel_ratio=canvas.devicePixelRatioF(),
                      exports=["PNG", "PDF"], cache_bytes=canvas.renderer.cache_bytes)
        scene.undo_stack.setClean()
    except Exception:
        result["error"] = traceback.format_exc()
    (directory / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    # QApplication.quit avoids a file-save dialog in automated test sessions.
    app.exit(0 if result["ok"] else 1)

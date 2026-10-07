"""Product branding must not relocate existing data or split instance locks."""
from __future__ import annotations

import contextlib
import io
import os
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QLockFile, QPointF, QSize
from PySide6.QtWidgets import QApplication

from whiteboard import __version__
from whiteboard.app import configure_application_identity, main
from whiteboard.exporting import export_pdf
from whiteboard.models import BoardDocument, Brush, InkSample, Stroke
from whiteboard.recovery import RecoveryManager
from whiteboard.scene import Scene
from whiteboard.settings import AppSettings, app_data_directory
from whiteboard.storage import document_to_dict, save_document
from whiteboard.window import MainWindow


APP = QApplication.instance() or QApplication([])


class BrandingTests(unittest.TestCase):
    def setUp(self):
        identity = (APP.applicationName(), APP.organizationName(),
                    APP.applicationDisplayName(), APP.applicationVersion())

        def restore_identity():
            APP.setApplicationName(identity[0])
            APP.setOrganizationName(identity[1])
            APP.setApplicationDisplayName(identity[2])
            APP.setApplicationVersion(identity[3])

        self.addCleanup(restore_identity)
        temporary = tempfile.TemporaryDirectory(prefix="simpleboard-branding-")
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)

    def test_rename_keeps_legacy_data_location_and_single_instance_lock(self):
        APP.setApplicationName("LocalWhiteboard")
        APP.setOrganizationName("LocalWhiteboard")
        APP.setApplicationDisplayName("白板")
        previous_directory = app_data_directory()

        # No real user files are read or written. The existing/new identities
        # must still contend for exactly the same lock in an isolated location.
        with patch("whiteboard.settings.QStandardPaths.writableLocation", return_value=str(self.directory)):
            previous_lock = QLockFile(str(app_data_directory() / "instance.lock"))
            previous_lock.setStaleLockTime(0)
            self.assertTrue(previous_lock.tryLock(0))
            self.addCleanup(previous_lock.unlock)

            configure_application_identity(APP)
            renamed_lock = QLockFile(str(app_data_directory() / "instance.lock"))
            renamed_lock.setStaleLockTime(0)
            self.assertFalse(renamed_lock.tryLock(0))
            previous_lock.unlock()
            self.assertTrue(renamed_lock.tryLock(0))
            renamed_lock.unlock()

        self.assertEqual(app_data_directory(), previous_directory)
        self.assertEqual(APP.applicationName(), "LocalWhiteboard")
        self.assertEqual(APP.organizationName(), "LocalWhiteboard")
        self.assertEqual(APP.applicationDisplayName(), "SimpleBoard")
        self.assertEqual(APP.applicationVersion(), __version__)

    def test_existing_settings_and_recovery_are_loaded_without_migration(self):
        settings = AppSettings(self.directory / "settings.json")
        settings.pens[0].color = "#E5484D"
        settings.eraser_radius = 33
        settings.save()
        document = BoardDocument(strokes=[Stroke([InkSample(10, 20), InkSample(50, 30)], Brush())])
        recovery_path = self.directory / "recovery.qboard"
        save_document(recovery_path, document)
        original_files = {path.name: path.read_bytes() for path in self.directory.iterdir()}

        configure_application_identity(APP)
        with patch("whiteboard.settings.QStandardPaths.writableLocation", return_value=str(self.directory)):
            restored_settings = AppSettings()
            recovery = RecoveryManager(lambda: BoardDocument())
            self.addCleanup(recovery.shutdown)
            self.assertEqual(restored_settings.path, settings.path)
            self.assertEqual(restored_settings.pens[0].color, "#E5484D")
            self.assertEqual(restored_settings.eraser_radius, 33)
            self.assertEqual(recovery.path, recovery_path)
            self.assertTrue(recovery.available())
            self.assertEqual(document_to_dict(recovery.recover()), document_to_dict(document))

        self.assertEqual({path.name: path.read_bytes() for path in self.directory.iterdir()}, original_files)

    def test_window_title_and_exported_pdf_use_new_product_name(self):
        configure_application_identity(APP)
        window = MainWindow(recovery_enabled=False, settings=AppSettings(self.directory / "settings.json"))
        window._startup_done = True

        def close_window():
            window._settings_timer.stop()
            window._closing = True
            window.close()
            window.deleteLater()
            APP.processEvents()

        self.addCleanup(close_window)
        self.assertEqual(window.windowTitle(), "未命名白板 — SimpleBoard")
        window.current_path = self.directory / "课堂.qboard"
        window._update_title()
        self.assertEqual(window.windowTitle(), "课堂.qboard — SimpleBoard")

        target = self.directory / "view.pdf"
        export_pdf(target, Scene(), QSize(320, 180), 1, QPointF())
        metadata = re.search(rb"/Creator\s*\((.*?)\)", target.read_bytes(), re.DOTALL)
        self.assertIsNotNone(metadata)
        creator = re.sub(rb"\\([0-7]{1,3})", lambda match: bytes([int(match.group(1), 8)]), metadata.group(1))
        creator = creator.decode("utf-16") if creator.startswith((b"\xfe\xff", b"\xff\xfe")) else creator.decode("ascii")
        self.assertEqual(creator, "SimpleBoard")

    def test_cli_help_and_version_identify_release_before_creating_a_window(self):
        for argument, expected in (("--help", "SimpleBoard · 本地离线手写"),
                                   ("--version", f"SimpleBoard {__version__}")):
            with self.subTest(argument=argument):
                output = io.StringIO()
                with contextlib.redirect_stdout(output), self.assertRaises(SystemExit) as stopped:
                    main([argument])
                self.assertEqual(stopped.exception.code, 0)
                self.assertIn(expected, output.getvalue())
        self.assertRegex(__version__, r"^\d+\.\d+\.\d+$")


if __name__ == "__main__":
    unittest.main()

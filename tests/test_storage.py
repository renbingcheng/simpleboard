from __future__ import annotations

import copy
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest import mock
import zipfile

from PySide6.QtCore import QCoreApplication

from whiteboard.models import BoardDocument, Brush, EraseMask, InkSample, Stroke
from whiteboard.recovery import RecoveryManager
from whiteboard.settings import AppSettings
from whiteboard.storage import DocumentError, document_from_dict, document_to_dict, load_document, save_document, validate_snapshot


def example_document() -> BoardDocument:
    return BoardDocument(
        strokes=[Stroke(
            samples=[InkSample(3, 4, 100, 0.15, -12, 20), InkSample(20, 23, 110, 0.85, 2, -8)],
            brush=Brush("#E5484D", 6, "pen", True, 1.4, 0.9),
            offset_x=18,
            offset_y=-9,
            erase_masks=[EraseMask([(8, 9), (10, 11)], 4)],
        )],
        view_scale=2.3,
        view_offset_x=37,
        view_offset_y=-120,
        background="#FAFAFA",
    )


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "白板.qboard"

    def write_raw(self, value, *, extra_member=False):
        with zipfile.ZipFile(self.path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("document.json", json.dumps(value))
            if extra_member:
                archive.writestr("../unexpected.txt", "nothing is ever extracted")

    def test_roundtrip_pressure_masks_and_view(self):
        original = example_document()
        save_document(self.path, original)
        restored = load_document(self.path)
        self.assertEqual(document_to_dict(original), document_to_dict(restored))
        self.assertEqual(restored.strokes[0].samples[0].tilt_x, -12)
        self.assertEqual(restored.strokes[0].erase_masks[0].points[-1], (10, 11))

    def test_detached_snapshot_has_no_live_lists(self):
        document = example_document()
        snapshot = document_to_dict(document)
        reference = copy.deepcopy(snapshot)
        document.strokes[0].samples.append(InkSample(900, 900))
        document.strokes[0].erase_masks[0].points.append((90, 100))
        document.strokes[0].brush.color = "#111111"
        self.assertEqual(snapshot, reference)

    def test_atomic_replace_failure_keeps_original_and_cleans_temporary(self):
        original = example_document()
        save_document(self.path, original)
        old_bytes = self.path.read_bytes()
        original.strokes.clear()
        with mock.patch("whiteboard.storage.os.replace", side_effect=PermissionError("模拟文件被锁定")):
            with self.assertRaises(DocumentError):
                save_document(self.path, original)
        self.assertEqual(self.path.read_bytes(), old_bytes)
        self.assertEqual(list(Path(self.directory.name).iterdir()), [self.path])

    def test_invalid_save_never_overwrites_previous(self):
        original = example_document()
        save_document(self.path, original)
        old_bytes = self.path.read_bytes()
        original.view_offset_x = float("nan")
        with self.assertRaises(DocumentError):
            save_document(self.path, original)
        self.assertEqual(self.path.read_bytes(), old_bytes)

    def test_corrupt_archive_and_extra_members_are_rejected(self):
        self.path.write_bytes(b"not a ZIP file")
        with self.assertRaises(DocumentError):
            load_document(self.path)
        self.write_raw(document_to_dict(example_document()), extra_member=True)
        with self.assertRaises(DocumentError):
            load_document(self.path)
        self.assertFalse((Path(self.directory.name).parent / "unexpected.txt").exists())

    def test_unknown_version_nonfinite_duplicate_and_malformed_are_rejected(self):
        base = document_to_dict(example_document())
        cases = []
        value = copy.deepcopy(base)
        value["version"] = 500
        cases.append(value)
        value = copy.deepcopy(base)
        value["view"]["scale"] = float("nan")
        cases.append(value)
        value = copy.deepcopy(base)
        value["strokes"][0]["samples"][0][0] = float("inf")
        cases.append(value)
        value = copy.deepcopy(base)
        value["strokes"].append(copy.deepcopy(value["strokes"][0]))
        cases.append(value)
        value = copy.deepcopy(base)
        value["strokes"][0]["erase_masks"][0]["points"] = [[1]]
        cases.append(value)
        value = copy.deepcopy(base)
        value["strokes"][0]["samples"][0][3] = True
        cases.append(value)
        for case in cases:
            with self.subTest(case=cases.index(case)):
                self.write_raw(case)
                with self.assertRaises(DocumentError):
                    load_document(self.path)

    def test_valid_benchmark_scale_document_is_accepted(self):
        document = BoardDocument(strokes=[
            Stroke(samples=[InkSample(float(i), float(j), i, 0.5) for i in range(100)], brush=Brush())
            for j in range(2000)
        ])
        save_document(self.path, document)
        restored = load_document(self.path)
        self.assertEqual(len(restored.strokes), 2000)
        self.assertEqual(sum(len(stroke.samples) for stroke in restored.strokes), 200_000)

    def test_size_limit_is_checked_before_loading_json(self):
        save_document(self.path, example_document())
        with mock.patch("whiteboard.storage.MAX_JSON_BYTES", 5):
            with self.assertRaises(DocumentError):
                load_document(self.path)

    def test_save_and_load_share_count_limits_including_mask_points(self):
        document = example_document()  # Two raw samples and two mask points.
        save_document(self.path, document)
        old_bytes = self.path.read_bytes()
        for name, limit in (("MAX_POINTS", 3), ("MAX_STROKES", 0), ("MAX_MASKS", 0),
                            ("MAX_JSON_BYTES", 5), ("MAX_ARCHIVE_BYTES", 5)):
            with self.subTest(limit=name), mock.patch(f"whiteboard.storage.{name}", limit):
                with self.assertRaises(DocumentError):
                    save_document(self.path, document)
                with self.assertRaises(DocumentError):
                    load_document(self.path)
                self.assertEqual(self.path.read_bytes(), old_bytes)
        with mock.patch("whiteboard.storage.MAX_POINTS", 4), mock.patch("whiteboard.storage.MAX_STROKES", 1), mock.patch("whiteboard.storage.MAX_MASKS", 1):
            save_document(self.path, document)
            self.assertEqual(len(load_document(self.path).strokes), 1)

    def test_validation_does_not_allocate_sample_models(self):
        snapshot = document_to_dict(example_document())
        with mock.patch("whiteboard.storage.InkSample", side_effect=AssertionError("No model allocation")):
            validate_snapshot(snapshot)

    def test_save_rejects_loader_invalid_coordinates_and_duplicate_ids(self):
        document = example_document()
        save_document(self.path, document)
        old_bytes = self.path.read_bytes()
        document.view_offset_x = 1e10
        with self.assertRaises(DocumentError):
            save_document(self.path, document)
        self.assertEqual(self.path.read_bytes(), old_bytes)
        document.view_offset_x = 0
        document.strokes.append(copy.deepcopy(document.strokes[0]))
        with self.assertRaises(DocumentError):
            save_document(self.path, document)
        self.assertEqual(self.path.read_bytes(), old_bytes)


class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "settings.json"

    def test_settings_roundtrip_and_recent_file_limit(self):
        settings = AppSettings(self.path)
        self.assertEqual(len(settings.pens), 6)
        settings.pens[2].color = "#ABCDEF"
        settings.pens[2].width = 8
        settings.pressure_enabled = False
        settings.sensitivity = 1.8
        settings.eraser_whole = True
        for i in range(12):
            settings.add_recent(Path(self.directory.name) / f"{i}.qboard")
        settings.add_recent(Path(self.directory.name) / "11.qboard")
        settings.save()
        restored = AppSettings(self.path)
        self.assertIsNone(restored.load_error)
        self.assertEqual(restored.pens[2].color, "#ABCDEF")
        self.assertEqual(restored.pens[2].width, 8)
        self.assertFalse(restored.pressure_enabled)
        self.assertTrue(restored.eraser_whole)
        self.assertEqual(restored.sensitivity, 1.8)
        self.assertEqual(len(restored.recent_files), 10)
        self.assertEqual(len(set(restored.recent_files)), 10)

    def test_invalid_settings_do_not_partially_apply(self):
        self.path.write_text('{"pressure_enabled": false, "sensitivity": -9}', encoding="utf-8")
        settings = AppSettings(self.path)
        self.assertTrue(settings.pressure_enabled)
        self.assertEqual(settings.sensitivity, 1)
        self.assertIsNotNone(settings.load_error)


class RecoveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QCoreApplication.instance() or QCoreApplication([])

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.document = example_document()
        self.manager = RecoveryManager(lambda: self.document, directory=self.directory.name)
        self.addCleanup(self.manager.shutdown)

    def wait_until(self, predicate, timeout=3):
        deadline = time.monotonic() + timeout
        while not predicate() and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.002)
        self.assertTrue(predicate(), "恢复后台任务没有按时完成")

    def test_flush_roundtrip_metadata_and_discard_allows_new_saves(self):
        self.manager.source_path = "D:/课程/讲解.qboard"
        self.manager.schedule()
        self.assertTrue(self.manager.flush())
        self.assertTrue(self.manager.available())
        recovered = self.manager.recover()
        self.assertEqual(document_to_dict(recovered), document_to_dict(self.document))
        self.assertEqual(self.manager.source_path, "D:/课程/讲解.qboard")
        self.manager.discard()
        self.assertFalse(self.manager.available())
        self.manager.schedule()
        self.assertTrue(self.manager.flush())
        self.assertTrue(self.manager.available())

    def test_existing_recovery_is_preserved_until_resolved(self):
        save_document(self.manager.path, self.document)
        previous = self.manager.path.read_bytes()
        other = RecoveryManager(lambda: BoardDocument(), directory=self.directory.name)
        self.addCleanup(other.shutdown)
        other.schedule()
        self.assertTrue(other.flush())
        self.assertEqual(previous, other.path.read_bytes())
        other.recover()
        other.schedule()
        self.assertTrue(other.flush())
        self.assertEqual(load_document(other.path).strokes, [])

    def test_debounce_writes_and_emits_saved(self):
        self.manager._debounce.setInterval(15)
        signals = []
        self.manager.saved.connect(lambda: signals.append(True))
        self.manager.schedule()
        self.wait_until(lambda: bool(signals))
        self.assertTrue(self.manager.available())

    def test_deadline_fires_despite_continuous_scheduling(self):
        self.manager._debounce.setInterval(500)
        self.manager._deadline.setInterval(40)
        self.manager.schedule()
        started = time.monotonic()
        while time.monotonic() - started < 0.15 and not self.manager.available():
            self.manager.schedule()
            self.app.processEvents()
            time.sleep(0.005)
        self.wait_until(self.manager.available)
        self.assertLess(time.monotonic() - started, 0.5)

    def test_inflight_save_cannot_overwrite_newer_flush(self):
        from whiteboard.storage import save_snapshot
        entered = threading.Event()
        release = threading.Event()
        observed = []

        def delayed_write(path, snapshot):
            observed.append(snapshot)
            entered.set()
            self.assertTrue(release.wait(3))
            save_snapshot(path, snapshot)

        with mock.patch("whiteboard.recovery.save_snapshot", side_effect=delayed_write):
            self.manager.schedule()
            self.manager._start_write()
            self.assertTrue(entered.wait(2))
            previous = self.document.strokes[0]
            self.document.strokes[0] = replace(previous, samples=[*previous.samples, InkSample(99, 100)])
            self.manager.schedule()
            self.assertEqual(len(observed[0]["strokes"][0]["samples"]), 2)
            release.set()
            self.assertTrue(self.manager.flush())
        self.app.processEvents()
        self.assertEqual(len(load_document(self.manager.path).strokes[0].samples), 3)

    def test_capture_is_cheap_and_serialization_runs_on_worker(self):
        from whiteboard.storage import document_to_dict
        gui_thread = threading.get_ident()
        serialization_threads = []
        entered = threading.Event()
        release = threading.Event()

        def blocked_serialize(document):
            serialization_threads.append(threading.get_ident())
            entered.set()
            self.assertTrue(release.wait(3))
            return document_to_dict(document)

        with mock.patch("whiteboard.recovery.document_to_dict", side_effect=blocked_serialize):
            self.manager.source_path = "original.qboard"
            self.manager.schedule()
            self.manager._start_write()
            self.assertTrue(entered.wait(2))
            # Production Scene edits replace committed strokes; the detached
            # ordered list and scalar view/source values must stay unchanged.
            self.document.strokes.clear()
            self.document.view_scale = 5
            self.manager.source_path = "new.qboard"
            release.set()
            self.assertTrue(self.manager.flush())
        restored = self.manager.recover()
        self.assertEqual(len(restored.strokes), 1)
        self.assertEqual(restored.view_scale, 2.3)
        self.assertEqual(self.manager.source_path, "original.qboard")
        self.assertTrue(serialization_threads)
        self.assertTrue(all(thread != gui_thread for thread in serialization_threads))

    def test_write_failure_keeps_previous_recovery_and_reports_error(self):
        self.manager.schedule()
        self.manager.flush()
        old = self.manager.path.read_bytes()
        errors = []
        self.manager.error.connect(errors.append)
        self.document.strokes.clear()
        self.manager.schedule()
        with mock.patch("whiteboard.storage.os.replace", side_effect=OSError("磁盘已满")):
            self.assertFalse(self.manager.flush())
        self.assertEqual(old, self.manager.path.read_bytes())
        self.assertTrue(errors)


if __name__ == "__main__":
    unittest.main()

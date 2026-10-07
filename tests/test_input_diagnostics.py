"""The opt-in recorder must preserve raw transitions without unbounded I/O."""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QInputDevice, QPointingDevice, QTabletEvent

from whiteboard.input_diagnostics import InputDiagnostics


class InputDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.device = QPointingDevice(
            "Surface test tail", 7010, QInputDevice.DeviceType.Stylus,
            QPointingDevice.PointerType.Eraser,
            QInputDevice.Capability.Position | QInputDevice.Capability.Pressure,
            1, 3)

    def event(self, kind=QEvent.Type.TabletRelease):
        event = QTabletEvent(kind, self.device, QPointF(128.25, 240.5), QPointF(300, 400),
                             0.25, 12, -8, 0, 0, 0, Qt.KeyboardModifier.NoModifier,
                             Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton)
        event.setTimestamp(123456)
        return event

    def test_disabled_recorder_never_reads_event_or_writes_files(self):
        recorder = InputDiagnostics()
        with patch("whiteboard.input_diagnostics.QSaveFile") as output:
            recorder.record_tablet(object())  # Cannot even access event methods.
            recorder.record_boundary("ignored", pressure=0.7)
            self.assertEqual(recorder.count, 0)
            recorder.set_enabled(True)
            recorder.record_tablet(self.event(), interaction="erase", tail_contact=True)
            recorder.record_boundary("finish", interaction="erase")
            recorder.set_enabled(False)
            count = recorder.count
            recorder.record_tablet(object())
            self.assertEqual(recorder.count, count)
            output.assert_not_called()

    def test_raw_release_fields_and_boundary_are_not_throttled_or_inferred(self):
        recorder = InputDiagnostics()
        recorder.set_enabled(True)
        recorder.record_tablet(self.event(), source="pen", interaction="erase", tail_contact=True)
        recorder.record_boundary("tablet_release", source="pen", interaction="erase")
        sample, boundary = recorder.snapshot()["events"][-2:]
        self.assertEqual(sample["event_type"], "TabletRelease")
        self.assertEqual(sample["pointer_type"], "Eraser")
        self.assertEqual((sample["button"], sample["buttons"]), (1, 0))
        self.assertEqual(sample["pressure"], 0.25)  # Never rewrite from event type.
        self.assertEqual(sample["timestamp_ms"], 123456)
        self.assertEqual((sample["x"], sample["y"]), (128.25, 240.5))
        self.assertEqual((sample["tilt_x"], sample["tilt_y"]), (12, -8))
        self.assertEqual(sample["device"]["system_id"], 7010)
        self.assertEqual(sample["state"]["interaction"], "erase")
        self.assertEqual(boundary["reason"], "tablet_release")
        self.assertEqual(boundary["sequence"], sample["sequence"] + 1)

    def test_capacity_drops_oldest_and_snapshots_are_independent(self):
        recorder = InputDiagnostics(3)
        recorder.set_enabled(True)
        for index in range(5):
            recorder.record_boundary("sample", index=index)
        before = recorder.snapshot()
        self.assertEqual(recorder.count, 3)
        self.assertEqual(recorder.dropped, 3)
        self.assertEqual([e["state"]["index"] for e in before["events"]], [2, 3, 4])
        before["events"][-1]["state"]["index"] = "edited by caller"
        self.assertEqual(recorder.snapshot()["events"][-1]["state"]["index"], 4)
        recorder.record_boundary("new_contact")
        self.assertEqual(before["recorded_total"], 6)
        self.assertEqual(recorder.snapshot()["recorded_total"], 7)

    def test_reenable_preserves_evidence_and_clear_is_explicit(self):
        recorder = InputDiagnostics()
        recorder.set_enabled(True)
        recorder.record_boundary("focus_out")
        recorder.set_enabled(False)
        recorder.set_enabled(False)
        recorder.set_enabled(True)
        self.assertEqual([e["reason"] for e in recorder.snapshot()["events"]],
                         ["recording_enabled", "focus_out", "recording_disabled", "recording_enabled"])
        recorder.clear()
        self.assertEqual(recorder.count, 0)
        self.assertEqual(recorder.dropped, 0)
        self.assertTrue(recorder.enabled)

    def test_export_is_utf8_json_and_failure_does_not_change_buffer(self):
        recorder = InputDiagnostics()
        recorder.set_enabled(True)
        recorder.record_tablet(self.event(), tool="笔尾橡皮")
        recorder.record_boundary("native_pointer_up", pointer_id=8, native_flags=0x40000)
        recorder.set_enabled(False)
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "input.json"
            self.assertFalse(target.exists())
            recorder.export_json(target)
            exported = json.loads(target.read_text(encoding="utf-8"))
            self.assertEqual(exported["schema"], "local-whiteboard.input-diagnostics")
            self.assertFalse(exported["enabled"])
            self.assertEqual(exported["events"][1]["state"]["tool"], "笔尾橡皮")
            self.assertEqual(exported["events"][2]["reason"], "native_pointer_up")
            self.assertEqual(exported["events"][2]["state"]["pointer_id"], 8)
            self.assertIn("qt", exported["environment"])
            before = recorder.snapshot()["events"]
            with self.assertRaises(OSError):
                recorder.export_json(Path(directory) / "missing-folder" / "input.json")
            self.assertEqual(recorder.snapshot()["events"], before)


if __name__ == "__main__":
    unittest.main()

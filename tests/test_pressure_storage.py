"""Rendering profiles are part of document semantics, not user preferences."""
from __future__ import annotations

import copy
from dataclasses import asdict, replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from whiteboard.models import Brush, EraseMask, InkSample, Stroke
from whiteboard.settings import AppSettings
from whiteboard.storage import (FORMAT_VERSION, DocumentError, brush_from_dict, document_from_dict,
                                document_to_dict, load_document, load_snapshot,
                                save_document, save_snapshot, validate_snapshot)


# Fixed pre-profile bytes, deliberately independent of the current writer.
LEGACY_JSON = '''{
  "format": "qboard", "version": 1, "background": "#FAFAFA",
  "view": {"scale": 2.3, "offset_x": 37, "offset_y": -120},
  "strokes": [{
    "id": "legacy-pressure-example",
    "brush": {"color": "#A1B2C3", "width": 6.25, "kind": "pen",
              "pressure_enabled": true, "sensitivity": 1.8, "opacity": 0.73},
    "offset_x": 18.125, "offset_y": -9.25,
    "samples": [[3, 4, 100, 0, -12, 20], [20, 23, 110, 0.85, 2, -8],
                [21, 23, 115, 0.13, 0, 0]],
    "erase_masks": [{"points": [[8, 9], [10, 11]], "radius": 4.5}]
  }]
}'''


class PressureStorageTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "压感 兼容.qboard"

    def write_fixture(self, contents=LEGACY_JSON):
        with zipfile.ZipFile(self.path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("document.json", contents.encode("utf-8"))

    def mixed_document(self):
        document = document_from_dict(json.loads(LEGACY_JSON))
        document.strokes.append(Stroke(
            id="new-pressure-example",
            samples=[InkSample(30, 40, 200, .05), InkSample(31, 42, 200, .7),
                     InkSample(40, 46, 201, .22)],
            brush=Brush(color="#80123456", width=7, sensitivity=2.4,
                        opacity=.4, render_profile="pressure-v2", input_scale=2.5),
            erase_masks=[EraseMask([(30, 40), (40, 44)], 1.5)],
        ))
        return document

    def test_fixed_v1_archive_loads_as_legacy_without_rewriting_raw_values(self):
        self.write_fixture()
        original_bytes = self.path.read_bytes()
        document = load_document(self.path)
        expected = json.loads(LEGACY_JSON)
        expected["version"] = FORMAT_VERSION
        expected["images"] = []
        expected["strokes"][0]["brush"]["render_profile"] = "legacy-v1"
        expected["strokes"][0]["brush"]["input_scale"] = 1.0
        self.assertEqual(document_to_dict(document), expected)
        self.assertEqual(self.path.read_bytes(), original_bytes)

    def test_v1_may_explicitly_confirm_legacy_but_cannot_claim_new_profile(self):
        value = json.loads(LEGACY_JSON)
        value["strokes"][0]["brush"]["render_profile"] = "legacy-v1"
        self.assertEqual(document_from_dict(value).strokes[0].brush.render_profile, "legacy-v1")
        for profile in ("pressure-v2", "future-v3"):
            with self.subTest(profile=profile):
                value["strokes"][0]["brush"]["render_profile"] = profile
                with self.assertRaises(DocumentError):
                    document_from_dict(value)
                with self.assertRaises(DocumentError):
                    validate_snapshot(value)

    def test_writer_uses_current_format_and_mixed_profiles_survive_roundtrip(self):
        document = self.mixed_document()
        expected = document_to_dict(document)
        save_document(self.path, document)
        serialized = load_snapshot(self.path)
        self.assertEqual(serialized["version"], FORMAT_VERSION)
        self.assertEqual([stroke["brush"]["render_profile"] for stroke in serialized["strokes"]],
                         ["legacy-v1", "pressure-v2"])
        self.assertEqual([stroke["brush"]["input_scale"] for stroke in serialized["strokes"]], [1.0, 2.5])
        self.assertEqual(document_to_dict(load_document(self.path)), expected)

    def test_input_scale_roundtrips_independently_of_current_view(self):
        for scale in (.1, .5, 1, 2, 4, 8):
            with self.subTest(input_scale=scale):
                document = self.mixed_document()
                document.strokes[1].brush = replace(document.strokes[1].brush, input_scale=scale)
                document.view_scale = .75
                save_document(self.path, document)
                restored = load_document(self.path)
                self.assertEqual(restored.strokes[1].brush.input_scale, scale)
                self.assertEqual(restored.strokes[1].samples, document.strokes[1].samples)
                restored.view_scale = 7
                save_document(self.path, restored)
                self.assertEqual(load_document(self.path).strokes[1].brush.input_scale, scale)

    def test_pressure_v2_requires_input_scale_but_legacy_can_default(self):
        value = document_to_dict(self.mixed_document())
        del value["strokes"][0]["brush"]["input_scale"]
        self.assertEqual(document_from_dict(value).strokes[0].brush.input_scale, 1)
        del value["strokes"][1]["brush"]["input_scale"]
        with self.assertRaisesRegex(DocumentError, "缺少落笔"):
            document_from_dict(value)
        with self.assertRaises(DocumentError):
            validate_snapshot(value)

    def test_invalid_input_scale_rejects_save_without_overwriting(self):
        document = self.mixed_document()
        save_document(self.path, document)
        previous = self.path.read_bytes()
        for scale in (0, .099, 8.001, -1, float("nan"), float("inf"), None, True, "1", [], {}):
            with self.subTest(input_scale=scale):
                document.strokes[1].brush = replace(document.strokes[1].brush, input_scale=scale)
                with self.assertRaises(DocumentError):
                    document_from_dict(document_to_dict(document))
                with self.assertRaises(DocumentError):
                    save_document(self.path, document)
                self.assertEqual(self.path.read_bytes(), previous)

    def test_v1_input_scale_must_be_absent_or_explicitly_one(self):
        value = json.loads(LEGACY_JSON)
        value["strokes"][0]["brush"]["input_scale"] = 1
        self.assertEqual(document_from_dict(value).strokes[0].brush.input_scale, 1)
        for scale in (.1, .5, 2, 8):
            with self.subTest(input_scale=scale):
                value["strokes"][0]["brush"]["input_scale"] = scale
                with self.assertRaises(DocumentError):
                    document_from_dict(value)
                with self.assertRaises(DocumentError):
                    validate_snapshot(value)

    def test_v2_requires_profile_for_every_stroke(self):
        for stroke_index in (0, 1):
            with self.subTest(stroke=stroke_index):
                value = document_to_dict(self.mixed_document())
                del value["strokes"][stroke_index]["brush"]["render_profile"]
                with self.assertRaisesRegex(DocumentError, "缺少渲染"):
                    document_from_dict(value)
                with self.assertRaises(DocumentError):
                    validate_snapshot(value)

    def test_unknown_and_non_string_profiles_are_rejected_by_reader_and_writer(self):
        valid = self.mixed_document()
        save_document(self.path, valid)
        previous = self.path.read_bytes()
        for profile in ("", "future-v3", "Pressure-v2", None, True, 2, [], {}):
            with self.subTest(profile=profile):
                document = copy.deepcopy(valid)
                document.strokes[1].brush = replace(document.strokes[1].brush, render_profile=profile)
                value = document_to_dict(document)
                with self.assertRaises(DocumentError):
                    document_from_dict(value)
                with self.assertRaises(DocumentError):
                    save_document(self.path, document)
                self.assertEqual(self.path.read_bytes(), previous)
                self.assertEqual(list(Path(self.directory.name).iterdir()), [self.path])

    def test_profile_validation_for_background_snapshots_preserves_previous_file(self):
        value = document_to_dict(self.mixed_document())
        value["recovery"] = {"source_path": "课程.qboard"}
        save_snapshot(self.path, value)
        previous = self.path.read_bytes()
        invalid = copy.deepcopy(value)
        del invalid["strokes"][1]["brush"]["render_profile"]
        # Snapshot validation must not allocate a second 200k-sample model.
        with patch("whiteboard.storage.InkSample", side_effect=AssertionError("Unexpected allocation")):
            validate_snapshot(value)
            with self.assertRaises(DocumentError):
                save_snapshot(self.path, invalid)
        self.assertEqual(self.path.read_bytes(), previous)
        restored = load_snapshot(self.path)
        self.assertEqual(restored["recovery"], value["recovery"])
        self.assertEqual(document_from_dict(restored).strokes[1].brush.render_profile, "pressure-v2")

    def test_noninteger_and_unsupported_document_versions_remain_rejected(self):
        value = document_to_dict(self.mixed_document())
        for version in (True, 1.0, 2.0, "2", 0, FORMAT_VERSION + 1, None):
            with self.subTest(version=version):
                value["version"] = version
                with self.assertRaises(DocumentError):
                    document_from_dict(value)

    def test_profile_changes_do_not_mutate_previously_detached_snapshot(self):
        document = self.mixed_document()
        snapshot = document_to_dict(document)
        document.strokes[1].brush = replace(document.strokes[1].brush, render_profile="legacy-v1")
        self.assertEqual(snapshot["strokes"][1]["brush"]["render_profile"], "pressure-v2")


class PressureSettingsCompatibilityTests(unittest.TestCase):
    def test_legacy_brush_parser_preserves_values_and_defaults_only_profile(self):
        raw = {"color": "#7FA1B2C3", "width": 3.75, "kind": "pen",
               "pressure_enabled": False, "sensitivity": .1, "opacity": .43}
        brush = brush_from_dict(raw)
        self.assertEqual(asdict(brush), {**raw, "render_profile": "legacy-v1", "input_scale": 1.0})
        self.assertNotIn("render_profile", raw)
        self.assertNotIn("input_scale", raw)
        # A saved tool preset is not a stroke in a v2 document. Pen-down will
        # capture its actual zoom, so missing preset input metadata is harmless.
        self.assertEqual(brush_from_dict({**raw, "render_profile": "pressure-v2"}).input_scale, 1)

    def test_old_settings_keep_palette_widths_pressure_sensitivity_and_other_preferences(self):
        colors = ["#102030", "#314253", "#546576", "#778899", "#AABBCC", "#DDEEFF"]
        value = {
            "version": 1,
            "pens": [
                {"color": color, "width": 2.5 + index, "kind": "pen",
                 "pressure_enabled": index % 2 == 0, "sensitivity": .7 + index * .2,
                 "opacity": .9}
                for index, color in enumerate(colors)
            ],
            "highlighter": {"color": "#EEAA11", "width": 31, "kind": "highlighter",
                            "pressure_enabled": False, "sensitivity": 1.3, "opacity": .27},
            "pressure_enabled": False, "sensitivity": 1.8,
            "eraser_radius": 23, "eraser_whole": True, "recent_files": ["课程 演示.qboard"],
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            path.write_text(json.dumps(value), encoding="utf-8")
            before = path.read_bytes()
            settings = AppSettings(path)
            self.assertIsNone(settings.load_error)
            self.assertEqual(path.read_bytes(), before)
            for brush, original in zip(settings.pens, value["pens"]):
                self.assertEqual(asdict(brush), {**original, "render_profile": "legacy-v1", "input_scale": 1.0})
            self.assertEqual(asdict(settings.highlighter),
                             {**value["highlighter"], "render_profile": "legacy-v1", "input_scale": 1.0})
            settings.save()
            restored = AppSettings(path)
            self.assertIsNone(restored.load_error)
            self.assertEqual(restored.pens, settings.pens)
            self.assertEqual(restored.highlighter, settings.highlighter)
            self.assertEqual(restored.pressure_enabled, value["pressure_enabled"])
            self.assertEqual(restored.sensitivity, value["sensitivity"])
            self.assertEqual(restored.eraser_radius, value["eraser_radius"])
            self.assertEqual(restored.eraser_whole, value["eraser_whole"])
            self.assertEqual(restored.recent_files, value["recent_files"])


if __name__ == "__main__":
    unittest.main()

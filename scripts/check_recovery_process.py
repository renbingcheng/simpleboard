"""Reproduce an abrupt process exit and reopen its isolated recovery file.

All documents, settings and recovery files stay below artifacts. The writer
exits with os._exit(23) after the background recovery writer finishes; no Qt
shutdown, close event or RecoveryManager.flush() runs on the crash path.
"""
from __future__ import annotations

import argparse
from dataclasses import replace
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def documents():
    from whiteboard.models import BoardDocument, Brush, EraseMask, InkSample, Stroke
    saved = BoardDocument(
        strokes=[Stroke(
            samples=[InkSample(10, 25, 120, .15, -12, 8), InkSample(130, 80, 140, .8, 4, -9)],
            brush=Brush("#2563EB", 7, pressure_enabled=True, sensitivity=1.5),
            id="process-check-ink", offset_x=12, offset_y=-7,
            erase_masks=[EraseMask([(60, 20), (60, 90)], 6)],
        )],
        view_scale=1.75, view_offset_x=321, view_offset_y=-90, background="#FFFFFE",
    )
    unsaved = replace(saved, strokes=[*saved.strokes, Stroke(
        samples=[InkSample(210, 140, 180, .3), InkSample(260, 160, 195, .9)],
        brush=Brush("#FFD84D", 24, "highlighter", False, 1, .3),
        id="process-check-unsaved-highlight",
    )])
    return saved, unsaved


def child(phase: str, directory: Path) -> int:
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication
    from whiteboard.recovery import RecoveryManager
    from whiteboard.settings import AppSettings
    from whiteboard.storage import document_to_dict, load_document, load_snapshot, save_document

    app = QApplication([])
    app.setApplicationName("IsolatedRecoveryProcessCheck")
    source_path = directory / "课堂 讲解 白板.qboard"
    settings_path = directory / "临时 设置.json"
    saved, unsaved = documents()

    if phase == "crash":
        settings = AppSettings(settings_path)
        settings.pens[0].color = "#123456"
        settings.pressure_enabled = False
        settings.save()
        save_document(source_path, saved)
        if document_to_dict(load_document(source_path)) != document_to_dict(saved):
            raise AssertionError("Chinese path save/reopen mismatch")
        manager = RecoveryManager(lambda: unsaved, directory=directory)
        if manager.available():
            raise AssertionError("Crash test requires a fresh isolated directory")
        manager.source_path = str(source_path)

        def abort(message, code):
            print(json.dumps({"error": message}, ensure_ascii=False), flush=True)
            os._exit(code)

        def crash_after_valid_write():
            snapshot = load_snapshot(manager.path)
            if document_to_dict(load_document(manager.path)) != document_to_dict(unsaved):
                abort("Recovery contents were not complete before simulated crash", 97)
            if snapshot.get("recovery", {}).get("source_path") != str(source_path):
                abort("Recovery source path missing before simulated crash", 96)
            print(json.dumps({"recovery_valid_before_crash": True, "saved_strokes": 1,
                              "recovery_strokes": 2, "exit": "os._exit(23)"}, ensure_ascii=False), flush=True)
            os._exit(23)

        manager.error.connect(lambda message: abort(message, 98))
        manager.saved.connect(crash_after_valid_write)
        manager._debounce.setInterval(25)
        QTimer.singleShot(10_000, lambda: abort("Recovery writer timed out", 99))
        manager.schedule()
        return app.exec()

    manager = RecoveryManager(lambda: saved, directory=directory)
    try:
        if not manager.available():
            raise AssertionError("Second process cannot find recovery file")
        recovered = manager.recover()
        checks = {
            "recovery_document_exact_match": document_to_dict(recovered) == document_to_dict(unsaved),
            "recovery_contains_unsaved_stroke": len(recovered.strokes) == 2,
            "source_path_exact_match": manager.source_path == str(source_path),
            "chinese_space_path_roundtrip": document_to_dict(load_document(source_path)) == document_to_dict(saved),
            "named_document_preserved_before_crash": len(load_document(source_path).strokes) == 1,
        }
        settings = AppSettings(settings_path)
        checks["isolated_settings_roundtrip"] = settings.pens[0].color == "#123456" and not settings.pressure_enabled
        second_path = directory / "恢复 后继续编辑.qboard"
        save_document(second_path, recovered)
        checks["recovered_document_can_be_saved_and_reopened"] = document_to_dict(load_document(second_path)) == document_to_dict(unsaved)
        if not all(checks.values()):
            raise AssertionError(json.dumps(checks, ensure_ascii=False))
        print(json.dumps({"checks": checks, "source_path": manager.source_path,
                          "recovered_strokes": len(recovered.strokes), "recovered_samples": sum(len(s.samples) for s in recovered.strokes)},
                         ensure_ascii=False), flush=True)
        return 0
    finally:
        manager.shutdown()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", choices=("crash", "read"), help=argparse.SUPPRESS)
    parser.add_argument("--directory", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.phase:
        if args.directory is None:
            parser.error("Child phase requires an explicit isolated directory")
        return child(args.phase, args.directory.resolve())

    artifacts = (ROOT / "artifacts").resolve()
    artifacts.mkdir(parents=True, exist_ok=True)
    run_directory = Path(tempfile.mkdtemp(prefix="recovery-process-", dir=artifacts)).resolve()
    if not run_directory.is_relative_to(artifacts):
        raise RuntimeError("Test directory escaped the artifacts root")
    env = {**os.environ, "QT_QPA_PLATFORM": "offscreen", "PYTHONIOENCODING": "utf-8"}
    result = {"checked_at_utc": datetime.now(timezone.utc).isoformat(),
              "python": sys.version, "test_directory": str(run_directory),
              "actual_appdata_used": False, "writer_expected_exit_code": 23,
              "crash_method": "os._exit(23), no graceful shutdown/flush", "passed": False}
    try:
        writer = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--phase", "crash", "--directory", str(run_directory)],
                                env=env, capture_output=True, text=True, encoding="utf-8", timeout=30)
        result["writer"] = {"exit_code": writer.returncode, "stdout": writer.stdout.strip(), "stderr": writer.stderr.strip()}
        if writer.returncode != 23:
            raise RuntimeError(f"Unexpected writer exit code: {writer.returncode}")
        reader = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--phase", "read", "--directory", str(run_directory)],
                                env=env, capture_output=True, text=True, encoding="utf-8", timeout=30)
        result["reader"] = {"exit_code": reader.returncode, "stderr": reader.stderr.strip()}
        if reader.returncode != 0:
            result["reader"]["stdout"] = reader.stdout.strip()
            raise RuntimeError("Recovery reopen failed in the second process")
        result["reader"].update(json.loads(reader.stdout))
        result["passed"] = True
    except Exception as exc:
        result["error"] = str(exc)
    report_path = artifacts / "recovery-process-check.json"
    report_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"passed": result["passed"], "report": str(report_path),
                      "writer_exit_code": result.get("writer", {}).get("exit_code"),
                      "reader_exit_code": result.get("reader", {}).get("exit_code")}, ensure_ascii=False))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

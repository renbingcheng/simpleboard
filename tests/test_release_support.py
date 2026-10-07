"""Release archives must contain reproducible reviewed source, never local data."""
from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path, PurePosixPath
import subprocess
import sys
import tempfile
import tomllib
import unittest
from unittest.mock import patch
from zipfile import ZipFile

from scripts.package_portable import (package_onedir, project_version, readable_documents,
                                     verify_onefile_resources)
from scripts.package_source import ROOT_FILES, create_archive
from scripts.public_distribution import PUBLIC_DOC_FILES, PUBLIC_DOCUMENTS, distribution_resources
from whiteboard import APP_DISPLAY_NAME, __version__


PROJECT = Path(__file__).resolve().parents[1]


class SourceReleaseTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="simpleboard-source-test-")
        self.addCleanup(temporary.cleanup)
        # Windows runners may expose TEMP through an 8.3 alias (RUNNER~1).
        # Resource helpers return resolved paths, so comparisons use that form.
        self.directory = Path(temporary.name).resolve()
        self.root = self.directory / "project"
        self.root.mkdir()
        # Only minimal fixture documents: no real licenses, screenshots, boards
        # or build outputs, and no dependency on concurrently edited prose.
        for name in ROOT_FILES:
            self.write(name, ("Release fixture: " + name + "\n").encode("utf-8"))
        self.write("whiteboard/__init__.py", (PROJECT / "whiteboard" / "__init__.py").read_bytes())
        for name in ("whiteboard/canvas.py", "tests/test_canvas.py", "scripts/build.py", "tools/check.py"):
            self.write(name, b"def example():\n    return 42\n")
        for name in PUBLIC_DOC_FILES:
            self.write(name, ("Public resource fixture: " + name + "\n").encode("utf-8"))
        self.write("assets/icon.svg", b"<svg/>\n")
        self.write(".github/workflows/check.yml", b"name: Check\n")
        self.write("licenses/README.txt", b"Minimal reviewed upstream-notice fixture.\n")
        self.notice = b"Example upstream notice for archive verification.\n"
        self.write("licenses/example/NOTICE.txt", self.notice)
        self.manifest = {"files": [{"file": "example/NOTICE.txt",
                                    "sha256": hashlib.sha256(self.notice).hexdigest()}]}
        self.write_license_manifest()

    def write(self, relative, data):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def write_license_manifest(self):
        self.write("licenses/sources.json", json.dumps(self.manifest).encode("utf-8"))

    def test_only_reviewed_source_and_notices_enter_archive(self):
        excluded = (
            "artifacts/private.png", "dist/private.py", "tmp/private.py", "output/private.py",
            ".env", "private.qboard", "whiteboard.log", "settings.json",
            "whiteboard/private.qboard", "whiteboard/local.log", "whiteboard/.env",
            "whiteboard/__pycache__/private.py", "whiteboard/.private/secret.py",
            "scripts/tmp/local.py", "tests/output/local.py", "docs/artifacts/private.png",
            "assets/dist/private.svg", "whiteboard/TMP/private.py", "docs/Output/private.png",
            "licenses/unreviewed.txt", "ACCEPTANCE.md", "VALIDATION.md", "PRIVATE_NOTES.md",
            "docs/guide.md", "docs/ARCHITECTURE.md", "docs/RELEASE_REVIEW.md",
            "docs/research/pen-input.md", "docs/images/private-capture.png",
        )
        for relative in excluded:
            self.write(relative, b"LOCAL DATA MUST NOT BE RELEASED\n")
        target = self.directory / "source.zip"
        create_archive(self.root, target)
        prefix = f"SimpleBoard-{__version__}/"
        with ZipFile(target) as archive:
            names = {name.removeprefix(prefix) for name in archive.namelist()}
            for relative in excluded:
                self.assertNotIn(relative, names, relative)
            for relative in ("main.py", "whiteboard/canvas.py", "tests/test_canvas.py",
                             "scripts/build.py", "tools/check.py", *PUBLIC_DOC_FILES, "assets/icon.svg",
                             ".github/workflows/check.yml", "licenses/example/NOTICE.txt",
                             "LICENSE", "AUTHORS.md", "SOURCE_MANIFEST.json"):
                self.assertIn(relative, names, relative)

    def test_public_checkout_without_internal_documents_can_create_source_archive(self):
        for name in ("ACCEPTANCE.md", "VALIDATION.md", "docs/ARCHITECTURE.md", "docs/RELEASE_REVIEW.md"):
            self.assertFalse((self.root / name).exists())
        target = self.directory / "public-checkout.zip"
        create_archive(self.root, target)
        with ZipFile(target) as archive:
            prefix = f"SimpleBoard-{__version__}/"
            for name in (*PUBLIC_DOCUMENTS, "licenses/README.txt", "licenses/sources.json",
                         "licenses/example/NOTICE.txt"):
                self.assertIn(prefix + name, archive.namelist())

    def test_readable_release_resources_exclude_private_notes_and_preserve_licenses(self):
        for name in ("ACCEPTANCE.md", "docs/research/pen.md", "docs/images/private.png",
                     "licenses/unreviewed.txt"):
            self.write(name, b"LOCAL ONLY\n")
        names = {path.relative_to(self.root).as_posix() for path in readable_documents(self.root)}
        self.assertEqual(names, {*PUBLIC_DOCUMENTS, "licenses/README.txt", "licenses/sources.json",
                                 "licenses/example/NOTICE.txt", "requirements.txt", "requirements-dev.txt"})

    def test_onefile_verification_rejects_old_embedded_private_documents(self):
        for name in ("ACCEPTANCE.md", "docs/research/pen.md", "PRIVATE_NOTES.md"):
            with self.subTest(path=name):
                with patch("PyInstaller.archive.readers.CArchiveReader") as reader:
                    reader.return_value.toc = {name: ()}
                    with self.assertRaisesRegex(RuntimeError, "non-public.*clean rebuild"):
                        verify_onefile_resources(self.root, self.root / "SimpleBoard.exe")
                    reader.return_value.extract.assert_not_called()

    def test_onedir_refuses_stale_private_documents_without_modifying_output(self):
        self.write("dist/SimpleBoard/SimpleBoard.exe", b"executable fixture")
        public = self.write("dist/SimpleBoard/README.md", b"existing output must stay unchanged")
        for name in ("ACCEPTANCE.md", "_internal/VALIDATION.md", "docs/research/pen.md",
                     "_internal/docs/RELEASE_REVIEW.md", "LOCAL_NOTES.md"):
            stale = self.write("dist/SimpleBoard/" + name, b"private existing output")
            with self.subTest(path=name):
                with self.assertRaisesRegex(RuntimeError, "non-public.*clean rebuild"):
                    package_onedir(self.root)
                self.assertEqual(stale.read_bytes(), b"private existing output")
                self.assertEqual(public.read_bytes(), b"existing output must stay unchanged")
            stale.unlink()

    def test_clean_onedir_copies_only_public_resources_and_all_reviewed_licenses(self):
        self.write("dist/SimpleBoard/SimpleBoard.exe", b"executable fixture")
        self.write("docs/research/private.md", b"LOCAL ONLY")
        self.write("VALIDATION.md", b"LOCAL ONLY")
        target, entries = package_onedir(self.root)
        self.assertEqual(target.name, f"SimpleBoard-{__version__}-win-x64-onedir.zip")
        names = {name for _, name in entries}
        for source in distribution_resources(self.root):
            relative = source.relative_to(self.root).as_posix()
            for prefix in ("SimpleBoard/", "SimpleBoard/_internal/"):
                self.assertIn(prefix + relative, names)
                self.assertEqual((self.root / "dist" / (prefix + relative)).read_bytes(), source.read_bytes())
        self.assertFalse(any("private.md" in name or "VALIDATION.md" in name for name in names))

    def test_onedir_accepts_noncanonical_project_root(self):
        self.write("dist/SimpleBoard/SimpleBoard.exe", b"executable fixture")
        alternate = self.root / ".." / self.root.name
        self.assertEqual(alternate.resolve(), self.root)
        target, entries = package_onedir(alternate)
        self.assertEqual(target.parent, self.root / "dist")
        names = {name for _, name in entries}
        self.assertIn("SimpleBoard/README.md", names)
        self.assertIn("SimpleBoard/_internal/licenses/example/NOTICE.txt", names)

    def test_notice_tampering_rejects_archive_without_overwriting_valid_output(self):
        destination = self.directory / "source.zip"
        create_archive(self.root, destination)
        previous = destination.read_bytes()
        checksum = destination.with_suffix(".zip.sha256").read_bytes()
        self.write("licenses/example/NOTICE.txt", self.notice + b"unauthorized change\n")
        with self.assertRaisesRegex(ValueError, "checksum"):
            create_archive(self.root, destination)
        self.assertEqual(destination.read_bytes(), previous)
        self.assertEqual(destination.with_suffix(".zip.sha256").read_bytes(), checksum)
        self.assertFalse(destination.with_suffix(".zip.tmp").exists())

    def test_archive_manifest_crc_and_hashes_agree_and_extracted_source_rebuilds(self):
        first = self.directory / "first.zip"
        report = create_archive(self.root, first)
        archive_bytes = first.read_bytes()
        self.assertEqual(report["sha256"], hashlib.sha256(archive_bytes).hexdigest())
        self.assertEqual(report["bytes"], len(archive_bytes))
        self.assertEqual(first.with_suffix(".zip.sha256").read_text(encoding="ascii"),
                         f"{report['sha256']}  first.zip\n")
        prefix = f"SimpleBoard-{__version__}/"
        with ZipFile(first) as archive:
            self.assertIsNone(archive.testzip())
            manifest = json.loads(archive.read(prefix + "SOURCE_MANIFEST.json"))
            self.assertEqual(manifest["version"], __version__)
            self.assertEqual(manifest["project"], "SimpleBoard")
            self.assertEqual(manifest["license"], "GPL-3.0-only")
            declared = {prefix + record["path"] for record in manifest["files"]}
            self.assertEqual(declared | {prefix + "SOURCE_MANIFEST.json"}, set(archive.namelist()))
            self.assertEqual(len(archive.namelist()), len(set(archive.namelist())))
            self.assertEqual(report["files"], len(manifest["files"]))
            for record in manifest["files"]:
                relative = PurePosixPath(record["path"])
                self.assertFalse(relative.is_absolute())
                self.assertNotIn("..", relative.parts)
                data = archive.read(prefix + record["path"])
                self.assertEqual(record["bytes"], len(data))
                self.assertEqual(record["sha256"], hashlib.sha256(data).hexdigest())
            unpacked = self.directory / "unpacked"
            archive.extractall(unpacked)
        # Build again from the extracted archive, with fresh filesystem times.
        # SOURCE_MANIFEST itself must not be accidentally included a second time.
        second = self.directory / "second.zip"
        create_archive(unpacked / prefix.rstrip("/"), second)
        self.assertEqual(second.read_bytes(), archive_bytes)

    def test_notice_manifest_cannot_import_files_outside_licenses_or_project(self):
        outside = self.directory / "private.txt"
        outside.write_bytes(b"private outside project")
        for relative in ("../../private.txt", "../README.md", str(outside), "C:/private.txt"):
            with self.subTest(path=relative):
                self.manifest["files"] = [{"file": relative, "sha256": hashlib.sha256(outside.read_bytes()).hexdigest()}]
                self.write_license_manifest()
                target = self.directory / "unsafe.zip"
                with self.assertRaises(ValueError):
                    create_archive(self.root, target)
                self.assertFalse(target.exists())

    def directory_link(self, link, target):
        if sys.platform == "win32":
            # Junction creation needs no developer mode or elevated symlink
            # privilege, and exercises Python 3.11's Windows reparse handling.
            quote = lambda value: "'" + str(value).replace("'", "''") + "'"
            command = ("New-Item -ItemType Junction -Path " + quote(link)
                       + " -Target " + quote(target) + " -ErrorAction Stop | Out-Null")
            subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
                           check=True, capture_output=True, text=True, timeout=15)
            self.addCleanup(link.rmdir)
        else:
            link.symlink_to(target, target_is_directory=True)
            self.addCleanup(link.unlink)

    def test_source_tree_links_are_rejected_even_when_target_is_inside_project(self):
        # Rejecting internal links as well avoids aliasing reviewed files and
        # ensures this does not pass only because resolve() noticed an escape.
        self.directory_link(self.root / "whiteboard" / "linked_tests", self.root / "tests")
        with self.assertRaisesRegex(ValueError, "[Ll]ink"):
            create_archive(self.root, self.directory / "linked.zip")

    def test_source_tree_cannot_escape_through_directory_link(self):
        outside = self.directory / "outside"
        outside.mkdir()
        (outside / "private.py").write_bytes(b"SECRET = 'must not be packaged'\n")
        self.directory_link(self.root / "whiteboard" / "external", outside)
        destination = self.directory / "external.zip"
        with self.assertRaises(ValueError):
            create_archive(self.root, destination)
        self.assertFalse(destination.exists())


class ReleaseVersionTests(unittest.TestCase):
    def test_windows_metadata_and_packaging_follow_runtime_version(self):
        expression = ast.parse((PROJECT / "version_info.txt").read_text(encoding="utf-8"))
        calls = [node for node in ast.walk(expression) if isinstance(node, ast.Call)]
        strings = {ast.literal_eval(call.args[0]): ast.literal_eval(call.args[1])
                   for call in calls if isinstance(call.func, ast.Name) and call.func.id == "StringStruct"}
        fixed = next(call for call in calls if isinstance(call.func, ast.Name) and call.func.id == "FixedFileInfo")
        fields = {item.arg: ast.literal_eval(item.value) for item in fixed.keywords}
        expected = (*map(int, __version__.split(".")), 0)
        self.assertEqual(fields["filevers"], expected)
        self.assertEqual(fields["prodvers"], expected)
        self.assertEqual(strings["FileVersion"], __version__)
        self.assertEqual(strings["ProductVersion"], __version__)
        self.assertEqual(strings["FileDescription"], APP_DISPLAY_NAME)
        self.assertEqual(strings["ProductName"], APP_DISPLAY_NAME)
        self.assertEqual(strings["OriginalFilename"], APP_DISPLAY_NAME + ".exe")
        self.assertEqual(project_version(PROJECT), __version__)
        metadata = tomllib.loads((PROJECT / "pyproject.toml").read_text(encoding="utf-8"))
        self.assertEqual(metadata["project"]["name"], APP_DISPLAY_NAME)
        self.assertEqual(metadata["tool"]["setuptools"]["dynamic"]["version"]["attr"], "whiteboard.__version__")


if __name__ == "__main__":
    unittest.main()

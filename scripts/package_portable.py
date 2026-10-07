"""Verify embedded notices and package the default single-file executable."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import re
from zipfile import ZIP_DEFLATED, ZipFile

if __package__:
    from .public_distribution import distribution_resources, reject_private_resources, reviewed_resource
else:
    from public_distribution import distribution_resources, reject_private_resources, reviewed_resource


def readable_documents(root: Path) -> list[Path]:
    return [*distribution_resources(root),
            *(reviewed_resource(root, name) for name in ("requirements.txt", "requirements-dev.txt"))]


def project_version(root: Path) -> str:
    match = re.search(r'^__version__ = "([0-9]+\.[0-9]+\.[0-9]+)"$',
                      (root / "whiteboard" / "__init__.py").read_text(encoding="utf-8"), re.M)
    if not match:
        raise ValueError("Missing SimpleBoard version")
    return match.group(1)


def checksum(path: Path) -> str:
    with path.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    path.with_suffix(path.suffix + ".sha256").write_text(f"{digest}  {path.name}\n", encoding="ascii")
    return digest


def verify_onefile_resources(root: Path, executable: Path) -> None:
    from PyInstaller.archive.readers import CArchiveReader

    archive = CArchiveReader(str(executable))
    names = {name.replace("\\", "/"): name for name in archive.toc}
    reject_private_resources(root, names)
    expected = distribution_resources(root)
    verified = []
    for source in expected:
        relative = source.relative_to(root).as_posix()
        if relative not in names:
            raise RuntimeError(f"Single-file executable is missing {relative}. Rebuild with build.ps1.")
        embedded = archive.extract(names[relative])
        if embedded != source.read_bytes():
            raise RuntimeError(f"Embedded {relative} differs from source. Rebuild after documentation/license changes.")
        verified.append({"path": relative, "sha256": hashlib.sha256(embedded).hexdigest()})
    required = ("python311.dll", "PySide6/Qt6Core.dll", "PySide6/Qt6Gui.dll", "PySide6/Qt6Widgets.dll",
                "PySide6/plugins/platforms/qwindows.dll")
    lower_names = {name.lower() for name in names}
    for name in required:
        if name.lower() not in lower_names:
            raise RuntimeError(f"Single-file executable is missing required runtime {name}.")
    clipper_modules = sorted(name for name in names
                             if name.lower().startswith("pyclipper/_pyclipper") and name.lower().endswith(".pyd"))
    if not clipper_modules:
        raise RuntimeError("Single-file executable is missing the pyclipper geometry runtime.")
    report = root / "artifacts" / "onefile-resource-check.json"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps({"ok": True, "executable": str(executable),
                                  "verified_resource_count": len(verified),
                                  "runtime_checked": [*required, *clipper_modules],
                                  "resources": verified}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Verified {len(verified)} embedded document/license files byte-for-byte against source.")


def package_onedir(root: Path) -> tuple[Path, list[tuple[Path, str]]]:
    portable = root / "dist" / "SimpleBoard"
    if not (portable / "SimpleBoard.exe").is_file():
        raise SystemExit("Build directory fallback with build.ps1 -Mode onedir first.")
    # Do this before copying anything: old builds may already contain private
    # docs at the top level or under PyInstaller's _internal directory.
    reject_private_resources(root, (path.relative_to(portable).as_posix()
                                   for path in portable.rglob("*") if path.is_file()))
    for source in distribution_resources(root):
        destination = portable / "_internal" / source.relative_to(root)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
    for source in readable_documents(root):
        destination = portable / source.relative_to(root)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
    # Test reports and internal documents are never release resources.
    entries = [(path, path.relative_to(portable.parent).as_posix())
               for path in sorted(portable.rglob("*")) if path.is_file()]
    return root / "dist" / f"SimpleBoard-{project_version(root)}-win-x64-onedir.zip", entries


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("onefile", "onedir"), default="onefile")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    if args.mode == "onefile":
        executable = root / "dist" / "SimpleBoard.exe"
        if not executable.is_file():
            raise SystemExit("Build the single-file SimpleBoard.exe with build.ps1 first.")
        verify_onefile_resources(root, executable)
        digest = checksum(executable)
        print(f"{executable}\n{executable.stat().st_size} bytes\nSHA256 {digest}")
        target = root / "dist" / f"SimpleBoard-{project_version(root)}-win-x64-onefile.zip"
        # Keep the standalone EXE, and make notices readable without launching it.
        entries = [(executable, "SimpleBoard.exe")]
        entries.extend((path, path.relative_to(root).as_posix()) for path in readable_documents(root))
    else:
        target, entries = package_onedir(root)
    temporary = target.with_suffix(".zip.tmp")
    with ZipFile(temporary, "w", compression=ZIP_DEFLATED, compresslevel=6) as archive:
        for source, name in entries:
            archive.write(source, name)
    with ZipFile(temporary) as archive:
        failed = archive.testzip()
        if failed:
            raise RuntimeError(f"Archive CRC verification failed: {failed}")
    temporary.replace(target)
    digest = checksum(target)
    print(f"{target}\n{target.stat().st_size} bytes\nSHA256 {digest}")


if __name__ == "__main__":
    main()

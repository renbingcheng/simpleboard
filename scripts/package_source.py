"""Create a reviewed-source ZIP without local boards, logs or build products.

This is a SimpleBoard source archive, not an archive of third-party source code.
Only the explicit root files and permitted source trees below are included.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import stat
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

if __package__:
    from .public_distribution import PUBLIC_DOC_FILES, PUBLIC_ROOT_DOCUMENTS, license_files
else:
    from public_distribution import PUBLIC_DOC_FILES, PUBLIC_ROOT_DOCUMENTS, license_files


ROOT_FILES = (
    ".gitignore", ".gitattributes", ".editorconfig", *PUBLIC_ROOT_DOCUMENTS,
    "pyproject.toml", "requirements.txt", "requirements-dev.txt",
    "main.py", "build.ps1", "launch.ps1", "simpleboard.spec", "version_info.txt",
)
SOURCE_TREES = {
    "whiteboard": {".py"}, "tests": {".py"}, "scripts": {".py", ".ps1"},
    "tools": {".py"},
    "assets": {".ico", ".png", ".svg"}, ".github": {".yml", ".yaml", ".md"},
}
EXCLUDED_DIRECTORIES = {"__pycache__", "artifacts", "output", "tmp", "build", "dist", "venv", "node_modules"}


def _reject_link(path: Path) -> None:
    attributes = getattr(path.lstat(), "st_file_attributes", 0)
    if path.is_symlink() or attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400):
        raise ValueError(f"Links are not permitted in source archives: {path.name}")


def _source_tree_files(tree: Path, extensions: set[str]):
    _reject_link(tree)
    for path in sorted(tree.iterdir()):
        if path.name.startswith(".") or path.name.casefold() in EXCLUDED_DIRECTORIES:
            continue
        _reject_link(path)
        if path.is_dir():
            yield from _source_tree_files(path, extensions)
        elif path.is_file() and path.suffix.lower() in extensions:
            yield path


def _inside(root: Path, relative: str) -> Path:
    parts = PurePosixPath(relative).parts
    if not parts or PurePosixPath(relative).is_absolute() or any(p in {"..", "."} or ":" in p for p in parts):
        raise ValueError(f"Unsafe source path: {relative}")
    path = root.joinpath(*parts)
    for part in (path, *path.parents):
        if part == root:
            break
        _reject_link(part)
    if not path.resolve().is_relative_to(root.resolve()) or not path.is_file():
        raise ValueError(f"Missing or external source file: {relative}")
    return path


def source_files(root: Path) -> list[Path]:
    root = root.resolve()
    files = [_inside(root, name) for name in (*ROOT_FILES, *PUBLIC_DOC_FILES)]
    for directory, extensions in SOURCE_TREES.items():
        tree = root / directory
        if tree.exists():
            for path in _source_tree_files(tree, extensions):
                files.append(_inside(root, path.relative_to(root).as_posix()))
    files.extend(license_files(root))
    return sorted(set(files), key=lambda p: p.relative_to(root).as_posix())


def create_archive(root: Path, destination: Path) -> dict:
    root = root.resolve()
    files = source_files(root)
    source = (root / "whiteboard" / "__init__.py").read_text(encoding="utf-8")
    match = re.search(r'^__version__ = "([0-9]+\.[0-9]+\.[0-9]+)"$', source, re.M)
    if not match:
        raise ValueError("Missing project version")
    version = match.group(1)
    prefix = f"SimpleBoard-{version}"
    manifest = {
        "project": "SimpleBoard", "version": version, "license": "GPL-3.0-only",
        "scope": "Project source and upstream notices; no dependency source tarballs or private test artifacts.",
        "files": [],
    }
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")

    def add(archive, name, data):
        info = ZipInfo(f"{prefix}/{name}", date_time=(1980, 1, 1, 0, 0, 0))
        info.compress_type = ZIP_DEFLATED
        info.create_system = 3
        info.external_attr = 0o100644 << 16
        archive.writestr(info, data, compresslevel=6)

    try:
        with ZipFile(temporary, "w") as archive:
            for path in files:
                relative = path.relative_to(root).as_posix()
                data = path.read_bytes()
                manifest["files"].append({"path": relative, "bytes": len(data),
                                          "sha256": hashlib.sha256(data).hexdigest()})
                add(archive, relative, data)
            add(archive, "SOURCE_MANIFEST.json", (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
        with ZipFile(temporary) as archive:
            failed = archive.testzip()
            if failed:
                raise ValueError(f"ZIP CRC failed: {failed}")
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    digest = hashlib.sha256(destination.read_bytes()).hexdigest()
    destination.with_suffix(destination.suffix + ".sha256").write_text(f"{digest}  {destination.name}\n", encoding="ascii")
    return {"archive": str(destination), "version": version, "files": len(files),
            "bytes": destination.stat().st_size, "sha256": digest}


def main():
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="Destination ZIP; default dist/SimpleBoard-<version>-source.zip")
    args = parser.parse_args()
    from package_portable import project_version
    destination = args.output or root / "dist" / f"SimpleBoard-{project_version(root)}-source.zip"
    print(json.dumps(create_archive(root, destination.resolve()), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

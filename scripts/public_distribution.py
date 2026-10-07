"""Explicit public resources shared by source and executable packaging.

Internal notes can stay in the working tree. They are never discovered by
recursively copying docs/: new public documents must be deliberately listed.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath
import stat


PUBLIC_ROOT_DOCUMENTS = (
    "README.md", "README.en.md", "AUTHORS.md", "CONTRIBUTING.md", "CONTRIBUTING.en.md",
    "CHANGELOG.md", "SECURITY.md",
    "LICENSE", "THIRD_PARTY_NOTICES.txt",
)
PUBLIC_DOC_FILES = ("docs/THIRD_PARTY.md", "docs/images/simpleboard.png")
PUBLIC_DOCUMENTS = (*PUBLIC_ROOT_DOCUMENTS, *PUBLIC_DOC_FILES)


def reviewed_resource(root: Path, relative: str) -> Path:
    root = root.resolve()
    parts = PurePosixPath(relative).parts
    if (not parts or PurePosixPath(relative).is_absolute()
            or any(part in {"..", "."} or ":" in part or "\\" in part for part in parts)):
        raise ValueError(f"Unsafe public resource path: {relative}")
    path = root.joinpath(*parts)
    for current in (path, *path.parents):
        if current == root:
            break
        attributes = getattr(current.lstat(), "st_file_attributes", 0)
        if current.is_symlink() or attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400):
            raise ValueError(f"Links are not permitted in public resources: {relative}")
    if not path.is_file() or not path.resolve().is_relative_to(root):
        raise ValueError(f"Missing or external public resource: {relative}")
    return path


def public_document_files(root: Path) -> list[Path]:
    return [reviewed_resource(root, name) for name in PUBLIC_DOCUMENTS]


def license_files(root: Path) -> list[Path]:
    """Retain every pinned notice, without publishing arbitrary local additions."""
    manifest_path = reviewed_resource(root, "licenses/sources.json")
    files = [manifest_path, reviewed_resource(root, "licenses/README.txt")]
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for record in manifest["files"]:
        path = reviewed_resource(root, "licenses/" + record["file"])
        if hashlib.sha256(path.read_bytes()).hexdigest() != record["sha256"]:
            raise ValueError(f"Upstream notice checksum mismatch: {record['file']}")
        files.append(path)
    return sorted(set(files))


def distribution_resources(root: Path) -> list[Path]:
    return [*public_document_files(root), *license_files(root)]


def reject_private_resources(root: Path, names) -> None:
    """Reject stale internal documents in already-built EXEs and directories."""
    allowed_documents = {name.casefold() for name in PUBLIC_DOCUMENTS}
    allowed_licenses = {path.relative_to(root.resolve()).as_posix().casefold()
                        for path in license_files(root)}
    unexpected = []
    for original in names:
        name = str(original).replace("\\", "/").casefold()
        if name.startswith("_internal/"):
            name = name.removeprefix("_internal/")
        if (name.startswith("docs/") and name not in allowed_documents
                or "/" not in name and name.endswith((".md", ".rst")) and name not in allowed_documents
                or name.startswith("licenses/") and name not in allowed_licenses):
            unexpected.append(str(original))
    if unexpected:
        raise RuntimeError(
            "Build contains non-public documents/resources: " + ", ".join(sorted(unexpected))
            + ". Use a clean rebuild before packaging; existing files have not been removed."
        )

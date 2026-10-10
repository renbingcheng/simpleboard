"""Versioned, bounded, atomic local document storage.

The archive is deliberately data-only: no pickle and no archive extraction.
Coordinates are logical world coordinates, independent of the screen DPI.
"""
from __future__ import annotations

from .i18n import tr

from dataclasses import asdict
import base64
import binascii
import json
import math
import os
from pathlib import Path
import re
import tempfile
from typing import Any
import zipfile
import zlib

from .images import ImageError, MAX_IMAGE_BYTES, MAX_IMAGES, MAX_TOTAL_IMAGE_BYTES, validate_images
from .models import BoardDocument, BoardImage, Brush, EraseMask, InkSample, Stroke

FORMAT_VERSION = 3
MAX_JSON_BYTES = 256 * 1024 * 1024
MAX_ARCHIVE_BYTES = 256 * 1024 * 1024
MAX_STROKES = 100_000
MAX_POINTS = 2_000_000
MAX_MASKS = 100_000
COLOR_PATTERN = re.compile(r"^#[0-9A-Fa-f]{6}(?:[0-9A-Fa-f]{2})?$")


class DocumentError(Exception):
    """A user-readable document or settings failure."""


def _number(value: Any, label: str, minimum=-1e9, maximum=1e9) -> float:
    if isinstance(value, bool) or not isinstance(value, (float, int)):
        raise DocumentError(tr('{p0}必须是数字。', p0=label))
    try:
        result = float(value)
    except (OverflowError, ValueError) as exc:
        raise DocumentError(tr('{p0}超出有效范围。', p0=label)) from exc
    if not math.isfinite(result) or not minimum <= result <= maximum:
        raise DocumentError(tr('{p0}超出有效范围。', p0=label))
    return result


def _mapping(value: Any, label: str) -> dict:
    if not isinstance(value, dict):
        raise DocumentError(tr('{p0}格式不正确。', p0=label))
    return value


def _list(value: Any, label: str, maximum: int) -> list:
    if not isinstance(value, list) or len(value) > maximum:
        raise DocumentError(tr('{p0}格式不正确或数量超出限制。', p0=label))
    return value


def _color(value: Any) -> str:
    if not isinstance(value, str) or not COLOR_PATTERN.fullmatch(value):
        raise DocumentError(tr('颜色格式不正确。'))
    return value


def brush_from_dict(value: Any, *, require_profile: bool = False) -> Brush:
    """Parse a brush; old settings and v1 documents default to legacy ink.

    A v2 document must declare its profile and pressure-v2 input scale explicitly
    so a missing field cannot silently change geometry. Old settings retain
    their existing values and default only the new rendering metadata.
    """
    value = _mapping(value, tr('画笔'))
    kind = value.get("kind", "pen")
    if kind not in ("pen", "highlighter"):
        raise DocumentError(tr('画笔类型不受支持。'))
    pressure = value.get("pressure_enabled", True)
    if not isinstance(pressure, bool):
        raise DocumentError(tr('压感设置格式不正确。'))
    if require_profile and "render_profile" not in value:
        raise DocumentError(tr('白板画笔缺少渲染配置版本。'))
    profile = value.get("render_profile", "legacy-v1")
    if not isinstance(profile, str) or profile not in ("legacy-v1", "pressure-v2"):
        raise DocumentError(tr('画笔渲染配置版本不受支持，请使用兼容的应用版本打开。'))
    if require_profile and profile == "pressure-v2" and "input_scale" not in value:
        raise DocumentError(tr('新版压感画笔缺少落笔时的缩放比例。'))
    return Brush(
        color=_color(value.get("color", "#222222")),
        width=_number(value.get("width", 4), tr('画笔宽度'), 0.01, 10000),
        kind=kind,
        pressure_enabled=pressure,
        sensitivity=_number(value.get("sensitivity", 1), tr('压感灵敏度'), 0.05, 10),
        opacity=_number(value.get("opacity", 1), tr('画笔透明度'), 0, 1),
        render_profile=profile,
        input_scale=_number(value.get("input_scale", 1), tr('落笔缩放比例'), 0.1, 8),
    )


def document_to_dict(document: BoardDocument) -> dict:
    """Convert immutable document values to a detached schema snapshot."""
    try:
        validate_images(document.images, used_ids={stroke.id for stroke in document.strokes if isinstance(stroke.id, str)})
    except ImageError as exc:
        raise DocumentError(str(exc)) from exc
    return {
        "format": "qboard",
        "version": FORMAT_VERSION,
        "background": document.background,
        "view": {
            "scale": document.view_scale,
            "offset_x": document.view_offset_x,
            "offset_y": document.view_offset_y,
        },
        "strokes": [
            {
                "id": str(stroke.id),
                "brush": asdict(stroke.brush),
                "offset_x": stroke.offset_x,
                "offset_y": stroke.offset_y,
                "samples": [
                    [p.x, p.y, p.t, p.pressure, p.tilt_x, p.tilt_y]
                    for p in stroke.samples
                ],
                "erase_masks": [
                    {"points": [[x, y] for x, y in mask.points], "radius": mask.radius}
                    for mask in stroke.erase_masks
                ],
            }
            for stroke in document.strokes
        ],
        "images": [
            {
                "id": image.id,
                "x": image.x,
                "y": image.y,
                "width": image.width,
                "height": image.height,
                "png": base64.b64encode(image.png_data).decode("ascii"),
            }
            for image in document.images
        ],
    }


def _parse_document(value: Any, construct: bool) -> BoardDocument | None:
    value = _mapping(value, tr('白板文档'))
    if value.get("format") != "qboard":
        raise DocumentError(tr('这不是有效的 QBoard 白板文档。'))
    version = value.get("version")
    if type(version) is not int or version not in (1, 2, FORMAT_VERSION):
        raise DocumentError(tr('白板文件版本不受支持，请使用兼容的应用版本打开。'))
    view = _mapping(value.get("view", {}), tr('画布视图'))
    background = _color(value.get("background", "#FFFFFF"))
    scale = _number(view.get("scale", 1), tr('缩放比例'), 0.1, 8)
    offset_x = _number(view.get("offset_x", 0), tr('视图位置'))
    offset_y = _number(view.get("offset_y", 0), tr('视图位置'))
    document = BoardDocument(background=background, view_scale=scale,
                             view_offset_x=offset_x, view_offset_y=offset_y) if construct else None
    point_count = mask_count = 0
    seen_ids: set[str] = set()
    for raw in _list(value.get("strokes"), tr('笔迹'), MAX_STROKES):
        raw = _mapping(raw, tr('笔迹'))
        stroke_id = raw.get("id")
        if not isinstance(stroke_id, str) or not 1 <= len(stroke_id) <= 128 or stroke_id in seen_ids:
            raise DocumentError(tr('笔迹标识缺失、重复或格式不正确。'))
        seen_ids.add(stroke_id)
        samples = []
        raw_samples = _list(raw.get("samples"), tr('采样点'), MAX_POINTS)
        point_count += len(raw_samples)
        if point_count > MAX_POINTS:
            raise DocumentError(tr('文档采样点数量超出限制。'))
        for sample in raw_samples:
            if not isinstance(sample, list) or len(sample) != 6:
                raise DocumentError(tr('采样点格式不正确。'))
            fields = (
                _number(sample[0], tr('采样点位置')),
                _number(sample[1], tr('采样点位置')),
                _number(sample[2], tr('采样时间'), 0, 1e16),
                _number(sample[3], tr('压感'), 0, 1),
                _number(sample[4], tr('笔倾斜'), -90, 90),
                _number(sample[5], tr('笔倾斜'), -90, 90),
            )
            if construct:
                samples.append(InkSample(*fields))
        masks = []
        raw_masks = _list(raw.get("erase_masks", []), tr('擦除蒙版'), MAX_MASKS)
        mask_count += len(raw_masks)
        if mask_count > MAX_MASKS:
            raise DocumentError(tr('文档擦除蒙版数量超出限制。'))
        for raw_mask in raw_masks:
            raw_mask = _mapping(raw_mask, tr('擦除蒙版'))
            points = _list(raw_mask.get("points"), tr('擦除路径'), MAX_POINTS)
            point_count += len(points)
            if point_count > MAX_POINTS:
                raise DocumentError(tr('文档采样点数量超出限制。'))
            path = []
            for point in points:
                if not isinstance(point, list) or len(point) != 2:
                    raise DocumentError(tr('擦除路径坐标格式不正确。'))
                point_x = _number(point[0], tr('擦除位置'))
                point_y = _number(point[1], tr('擦除位置'))
                if construct:
                    path.append((point_x, point_y))
            radius = _number(raw_mask.get("radius"), tr('橡皮半径'), 0.01, 100000)
            if construct:
                masks.append(EraseMask(path, radius))
        brush = brush_from_dict(raw.get("brush"), require_profile=version >= 2)
        if version == 1 and brush.render_profile != "legacy-v1":
            # Original v1 readers ignore unknown brush keys. Accepting a new
            # profile under v1 would allow those readers to silently redraw it.
            raise DocumentError(tr('第 1 版白板文件不能包含新版画笔渲染配置。'))
        if version == 1 and brush.input_scale != 1:
            raise DocumentError(tr('第 1 版白板文件的落笔缩放比例必须为 1。'))
        stroke_x = _number(raw.get("offset_x", 0), tr('笔迹位置'))
        stroke_y = _number(raw.get("offset_y", 0), tr('笔迹位置'))
        if construct:
            document.strokes.append(Stroke(samples=samples, brush=brush, id=stroke_id,
                                           offset_x=stroke_x, offset_y=stroke_y, erase_masks=masks))
    raw_images = _list(value.get("images", []), tr('图片'), MAX_IMAGES)
    if version < 3 and raw_images:
        raise DocumentError(tr('第 1、2 版白板文件不能包含图片。'))
    images = []
    total_image_bytes = 0
    for raw in raw_images:
        raw = _mapping(raw, tr('图片'))
        encoded = raw.get("png")
        if not isinstance(encoded, str) or len(encoded) > ((MAX_IMAGE_BYTES + 2) // 3) * 4:
            raise DocumentError(tr('图片编码无效或数据过大。'))
        try:
            data = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise DocumentError(tr('图片编码无效或数据过大。')) from exc
        total_image_bytes += len(data)
        if total_image_bytes > MAX_TOTAL_IMAGE_BYTES:
            raise DocumentError(tr('文档图片总量超出限制：64 MiB 或 3200 万像素。'))
        images.append(BoardImage(
            png_data=data,
            x=_number(raw.get("x"), tr('图片位置')),
            y=_number(raw.get("y"), tr('图片位置')),
            width=_number(raw.get("width"), tr('图片宽度'), 0.01, 1e9),
            height=_number(raw.get("height"), tr('图片高度'), 0.01, 1e9),
            id=raw.get("id"),
        ))
    try:
        validate_images(images, used_ids=seen_ids)
    except ImageError as exc:
        raise DocumentError(str(exc)) from exc
    if construct:
        document.images = images
    return document


def document_from_dict(value: Any) -> BoardDocument:
    return _parse_document(value, construct=True)


def validate_snapshot(value: Any) -> None:
    """Apply exactly the loader's limits without allocating sample/model copies."""
    _parse_document(value, construct=False)


# Short alias retained for callers that use the schema parser directly.
doc_from_dict = document_from_dict


def _atomic_write_bytes(path: Path, contents: bytes) -> None:
    """Replace only after writing and syncing a sibling temporary file."""
    temporary: str | None = None
    try:
        with tempfile.NamedTemporaryFile(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent, delete=False) as stream:
            temporary = stream.name
            stream.write(contents)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            try:
                os.unlink(temporary)
            except OSError:
                pass


def save_snapshot(path: Path | str, snapshot: dict) -> None:
    """Write a detached schema snapshot; usable from the recovery worker."""
    path = Path(path)
    temporary: str | None = None
    try:
        # Never report a successful save for a document this version cannot
        # reopen. Recovery calls this on its worker, once per saved snapshot.
        validate_snapshot(snapshot)
        with tempfile.NamedTemporaryFile(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent, delete=False) as stream:
            temporary = stream.name
            with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=3) as archive:
                with archive.open("document.json", "w") as member:
                    # json.dumps uses a long C loop that holds the GIL even on a
                    # worker thread. Incremental encoding lets pen input run
                    # between Python timeslices and limits temporary memory.
                    encoder = json.JSONEncoder(ensure_ascii=False, allow_nan=False, separators=(",", ":"))
                    chunks: list[str] = []
                    characters = total_bytes = 0

                    def write_chunks() -> None:
                        nonlocal characters, total_bytes
                        data = "".join(chunks).encode("utf-8")
                        total_bytes += len(data)
                        if total_bytes > MAX_JSON_BYTES:
                            raise DocumentError(tr('白板文件过大，无法保存。'))
                        member.write(data)
                        chunks.clear()
                        characters = 0

                    for chunk in encoder.iterencode(snapshot):
                        chunks.append(chunk)
                        characters += len(chunk)
                        if characters >= 64 * 1024:
                            write_chunks()
                    if chunks:
                        write_chunks()
            if stream.tell() > MAX_ARCHIVE_BYTES:
                raise DocumentError(tr('白板压缩文件过大，无法保存。'))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        temporary = None
    except DocumentError:
        raise
    except (OSError, ValueError, TypeError, OverflowError, RecursionError, zipfile.BadZipFile) as exc:
        raise DocumentError(tr('无法保存白板：{p0}', p0=exc)) from exc
    finally:
        if temporary is not None:
            try:
                os.unlink(temporary)
            except OSError:
                pass


def save_document(path: Path | str, document: BoardDocument) -> None:
    save_snapshot(path, document_to_dict(document))


def load_snapshot(path: Path | str) -> dict:
    path = Path(path)
    try:
        if path.stat().st_size > MAX_ARCHIVE_BYTES:
            raise DocumentError(tr('白板文件过大，无法打开。'))
        with zipfile.ZipFile(path, "r") as archive:
            entries = archive.infolist()
            if len(entries) != 1 or entries[0].filename != "document.json":
                raise DocumentError(tr('白板压缩包结构不正确。'))
            info = entries[0]
            if info.file_size > MAX_JSON_BYTES or info.flag_bits & 1:
                raise DocumentError(tr('白板文档过大或已加密，无法打开。'))
            with archive.open(info) as stream:
                data = stream.read(MAX_JSON_BYTES + 1)
            if len(data) > MAX_JSON_BYTES:
                raise DocumentError(tr('白板文档过大，无法打开。'))
        def reject_constant(constant):
            raise DocumentError(tr('白板包含无效数字：{p0}。', p0=constant))
        value = json.loads(data.decode("utf-8"), parse_constant=reject_constant)
        return _mapping(value, tr('白板文档'))
    except DocumentError:
        raise
    except (OSError, RuntimeError, ValueError, UnicodeError, KeyError, EOFError, RecursionError, zipfile.BadZipFile, NotImplementedError, zlib.error) as exc:
        raise DocumentError(tr('无法打开白板，文件可能已损坏：{p0}', p0=exc)) from exc


def load_document(path: Path | str) -> BoardDocument:
    return document_from_dict(load_snapshot(path))

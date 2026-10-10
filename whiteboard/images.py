"""Bounded raster import and document PNG decoding; no external image paths.

Image I/O uses QImage rather than QPixmap so recovery validation can run on its
worker. Imports preserve EXIF orientation and alpha, and normalize large images
to a practical canvas resolution. Saved PNGs are never silently resampled.
"""
from __future__ import annotations

import math
import hashlib
from collections import OrderedDict
from pathlib import Path
import struct
import threading
import zlib

from PySide6.QtCore import QByteArray, QBuffer, QIODevice, QRectF, QSize, Qt
from PySide6.QtGui import QImage, QImageReader

from .i18n import tr
from .models import BoardImage

MAX_IMAGES = 100
MAX_IMAGE_EDGE = 4096
MAX_IMAGE_PIXELS = 8_000_000
MAX_IMAGE_BYTES = 32 * 1024 * 1024
MAX_TOTAL_IMAGE_BYTES = 64 * 1024 * 1024
MAX_TOTAL_IMAGE_PIXELS = 32_000_000
MAX_IMPORT_BYTES = 64 * 1024 * 1024
MAX_IMPORT_EDGE = 16_384
MAX_IMPORT_PIXELS = 32_000_000
MIN_IMAGE_WORLD_SIZE = 0.01
MAX_IMAGE_WORLD_SIZE = 1e9
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_validated_pngs: OrderedDict[bytes, None] = OrderedDict()
_validation_lock = threading.Lock()


class ImageError(ValueError):
    """An unsupported, damaged or oversized image."""


def image_rect(image: BoardImage) -> QRectF:
    return QRectF(image.x, image.y, image.width, image.height)


def image_size(data: bytes) -> QSize:
    """Validate PNG structure and bounded dimensions without allocating pixels.

    Qt can accept truncated PNGs after emitting a warning. Check chunk lengths,
    CRCs and IEND ourselves before decoding so damaged documents fail cleanly.
    """
    return _png_size(data, MAX_IMAGE_BYTES, MAX_IMAGE_EDGE, MAX_IMAGE_PIXELS)


def _png_size(data: bytes, maximum_bytes: int, maximum_edge: int, maximum_pixels: int) -> QSize:
    if not isinstance(data, bytes) or not data or len(data) > maximum_bytes:
        raise ImageError(tr('图片数据为空或超过允许的大小。'))
    if not data.startswith(_PNG_SIGNATURE):
        raise ImageError(tr('白板内嵌图片必须是 PNG 格式。'))
    position = 8
    size = None
    has_pixels = False
    view = memoryview(data)
    while position + 12 <= len(data):
        length = struct.unpack_from('>I', data, position)[0]
        kind = data[position + 4:position + 8]
        end = position + 12 + length
        if end > len(data):
            raise ImageError(tr('PNG 图片已损坏或不完整。'))
        checksum = struct.unpack_from('>I', data, end - 4)[0]
        if zlib.crc32(view[position + 4:end - 4]) & 0xffffffff != checksum:
            raise ImageError(tr('PNG 图片已损坏或不完整。'))
        if position == 8:
            if kind != b'IHDR' or length != 13:
                raise ImageError(tr('PNG 图片已损坏或不完整。'))
            width, height = struct.unpack_from('>II', data, position + 8)
            if not 0 < width <= maximum_edge or not 0 < height <= maximum_edge or width * height > maximum_pixels:
                raise ImageError(tr('图片像素尺寸超出允许的范围。'))
            size = QSize(width, height)
        elif kind == b'IHDR':
            raise ImageError(tr('PNG 图片已损坏或不完整。'))
        if kind == b'IDAT':
            has_pixels = True
        if kind == b'IEND':
            if length != 0 or not has_pixels or end != len(data):
                raise ImageError(tr('PNG 图片已损坏或不完整。'))
            return size
        position = end
    raise ImageError(tr('PNG 图片已损坏或不完整。'))


def _reader(data: bytes) -> tuple[QBuffer, QImageReader]:
    buffer = QBuffer()
    buffer.setData(QByteArray(data))
    buffer.open(QIODevice.OpenModeFlag.ReadOnly)
    reader = QImageReader(buffer)
    reader.setDecideFormatFromContent(True)
    return buffer, reader


def decode_image(data: bytes) -> QImage:
    expected_size = image_size(data)
    buffer, reader = _reader(data)
    reader.setAutoTransform(False)
    image = reader.read()
    if image.isNull() or image.size() != expected_size:
        raise ImageError(tr('无法读取图片，文件可能已损坏。'))
    _remember_validated(data)
    return image.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)


def _remember_validated(data: bytes) -> None:
    digest = hashlib.sha256(data).digest()
    with _validation_lock:
        _validated_pngs[digest] = None
        _validated_pngs.move_to_end(digest)
        while len(_validated_pngs) > 256:
            _validated_pngs.popitem(last=False)


def validate_images(images: list[BoardImage], used_ids: set[str] | None = None) -> None:
    """Enforce the same budgets for insertion, explicit save and recovery.

    Only validation fingerprints are cached, never image bytes or decoded pixel
    buffers. Repeated autosaves need not decode all unchanged images again.
    """
    if not isinstance(images, list) or len(images) > MAX_IMAGES:
        raise ImageError(tr('文档图片数量不能超过 100 张。'))
    ids = set(used_ids or ())
    total_bytes = total_pixels = 0
    for image in images:
        if not isinstance(image, BoardImage):
            raise ImageError(tr('图片数据格式不正确。'))
        if not isinstance(image.id, str) or not 1 <= len(image.id) <= 128 or image.id in ids:
            raise ImageError(tr('图片标识缺失、重复或格式不正确。'))
        ids.add(image.id)
        for coordinate in (image.x, image.y, image.width, image.height):
            if (isinstance(coordinate, bool) or not isinstance(coordinate, (int, float))
                    or abs(coordinate) > MAX_IMAGE_WORLD_SIZE or not math.isfinite(coordinate)):
                raise ImageError(tr('图片位置或尺寸超出有效范围。'))
        if image.width < MIN_IMAGE_WORLD_SIZE or image.height < MIN_IMAGE_WORLD_SIZE:
            raise ImageError(tr('图片位置或尺寸超出有效范围。'))
        size = image_size(image.png_data)
        total_bytes += len(image.png_data)
        total_pixels += size.width() * size.height()
        if total_bytes > MAX_TOTAL_IMAGE_BYTES or total_pixels > MAX_TOTAL_IMAGE_PIXELS:
            raise ImageError(tr('文档图片总量超出限制：64 MiB 或 3200 万像素。'))
    # Reject total-budget violations before allocating a decoded pixel buffer.
    for image in images:
        digest = hashlib.sha256(image.png_data).digest()
        with _validation_lock:
            known = digest in _validated_pngs
            if known:
                _validated_pngs.move_to_end(digest)
        if not known:
            decode_image(image.png_data)


def _normalized_size(size: QSize) -> QSize:
    ratio = min(1.0, MAX_IMAGE_EDGE / size.width(), MAX_IMAGE_EDGE / size.height(),
                math.sqrt(MAX_IMAGE_PIXELS / (size.width() * size.height())))
    return QSize(max(1, int(size.width() * ratio)), max(1, int(size.height() * ratio)))


def _check_source_structure(data: bytes, image_format: bytes) -> None:
    """Reject incomplete files that Qt's deliberately tolerant decoders accept."""
    if image_format == b'png':
        _png_size(data, MAX_IMPORT_BYTES, MAX_IMPORT_EDGE, MAX_IMPORT_PIXELS)
        return
    if image_format in (b'jpeg', b'jpg'):
        position = 2
        saw_scan = False
        while position < len(data) and data[position] == 0xff:
            while position < len(data) and data[position] == 0xff:
                position += 1
            if position >= len(data):
                break
            marker = data[position]
            position += 1
            if marker == 0xd9:
                if saw_scan:
                    return
                break
            if marker in (0, 0xd8) or 0xd0 <= marker <= 0xd7 or position + 2 > len(data):
                break
            length = struct.unpack_from('>H', data, position)[0]
            if length < 2 or position + length > len(data):
                break
            position += length
            if marker == 0xda:
                saw_scan = True
                # Entropy data escapes FF with 00; restart markers have no
                # length. Other markers return to normal segment parsing.
                while position < len(data):
                    position = data.find(b'\xff', position)
                    if position < 0 or position + 1 >= len(data):
                        break
                    following = data[position + 1]
                    if following == 0 or 0xd0 <= following <= 0xd7:
                        position += 2
                    elif following == 0xff:
                        position += 1
                    else:
                        break
                if position < 0:
                    break
    elif image_format == b'bmp' and len(data) >= 26:
        file_size = struct.unpack_from('<I', data, 2)[0]
        pixel_offset = struct.unpack_from('<I', data, 10)[0]
        header_size = struct.unpack_from('<I', data, 14)[0]
        if 26 <= file_size <= len(data) and 12 <= header_size <= pixel_offset - 14 and pixel_offset < file_size:
            if header_size >= 40 and len(data) >= 54:
                width, height = struct.unpack_from('<ii', data, 18)
                bits = struct.unpack_from('<H', data, 28)[0]
                compression = struct.unpack_from('<I', data, 30)[0]
                if compression in (0, 3, 6):
                    stride = ((abs(width) * bits + 31) // 32) * 4
                    if pixel_offset + stride * abs(height) > file_size:
                        raise ImageError(tr('无法读取图片，文件可能已损坏。'))
            return
    raise ImageError(tr('无法读取图片，文件可能已损坏。'))


def load_image(path: str | Path) -> bytes:
    """Import PNG/JPEG/BMP, orient and shrink when needed, then return PNG bytes."""
    path = Path(path)
    try:
        if path.stat().st_size > MAX_IMPORT_BYTES:
            raise ImageError(tr('原始图片超过 64 MiB，无法插入。'))
        with path.open('rb') as stream:
            data = stream.read(MAX_IMPORT_BYTES + 1)
        if not data or len(data) > MAX_IMPORT_BYTES:
            raise ImageError(tr('原始图片为空或超过 64 MiB，无法插入。'))
    except OSError as exc:
        raise ImageError(tr('无法打开图片：{p0}', p0=exc)) from exc
    buffer, reader = _reader(data)
    image_format = bytes(reader.format()).lower()
    if image_format not in (b'png', b'jpeg', b'jpg', b'bmp'):
        raise ImageError(tr('仅支持 PNG、JPEG 和 BMP 图片。'))
    _check_source_structure(data, image_format)
    size = reader.size()
    if not size.isValid() or size.isEmpty():
        raise ImageError(tr('无法读取图片，文件可能已损坏。'))
    if max(size.width(), size.height()) > MAX_IMPORT_EDGE or size.width() * size.height() > MAX_IMPORT_PIXELS:
        raise ImageError(tr('原始图片过大：最长边不能超过 16384 像素，总计不能超过 3200 万像素。'))
    target = _normalized_size(size)
    if target != size:
        reader.setScaledSize(target)
    reader.setAutoTransform(True)
    image = reader.read()
    if image.isNull():
        raise ImageError(tr('无法读取图片，文件可能已损坏。'))
    target = _normalized_size(image.size())
    if image.size() != target:
        image = image.scaled(target, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
    image = image.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
    output = QBuffer()
    output.open(QIODevice.OpenModeFlag.WriteOnly)
    if not image.save(output, 'PNG'):
        raise ImageError(tr('无法将图片转换为 PNG。'))
    normalized = bytes(output.data())
    image_size(normalized)
    return normalized

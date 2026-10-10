"""Atomic current-view PNG/PDF export using Qt's built-in paint engines only."""
from __future__ import annotations

from .i18n import tr

import math
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QIODevice, QMarginsF, QPointF, QRectF, QSaveFile, QSizeF
from PySide6.QtGui import QColor, QImage, QPageLayout, QPageSize, QPainter, QPdfWriter

from . import APP_DISPLAY_NAME
from .geometry import visible_path
from .renderer import ImageRenderer, paint_stroke
from .storage import DocumentError


class ExportError(DocumentError):
    """A user-readable rendering or atomic replacement failure."""


def _dimensions(size, scale: float, offset: QPointF) -> QSizeF:
    size = QSizeF(size)
    values = (size.width(), size.height(), scale, offset.x(), offset.y())
    if not all(math.isfinite(value) for value in values) or size.isEmpty() or scale <= 0:
        raise ExportError(tr('画布尺寸或视图比例无效，无法导出。'))
    return size


def render_document(painter: QPainter, scene, size, scale: float, offset: QPointF) -> None:
    """Paint only document ink/background; no canvas grid or UI overlays."""
    size = _dimensions(size, scale, offset)
    viewport = QRectF(QPointF(), size)
    world = QRectF(-offset.x() / scale, -offset.y() / scale,
                   size.width() / scale, size.height() / scale)
    painter.save()
    try:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setClipRect(viewport)
        painter.fillRect(viewport, QColor(scene.document.background))
        painter.translate(offset)
        painter.scale(scale, scale)
        image_renderer = ImageRenderer()
        for image in scene.query_images(world):
            image_renderer.paint(painter, image)
        for stroke in scene.query(world):
            paint_stroke(painter, stroke, visible_path(stroke))
    finally:
        painter.restore()


def _atomic_export(path: Path | str, writer: Callable[[QSaveFile], None]) -> None:
    destination = QSaveFile(str(path))
    # A failed export must never fall back to truncating the destination.
    destination.setDirectWriteFallback(False)
    if not destination.open(QIODevice.OpenModeFlag.WriteOnly):
        raise ExportError(tr('无法创建导出文件：{p0}', p0=destination.errorString()))
    try:
        writer(destination)
        if not destination.commit():
            raise ExportError(tr('无法保存导出文件：{p0}', p0=destination.errorString()))
    except Exception:
        destination.cancelWriting()
        raise


def export_png(path: Path | str, scene, size, scale: float, offset: QPointF, dpr: float = 1.0) -> None:
    size = _dimensions(size, scale, offset)
    if not math.isfinite(dpr) or dpr <= 0:
        raise ExportError(tr('屏幕像素比例无效，无法导出。'))
    image = QImage(math.ceil(size.width() * dpr), math.ceil(size.height() * dpr),
                   QImage.Format.Format_ARGB32_Premultiplied)
    if image.isNull():
        raise ExportError(tr('无法创建导出图片，请缩小窗口后重试。'))
    image.setDevicePixelRatio(dpr)
    painter = QPainter(image)
    if not painter.isActive():
        raise ExportError(tr('无法开始绘制导出图片。'))
    try:
        render_document(painter, scene, size, scale, offset)
    finally:
        painter.end()

    def write(device):
        if not image.save(device, "PNG"):
            raise ExportError(tr('无法编码 PNG 图片，请检查保存位置。'))

    _atomic_export(path, write)


def export_pdf(path: Path | str, scene, size, scale: float, offset: QPointF,
               title: str | None = None) -> None:
    size = _dimensions(size, scale, offset)

    def write(device):
        writer = QPdfWriter(device)
        writer.setTitle(title if title is not None else tr('白板当前视图'))
        writer.setCreator(APP_DISPLAY_NAME)
        writer.setResolution(96)
        # Logical screen pixels map to 96 dpi; custom zero-margin page geometry
        # keeps the viewport's landscape/portrait aspect ratio exactly.
        page = QPageSize(QSizeF(size.width() * 72 / 96, size.height() * 72 / 96),
                         QPageSize.Unit.Point, tr('当前视图'), QPageSize.SizeMatchPolicy.ExactMatch)
        if not writer.setPageSize(page) or not writer.setPageMargins(QMarginsF(0, 0, 0, 0), QPageLayout.Unit.Point):
            raise ExportError(tr('无法设置 PDF 页面尺寸。'))
        painter = QPainter()
        if not painter.begin(writer):
            raise ExportError(tr('无法开始绘制 PDF。'))
        try:
            painter.scale(writer.width() / size.width(), writer.height() / size.height())
            render_document(painter, scene, size, scale, offset)
        finally:
            if not painter.end():
                raise ExportError(tr('无法完成 PDF 页面绘制。'))
        # Writer destruction closes its PDF trailer before QSaveFile commits.
        del painter
        del writer

    _atomic_export(path, write)

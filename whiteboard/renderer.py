"""Ink painting and a byte-bounded, zoom-aware world tile LRU."""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath

from .geometry import visible_path
from .models import Stroke
from .scene import Scene


def paint_stroke(painter: QPainter, stroke: Stroke, path: QPainterPath | None = None) -> None:
    """One fill and one opacity operation per stroke, including intersections."""
    painter.save()
    painter.setPen(Qt.PenStyle.NoPen)
    color = QColor(stroke.brush.color)
    opacity = max(0.0, min(1.0, stroke.brush.opacity))
    color.setAlphaF(color.alphaF() * opacity)
    painter.setBrush(color)
    painter.drawPath(visible_path(stroke) if path is None else path)
    painter.restore()


@dataclass(slots=True)
class _Tile:
    image: QImage
    rect: QRectF
    core_pixels: int
    cost: int


class TileRenderer:
    def __init__(self, scene: Scene, budget_bytes: int = 128 * 1024 * 1024):
        self.scene = scene
        self.budget_bytes = max(0, budget_bytes)
        self._tiles: OrderedDict[tuple, _Tile] = OrderedDict()
        self.cache_bytes = 0
        self._known_strokes = list(scene.document.strokes)
        scene.changed.connect(self.invalidate)

    def invalidate(self, rect: QRectF | None = None) -> None:
        current = self.scene.document.strokes
        appended = (rect is not None and len(current) == len(self._known_strokes) + 1
                    and all(a is b for a, b in zip(current, self._known_strokes)))
        self._known_strokes = list(current)
        if rect is None:
            self._tiles.clear()
            self.cache_bytes = 0
            return
        for key, tile in list(self._tiles.items()):
            # Account for ink just outside a tile contributing antialias pixels.
            margin = tile.rect.width() * 2 / tile.core_pixels
            if tile.rect.adjusted(-margin, -margin, margin, margin).intersects(rect):
                if appended:
                    # A newly committed final stroke can be composed onto the
                    # cached history directly, without replaying older vectors.
                    self._paint_into_tile(tile, [current[-1]])
                else:
                    self.cache_bytes -= tile.cost
                    del self._tiles[key]

    @staticmethod
    def paint_strokes(painter: QPainter, strokes: list[Stroke]) -> None:
        for stroke in strokes:
            paint_stroke(painter, stroke)

    def _make_tile(self, rect: QRectF, core_pixels: int, strokes: list[Stroke]) -> _Tile:
        image = QImage(core_pixels + 4, core_pixels + 4, QImage.Format.Format_ARGB32_Premultiplied)
        # Transparent history allows viewport aids (e.g. the world grid) to be
        # painted below ink without invalidating every tile as the aid fades.
        image.fill(Qt.GlobalColor.transparent)
        tile = _Tile(image, rect, core_pixels, image.sizeInBytes())
        self._paint_into_tile(tile, strokes)
        return tile

    def _paint_into_tile(self, tile: _Tile, strokes: list[Stroke]) -> None:
        painter = QPainter(tile.image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pixel_scale = tile.core_pixels / tile.rect.width()
        painter.translate(2, 2)
        painter.scale(pixel_scale, pixel_scale)
        painter.translate(-tile.rect.left(), -tile.rect.top())
        self.paint_strokes(painter, strokes)
        painter.end()

    def paint(self, painter: QPainter, viewport: QRectF, scale: float, offset: QPointF,
              dpr: float = 1.0, exclude_ids: set[str] | None = None, *, background: bool = True) -> None:
        if scale <= 0 or viewport.isEmpty():
            return
        if background:
            painter.fillRect(viewport, QColor(self.scene.document.background))
        dpr = max(1.0, dpr)
        bucket = 2 ** (math.ceil(math.log2(scale) * 4) / 4)
        size = 256.0 / bucket
        core = math.ceil(256 * dpr)
        world = QRectF((viewport.left() - offset.x()) / scale, (viewport.top() - offset.y()) / scale,
                       viewport.width() / scale, viewport.height() / scale)
        x0, y0 = math.floor(world.left() / size), math.floor(world.top() / size)
        x1, y1 = math.floor(world.right() / size), math.floor(world.bottom() / size)
        excluded = exclude_ids or set()
        painter.save()
        painter.setClipRect(viewport, Qt.ClipOperation.IntersectClip)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        painter.translate(offset)
        painter.scale(scale, scale)
        for ty in range(y0, y1 + 1):
            for tx in range(x0, x1 + 1):
                key = (bucket, core, tx, ty, self.scene.document.background)
                tile = self._tiles.get(key)
                rect = QRectF(tx * size, ty * size, size, size)
                candidates = None
                temporary = False
                if excluded:
                    margin = size * 2 / core
                    candidates = self.scene.query(rect.adjusted(-margin, -margin, margin, margin))
                    temporary = any(s.id in excluded for s in candidates)
                if tile is None or temporary:
                    if candidates is None:
                        margin = size * 2 / core
                        candidates = self.scene.query(rect.adjusted(-margin, -margin, margin, margin))
                    tile = self._make_tile(rect, core, [s for s in candidates if s.id not in excluded])
                    if not temporary and tile.cost <= self.budget_bytes:
                        while self._tiles and self.cache_bytes + tile.cost > self.budget_bytes:
                            _, old = self._tiles.popitem(last=False)
                            self.cache_bytes -= old.cost
                        self._tiles[key] = tile
                        self.cache_bytes += tile.cost
                elif key in self._tiles:
                    self._tiles.move_to_end(key)
                painter.drawImage(rect, tile.image, QRectF(2, 2, core, core))
        painter.restore()

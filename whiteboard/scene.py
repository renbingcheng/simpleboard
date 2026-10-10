"""Document transactions and bounded spatial indexing, independent of widgets."""
from __future__ import annotations

from .i18n import tr

from collections import defaultdict
from copy import deepcopy
from dataclasses import replace
import math

from PySide6.QtCore import QObject, QRectF, Signal
from PySide6.QtGui import QPainterPath, QUndoCommand, QUndoStack

from .geometry import (clear_geometry_cache, conservative_stroke_bounds, erase_stroke, eraser_chunks,
                       seed_visible_path, stroke_bounds, visible_path)
from .images import ImageError, MAX_IMAGE_WORLD_SIZE, MIN_IMAGE_WORLD_SIZE, image_rect
from .models import BoardDocument, BoardImage, EraseMask, Stroke


def _validate_image_geometry(item: BoardImage) -> None:
    """Keep committed edits serializable without decoding pixels during edits."""
    for coordinate in (item.x, item.y, item.width, item.height):
        if (isinstance(coordinate, bool) or not isinstance(coordinate, (int, float))
                or not math.isfinite(coordinate) or abs(coordinate) > MAX_IMAGE_WORLD_SIZE):
            raise ImageError(tr('图片位置或尺寸超出有效范围。'))
    if item.width < MIN_IMAGE_WORLD_SIZE or item.height < MIN_IMAGE_WORLD_SIZE:
        raise ImageError(tr('图片位置或尺寸超出有效范围。'))


class SpatialIndex:
    """Uniform grid; exceptionally large strokes live in one overflow bucket."""
    def __init__(self, cell_size: float = 256.0, max_cells: int = 4096):
        self.cell_size = cell_size
        self.max_cells = max_cells
        self.cells: dict[tuple[int, int], set[str]] = defaultdict(set)
        self.memberships: dict[str, list[tuple[int, int]]] = {}
        self.bounds: dict[str, QRectF] = {}
        self.overflow: set[str] = set()

    def _range(self, rect: QRectF) -> tuple[int, int, int, int]:
        return (math.floor(rect.left() / self.cell_size), math.floor(rect.top() / self.cell_size),
                math.floor(rect.right() / self.cell_size), math.floor(rect.bottom() / self.cell_size))

    def remove(self, stroke_id: str) -> None:
        for cell in self.memberships.pop(stroke_id, []):
            bucket = self.cells[cell]
            bucket.discard(stroke_id)
            if not bucket:
                del self.cells[cell]
        self.bounds.pop(stroke_id, None)
        self.overflow.discard(stroke_id)

    def insert(self, stroke_id: str, rect: QRectF) -> None:
        self.remove(stroke_id)
        if rect.isEmpty():
            return
        self.bounds[stroke_id] = QRectF(rect)
        x0, y0, x1, y1 = self._range(rect)
        if (x1 - x0 + 1) * (y1 - y0 + 1) > self.max_cells:
            self.overflow.add(stroke_id)
            return
        cells = [(x, y) for x in range(x0, x1 + 1) for y in range(y0, y1 + 1)]
        self.memberships[stroke_id] = cells
        for cell in cells:
            self.cells[cell].add(stroke_id)

    def query(self, rect: QRectF) -> set[str]:
        if rect.isEmpty():
            return set()
        x0, y0, x1, y1 = self._range(rect)
        if (x1 - x0 + 1) * (y1 - y0 + 1) > self.max_cells:
            candidates = self.bounds.keys()
        else:
            candidates = set(self.overflow)
            for x in range(x0, x1 + 1):
                for y in range(y0, y1 + 1):
                    candidates.update(self.cells.get((x, y), ()))
        return {key for key in candidates if self.bounds[key].intersects(rect)}


class _SceneCommand(QUndoCommand):
    def __init__(self, scene: Scene, text: str, before: list[Stroke], after: list[Stroke], dirty: QRectF | None,
                 before_images: list[BoardImage] | None = None, after_images: list[BoardImage] | None = None):
        super().__init__(text)
        self.scene, self.before, self.after, self.dirty = scene, before, after, dirty
        self.before_images, self.after_images = before_images, after_images

    def redo(self) -> None:
        self.scene._apply(self.after, self.dirty, self.after_images)

    def undo(self) -> None:
        self.scene._apply(self.before, self.dirty, self.before_images)


class Scene(QObject):
    changed = Signal(object)

    def __init__(self, document: BoardDocument | None = None, parent: QObject | None = None):
        super().__init__(parent)
        self.document = document if document is not None else BoardDocument()
        self.undo_stack = QUndoStack(self)
        self.undo_stack.setUndoLimit(200)
        self._index = SpatialIndex()
        self._strokes: dict[str, Stroke] = {}
        self._order: dict[str, int] = {}
        self._image_index = SpatialIndex()
        self._images: dict[str, BoardImage] = {}
        self._image_order: dict[str, int] = {}
        self._apply(self.document.strokes, None, self.document.images)

    def _apply(self, strokes: list[Stroke], dirty: QRectF | None,
               images: list[BoardImage] | None = None) -> None:
        # A stroke-only undo must preserve image state. Mixed transactions pass
        # both collections and replace them atomically before notifying views.
        effective_images = self.document.images if images is None else images
        ids = [stroke.id for stroke in strokes] + [item.id for item in effective_images]
        if len(set(ids)) != len(ids):
            raise ValueError("Board item IDs must be unique")
        replacements = {stroke.id: stroke for stroke in strokes}
        for key, old in self._strokes.items():
            if key not in replacements or replacements[key] is not old:
                self._index.remove(key)
        for key, stroke in replacements.items():
            if self._strokes.get(key) is not stroke:
                self._index.insert(key, conservative_stroke_bounds(stroke))
        self.document.strokes = list(strokes)
        self._strokes = replacements
        self._order = {stroke.id: i for i, stroke in enumerate(strokes)}
        if images is not None:
            replacements_images = {item.id: item for item in images}
            for key, old in self._images.items():
                if key not in replacements_images or replacements_images[key] is not old:
                    self._image_index.remove(key)
            for key, item in replacements_images.items():
                if self._images.get(key) is not item:
                    self._image_index.insert(key, image_rect(item))
            self.document.images = list(images)
            self._images = replacements_images
            self._image_order = {item.id: i for i, item in enumerate(images)}
        self.changed.emit(dirty)

    def reset(self, document: BoardDocument) -> None:
        self.undo_stack.clear()
        clear_geometry_cache()
        self.document = document
        self._index = SpatialIndex()
        self._strokes = {}
        self._image_index = SpatialIndex()
        self._images = {}
        self._apply(document.strokes, None, document.images)
        self.undo_stack.setClean()

    def get(self, stroke_id: str) -> Stroke | None:
        return self._strokes.get(stroke_id)

    def query(self, rect: QRectF) -> list[Stroke]:
        keys = self._index.query(rect)
        return [self._strokes[key] for key in sorted(keys, key=self._order.__getitem__)]

    def get_image(self, image_id: str) -> BoardImage | None:
        return self._images.get(image_id)

    def query_images(self, rect: QRectF) -> list[BoardImage]:
        keys = self._image_index.query(rect)
        return [self._images[key] for key in sorted(keys, key=self._image_order.__getitem__)]

    def _commit(self, label: str, after: list[Stroke], dirty: QRectF | None,
                after_images: list[BoardImage] | None = None) -> None:
        before_images = list(self.document.images) if after_images is not None else None
        self.undo_stack.push(_SceneCommand(self, label, list(self.document.strokes), after, dirty,
                                          before_images, after_images))

    def add_stroke(self, stroke: Stroke, cached_path: QPainterPath | None = None) -> None:
        if not stroke.samples:
            return
        if stroke.id in self._strokes or stroke.id in self._images:
            raise ValueError("Board item IDs must be unique")
        # A live input builder may still hold the passed sample list or brush.
        stroke = deepcopy(stroke)
        if cached_path is not None:
            seed_visible_path(stroke, cached_path)
        self._commit(tr('书写'), self.document.strokes + [stroke], stroke_bounds(stroke))

    def add_image(self, item: BoardImage) -> None:
        if item.id in self._strokes or item.id in self._images:
            raise ValueError("Board item IDs must be unique")
        item = deepcopy(item)
        _validate_image_geometry(item)
        rect = image_rect(item)
        self._commit(tr('插入图片'), list(self.document.strokes), rect, self.document.images + [item])

    def erase(self, points: list[tuple[float, float]], radius: float, whole: bool = False) -> None:
        sweeps = list(eraser_chunks(points, radius))
        if not sweeps:
            return
        region = QRectF()
        sweep_bounds = []
        for sweep in sweeps:
            bounds = sweep.boundingRect()
            sweep_bounds.append(bounds)
            region = region.united(bounds)
        changes: dict[str, Stroke | None] = {}
        dirty = QRectF()
        translated_sweeps = {(0.0, 0.0): sweeps}
        for stroke in self.query(region):
            bounds = self._index.bounds[stroke.id]
            relevant = [index for index, rect in enumerate(sweep_bounds) if rect.intersects(bounds)]
            # A conservative ink box covered by a sweep guarantees all visible
            # ink is erased. Avoid rebuilding any raw geometry for this case.
            if any(sweeps[index].contains(bounds) for index in relevant):
                changes[stroke.id] = None
                dirty = dirty.united(bounds)
                continue
            if whole:
                path = visible_path(stroke)
                if any(path.intersects(sweeps[index].path) for index in relevant):
                    changes[stroke.id] = None
                    dirty = dirty.united(bounds)
                continue
            # Inspect and clip the same bounded groups once. Building a full
            # outline just to hit-test would replay all input a second time.
            offset = (stroke.offset_x, stroke.offset_y)
            if offset not in translated_sweeps:
                # Quantize in the same local coordinate frame as saved masks.
                # Translating already quantized world polygons would shift the
                # fine grid for arbitrary offsets and differ from file replay.
                local_points = [(x - offset[0], y - offset[1]) for x, y in points]
                translated_sweeps[offset] = list(eraser_chunks(local_points, radius))
            local_bounds = bounds.translated(-offset[0], -offset[1])
            local_sweeps = [sweep for sweep in translated_sweeps[offset]
                            if sweep.boundingRect().intersects(local_bounds)]
            result = erase_stroke(stroke, points, radius,
                                  sweeps=local_sweeps)
            if result is None:
                continue
            erased, parts = result
            dirty = dirty.united(bounds)
            if erased.isEmpty():
                changes[stroke.id] = None
            else:
                mask = EraseMask([(x - stroke.offset_x, y - stroke.offset_y) for x, y in points], radius)
                changed = replace(stroke, erase_masks=[*stroke.erase_masks, mask])
                seed_visible_path(changed, erased, parts)
                changes[stroke.id] = changed
        if not changes:
            return
        after = [changes.get(stroke.id, stroke) for stroke in self.document.strokes]
        self._commit(tr('整笔擦除') if whole else tr('局部擦除'), [s for s in after if s is not None], dirty)

    def move_strokes(self, ids, dx: float, dy: float) -> None:
        keys = set(ids)
        if not keys or (abs(dx) < 1e-12 and abs(dy) < 1e-12):
            return
        after = []
        dirty = QRectF()
        changed = False
        for stroke in self.document.strokes:
            if stroke.id in keys:
                dirty = dirty.united(stroke_bounds(stroke))
                stroke = replace(stroke, offset_x=stroke.offset_x + dx, offset_y=stroke.offset_y + dy)
                dirty = dirty.united(stroke_bounds(stroke))
                changed = True
            after.append(stroke)
        if changed:
            self._commit(tr('移动笔迹'), after, dirty)

    def delete_strokes(self, ids) -> None:
        keys = set(ids)
        removed = [s for s in self.document.strokes if s.id in keys]
        if not removed:
            return
        dirty = QRectF()
        for stroke in removed:
            dirty = dirty.united(stroke_bounds(stroke))
        self._commit(tr('删除笔迹'), [s for s in self.document.strokes if s.id not in keys], dirty)

    def move_items(self, ids, dx: float, dy: float) -> None:
        keys = set(ids)
        if not all(math.isfinite(value) for value in (dx, dy)):
            raise ValueError("Movement must be finite")
        if not keys or (abs(dx) < 1e-12 and abs(dy) < 1e-12):
            return
        strokes = []
        images = []
        dirty = QRectF()
        changed = False
        for stroke in self.document.strokes:
            if stroke.id in keys:
                dirty = dirty.united(stroke_bounds(stroke))
                stroke = replace(stroke, offset_x=stroke.offset_x + dx, offset_y=stroke.offset_y + dy)
                dirty = dirty.united(stroke_bounds(stroke))
                changed = True
            strokes.append(stroke)
        for item in self.document.images:
            if item.id in keys:
                dirty = dirty.united(image_rect(item))
                item = replace(item, x=item.x + dx, y=item.y + dy)
                _validate_image_geometry(item)
                dirty = dirty.united(image_rect(item))
                changed = True
            images.append(item)
        if changed:
            self._commit(tr('移动对象'), strokes, dirty, images)

    def delete_items(self, ids) -> None:
        keys = set(ids)
        removed_strokes = [stroke for stroke in self.document.strokes if stroke.id in keys]
        removed_images = [item for item in self.document.images if item.id in keys]
        if not removed_strokes and not removed_images:
            return
        dirty = QRectF()
        for stroke in removed_strokes:
            dirty = dirty.united(stroke_bounds(stroke))
        for item in removed_images:
            dirty = dirty.united(image_rect(item))
        self._commit(tr('删除对象'), [stroke for stroke in self.document.strokes if stroke.id not in keys],
                     dirty, [item for item in self.document.images if item.id not in keys])

    def resize_image(self, image_id: str, rect: QRectF) -> None:
        item = self.get_image(image_id)
        if item is None:
            return
        previous_rect = image_rect(item)
        if previous_rect == rect:
            return
        resized = replace(item, x=rect.x(), y=rect.y(), width=rect.width(), height=rect.height())
        _validate_image_geometry(resized)
        after = [resized if candidate.id == image_id else candidate for candidate in self.document.images]
        self._commit(tr('缩放图片'), list(self.document.strokes), previous_rect.united(rect), after)

    def clear(self) -> None:
        if self.document.strokes or self.document.images:
            self._commit(tr('清空画布'), [], None, [])

    def bounds(self) -> QRectF:
        result = QRectF()
        for stroke in self.document.strokes:
            result = result.united(stroke_bounds(stroke))
        for item in self.document.images:
            result = result.united(image_rect(item))
        return result

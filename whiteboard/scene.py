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
from .models import BoardDocument, EraseMask, Stroke


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
    def __init__(self, scene: Scene, text: str, before: list[Stroke], after: list[Stroke], dirty: QRectF | None):
        super().__init__(text)
        self.scene, self.before, self.after, self.dirty = scene, before, after, dirty

    def redo(self) -> None:
        self.scene._apply(self.after, self.dirty)

    def undo(self) -> None:
        self.scene._apply(self.before, self.dirty)


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
        self._apply(self.document.strokes, None)

    def _apply(self, strokes: list[Stroke], dirty: QRectF | None) -> None:
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
        self.changed.emit(dirty)

    def reset(self, document: BoardDocument) -> None:
        self.undo_stack.clear()
        clear_geometry_cache()
        self.document = document
        self._index = SpatialIndex()
        self._strokes = {}
        self._apply(document.strokes, None)
        self.undo_stack.setClean()

    def get(self, stroke_id: str) -> Stroke | None:
        return self._strokes.get(stroke_id)

    def query(self, rect: QRectF) -> list[Stroke]:
        keys = self._index.query(rect)
        return [self._strokes[key] for key in sorted(keys, key=self._order.__getitem__)]

    def _commit(self, label: str, after: list[Stroke], dirty: QRectF | None) -> None:
        self.undo_stack.push(_SceneCommand(self, label, list(self.document.strokes), after, dirty))

    def add_stroke(self, stroke: Stroke, cached_path: QPainterPath | None = None) -> None:
        if not stroke.samples:
            return
        if stroke.id in self._strokes:
            raise ValueError("Stroke IDs must be unique")
        # A live input builder may still hold the passed sample list or brush.
        stroke = deepcopy(stroke)
        if cached_path is not None:
            seed_visible_path(stroke, cached_path)
        self._commit(tr('书写'), self.document.strokes + [stroke], stroke_bounds(stroke))

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

    def clear(self) -> None:
        if self.document.strokes:
            self._commit(tr('清空画布'), [], None)

    def bounds(self) -> QRectF:
        result = QRectF()
        for stroke in self.document.strokes:
            result = result.united(stroke_bounds(stroke))
        return result

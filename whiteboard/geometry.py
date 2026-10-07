"""Deterministic variable-width ink and vector erasing in document coordinates."""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
import math

import pyclipper

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QPainterPath, QPainterPathStroker, QPolygonF, QTransform

from .models import Brush, InkSample, Stroke


def pressure_width(brush: Brush, pressure: float) -> float:
    """Width in world units; sensitivity > 1 makes a light touch wider."""
    width = max(0.05, brush.width)
    if brush.kind == "highlighter" or not brush.pressure_enabled:
        return width
    pressure = max(0.0, min(1.0, pressure))
    sensitivity = max(0.25, min(4.0, brush.sensitivity))
    return width * (0.12 + 0.88 * pressure ** (1.0 / sensitivity))


class IncrementalStrokeBuilder:
    """Append-only outline for the live stroke, also used when replaying a file.

    Each raw sample is kept intact. A short exponential filter removes jitter
    without waiting for a future sample. Winding fill means an entire logical
    stroke is painted once, including its self intersections.
    """

    def __init__(self, brush: Brush):
        self.brush = brush
        self.path = QPainterPath()
        self.path.setFillRule(Qt.FillRule.WindingFill)
        self.last_segment = QPainterPath()
        self.last_segment.setFillRule(Qt.FillRule.WindingFill)
        self.samples: list[InkSample] = []
        self._last: tuple[float, float] | None = None
        self._radius = 0.0
        self._base_radius = max(0.05, brush.width) / 2.0
        self._pressure_enabled = brush.kind != "highlighter" and brush.pressure_enabled
        self._gamma = 1.0 / max(0.25, min(4.0, brush.sensitivity))
        self._paired_filter = brush.render_profile == "pressure-v2"
        self._filtered_pressure = 0.0
        self._trusted_sample: InkSample | None = None
        self._finished = False

    def add(self, sample: InkSample) -> QRectF:
        segment = QPainterPath()
        segment.setFillRule(Qt.FillRule.WindingFill)
        self.samples.append(sample)
        affected = self._append_outline(sample, segment, True)
        self.last_segment = segment
        self.path.addPath(segment)
        return affected

    def needs_filter_update(self, x: float, y: float, pressure: float) -> bool:
        """Repeated raw samples still matter while the time filter settles."""
        if not self._paired_filter or self._last is None:
            return False
        if self._pressure_enabled and pressure <= 0:
            return False  # Zero-pressure moves deliberately do not advance ink.
        return (math.hypot(x - self._last[0], y - self._last[1]) * self.brush.input_scale >= 0.025
                or (self._pressure_enabled and abs(pressure - self._filtered_pressure) >= 0.0001))

    def finish(self) -> QRectF:
        """Resolve the short filter to the last drawing sample, never to hover."""
        segment = QPainterPath()
        segment.setFillRule(Qt.FillRule.WindingFill)
        affected = self._finish_outline(segment, True)
        self.last_segment = segment
        self.path.addPath(segment)
        return affected

    def _finish_outline(self, path: QPainterPath, bounds: bool = False) -> QRectF | None:
        if not self._paired_filter or self._finished or self._trusted_sample is None:
            return QRectF() if bounds else None
        self._finished = True
        sample = self._trusted_sample
        if self._last == (sample.x, sample.y) and self._filtered_pressure == sample.pressure:
            return QRectF() if bounds else None
        return self._append_outline(sample, path, bounds, finishing=True)

    def _append_outline(self, sample: InkSample, path: QPainterPath, bounds: bool = False,
                        *, finishing: bool = False) -> QRectF | None:
        # Scalar math avoids allocating many QPointF wrappers per raw sample.
        # Replay appends straight into the final path, while live input uses the
        # same function to produce an individually paintable last_segment.
        pressure = max(0.0, min(1.0, sample.pressure))
        if self._paired_filter:
            # A zero-pressure move can be a lift transition. Retain it in raw
            # input, but do not manufacture ink or terminate the contact.
            if self._last is not None and self._pressure_enabled and pressure == 0:
                return QRectF() if bounds else None
            x, y = sample.x, sample.y
            if self._last is not None and not finishing:
                old_x, old_y = self._last
                distance = math.hypot(x - old_x, y - old_y) * self.brush.input_scale
                elapsed = max(0.0, sample.t - self._trusted_sample.t)
                # Shared weights keep pressure at the same point as position.
                # The distance term bounds live lag below 0.65 logical pixels;
                # stationary pressure uses an 8 ms time constant.
                alpha = max(-math.expm1(-elapsed / 8.0), distance / (distance + 0.65))
                if distance == 0 and elapsed == 0:
                    alpha = 1.0
                x = old_x + (x - old_x) * alpha
                y = old_y + (y - old_y) * alpha
                pressure = self._filtered_pressure + (pressure - self._filtered_pressure) * alpha
            self._filtered_pressure = pressure
            self._trusted_sample = sample
        radius = self._base_radius
        if self._pressure_enabled:
            radius *= 0.12 + 0.88 * pressure ** self._gamma
        if self._last is None:
            x, y = sample.x, sample.y
            affected = QRectF(x - radius, y - radius, radius * 2, radius * 2) if bounds else None
        else:
            # Greater movement gets less filtering; a stationary pen converges.
            old_x, old_y = self._last
            if not self._paired_filter:
                # Frozen v1 replay: changing this changes already saved ink.
                distance = math.hypot(sample.x - old_x, sample.y - old_y)
                alpha = min(0.9, 0.62 + distance * 0.015)
                x = old_x + (sample.x - old_x) * alpha
                y = old_y + (sample.y - old_y) * alpha
            dx, dy = x - old_x, y - old_y
            length = math.hypot(dx, dy)
            if length > 1e-9:
                nx, ny = -dy / length, dx / length
                path.moveTo(old_x - nx * self._radius, old_y - ny * self._radius)
                path.lineTo(x - nx * radius, y - ny * radius)
                path.lineTo(x + nx * radius, y + ny * radius)
                path.lineTo(old_x + nx * self._radius, old_y + ny * self._radius)
                path.closeSubpath()
            if bounds:
                left, top = min(old_x - self._radius, x - radius), min(old_y - self._radius, y - radius)
                right, bottom = max(old_x + self._radius, x + radius), max(old_y + self._radius, y + radius)
                affected = QRectF(left, top, right - left, bottom - top)
            else:
                affected = None
        path.addEllipse(x - radius, y - radius, radius * 2, radius * 2)
        self._last, self._radius = (x, y), radius
        return affected


def eraser_path(points: list[tuple[float, float]], radius: float) -> QPainterPath:
    """A swept circle, including the area between sparse eraser samples."""
    result = QPainterPath()
    result.setFillRule(Qt.FillRule.WindingFill)
    if not points or radius <= 0:
        return result
    if len(points) == 1:
        result.addEllipse(QPointF(*points[0]), radius, radius)
        return result
    center = QPainterPath(QPointF(*points[0]))
    for point in points[1:]:
        center.lineTo(QPointF(*point))
    stroker = QPainterPathStroker()
    stroker.setWidth(radius * 2)
    stroker.setCapStyle(Qt.PenCapStyle.RoundCap)
    stroker.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    result = stroker.createStroke(center)
    # A path consisting only of repeated coordinates has no stroked segments.
    result.addEllipse(QPointF(*points[0]), radius, radius)
    result.addEllipse(QPointF(*points[-1]), radius, radius)
    result.setFillRule(Qt.FillRule.WindingFill)
    return result


# Public spelling shared with live eraser preview.
erase_path = eraser_path


_CURVE_SCALE = 32.0
_INTEGER_SCALE = 4096


def _integer_paths(path: QPainterPath) -> list[list[tuple[int, int]]]:
    """Flatten curves deterministically, then use a fine view-independent grid."""
    result = []
    factor = _INTEGER_SCALE / _CURVE_SCALE
    for polygon in path.toSubpathPolygons(QTransform.fromScale(_CURVE_SCALE, _CURVE_SCALE)):
        points = [(round(p.x() * factor), round(p.y() * factor)) for p in polygon]
        if len(set(points)) >= 3:
            result.append(points)
    return result


def _polygon_path(polygons) -> QPainterPath:
    # Clipper orients outer contours and holes oppositely. This preserves holes
    # when independently clipped ink groups overlap under the nonzero rule.
    path = QPainterPath()
    path.setFillRule(Qt.FillRule.WindingFill)
    for polygon in polygons:
        path.addPolygon(QPolygonF([QPointF(x / _INTEGER_SCALE, y / _INTEGER_SCALE) for x, y in polygon]))
        path.closeSubpath()
    return path


@dataclass(frozen=True, slots=True)
class _InkPart:
    path: QPainterPath
    polygons: object = None


@dataclass(frozen=True, slots=True)
class _EraserSweep:
    path: QPainterPath
    polygons: object

    def boundingRect(self) -> QRectF:
        return self.path.boundingRect()

    def contains(self, value) -> bool:
        return self.path.contains(value)


def _prepared_sweep(points: list[tuple[float, float]], radius: float) -> _EraserSweep:
    polygons = _integer_paths(eraser_path(points, radius))
    if polygons:
        clipper = pyclipper.Pyclipper()
        clipper.AddPaths(polygons, pyclipper.PT_SUBJECT, True)
        polygons = clipper.Execute(pyclipper.CT_UNION, pyclipper.PFT_NONZERO, pyclipper.PFT_NONZERO)
    return _EraserSweep(_polygon_path(polygons), polygons)


def _difference(subject, clips):
    if not subject or not clips:
        return subject
    clipper = pyclipper.Pyclipper()
    clipper.AddPaths(subject, pyclipper.PT_SUBJECT, True)
    clipper.AddPaths(clips, pyclipper.PT_CLIP, True)
    return clipper.Execute(pyclipper.CT_DIFFERENCE, pyclipper.PFT_NONZERO, pyclipper.PFT_NONZERO)


def eraser_chunks(points: list[tuple[float, float]], radius: float, segments: int = 24):
    """Yield bounded sweeps sharing endpoints; their union is the full gesture.

    Consecutive duplicate and exactly collinear forward points are redundant
    for a fixed-radius eraser. Remove only those, without distance tolerances.
    """
    compact: list[tuple[float, float]] = []
    for point in points:
        if compact and point == compact[-1]:
            continue
        while len(compact) >= 2:
            a, b = compact[-2], compact[-1]
            ax, ay = b[0] - a[0], b[1] - a[1]
            bx, by = point[0] - b[0], point[1] - b[1]
            if ax * by != ay * bx or ax * bx + ay * by < 0:
                break
            compact.pop()
        compact.append(point)
    if len(compact) == 1:
        yield _prepared_sweep(compact, radius)
    else:
        for start in range(0, len(compact) - 1, max(1, segments)):
            yield _prepared_sweep(compact[start:start + max(1, segments) + 1], radius)


def subtract_eraser(path: QPainterPath, points: list[tuple[float, float]], radius: float) -> QPainterPath:
    """Apply a gesture in bounded pieces, used identically on edit and replay."""
    polygons = _integer_paths(path)
    for sweep in eraser_chunks(points, radius):
        if not polygons:
            break
        polygons = _difference(polygons, sweep.polygons)
    return _polygon_path(polygons)


def _raw_ink_parts(stroke: Stroke) -> tuple[_InkPart, ...]:
    """Partition the exact original ellipse/quad union, preserving filter state."""
    builder = IncrementalStrokeBuilder(stroke.brush)
    parts = []
    part = QPainterPath()
    part.setFillRule(Qt.FillRule.WindingFill)
    for index, sample in enumerate(stroke.samples):
        builder._append_outline(sample, part)
        if (index + 1) % 24 == 0:
            parts.append(_InkPart(part))
            part = QPainterPath()
            part.setFillRule(Qt.FillRule.WindingFill)
    builder._finish_outline(part)
    if not part.isEmpty():
        parts.append(_InkPart(part))
    return tuple(parts)


def _subtract_parts(parts: tuple[_InkPart, ...], points: list[tuple[float, float]],
                    radius: float, sweeps=None) -> tuple[_InkPart, ...]:
    current = list(parts)
    changed = False
    for sweep in eraser_chunks(points, radius) if sweeps is None else sweeps:
        sweep_bounds = sweep.boundingRect()
        for index, part in enumerate(current):
            if part is None:
                continue
            bounds = part.path.boundingRect()
            if not bounds.intersects(sweep_bounds):
                continue
            if sweep.contains(bounds):
                # Fully covered groups need no expensive boolean operation.
                current[index] = None
                changed = True
            elif part.path.intersects(sweep.path):
                subject = part.polygons if part.polygons is not None else _integer_paths(part.path)
                polygons = _difference(subject, sweep.polygons)
                current[index] = _InkPart(_polygon_path(polygons), polygons) if polygons else None
                changed = True
    return tuple(part for part in current if part is not None) if changed else parts


def _parts_world_path(stroke: Stroke, parts: tuple[_InkPart, ...]) -> QPainterPath:
    result = QPainterPath()
    result.setFillRule(Qt.FillRule.WindingFill)
    for part in parts:
        result.addPath(part.path)
    if stroke.offset_x or stroke.offset_y:
        result = QTransform.fromTranslate(stroke.offset_x, stroke.offset_y).map(result)
    return result


def _replay_parts(stroke: Stroke) -> tuple[_InkPart, ...]:
    parts = _raw_ink_parts(stroke)
    for mask in stroke.erase_masks:
        parts = _subtract_parts(parts, mask.points, mask.radius)
    return parts


def erase_stroke(stroke: Stroke, points: list[tuple[float, float]], radius: float,
                 parts: tuple[_InkPart, ...] | None = None, *, sweeps=None
                 ) -> tuple[QPainterPath, tuple[_InkPart, ...]] | None:
    """Subtract WORLD eraser input from bounded LOCAL ink groups.

    The groups form the same mathematical union as the original outline. Keeping
    them between edits avoids repeatedly normalizing thousands of overlapping
    circles in one global Qt boolean operation. Reopening uses this same path.
    Return None when the gesture does not touch visible ink.
    """
    if parts is None:
        cached = _PATH_CACHE.get(id(stroke))
        parts = cached[3] if cached is not None else None
        if parts is None:
            parts = _replay_parts(stroke)
    local_points = [(x - stroke.offset_x, y - stroke.offset_y) for x, y in points]
    erased = _subtract_parts(parts, local_points, radius, sweeps)
    if erased is parts:
        return None
    return _parts_world_path(stroke, erased), erased


# The cache holds its Stroke strongly, preventing Python object-id reuse. Scene
# edits replace strokes, so entries never become stale through an internal edit.
_PATH_CACHE: OrderedDict[int, tuple[Stroke, QPainterPath, int, tuple[_InkPart, ...] | None]] = OrderedDict()
_PATH_CACHE_BYTES = 0
_PATH_CACHE_LIMIT = 64 * 1024 * 1024


def clear_geometry_cache() -> None:
    global _PATH_CACHE_BYTES
    _PATH_CACHE.clear()
    _PATH_CACHE_BYTES = 0


def seed_visible_path(stroke: Stroke, path: QPainterPath,
                      parts: tuple[_InkPart, ...] | None = None) -> None:
    """Reuse a completed live WORLD outline without replaying all raw samples."""
    global _PATH_CACHE_BYTES
    key = id(stroke)
    previous = _PATH_CACHE.pop(key, None)
    if previous is not None:
        _PATH_CACHE_BYTES -= previous[2]
    # Entries retain the model strongly. Count its raw input as well, otherwise
    # a fully erased large stroke could cost almost zero while retaining MBs.
    cost = (path.elementCount() * 24 + len(stroke.samples) * 128
            + sum(len(mask.points) * 64 + 128 for mask in stroke.erase_masks) + 512)
    if parts is not None:
        cost += sum(part.path.elementCount() * 24 + 128
                    + sum(len(polygon) * 144 + 128 for polygon in (part.polygons or ())) for part in parts)
    if cost > _PATH_CACHE_LIMIT:
        return
    while _PATH_CACHE and _PATH_CACHE_BYTES + cost > _PATH_CACHE_LIMIT:
        _, (_, _, old_cost, _) = _PATH_CACHE.popitem(last=False)
        _PATH_CACHE_BYTES -= old_cost
    _PATH_CACHE[key] = (stroke, QPainterPath(path), cost, parts)
    _PATH_CACHE_BYTES += cost


def visible_path(stroke: Stroke) -> QPainterPath:
    """Return the filled, erased ink outline, translated to WORLD coordinates.

    Model instances are treated as immutable once handed to Scene. Construct a
    replacement Stroke to change its geometry, rather than modifying it in place.
    """
    key = id(stroke)
    cached = _PATH_CACHE.get(key)
    if cached is not None:
        _PATH_CACHE.move_to_end(key)
        return QPainterPath(cached[1])
    if stroke.erase_masks:
        parts = _replay_parts(stroke)
        path = _parts_world_path(stroke, parts)
        seed_visible_path(stroke, path, parts)
        return QPainterPath(path)
    builder = IncrementalStrokeBuilder(stroke.brush)
    for sample in stroke.samples:
        builder._append_outline(sample, builder.path)
    builder._finish_outline(builder.path)
    path = builder.path
    if stroke.offset_x or stroke.offset_y:
        path = QTransform.fromTranslate(stroke.offset_x, stroke.offset_y).map(path)
    seed_visible_path(stroke, path)
    return QPainterPath(path)


def stroke_bounds(stroke: Stroke) -> QRectF:
    return visible_path(stroke).boundingRect()


def conservative_stroke_bounds(stroke: Stroke) -> QRectF:
    """Cheap spatial-index bounds without constructing the full ink outline.

    The online smoother is a convex combination of raw positions, so all filled
    ink lies inside the raw point bounds expanded by the maximum brush radius.
    Erasing can only reduce that bound. Exact bounds remain available for fit.
    """
    if not stroke.samples:
        return QRectF()
    first = stroke.samples[0]
    left = right = first.x
    top = bottom = first.y
    for sample in stroke.samples[1:]:
        left = min(left, sample.x)
        right = max(right, sample.x)
        top = min(top, sample.y)
        bottom = max(bottom, sample.y)
    radius = max(0.05, stroke.brush.width) / 2
    return QRectF(left + stroke.offset_x - radius, top + stroke.offset_y - radius,
                  right - left + radius * 2, bottom - top + radius * 2)
